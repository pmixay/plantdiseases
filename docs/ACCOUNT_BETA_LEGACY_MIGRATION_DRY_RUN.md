# Legacy Training Migration Dry-Run Report

Report date: 2026-08-07 UTC

Scope: repository-owned synthetic fixture only. No current VDS database, media,
backup, log, cache, snapshot, export, or production path was opened or changed.
The real German inventory remains a Phase D operator/legal gate.

## Dry-run input and result

The fixture contained one approved legacy labeling candidate, two approved
feedback rows, one deliberately unapproved feedback row, EXIF camera/device
sentinels, free-text sentinels, scan IDs, pseudo/installation IDs, raw and
hashed documentation-range IPs, source URL, device model, and an image metadata
comment. The explicit approval document selected labeling row `1` and feedback
rows `10` and `11`; row `12` remained excluded.

The default read-only invocation produced:

```json
{
  "approved_feedback_rows": 2,
  "approved_labeling_rows": 1,
  "in_memory_joins": 1,
  "mode": "dry_run",
  "potential_samples": 2,
  "standalone_feedback_images": 1
}
```

No destination database or image directory existed after dry run. Missing,
duplicate, unsafe-path, nonexistent, unsupported-source, malformed-consent, and
already-existing-destination cases are refusal paths.

## Synthetic apply verification

The apply test used a fresh pytest temporary destination and the explicit
`legacy-reviewed-v1` consent/provenance marker. It produced two samples, zero
deduplication collisions, one in-memory join, and one standalone negative
feedback image. It then proved:

- no `scan_id`, pseudo/user ID, installation ID, IP/hash, device model, source
  URL, free text, claim session, or account/community foreign key exists in the
  replacement schema;
- joins exist only in process memory during transformation;
- each selected image is freshly decoded, orientation-normalized, RGB JPEG
  encoded, and free of EXIF/XMP/IPTC/ICC/source filename;
- every forbidden sentinel and the unapproved row is absent from database,
  images, and exported archive bytes;
- an offline human verdict remains authoritative when user feedback is joined;
- a failed apply removes only its newly created destination and never changes
  the read-only sources;
- an existing destination is never overwritten.

## Operator command shape

The tool discovers no default paths and does not apply unless `--apply` is
present. The real audit must first produce a reviewed allowlist containing only
the exact approved legacy row IDs. A dry run uses explicit paths equivalent to:

```sh
python server/tools/migrate_legacy_training_data.py \
  --legacy-labeling-db /reviewed/source/labeling.db \
  --legacy-labeling-images /reviewed/source/labeling-images \
  --legacy-feedback-db /reviewed/source/feedback.db \
  --legacy-feedback-images /reviewed/source/feedback-images \
  --approved-ids /reviewed/approval.json \
  --destination-db /fresh/target/training.db \
  --destination-images /fresh/target/training-images \
  --consent-version legacy-reviewed-v1
```

Real application is release-blocked until the operator freezes writes,
inventories every item listed in plan section 16, records legal basis and
exclusions, takes a protected source snapshot, reviews the dry-run counts, and
approves a fresh Russian destination. Old anonymous FCM rows are discarded
unless lawful ownership can be established; legacy community rows remain
read-only and unclaimable. After verified Russian restore/cutover, deletion of
German data/backups/logs/snapshots requires separate recorded authorization.
