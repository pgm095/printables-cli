"""Client for the internal Printables GraphQL API, executed inside the logged-in Chrome."""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
from importlib import resources
from pathlib import Path

from playwright.sync_api import Page

from . import session

URL = "https://api.printables.com/graphql/"
OPS: dict[str, str] = json.loads(resources.files(__package__).joinpath("operaciones.json").read_text())


class ErrorAPI(RuntimeError):
    pass


class API:
    def __init__(self, page: Page):
        self.page = page
        if not page.url.startswith(session.BASE):
            page.goto(session.BASE + "/robots.txt", wait_until="domcontentloaded")

    def gql(self, op: str, variables: dict | None = None, query: str | None = None) -> dict:
        q = query or OPS[op]
        r = self.page.evaluate(
            """async ([url, op, q, v]) => {
                const r = await fetch(url, {method: 'POST', credentials: 'include',
                    headers: {'content-type': 'application/json'},
                    body: JSON.stringify({operationName: op, query: q, variables: v})});
                return [r.status, await r.text()];
            }""",
            [URL, op, q, variables or {}],
        )
        status, text = r
        if status != 200:
            raise ErrorAPI(f"{op}: HTTP {status}")
        data = json.loads(text)
        if data.get("errors"):
            raise ErrorAPI(f"{op}: " + "; ".join(e.get("message", "?") for e in data["errors"]))
        return data["data"]

    def me(self) -> dict | None:
        d = self.gql("Me", query="query Me { me { id handle publicUsername } }")
        return d.get("me")

    def mis_modelos(self, uid: str) -> list[dict]:
        items, cursor = [], None
        while True:
            d = self.gql("UserModels", {"id": uid, "limit": 36, "cursor": cursor, "ordering": "-first_publish"})
            page = d["userModels"]
            items += page["items"]
            cursor = page.get("cursor")
            if not cursor or not page["items"]:
                return items

    def archivos(self, model_id: str) -> dict:
        return self.gql("ModelFiles", {"id": model_id})["model"]

    def enlace_descarga(self, model_id: str, file_id: str, tipo: str) -> str:
        d = self.gql("GetDownloadLink", {"id": file_id, "modelId": model_id, "fileType": tipo,
                                          "source": "model_detail"})["getDownloadLink"]
        if not d.get("ok"):
            raise ErrorAPI("GetDownloadLink: " + json.dumps(d.get("errors")))
        return d["output"]["link"]


    # ---------- escrituras (flow in docs/API.md) ----------

    def licencias(self) -> list[dict]:
        return self.gql("Licenses")["licenses"]

    def categorias(self) -> list[dict]:
        return self.gql("Categories")["categories"]

    def modelo_edicion(self, model_id: str) -> dict | None:
        return self.gql("ModelEditDetail", {"id": model_id})["model"]

    def padre_printables(self, model_id: str) -> dict | None:
        return self.gql("RemixParentModel", {"id": model_id})["model"]

    def padre_url(self, url: str) -> dict | None:
        return self.gql("SearchRemixParentByUrl", {"url": url})["remixUrlInfo"]

    def subir(self, ruta: Path) -> str:
        """Upload a file or photo and return the upload id (fileUpload)."""
        datos = ruta.read_bytes()
        mime = mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"
        dims = self.page.evaluate(
            """async ([b64, nombre, mime]) => {
                const bin = atob(b64), u8 = new Uint8Array(bin.length);
                for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
                globalThis.__pcli_archivo = new File([u8], nombre, {type: mime});
                if (!mime.startsWith('image/')) return null;
                try {
                    const bm = await createImageBitmap(globalThis.__pcli_archivo);
                    return [bm.width, bm.height];
                } catch (e) { return null; }
            }""",
            [base64.b64encode(datos).decode(), ruta.name, mime],
        )
        v = {"fileName": ruta.name, "folder": "", "unzip": False}
        if dims:
            v["imageWidth"], v["imageHeight"] = dims
        try:
            up = _ok(self.gql("UploadModel", v)["upload"], "UploadModel")
            campos = up["uploadData"]["fields"]
            if isinstance(campos, str):
                campos = json.loads(campos)
            estado = self.page.evaluate(
                """async ([url, campos]) => {
                    const fd = new FormData();
                    for (const [k, v] of Object.entries(campos)) fd.set(k, v);
                    fd.append('file', globalThis.__pcli_archivo);
                    try {
                        const r = await fetch(url, {method: 'POST', body: fd});
                        return r.status;
                    } catch (e) { return -1; }
                }""",
                [up["uploadData"]["url"], campos],
            )
        finally:
            self.page.evaluate("() => { delete globalThis.__pcli_archivo; }")
        if not 200 <= estado < 300:
            raise ErrorAPI(f"upload of {ruta.name}: storage responded {estado}")
        fid = up["fileUpload"]["id"]
        _ok(self.gql("UploadModelFinished", {"fileUploadId": fid, "crc32c": crc32c_b64(datos)})["uploadFinished"],
            "UploadModelFinished")
        return fid

    def esperar_subidas(self, ids: list[str], timeout: float = 180) -> dict[str, dict]:
        """Poll until Printables processes the uploads; return {upload id: result}."""
        fin = time.monotonic() + timeout
        while True:
            fus = self.gql("PollFileUploads", {"ids": ids})["fileUploads"]
            listos = {f["id"]: f for f in fus if f.get("isProcessed")}
            if len(listos) == len(ids):
                break
            if time.monotonic() > fin:
                raise ErrorAPI(f"Printables did not finish processing {len(ids) - len(listos)} uploads in {timeout:.0f} s")
            time.sleep(1.5)
        for f in listos.values():
            malos = [n for n in f.get("notInspectedFiles") or [] if n.get("error")]
            if malos:
                raise ErrorAPI("file rejected: " + "; ".join(
                    f"{n.get('virtual_filename') or n.get('filename')}: {n.get('error')}" for n in malos))
        return listos

    def guardar_modelo(self, variables: dict) -> dict:
        """Create (without `id`) or update a model. `draft: false` publishes."""
        return _ok(self.gql("ModelUpdate", variables)["modelUpdate"], "ModelUpdate")["output"]

    def solicitar_publicacion(self, model_id: str) -> dict:
        return _ok(self.gql("PrintPublishRequest", {"printId": model_id})["printPublishRequest"],
                   "PrintPublishRequest")["output"]


def _ok(r: dict | None, op: str) -> dict:
    if not r or not r.get("ok"):
        errs = "; ".join(f"{e.get('field')}: {', '.join(e.get('messages') or [])}" for e in (r or {}).get("errors") or [])
        raise ErrorAPI(f"{op}: {errs or 'response without ok'}")
    return r


def _tabla_crc32c() -> list[int]:
    t = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0x82F63B78 if c & 1 else c >> 1
        t.append(c)
    return t


_CRC = _tabla_crc32c()


def crc32c_b64(datos: bytes) -> str:
    """CRC32C (Castagnoli) as big-endian base64, as the web app computes it before UploadModelFinished."""
    c = 0xFFFFFFFF
    for b in datos:
        c = _CRC[(c ^ b) & 0xFF] ^ (c >> 8)
    return base64.b64encode(((c ^ 0xFFFFFFFF) & 0xFFFFFFFF).to_bytes(4, "big")).decode()


def id_modelo(url_o_id: str) -> str:
    m = re.search(r"/model/(\d+)", url_o_id) or re.fullmatch(r"(\d+)", url_o_id.strip())
    if not m:
        raise ValueError(f"Unrecognized model: {url_o_id}")
    return m.group(1)
