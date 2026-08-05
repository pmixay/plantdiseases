# Hosted Aqua on-device model

Drop the trained **Aqua** artefacts here to let the app download + cache them:

- `aqua_classifier.tflite` — MobileNetV3-Small student (notebook §5).
- `classes.json` — the sidecar the notebook writes next to it.

These are **provisioned out of band and not committed** (same policy as the
server weights). When absent, `GET /api/aqua/manifest` returns
`{"available": false}` and the app falls back to the server model. The app
polls the manifest and re-downloads only when the `sha256` changes.
