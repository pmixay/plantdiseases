# Manual label-review hook (physiological classes)

The `nutrient_deficiency` and `leaf_scorch_underwatering` classes are
partly sourced from **free-text iNaturalist queries** (`chlorosis`,
`leaf scorch`, …), which are inherently noisy. The training notebook
(`train_notebook.ipynb`, §2) already removes most of that noise
automatically:

- queries are constrained to `iconic_taxa=Plantae` (drops insects, fungi,
  and birds whose names share those words), and
- the `nutrient_deficiency` class is supplemented with the curated,
  expert-labelled **CoLeaf** dataset (high `label_confidence`).

This directory is the **non-destructive manual-audit layer** the report
(Stage 2: *"manually audit and re-label a clean subset"*) asks for. It
lets a human reject individual observations **without deleting any image
and without editing code** — the curation lives in version control and a
re-run reproduces the cleaned set deterministically.

## `inat_physiological_blocklist.json`

A JSON list of **iNaturalist observation ids** to exclude from the
physiological-class fetch pool. Example:

```json
[123456789, 987654321, "55501234"]
```

Ids may be integers or strings (both are matched). An empty list (`[]`)
blocks nothing, which is the default.

### How to find ids to add

1. Run §2, then open `data_v2/classification/train/nutrient_deficiency/`
   (or `…/leaf_scorch_underwatering/`) and eyeball the images.
2. Each image has a sidecar `*.json`; its `plant_id` is
   `inat_phys_<observation_id>` (or `inat_ft_<observation_id>`). The
   `<observation_id>` is what you add to the blocklist.
3. Add the ids of any obviously-wrong photos (a healthy leaf, a whole
   greenhouse, a pest close-up, a product label, …), save, and re-run §2.

The blocklisted observations are skipped at fetch time for **both**
physiological classes. Nothing is deleted; remove an id to let it back in.

> This file lives outside `data_v2/` on purpose, so it survives a
> `REBUILD_GENERATED_SPLITS = True` wipe and stays committed.
