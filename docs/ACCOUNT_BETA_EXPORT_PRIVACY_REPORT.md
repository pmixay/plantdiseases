# Anonymous Training Export Privacy Report

Report date: 2026-08-07 UTC

Assessment result: the implemented exporter satisfies the technical
de-identification contract on generated test material. It must be described as
de-identified—not legally anonymous—until counsel/security reviews the
residual identifying risk in image pixels and the operator's release process.

## Included export fields

- random server sample and image keys;
- SHA-256 and perceptual hash, plus near-duplicate group;
- coarse creation day;
- allowlisted capture source and consent version;
- model family/version, predicted class/confidence, structured top-k and
  conformal set, bounded warnings, coarse blur/region/area/severity values;
- structured correctness, correction class/reason, out-of-head flag, review
  status, label provenance, and downstream usage eligibility.

## Excluded linkage

The database schema, manifest, images, inventory, duplicate report, and archive
contain no account ID, actor ID, session, provider subject/profile, nickname,
installation ID, community/scan ID, IP/hash, FCM token, phone/device model,
source filename/path/URL, free text, request header, OAuth/application token, or
original image metadata.

## Automated evidence

- source SQLite is opened read-only and output is a new timestamped archive;
- every image hash is verified before export;
- every image is inspected for EXIF/XMP/IPTC/ICC and export fails closed if
  metadata exists;
- manifest keys and structured JSON shapes are fixed and regression-tested;
- checksums cover the archive contents;
- exact and pHash-near duplicate groups are emitted;
- related groups are locked to one downstream split, and `auto_yes` is
  train-only; only reviewed material is evaluation-eligible;
- corrupted images/hashes, unsafe keys, unknown structure, missing files, and
  existing targets are refusal cases;
- synthetic migration sentinels for scans, users, IPs, devices, free text, and
  camera metadata are absent from the exported tar members.

## Residual controls before release

- a named reviewer inspects the manifest inventory and a bounded random image
  sample for people, homes, plates, documents, and other unintended content;
- the archive stays encrypted at rest and in transit in Russia with access and
  retention controls;
- export and training are offline operations; no serving/admin labeling queue
  or retraining endpoint is enabled;
- image hashes are not joined against community, account, support, analytics,
  or external datasets;
- deletion statements accurately disclose that a genuinely unlinked sample
  cannot be selected by account deletion; any retained mapping would invalidate
  that claim and must join the deletion workflow.
