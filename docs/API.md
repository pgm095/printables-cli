# Printables write API

Derived from the JavaScript of the Printables web app (model form) and verified by creating a draft with a real account. Introspection is disabled; the schema bundled in the web app declares every argument as `Any`, so input types come from the form code. This document contains no cookies or tokens.

All calls go to `https://api.printables.com/graphql/` and run as `fetch` inside a Printables tab of the logged-in Chrome, so cookies and CSRF handling are the browser's own. The operations are copied from the web app into `printables_cli/operaciones.json`.

## Creation flow

1. **Upload each file and each photo**, one at a time:
   - `UploadModel` (`printFileUpload2`) with `{fileName, folder: "", unzip: false}`; for images add `imageWidth` and `imageHeight` (`imageHash` is optional: the web app fills it later with `UpdateImageParameters`). Returns `uploadData {url, fields}` and `fileUpload.id`.
   - Multipart `POST` to `uploadData.url` with all `fields` and, last, the `file` field. Done with `fetch` + `FormData` from the printables.com tab, like the web app; answers 2xx.
   - `UploadModelFinished` (`printFileUploadFinished`) with `{fileUploadId, crc32c}`; `crc32c` is the CRC32C (Castagnoli) of the file, 4 bytes big-endian, base64.
2. **Poll** `PollFileUploads(ids)` (the web app does it every second) until `isProcessed`. Each upload returns the already created objects, classified by extension: `stls`, `gcodes`, `slas`, `otherFiles` or `images`, each with its `id`. `notInspectedFiles[].error` carries rejections.
3. **Create the model**: `ModelUpdate` **without `id`**. Files are attached by passing their ids in the lists; there is no intermediate "create empty draft" step.
4. **Publish**: the same `ModelUpdate` with `draft: false`. `PrintPublishRequest` is only needed when the model has `publishApprovalRequired`: the web app saves a draft and sends the request, which stays pending until Printables approves it. This branch is implemented from the web app code but untested (the test account returned `publishApprovalRequired: false`).

## `ModelUpdate` variables

| Variable | Value |
|---|---|
| `id` | absent on create; model id on update |
| `draft` | `true` draft, `false` published |
| `name`, `summary`, `description` | text; `description` is editor HTML (`p`, `h3`, `ul`, `ol`, `a`, `strong`, `em` and `code` are stored as sent) |
| `category`, `license`, `printer` | ids (`Categories`, `Licenses`) |
| `tags` | tag **names**, even though the declared type is `[ID]` |
| `authorship` | `PrintAuthorshipEnum`: `author`, `remix`, `reupload` |
| `remixParents` | for each parent, the Printables **model id** or the external **URL** |
| `remixDescription` | required with `remix`; `null` otherwise |
| `nsfw`, `aiGenerated`, `politicalContent` | booleans; `aiGenerated` is required to publish |
| `club`, `price`, `excludeCommercialUsage` | `false`, `0`, `false` for free models |
| `stls`, `slas`, `otherFiles` | `[{id, folder, name, note}]` (`STLFileInputType`, `SLAFileInputType`, `OtherFileInputType`) |
| `gcodes` | the above plus `weight`, `material` (id), `nozzleDiameter`, `layerHeight`, `printDuration`, `excludeFromTotalSum` |
| `images` | `[{id}]` (`PrintImageInputType`); list order is gallery order and the first is the main image |
| `mainImage`, `variationOf` | not sent by the web form |

The web app resends the full listing on every save and only includes the file or image lists that changed. `update` does the same: an omitted list is untouched; a list that is sent replaces the previous one, so existing entries plus the new ones are always sent.

Publishing requirements in the web form: name, summary, category, authorship, license, `aiGenerated`, at least one model or print file and at least one image. A draft accepts empty fields.

## Remix and licenses

- Printables URL: the web app does not use `SearchRemixParentByUrl`; it reads the model by id (the `RemixParentModel` query) and sends that id. `SearchRemixParentByUrl` (`remixUrlInfo`) is meant for external URLs and, given a Printables URL, returns title and image but `license: null`.
- The remix license must be in `allowedLicensesAfterRemixing` of the parent license, unless the parent is the user's own original; `disallowRemixing` forbids it entirely. The CLI checks this before uploading anything.
- Example: a CC BY-NC parent only allows CC BY-NC, CC BY-NC-SA and CC BY-NC-ND remixes; CC BY-SA is rejected.

## Observations

- `ModelEditDetail` keeps the original photo name at the end of `filePath`; `update --photos` uses it to avoid duplicates.
- `UserModels` only returns published models, so `printables list` does not show drafts. `DraftsCount` gives the number of drafts.
- `update` with real uploads of new or replaced files or photos uses the same calls as `publish`, but that combination has not been run against Printables; `update --description` and `update` without file changes have.
- Practical upload limit: the file travels as base64 over CDP into the tab; tested with files up to about 130 kB. Measure before using files of tens of MB.
