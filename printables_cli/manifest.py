"""Load and validate `modelo.toml` (see docs/BACKEND_SCHEMA.md) and convert the description to HTML."""
from __future__ import annotations

import html
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

MANIFIESTO = "modelo.toml"
RECIBO = "printables.toml"
FOTOS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
# Extensions Printables classifies by itself; anything else is stored as "other files".
MODELOS = {".stl", ".3mf", ".step", ".stp", ".obj", ".amf", ".scad", ".f3d", ".fcstd", ".zip",
           ".gcode", ".bgcode", ".sl1", ".sl1s", ".pdf", ".dxf", ".svg", ".py", ".txt"}
ETIQUETAS_IMPRESION = {"material": "Material", "capa": "Layer height", "relleno": "Infill",
                       "soportes": "Supports", "boquilla": "Nozzle", "paredes": "Perimeters",
                       "brim": "Brim", "orientacion": "Orientation"}
MAX_TITULO, MAX_RESUMEN = 255, 250
# English aliases for the manifest keys (the Spanish names remain the canonical ones).
ALIAS = {"title": "titulo", "summary": "resumen", "description": "descripcion", "category": "categoria",
         "tags": "etiquetas", "license": "licencia", "files": "archivos", "photos": "fotos",
         "remix_of": "remix_de", "remix_description": "remix_descripcion", "ai_generated": "generado_con_ia",
         "print_settings": "impresion"}
ALIAS_IMPRESION = {"layer_height": "capa", "infill": "relleno", "supports": "soportes", "nozzle": "boquilla",
                   "perimeters": "paredes", "orientation": "orientacion"}


def _normalizar(d: dict, alias: dict[str, str], errores: list[str], donde: str = "") -> dict:
    out: dict = {}
    for k, v in d.items():
        k2 = alias.get(k, k)
        if k2 in out:
            errores.append(f"{donde}`{k}` and `{k2}` are the same key; use only one")
        out[k2] = v
    return out


class ErrorManifiesto(ValueError):
    pass


@dataclass
class Modelo:
    carpeta: Path
    titulo: str
    resumen: str
    descripcion_html: str
    categoria: str
    etiquetas: list[str]
    licencia: str
    archivos: list[Path]
    fotos: list[Path]
    remix_de: list[str] = field(default_factory=list)
    remix_descripcion: str = ""
    generado_con_ia: bool = False
    impresion: dict = field(default_factory=dict)


