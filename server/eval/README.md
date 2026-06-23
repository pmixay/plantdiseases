# Frozen eval set — persistent, content-addressed

`frozen_eval_manifest.json` pins a fixed set of held-out images into
`val_realworld` on **every** training run, so two model versions are graded on
the **same** photos and a *paired* bootstrap (`tools/paired_eval.py`) can tell a
real regression from eval-set churn.

## Why it's needed

The notebook's `make_group_split` (§2) draws held-out splits with
`rng.shuffle(ids)` and `k = int(len(ids) * ratio)`. Both depend on the *current*
dataset pool, so when the Kaggle supplement grows the held-out set changes —
`val_realworld` is a moving target. That is why the v2.6.6 per-class numbers
were *not* comparable to v2.6.5's (`not_a_plant` n 625→501, `viral` 219→150,
…), and why the "leaf_scorch regression" could not be separated from noise.

## How persistence is guaranteed

1. **Committed to git** — the manifest lives in the repo, so it survives Colab's
   ephemeral filesystem and is identical on every clone.
2. **Content-addressed** — each entry is keyed by `sha256` (exact) with an
   `8×8 DCT pHash` fallback (Hamming ≤ `phash_threshold`). Identity is the image
   *bytes/pixels*, not its path, so renames, re-encodes and dataset-version
   reshuffles don't lose an entry.
3. **Fail-fast** — `frozen_eval.pin_frozen_eval` raises if realised coverage
   drops below its floor (default 0.90): a silently shrinking frozen set would
   de-pair the very comparison it protects.
4. **Leakage-checked** — pinned images are *moved out of train* before the random
   split runs, and the existing §3 SHA + pHash leakage guards then confirm none
   leak back into train.

## Workflow

```bash
# 1. ONCE, on Colab, right after §2 populates the held-out split — snapshot it:
python -m server.tools.build_frozen_eval \
    --eval-root data_v2/classification/val/realworld \
    --out server/eval/frozen_eval_manifest.json \
    --sources doctorp inat plantdoc supplement \
    --per-class-cap 150
# commit the manifest.

# 2. The notebook §2 then pins it automatically on every future run
#    (frozen_eval.pin_frozen_eval, before make_val_realworld()).

# 3. Compare two model bundles on the frozen set (paired, trustworthy):
python -m server.tools.paired_eval \
    --eval-root <exported frozen val_realworld> \
    --bundle-a models_v2.6.5 --label-a v2.6.5 \
    --bundle-b models_v2.6.6 --label-b v2.6.6 \
    --focus leaf_scorch_underwatering nutrient_deficiency \
    --out-md report.md
```

An **empty** manifest (the committed seed) makes the §2 pin a safe no-op, so
nothing breaks before the one-time snapshot is taken.
