# Hosted Aqua on-device model

Publish the exact **Aqua** subtree from one verified notebook-produced combined
bundle here to let the app download and cache it. Never mix loose files/runs:

- `aqua_classifier.tflite` — MobileNetV3-Small student (notebook §5).
- `classes.json` — the sidecar the notebook writes next to it.

These are **provisioned out of band and not committed** (same policy as the
server weights). When absent, `GET /api/aqua/manifest` returns
`{"available": false}` and the app falls back to the server model. The app
polls the manifest and re-downloads only when the combined `bundle_sha256`
changes.

The sidecar is a strict serving contract. It must declare a non-empty unique
`class_names` list, `model_family: "aqua"`, a non-empty `model_version`,
`training.img_size`, `training.eval_resize_ratio`, and an `inference` object
with `layout: "NHWC"`, `tta: "none"`, `output_semantics: "logits"`, and
three finite `normalize_mean` / positive `normalize_std` values. The manifest
returns model, sidecar, and combined bundle digests; clients use the combined
digest so a metadata-only change cannot leave a stale install active. Before
activation the app also inspects the staged graph (without inference) and
requires one FLOAT32 NHWC input at `training.img_size` plus one FLOAT32 logits
output whose width matches `class_names`.

Lifecycle semantics are deliberate: an HTTP-success manifest with
`available:false` authoritatively clears the active pointer, while leaving the
immutable bundle directory available for rollback/debugging. A network error
or non-2xx manifest response is not a revocation and preserves the
last-known-good active bundle.
