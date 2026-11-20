# Manifest schema

A model is described by a folder:

```
my-model/
  modelo.toml        manifest
  descripcion.md     description (Markdown subset)
  part.stl ...       model files
  photos/main.jpg    photos
```

## `modelo.toml`

Keys keep their Spanish names (canonical); English aliases are accepted and listed in the second column.

```toml
titulo = "Webcam mount for 4040 rail"            # alias: title
resumen = "Slide-in clip with a wedge, no supports"  # alias: summary (optional, max 250 chars)
descripcion = "descripcion.md"                   # alias: description (path to Markdown file)
categoria = "3D Printers > Accessories"          # alias: category (full path or leaf name)
etiquetas = ["ender3", "webcam"]                 # alias: tags
licencia = "CC BY-SA 4.0"                        # alias: license (Printables name or abbreviation)
archivos = ["mount.stl", "wedge.stl"]            # alias: files
fotos = ["photos/mounted.jpg"]                   # alias: photos (the first one is the main image)
remix_de = ["https://www.printables.com/model/123456"]  # alias: remix_of (optional; Printables model or external URL)
remix_descripcion = "What changes from the original"    # alias: remix_description (required with remix_de)
generado_con_ia = false                          # alias: ai_generated (optional; "AI generated" checkbox)

[impresion]          # alias: [print_settings]; optional; appended as a "Print settings" section
material = "PLA"
capa = 0.2           # alias: layer_height (mm)
relleno = "20 %"     # alias: infill
soportes = false     # alias: supports
# also: boquilla/nozzle, paredes/perimeters, brim, orientacion/orientation
```

Rules: `titulo`, `descripcion`, `categoria`, `licencia`, `archivos` and `fotos` are required. Paths are relative to the folder and cannot leave it. Using both a key and its alias is an error. `descripcion.md` supports headings (`#`, `##`, `###`), paragraphs, lists, links, bold, italic and code, and is converted to HTML. With `remix_de`, authorship becomes "remix" and the license must be compatible with the parent's (see `API.md`). Printables has no print-settings fields for models without G-code, which is why `[impresion]` goes into the description.

## Files written by the CLI

`printables.toml` (written by `publish`): `id`, `url`, `published` (bool), `date`.
`fuente.toml` (written by `download`): `url`, `date`, `files`.

## Idempotency

If the folder already has `printables.toml`, `publish` refuses to run and suggests `update`.
