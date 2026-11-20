"""printables: unofficial command line client for Printables (see docs/)."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import api, manifest, publicar, session

OK, USO, SESION, FALLO = 0, 2, 3, 4
TIPOS = {"stls": "stl", "gcodes": "gcode", "slas": "sla", "otherFiles": "other"}


def _con_api(fn):
    """Connect to the dedicated Chrome, open a work tab and run fn(API)."""
    with sync_playwright() as p:
        try:
            b, ctx = session.conectar(p, abrir_si_falta=False)
        except session.SinChrome as e:
            print(e, file=sys.stderr)
            return SESION
        pg = session.pagina(ctx)
        try:
            return fn(api.API(pg))
        except api.ErrorAPI as e:
            print(f"Printables error: {e}", file=sys.stderr)
            return FALLO
        except publicar.ErrorPlan as e:
            print(f"The manifest does not match Printables: {e}", file=sys.stderr)
            return USO
        finally:
            pg.close()


def cmd_login(a) -> int:
    if not session.chrome_activo():
        session.lanzar_chrome(session.BASE + "/login")
        print("Opened the dedicated Chrome. Log in to Printables there (leave that Chrome open), then run: printables whoami")
        return OK
    return cmd_whoami(a)


def cmd_whoami(a) -> int:
    def run(c: api.API):
        me = c.me()
        if a.json:
            print(json.dumps({"usuario": me}))
        elif me:
            print(f"Logged in as {me['publicUsername']} (@{me['handle']}, id {me['id']})")
        else:
            print("Not logged in: log in to Printables in the dedicated Chrome.", file=sys.stderr)
        return OK if me else SESION
    return _con_api(run)


def cmd_list(a) -> int:
    def run(c: api.API):
        me = c.me()
        if not me:
            print("Not logged in.", file=sys.stderr)
            return SESION
        ms = c.mis_modelos(me["id"])
        if a.json:
            print(json.dumps(ms, ensure_ascii=False, indent=1))
            return OK
        if not ms:
            print("You have no published models.")
        for m in ms:
            print(f"{m['id']:>8}  {m.get('name','')[:60]:<60}  {session.BASE}/model/{m['id']}")
        return OK
    return _con_api(run)


def cmd_download(a) -> int:
    mid = api.id_modelo(a.modelo)
    dest = Path(a.dest or f"printables-{mid}").expanduser()
    dest.mkdir(parents=True, exist_ok=True)

    def run(c: api.API):
        f = c.archivos(mid)
        bajados, fallos = [], []
        for campo, tipo in TIPOS.items():
            for arch in f.get(campo) or []:
                try:
                    link = c.enlace_descarga(mid, arch["id"], tipo)
                    r = c.page.context.request.get(link, timeout=120_000)
                    if not r.ok:
                        raise api.ErrorAPI(f"HTTP {r.status}")
                    (dest / arch["name"]).write_bytes(r.body())
                    bajados.append(arch["name"])
                    print(f"  ✓ {arch['name']}")
                except Exception as e:
                    fallos.append(arch["name"])
                    print(f"  ✗ {arch['name']}: {e}", file=sys.stderr)
        (dest / "fuente.toml").write_text(
            f'url = "{session.BASE}/model/{mid}"\ndate = "{dt.date.today()}"\n'
            f"files = {json.dumps(bajados, ensure_ascii=False)}\n"
        )
        print(f"{len(bajados)} files in {dest}")
        return OK if bajados else FALLO
    return _con_api(run)


def _mostrar_plan(plan: dict, modo: str) -> None:
    print(f"Plan ({modo}):")
    print(f"  title       {plan['titulo']}")
    if plan["resumen"]:
        print(f"  summary     {plan['resumen']}")
    print(f"  category    {plan['categoria']['ruta']} (id {plan['categoria']['id']})")
    print(f"  license     {plan['licencia']['abreviatura']} — {plan['licencia']['nombre']} (id {plan['licencia']['id']})")
    print(f"  tags        {', '.join(plan['etiquetas']) or '—'}")
    print(f"  authorship  {plan['autoria']}" + ("; AI generated" if plan["generado_con_ia"] else ""))
    for p in plan["remix_de"]:
        lic = (p.get("licencia") or {}).get("name") or "unknown license"
        print(f"  remix of    {p['titulo']} — {p['autor']} ({lic}) → {p['ref']}")
    print(f"  description {plan['descripcion_html_caracteres']} characters of HTML")
    for f in plan["archivos"]:
        print(f"  file        {f['nombre']} ({f['bytes'] / 1024:.0f} kB)")
    for f in plan["fotos"]:
        print(f"  photo       {f['nombre']} ({f['bytes'] / 1024:.0f} kB)" + (" <- main" if f["principal"] else ""))


def _cargar(carpeta: str) -> manifest.Modelo | None:
    try:
        return manifest.cargar(Path(carpeta))
    except manifest.ErrorManifiesto as e:
        print(f"Invalid manifest: {e}", file=sys.stderr)
        return None


def cmd_publish(a) -> int:
    m = _cargar(a.carpeta)
    if not m:
        return USO
    recibo = m.carpeta / manifest.RECIBO
    if recibo.exists():
        print(f"{recibo} already exists: this model was already created. Use: printables update <id> --folder {m.carpeta}",
              file=sys.stderr)
        return USO
    modo = "draft" if a.borrador else "publish"

    def run(c: api.API):
        me = c.me()
        if not me:
            print("Not logged in: log in to Printables in the dedicated Chrome.", file=sys.stderr)
            return SESION
        plan = publicar.plan_publicacion(c, m, me["id"])
        if a.dry_run:
            if a.json:
                print(json.dumps({"dry_run": True, "modo": modo, "plan": plan}, ensure_ascii=False, indent=1))
            else:
                _mostrar_plan(plan, modo)
                print("Dry run: no mutation was called.")
            return OK
        if not a.json:
            _mostrar_plan(plan, modo)
        res = publicar.publicar(c, m, plan, a.borrador)
        _reportar(res, a.json, recibo)
        return OK
    return _con_api(run)


def _reportar(res: dict, como_json: bool, recibo: Path | None = None) -> None:
    if como_json:
        print(json.dumps(res, ensure_ascii=False))
        return
    if res["publicado"]:
        estado = "published"
    elif res["pendiente_de_aprobacion"]:
        estado = "draft; Printables must approve the publication (request sent)"
    else:
        estado = "draft"
    print(f"Model {res['id']} ({estado}): {res['url']}")
    if not res["publicado"]:
        print(f"Review and edit: {session.BASE}/model/{res['id']}/edit")
    if recibo:
        print(f"Receipt: {recibo}")


def cmd_update(a) -> int:
    mid = api.id_modelo(a.modelo)
    secciones = {s for s in ("descripcion", "archivos", "fotos") if getattr(a, s)}
    if not secciones and not a.publicar:
        secciones = {"descripcion", "archivos", "fotos"}
    m = None
    if secciones:
        m = _cargar(a.carpeta)
        if not m:
            return USO

    def run(c: api.API):
        me = c.me()
        if not me:
            print("Not logged in: log in to Printables in the dedicated Chrome.", file=sys.stderr)
            return SESION
        det = c.modelo_edicion(mid)
        if not det:
            print(f"Model {mid} does not exist.", file=sys.stderr)
            return FALLO
        if det["user"]["id"] != me["id"]:
            print(f"Model {mid} is not yours.", file=sys.stderr)
            return USO
        plan = publicar.plan_actualizacion(det, m, secciones) if m else \
            {"id": det["id"], "titulo_actual": det["name"], "borrador": not det.get("datePublished"), "secciones": []}
        ficha = None
        if "descripcion" in secciones:
            pp = publicar.plan_publicacion(c, m, me["id"])
            ficha = publicar.variables_ficha(m, pp)
            plan["ficha"] = {k: pp[k] for k in ("titulo", "resumen", "categoria", "licencia", "etiquetas", "autoria",
                                                "remix_de", "descripcion_html_caracteres")}
        plan["publicar"] = bool(a.publicar)
        visible = json.loads(json.dumps(plan, default=lambda p: p.name, ensure_ascii=False))
        if a.json and a.dry_run:
            print(json.dumps({"dry_run": True, "plan": visible}, ensure_ascii=False, indent=1))
        elif not a.json:
            estado = "draft" if plan["borrador"] else "published"
            print(f"Model {mid} «{plan['titulo_actual']}» ({estado}); sections: {', '.join(plan['secciones']) or '—'}")
            if ficha:
                f = visible["ficha"]
                print(f"  details     title «{f['titulo']}», {f['categoria']['ruta']}, {f['licencia']['abreviatura']}, "
                      f"{len(f['etiquetas'])} tags, {f['descripcion_html_caracteres']} characters of HTML")
            for sec in ("archivos", "fotos"):
                if sec in visible:
                    s = visible[sec]
                    print(f"  {sec:<11} upload: {', '.join(s['subir']) or '—'}; unchanged: {', '.join(s['sin_cambios']) or '—'}")
                    if s.get("reemplaza"):
                        print(f"              replaces: {', '.join(s['reemplaza'])}")
            if a.publicar:
                print("  publish     yes")
        if a.dry_run:
            if not a.json:
                print("Dry run: no mutation was called.")
            return OK
        res = publicar.actualizar(c, det, m, plan, ficha, a.publicar)
        recibo = Path(a.carpeta).expanduser().resolve() / manifest.RECIBO
        if recibo.exists() and f'id = "{res["id"]}"' in recibo.read_text():
            publicar.escribir_recibo(recibo.parent, res)
        _reportar(res, a.json)
        return OK
    return _con_api(run)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="printables", description="Unofficial command line client for Printables")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("login", help="open the dedicated Chrome so you can log in to Printables")
    s.add_argument("--json", action="store_true", help="machine-readable output"); s.set_defaults(fn=cmd_login)
    s = sub.add_parser("whoami", help="show the logged-in user")
    s.add_argument("--json", action="store_true", help="machine-readable output"); s.set_defaults(fn=cmd_whoami)
    s = sub.add_parser("list", help="list your published models")
    s.add_argument("--json", action="store_true", help="machine-readable output"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("download", help="download the files of a model")
    s.add_argument("modelo", help="model URL or ID")
    s.add_argument("--dest", help="destination folder (default: printables-<id>)")
    s.set_defaults(fn=cmd_download)
    s = sub.add_parser("publish", help="create a model from a folder with modelo.toml (publishes by default)")
    s.add_argument("carpeta", metavar="folder", help="folder with modelo.toml, descripcion.md, files and photos")
    s.add_argument("--draft", "--borrador", dest="borrador", action="store_true", help="save the model as a draft instead of publishing it")
    s.add_argument("--dry-run", action="store_true", help="validate and show the plan without touching Printables")
    s.add_argument("--json", action="store_true", help="machine-readable output"); s.set_defaults(fn=cmd_publish)
    s = sub.add_parser("update", help="update one of your models from its folder, without removing what it already has")
    s.add_argument("modelo", metavar="model", help="model ID or URL")
    s.add_argument("--folder", "--carpeta", dest="carpeta", metavar="folder", default=".", help="folder with modelo.toml (default: current)")
    s.add_argument("--description", "--descripcion", dest="descripcion", action="store_true",
                   help="listing details: title, summary, description, category, tags, license and remix")
    s.add_argument("--files", "--archivos", dest="archivos", action="store_true", help="upload new or changed files")
    s.add_argument("--photos", "--fotos", dest="fotos", action="store_true", help="add the photos that are missing")
    s.add_argument("--publish", "--publicar", dest="publicar", action="store_true", help="publish the draft")
    s.add_argument("--dry-run", action="store_true", help="validate and show the plan without touching Printables")
    s.add_argument("--json", action="store_true", help="machine-readable output"); s.set_defaults(fn=cmd_update)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
