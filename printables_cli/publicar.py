"""Planning and execution of `publish` and `update` against the Printables API (docs/API.md)."""
from __future__ import annotations

import datetime as dt
import difflib
import re
import sys
from pathlib import Path

from . import api, manifest, session

LISTAS = ("stls", "gcodes", "slas", "otherFiles")
CAMPOS_ARCHIVO = ("id", "folder", "name", "note")
CAMPOS_GCODE = ("weight", "nozzleDiameter", "layerHeight", "printDuration", "excludeFromTotalSum")


class ErrorPlan(ValueError):
    """The manifest does not match Printables (category, license or remix)."""


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", t.lower())


# ---------- name to ID resolution (queries only) ----------

def resolver_categoria(cats: list[dict], texto: str) -> dict:
    partes = [_norm(p) for p in texto.split(">")]
    rutas = {c["id"]: [_norm(p["name"]) for p in c["path"]] for c in cats}
    exactas = [c for c in cats if rutas[c["id"]] == partes]
    if not exactas and len(partes) == 1:
        exactas = [c for c in cats if rutas[c["id"]][-1] == partes[0]]
    if len(exactas) == 1:
        if exactas[0].get("children"):
            hijos = ", ".join(h["name"] for h in exactas[0]["children"])
            raise ErrorPlan(f"Category «{texto}» has subcategories; pick one: {hijos}")
        return exactas[0]
    nombres = {" > ".join(p["name"] for p in c["path"]): c for c in cats if not c.get("children")}
    cerca = difflib.get_close_matches(texto, nombres, n=5, cutoff=0.4)
    raise ErrorPlan(f"Category «{texto}» not found." + (f" Similar: {'; '.join(cerca)}" if cerca else ""))


def resolver_licencia(lics: list[dict], texto: str) -> dict:
    n = re.sub(r"(40|4)$", "", _norm(texto)) if _norm(texto).startswith("cc") else _norm(texto)
    for lic in lics:
        if n in (_norm(lic["abbreviation"] or ""), _norm(lic["name"]), lic["id"]):
            if not lic.get("isSelectable") or not lic.get("freeModels"):
                raise ErrorPlan(f"License «{lic['name']}» cannot be chosen for a free model")
            return lic
    raise ErrorPlan(f"License «{texto}» not found. Available: "
                    + ", ".join(x["abbreviation"] for x in lics if x.get("isSelectable") and x.get("freeModels")))


def resolver_remix(c: api.API, lics: list[dict], urls: list[str], licencia: dict, mi_id: str) -> list[dict]:
    """Each parent becomes {ref, titulo, autor, licencia}. `ref` is the Printables model ID or the external URL."""
    por_id = {x["id"]: x for x in lics}
    padres = []
    for url in urls:
        m = re.search(r"printables\.com/(?:[a-z]{2}/)?model/(\d+)", url)
        if m:
            p = c.padre_printables(m.group(1))
            if not p:
                raise ErrorPlan(f"remix_de: model {url} does not exist")
            lic, propio = p.get("license"), p["user"]["id"] == mi_id and p.get("authorship") == "author"
            padre = {"ref": p["id"], "titulo": p["name"], "autor": "@" + p["user"]["handle"], "licencia": lic}
        else:
            info = c.padre_url(url) or {}
            lic, propio = info.get("license"), False
            padre = {"ref": url, "titulo": info.get("title") or url, "autor": info.get("author") or "?",
                     "licencia": lic}
        if lic and not propio:
            completa = por_id.get(lic["id"], lic)
            permitidas = [x["id"] for x in completa.get("allowedLicensesAfterRemixing") or []]
            if completa.get("disallowRemixing"):
                raise ErrorPlan(f"remix_de: «{padre['titulo']}» ({completa['name']}) does not allow remixes")
            if licencia["id"] not in permitidas:
                validas = ", ".join(por_id[i]["abbreviation"] for i in permitidas
                                    if i in por_id and por_id[i].get("isSelectable") and por_id[i].get("freeModels")
                                    and not por_id[i].get("disallowRemixing"))
                raise ErrorPlan(f"remix_de: «{padre['titulo']}» has license {completa.get('abbreviation') or completa['name']}; "
                                f"a remix cannot use {licencia['abbreviation']}. Compatible: {validas}")
        padres.append(padre)
    return padres


