# On-device model assets (Aqua family)

Drop the trained **Aqua** artefacts here to enable fully-offline scanning:

- `aqua_classifier.tflite` — MobileNetV3-Small student (TFLite, dynamic-range
  INT8) produced by §5 of `server/train_notebook.ipynb`.
- `aqua_classes.json` — the sidecar the notebook writes next to it
  (`models/aqua/classes.json`), renamed to `aqua_classes.json`.

These are provisioned out of band and are **not committed** (same policy as the
server weights). When absent, `AquaClassifier.isAvailable()` returns false and
the app transparently falls back to the server model.
