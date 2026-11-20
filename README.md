# printables-cli

Command line client for [Printables](https://www.printables.com): check your session, list your models, download model files, and publish or update your own models from a folder on disk.

## Disclaimer

- This project is **not official** and is not affiliated with, endorsed by or sponsored by Prusa Research or Printables.
- It uses the **internal, undocumented GraphQL API** of the Printables website, with your own account. It can break at any time if Printables changes its site.
- It is meant for managing **your own** models. Do not use it for bulk downloads or scraping. Respect the Printables terms of service and the license of every model you download.
- Use it at your own risk. See [LICENSE](LICENSE).

## Why it attaches to a normal Chrome

Printables is behind Cloudflare, which blocks browsers launched by automation tools. So the CLI does not launch a browser for itself: you open a regular Google Chrome with a dedicated profile and a local debugging port, log in to Printables by hand (including any verification Cloudflare asks for), and the CLI attaches to that Chrome over CDP (Chrome DevTools Protocol). It opens its own tab, makes API calls with `fetch` from inside that tab and closes the tab when it finishes. The CLI does not try to evade bot detection.

## Install

Requires Python 3.12 or 3.13, [uv](https://docs.astral.sh/uv/) and Google Chrome. The CLI only attaches to your Chrome, so no Playwright browser download is needed.

```sh
git clone https://github.com/pgm095/printables-cli
cd printables-cli
uv tool install .      # installs the `printables` command
# or run from the checkout without installing:
uv run printables --help
```

## Open the dedicated Chrome

Start Chrome with its own profile and the debugging port bound to localhost:

```sh
google-chrome \
  --user-data-dir="$HOME/.config/printables-cli/chrome" \
  --remote-debugging-port=9333 \
  --remote-debugging-address=127.0.0.1 \
  https://www.printables.com/login
```

Log in to Printables in that window and leave it open. `printables login` runs the same command for you if the port is not responding. A launcher file, for example `~/.local/share/applications/printables-chrome.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Chrome for Printables CLI
Exec=google-chrome --user-data-dir=%h/.config/printables-cli/chrome --remote-debugging-port=9333 --remote-debugging-address=127.0.0.1 https://www.printables.com/login
Terminal=false
Categories=Network;
```

Environment variables: `PRINTABLES_CDP_PORT` (default `9333`) and `PRINTABLES_CLI_HOME` (default `~/.config/printables-cli`).

## Commands

```sh
printables login                     # open the dedicated Chrome if it is not running
printables whoami                    # check the logged-in user
printables list                      # list your published models (--json available)
printables download https://www.printables.com/model/123456 --dest ./model

# Publish from a folder with modelo.toml + descripcion.md
printables publish my-model --dry-run   # validate and show the plan, touch nothing
printables publish my-model --draft     # create a draft to review on the website
printables publish my-model             # publish

# Update without removing anything already on Printables
printables update 123456 --folder my-model --description   # title, summary, description, category, tags, license, remix
printables update 123456 --folder my-model --files --photos
printables update 123456 --publish                          # publish a reviewed draft
```

English flags and the original Spanish ones are both accepted: `--draft`/`--borrador`, `--publish`/`--publicar`, `--description`/`--descripcion`, `--files`/`--archivos`, `--photos`/`--fotos`, `--folder`/`--carpeta`. `--dry-run` and `--json` are available on `publish` and `update`. Machine-readable (`--json`) output keys are in Spanish.

`publish` writes `printables.toml` (id, url, published, date) in the folder and refuses to run again if it exists; use `update` from then on.

## Model format

A model folder contains `modelo.toml`, a Markdown description and the files and photos it references:

```toml
title = "Webcam mount for 4040 rail"
summary = "Slide-in clip with a wedge, no supports"
description = "descripcion.md"
category = "3D Printers > Accessories"
tags = ["ender3", "webcam"]
license = "CC BY-SA 4.0"
files = ["mount.stl", "wedge.stl"]
photos = ["photos/mounted.jpg"]      # the first photo is the main image
```

The canonical key names are Spanish (`titulo`, `resumen`, `descripcion`, `categoria`, `etiquetas`, `licencia`, `archivos`, `fotos`, `remix_de`, `remix_descripcion`, `generado_con_ia`, `[impresion]`); the English aliases above are equivalent. The description file supports headings, paragraphs, lists, links, bold, italic and inline code. For remixes, add `remix_of` and `remix_description`; the license must be compatible with the parent's. Full reference in [docs/BACKEND_SCHEMA.md](docs/BACKEND_SCHEMA.md).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | ok |
| 2 | usage error, invalid manifest or manifest that does not match Printables |
| 3 | no browser or no logged-in session |
| 4 | Printables or download failure |

## Security

- The CLI never asks for or stores your password. The session lives in the Chrome profile directory (`~/.config/printables-cli/chrome`, created with mode 700).
- The CDP port listens on `127.0.0.1` only, but while that Chrome is open any local process can control it, including your logged-in Printables session. Close the dedicated Chrome when you are not using it.
- Do not commit your Chrome profile or `printables.toml` receipts you do not want public.

## Known limitations

- `list` does not show drafts (the API only returns published models).
- Files travel as base64 over CDP into the tab; tested up to about 130 kB. Very large files are untested.
- The publication-approval flow (`PrintPublishRequest`) follows the web app code but has not been tested with an account that requires approval.
- Uploading new or replaced files and photos through `update` has not been exercised against Printables; `publish` has.

## Docs

- [docs/DESIGN.md](docs/DESIGN.md): scope, architecture, decisions and flows.
- [docs/API.md](docs/API.md): the reverse-engineered upload, create and publish flow.
- [docs/BACKEND_SCHEMA.md](docs/BACKEND_SCHEMA.md): `modelo.toml` reference.

## License

[MIT](LICENSE)