def plan_publicacion(c: api.API, m: manifest.Modelo, mi_id: str) -> dict:
    lics = c.licencias()
    cat = resolver_categoria(c.categorias(), m.categoria)
    lic = resolver_licencia(lics, m.licencia)
    padres = resolver_remix(c, lics, m.remix_de, lic, mi_id)
    return {
        "titulo": m.titulo, "resumen": m.resumen,
        "categoria": {"id": cat["id"], "ruta": " > ".join(p["name"] for p in cat["path"])},
        "licencia": {"id": lic["id"], "nombre": lic["name"], "abreviatura": lic["abbreviation"]},
        "etiquetas": m.etiquetas,
        "autoria": "remix" if padres else "author",
        "remix_de": padres, "remix_descripcion": m.remix_descripcion,
        "generado_con_ia": m.generado_con_ia,
        "archivos": [{"nombre": p.name, "bytes": p.stat().st_size} for p in m.archivos],
        "fotos": [{"nombre": p.name, "bytes": p.stat().st_size, "principal": i == 0} for i, p in enumerate(m.fotos)],
        "descripcion_html_caracteres": len(m.descripcion_html),
    }


def variables_ficha(m: manifest.Modelo, plan: dict) -> dict:
    return {
        "name": m.titulo, "summary": m.resumen, "description": m.descripcion_html,
        "category": plan["categoria"]["id"], "license": plan["licencia"]["id"], "tags": m.etiquetas,
        "authorship": plan["autoria"], "remixParents": [p["ref"] for p in plan["remix_de"]],
        "remixDescription": m.remix_descripcion if plan["remix_de"] else None,
        "aiGenerated": m.generado_con_ia,
    }


# ---------- uploads ----------

def subir_todo(c: api.API, rutas: list[Path]) -> list[dict]:
    """Upload in order, one at a time (human pace), and return each upload result in the same order."""
    ids = []
    for i, r in enumerate(rutas, 1):
        _log(f"  ↑ [{i}/{len(rutas)}] {r.name} ({r.stat().st_size / 1024:.0f} kB)")
        ids.append(c.subir(r))
    if not ids:
        return []
    _log("  ... Printables is processing the uploads")
    hechos = c.esperar_subidas(ids)
    return [hechos[i] for i in ids]


