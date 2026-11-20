"""Printables session through a dedicated Chrome (CDP on 127.0.0.1).

Printables sits behind Cloudflare, which blocks browsers launched by automation.
The CLI therefore attaches to a regular Google Chrome, opened with its own profile and a
local debugging port, where the user logs in by hand (D04, D07).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, Playwright

BASE = "https://www.printables.com"
CONFIG = Path(os.environ.get("PRINTABLES_CLI_HOME", Path.home() / ".config" / "printables-cli"))
CHROME_PROFILE = CONFIG / "chrome"
CACHE = Path.home() / ".cache" / "printables-cli"
GQL_LOG = CACHE / "graphql.jsonl"
PORT = int(os.environ.get("PRINTABLES_CDP_PORT", "9333"))
CDP = f"http://127.0.0.1:{PORT}"


class SinChrome(RuntimeError):
    pass


def preparar_dirs() -> None:
    for d in (CONFIG, CHROME_PROFILE, CACHE):
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG, 0o700)
    os.chmod(CHROME_PROFILE, 0o700)


def chrome_activo() -> bool:
    try:
        with urllib.request.urlopen(CDP + "/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def lanzar_chrome(url: str = BASE) -> None:
    """Open the dedicated Chrome (same as launching it by hand with the documented flags)."""
    preparar_dirs()
    exe = next((p for n in ("google-chrome", "google-chrome-stable") if (p := shutil.which(n))), None)
    if not exe:
        raise SinChrome("google-chrome not found in PATH; start Chrome by hand (see README)")
    env = dict(os.environ)
    subprocess.Popen(
        [exe, f"--user-data-dir={CHROME_PROFILE}", f"--remote-debugging-port={PORT}",
         "--remote-debugging-address=127.0.0.1", "--no-first-run", url],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    for _ in range(30):
        if chrome_activo():
            return
        time.sleep(1)
    raise SinChrome("The dedicated Chrome did not respond on port %d" % PORT)


def conectar(p: Playwright, abrir_si_falta: bool = True) -> tuple[Browser, BrowserContext]:
    if not chrome_activo():
        if not abrir_si_falta:
            raise SinChrome("Open the dedicated Chrome (see README) or run: printables login")
        lanzar_chrome()
    b = p.chromium.connect_over_cdp(CDP)
    ctx = b.contexts[0] if b.contexts else b.new_context()
    return b, ctx


def registrar_api(ctx: BrowserContext) -> None:
    ctx.on("request", _registrar)


def _registrar(req) -> None:
    """Log GraphQL operation and query (no headers or cookies) for API exploration."""
    if "graphql" not in req.url or req.method != "POST":
        return
    try:
        body = req.post_data_json
    except Exception:
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    items = body if isinstance(body, list) else [body]
    with GQL_LOG.open("a") as f:
        for it in items:
            if isinstance(it, dict):
                f.write(json.dumps({"t": time.time(), "url": req.url, "op": it.get("operationName"),
                                    "query": it.get("query"), "vars": it.get("variables")}) + "\n")


def pagina(ctx: BrowserContext) -> Page:
    """New tab for working without touching the tabs the user has open."""
    return ctx.new_page()