def cargar(carpeta: Path) -> Modelo:
    carpeta = carpeta.expanduser().resolve()
    ruta = carpeta / MANIFIESTO
    if not ruta.is_file():
        raise ErrorManifiesto(f"Missing {ruta}")
    try:
        d = tomllib.loads(ruta.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ErrorManifiesto(f"{MANIFIESTO} is not valid TOML: {e}") from e

    errores: list[str] = []
    d = _normalizar(d, ALIAS, errores)
    if isinstance(d.get("impresion"), dict):
        d["impresion"] = _normalizar(d["impresion"], ALIAS_IMPRESION, errores, "[impresion] ")

    def texto(clave: str, obligatorio: bool = True, maximo: int | None = None) -> str:
        v = d.get(clave, "")
        if not isinstance(v, str) or (obligatorio and not v.strip()):
            errores.append(f"`{clave}` must be a string" + (" and not empty" if obligatorio else ""))
            return ""
        if maximo and len(v) > maximo:
            errores.append(f"`{clave}` exceeds {maximo} characters ({len(v)})")
        return v.strip()

    def lista(clave: str, obligatorio: bool = True) -> list[str]:
        v = d.get(clave, [])
        if not isinstance(v, list) or any(not isinstance(x, str) or not x.strip() for x in v):
            errores.append(f"`{clave}` must be a list of strings")
            return []
        if obligatorio and not v:
            errores.append(f"`{clave}` cannot be empty")
        if len(set(v)) != len(v):
            errores.append(f"`{clave}` has duplicate items")
        return v

    def rutas(clave: str, permitidas: set[str], que: str) -> list[Path]:
        out = []
        for rel in lista(clave):
            p = (carpeta / rel).resolve()
            if not p.is_relative_to(carpeta):
                errores.append(f"`{clave}`: {rel} is outside the model folder")
            elif not p.is_file():
                errores.append(f"`{clave}`: {rel} does not exist")
            elif p.stat().st_size == 0:
                errores.append(f"`{clave}`: {rel} is empty")
            elif p.suffix.lower() not in permitidas:
                errores.append(f"`{clave}`: {rel} is not {que} ({', '.join(sorted(permitidas))})")
            else:
                out.append(p)
        nombres = [p.name.lower() for p in out]
        if len(set(nombres)) != len(nombres):
            errores.append(f"`{clave}` has duplicate file names")
        return out

    titulo = texto("titulo", maximo=MAX_TITULO)
    resumen = texto("resumen", obligatorio=False, maximo=MAX_RESUMEN)
    categoria = texto("categoria")
    licencia = texto("licencia")
    etiquetas = lista("etiquetas", obligatorio=False)
    archivos = rutas("archivos", MODELOS, "a supported file type")
    fotos = rutas("fotos", FOTOS, "an image")
    remix_de = lista("remix_de", obligatorio=False)
    for u in remix_de:
        if not re.match(r"https?://\S+$", u):
            errores.append(f"`remix_de`: {u} is not a URL")
    remix_descripcion = texto("remix_descripcion", obligatorio=bool(remix_de))
    ia = d.get("generado_con_ia", False)
    if not isinstance(ia, bool):
        errores.append("`generado_con_ia` must be true or false")
    impresion = d.get("impresion", {})
    if not isinstance(impresion, dict):
        errores.append("`[impresion]` must be a table")
        impresion = {}

    desc_html = ""
    rel = texto("descripcion")
    if rel:
        p = carpeta / rel
        if not p.is_file():
            errores.append(f"`descripcion`: {rel} does not exist")
        else:
            md = p.read_text(encoding="utf-8").strip()
            if not md:
                errores.append(f"`descripcion`: {rel} is empty")
            desc_html = md_a_html(md) + impresion_html(impresion)

    if errores:
        raise ErrorManifiesto(f"{ruta}:\n  - " + "\n  - ".join(errores))
    return Modelo(carpeta, titulo, resumen, desc_html, categoria, etiquetas, licencia, archivos, fotos,
                  remix_de, remix_descripcion, bool(ia), impresion)


def impresion_html(imp: dict) -> str:
    """`[impresion]` is appended to the end of the description: Printables has no dedicated
    print-settings fields for models without G-code."""
    if not imp:
        return ""
    filas = []
    for k, v in imp.items():
        if isinstance(v, bool):
            v = "yes" if v else "no"
        elif k == "capa" and isinstance(v, (int, float)):
            v = f"{v} mm"
        filas.append(f"<li><strong>{html.escape(ETIQUETAS_IMPRESION.get(k, k.capitalize()))}:</strong> "
                     f"{html.escape(str(v))}</li>")
    return "<h3>Print settings</h3><ul>" + "".join(filas) + "</ul>"


def _en_linea(t: str) -> str:
    t = html.escape(t, quote=False)
    codigos: list[str] = []

    def guarda(m: re.Match) -> str:
        codigos.append(f"<code>{m.group(1)}</code>")
        return f"\x00{len(codigos) - 1}\x00"

    t = re.sub(r"`([^`]+)`", guarda, t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
               lambda m: f'<a href="{m.group(2).replace(chr(34), "%22")}">{m.group(1)}</a>', t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*([^*\s][^*]*)\*(?![\w*])", r"<em>\1</em>", t)
    return re.sub(r"\x00(\d+)\x00", lambda m: codigos[int(m.group(1))], t)


def md_a_html(md: str) -> str:
    """Minimal Markdown to HTML for the Printables editor: headings (#, ##, ###), paragraphs,
    lists (-, *, 1.), links, **bold**, *italic* and `code`."""
    out: list[str] = []
    parrafo: list[str] = []
    lista: str | None = None

    def cierra() -> None:
        nonlocal lista
        if parrafo:
            out.append("<p>" + _en_linea(" ".join(parrafo)) + "</p>")
            parrafo.clear()
        if lista:
            out.append(f"</{lista}>")
            lista = None

    for linea in md.splitlines():
        s = linea.strip()
        if not s:
            cierra()
        elif m := re.match(r"(#{1,3})\s+(.+)", s):
            cierra()
            n = len(m.group(1)) + 1          # "#" becomes h2: the page h1 is the model title
            out.append(f"<h{n}>{_en_linea(m.group(2))}</h{n}>")
        elif m := re.match(r"([-*]|\d+[.)])\s+(.+)", s):
            tipo = "ul" if m.group(1) in "-*" else "ol"
            if parrafo or lista != tipo:
                cierra()
                out.append(f"<{tipo}>")
                lista = tipo
            out.append(f"<li>{_en_linea(m.group(2))}</li>")
        elif lista and linea.startswith((" ", "\t")):
            out[-1] = out[-1][:-5] + " " + _en_linea(s) + "</li>"   # list item continuation
        else:
            if lista:
                cierra()
            parrafo.append(s)
    cierra()
    return "".join(out)