def _entradas(subidas: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {k: [] for k in (*LISTAS, "images")}
    for s in subidas:
        for k in LISTAS:
            out[k] += [{x: f.get(x) for x in CAMPOS_ARCHIVO} for f in s.get(k) or []]
        out["images"] += [{"id": im["id"]} for im in s.get("images") or []]
    return out


# ---------- publish ----------

def publicar(c: api.API, m: manifest.Modelo, plan: dict, borrador: bool) -> dict:
    _log(f"Uploading {len(m.archivos)} files and {len(m.fotos)} photos")
    nuevos = _entradas(subir_todo(c, m.archivos + m.fotos))
    if len(nuevos["images"]) != len(m.fotos):
        raise api.ErrorAPI(f"Printables recognized {len(nuevos['images'])} of {len(m.fotos)} images")
    if sum(len(nuevos[k]) for k in LISTAS) != len(m.archivos):
        raise api.ErrorAPI("Printables did not recognize all the uploaded files")
    v = {**variables_ficha(m, plan), "draft": borrador, "nsfw": False, "politicalContent": False,
         "club": False, "price": 0, "excludeCommercialUsage": False,
         **{k: val for k, val in nuevos.items() if val}}
    _log("Saving the model" + (" as a draft" if borrador else " and publishing"))
    out = c.guardar_modelo(v)
    return _recibo(c, m.carpeta, out, borrador)


def _recibo(c: api.API, carpeta: Path, out: dict, borrador: bool) -> dict:
    mid = out["id"]
    url = f"{session.BASE}/model/{mid}" + (f"-{out['slug']}" if out.get("slug") else "")
    res = {"id": mid, "url": url, "editar": f"{session.BASE}/model/{mid}/edit",
           "publicado": bool(out.get("datePublished")), "pendiente_de_aprobacion": False}
    escribir_recibo(carpeta, res)          # before any step that can fail: the model already exists
    if not borrador and not res["publicado"]:
        res["pendiente_de_aprobacion"] = _pedir_aprobacion(c, mid)
    return res


def _pedir_aprobacion(c: api.API, mid: str) -> bool:
    """Accounts with prior approval: the model stays a draft and a publish request is sent."""
    det = c.modelo_edicion(mid) or {}
    if not det.get("publishApprovalRequired"):
        return False
    if not any(r.get("status") == "PENDING" for r in det.get("publishRequests") or []):
        c.solicitar_publicacion(mid)
    return True


def escribir_recibo(carpeta: Path, res: dict) -> None:
    (carpeta / manifest.RECIBO).write_text(
        f'id = "{res["id"]}"\nurl = "{res["url"]}"\npublished = {"true" if res["publicado"] else "false"}\n'
        f'date = "{dt.date.today()}"\n', encoding="utf-8")


# ---------- update ----------

def variables_actuales(det: dict) -> dict:
    """What the web app resends on every save: the full listing as it is on Printables."""
    return {
        "id": det["id"], "draft": not det.get("datePublished"),
        "name": det["name"], "summary": det.get("summary") or "", "description": det.get("description") or "",
        "category": (det.get("category") or {}).get("id"), "license": (det.get("license") or {}).get("id"),
        "printer": (det.get("printer") or {}).get("id"),
        "tags": [t["name"] for t in det.get("tags") or []],
        "authorship": det.get("authorship"),
        "remixParents": [p.get("modelId") or p.get("url") for p in det.get("remixParents") or []],
        "remixDescription": det.get("remixDescription") if det.get("authorship") == "remix" else None,
        "nsfw": bool(det.get("nsfw")), "aiGenerated": det.get("aiGenerated"),
        "politicalContent": bool(det.get("politicalContent")),
        "club": bool(det.get("club")), "price": det.get("price") or 0,
        "excludeCommercialUsage": bool(det.get("excludeCommercialUsage")),
    }


def _actual(lista: str, f: dict) -> dict:
    e = {x: f.get(x) for x in CAMPOS_ARCHIVO}
    if lista == "gcodes":
        e.update({x: f.get(x) for x in CAMPOS_GCODE})
        e["material"] = (f.get("material") or {}).get("id")
    return e


def _nombre_foto(im: dict) -> str:
    return _norm(Path(im.get("filePath") or "").stem)


def plan_actualizacion(det: dict, m: manifest.Modelo, secciones: set[str]) -> dict:
    """What `update` would change. It never removes files or photos already on Printables."""
    plan: dict = {"id": det["id"], "titulo_actual": det["name"], "borrador": not det.get("datePublished"),
                  "secciones": sorted(secciones)}
    if "archivos" in secciones:
        ya = {f["name"].lower(): (k, f) for k in LISTAS for f in det.get(k) or []}
        subir, igual, reemplaza = [], [], []
        for p in m.archivos:
            hay = ya.get(p.name.lower())
            if hay and hay[1].get("fileSize") == p.stat().st_size:
                igual.append(p.name)
            else:
                subir.append(p)
                if hay:
                    reemplaza.append(p.name)
        plan["archivos"] = {"subir": subir, "sin_cambios": igual, "reemplaza": reemplaza,
                            "se_conservan": sorted(set(n for n in ya) - {p.name.lower() for p in m.archivos})}
    if "fotos" in secciones:
        ya_f = {_nombre_foto(im) for im in det.get("images") or []}
        plan["fotos"] = {"subir": [p for p in m.fotos if _norm(p.stem) not in ya_f],
                         "sin_cambios": [p.name for p in m.fotos if _norm(p.stem) in ya_f],
                         "ya_en_printables": len(det.get("images") or [])}
    return plan


def actualizar(c: api.API, det: dict, m: manifest.Modelo | None, plan: dict, ficha: dict | None,
               publicar_ahora: bool) -> dict:
    v = variables_actuales(det)
    if ficha:
        v.update(ficha)
    if plan.get("archivos", {}).get("subir"):
        _log("Uploading files")
        nuevos = _entradas(subir_todo(c, plan["archivos"]["subir"]))
        for k in LISTAS:
            if not nuevos[k]:
                continue
            nombres = {e["name"].lower() for e in nuevos[k]}
            v[k] = [_actual(k, f) for f in det.get(k) or [] if f["name"].lower() not in nombres] + nuevos[k]
        # a replaced file never changes list (e.g. .stl to .3mf with the same name):
        # the name includes the extension and Printables classifies by extension.
    if plan.get("fotos", {}).get("subir"):
        _log("Uploading photos")
        nuevas = _entradas(subir_todo(c, plan["fotos"]["subir"]))["images"]
        v["images"] = [{"id": im["id"]} for im in det.get("images") or []] + nuevas
    if publicar_ahora:
        v["draft"] = False
    _log("Saving the model")
    out = c.guardar_modelo(v)
    res = {"id": out["id"], "url": f"{session.BASE}/model/{out['id']}" + (f"-{out['slug']}" if out.get("slug") else ""),
           "publicado": bool(out.get("datePublished")), "pendiente_de_aprobacion": False}
    if publicar_ahora and not res["publicado"]:
        res["pendiente_de_aprobacion"] = _pedir_aprobacion(c, out["id"])
    return res
