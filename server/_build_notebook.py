"""Build server/train_notebook.ipynb from scratch.

This is a build-time helper checked in alongside the notebook so the
notebook structure stays auditable and can be regenerated. Run with:

    python3 server/_build_notebook.py
"""
from __future__ import annotations

import json
from pathlib import Path

NB_PATH = Path(__file__).resolve().parent / 'train_notebook.ipynb'

cells: list[dict] = []


def md(text: str) -> None:
    cells.append({"cell_type": "markdown", "metadata": {},
                  "source": text.splitlines(keepends=True)})


def code(text: str) -> None:
    cells.append({"cell_type": "code", "execution_count": None, "metadata": {},
                  "outputs": [], "source": text.splitlines(keepends=True)})


# 0. Intro -----------------------------------------------------------
md("""# PlantVision 2.0 — Houseplant disease pipeline

**Stage 1:** YOLOv8n single-class `plant_leaf` detector (indoor-trained on a Roboflow Universe houseplant-leaf bbox slice — PlantDoc is excluded by design).

**Stage 2:** EfficientNetV2-S teacher → distilled MobileNetV3-Large student. 11 final classes:

1. `bacterial_leaf_spot`
2. `edema`
3. `gray_mold_botrytis`
4. `healthy`
5. `leaf_scorch_underwatering`
6. `leaf_spot_fungal` (anthracnose + cercospora + alternaria)
7. `not_a_plant`
8. `nutrient_deficiency` (Fe + N + Mg + K)
9. `powdery_mildew`
10. `sunburn_or_scorch`
11. `viral_disease` (mosaic + leaf_curl + ringspot)

Removed vs v1.x: `late_blight`, `iron_chlorosis`. Pests are out of the taxonomy entirely.

**Primary metric:** disease macro-F1 on `val_realworld` (held-out user photos). PlantVillage stays only as a frozen `val_studio` slice (transfer / background-bias monitor — Noyan 2022: 49 % accuracy from 8 background pixels).

**Time budget on 1× A100-40:**
- 5–10 min DINOv2-S frozen + linear probe (diagnostic)
- ~35 min EfficientNetV2-S fine-tune (teacher)
- 10–15 min KD into MobileNetV3-Large student
- 5 min static INT8 calibration + temperature + conformal RAPS

**No private user-data upload step.** Everything below pulls public datasets via API.""")


# 1. Environment -----------------------------------------------------
md("""## 1. Environment

`timm` for the V2-S backbone, `cleanlab` + `fiftyone` for label-noise triage, `imagehash` for pHash dedup, `roboflow` for Roboflow Universe access (optional — only needed for the disease classes that iNat doesn't cover well, and for the Stage 1 detector dataset). The iNat + COCO downloads use plain `requests`.""")

code("""import sys, subprocess
PIP = [sys.executable, '-m', 'pip', 'install', '-q']
PKGS = [
    'torch>=2.2', 'torchvision', 'timm>=1.0',
    'ultralytics>=8.2', 'onnx', 'onnxruntime',
    'cleanlab>=2.6', 'fiftyone>=0.24',
    'imagehash', 'Pillow', 'numpy', 'scikit-learn', 'pandas',
    'opencv-python-headless', 'tqdm', 'matplotlib',
    'requests', 'roboflow',
]
subprocess.check_call(PIP + PKGS)""")


# 2. Hierarchical schema ---------------------------------------------
md("""## 2. Hierarchical annotation schema

Every cropped leaf carries the metadata below as a sidecar JSON, so error-analysis joins (group by `source`, `capture_session`, …) stay cheap even though the model is single-head. The data fetchers in §4 write a sidecar per downloaded crop **automatically** — there's no manual annotation step on the public-data path.

Splits are disjoint at `plant_id` level: the same plant must never appear in train and val.""")

code("""from dataclasses import dataclass, asdict
from typing import Optional, List
import json, pathlib

@dataclass
class CropAnnotation:
    plant_id: str
    species: Optional[str]
    diagnosis: str
    severity: str               # low | medium | high | unknown
    leaf_or_whole_plant: str    # leaf | whole_plant
    capture_session: str
    source: str                 # inat | coco | roboflow | money_atlas
    label_confidence: float     # in [0, 1]
    bbox: List[int]             # [x, y, w, h] — [0,0,0,0] when whole-image

def write_sidecar(crop_path: pathlib.Path, ann: CropAnnotation):
    crop_path.with_suffix('.json').write_text(
        json.dumps(asdict(ann), ensure_ascii=False, indent=2)
    )

def read_sidecar(crop_path: pathlib.Path) -> dict | None:
    p = crop_path.with_suffix('.json')
    return json.loads(p.read_text()) if p.exists() else None""")


# 3. Layout ----------------------------------------------------------
md("""## 3. Class layout & three-val protocol

| Slice | Source | Used for | Selection rule |
|---|---|---|---|
| `train/` | iNat + COCO + (optional) Roboflow + Money Plant Atlas | training | group-aware split by `plant_id` |
| `val_indomain` | random 10 % of `train/` after fetch | early stopping, schedule decisions | disjoint plant_ids |
| `val_realworld` | a separate Roboflow Universe houseplant-disease slice | **primary — ship/no-ship gate** | frozen at v2.0 |
| `val_studio` | PlantVillage (Mendeley / Hugging Face) | studio-transfer + background-bias monitor | never enters train |

Target on `val_realworld`: disease macro-F1 ∈ **0.70 – 0.78**, no class with recall = 0, healthy-bias < 1.5.""")

code("""import pathlib

ROOT = pathlib.Path('data_v2')
TRAIN_ROOT     = ROOT / 'train'
VAL_INDOMAIN   = ROOT / 'val' / 'indomain'
VAL_PRIMARY    = ROOT / 'val' / 'realworld'      # PRIMARY — ship/no-ship gate
VAL_STUDIO     = ROOT / 'val' / 'plantvillage_studio'
CONFORMAL_CAL  = ROOT / 'val' / 'conformal_cal'

CLASSES = [
    'bacterial_leaf_spot', 'edema', 'gray_mold_botrytis', 'healthy',
    'leaf_scorch_underwatering', 'leaf_spot_fungal', 'not_a_plant',
    'nutrient_deficiency', 'powdery_mildew', 'sunburn_or_scorch', 'viral_disease',
]
for c in CLASSES:
    (TRAIN_ROOT / c).mkdir(parents=True, exist_ok=True)
for d in (VAL_INDOMAIN, VAL_PRIMARY, VAL_STUDIO, CONFORMAL_CAL):
    d.mkdir(parents=True, exist_ok=True)
print('Layout ready under', ROOT)""")


# 3a. Eval helpers ---------------------------------------------------
md("""## 3a. Eval helpers — three independent val loaders

One `evaluate()` helper, called every epoch on **all three** val slices. Logs disease macro-F1 (excludes `healthy`/`not_a_plant`), per-class recall, healthy-bias and the confusion matrix. Top-1 alone hides healthy collapse and rare-class death.""")

code("""import torch
from torch.utils.data import DataLoader
from torchvision.datasets import ImageFolder
from torchvision import transforms as T
import numpy as np
from sklearn.metrics import f1_score, recall_score, confusion_matrix

NORM = T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
EVAL_WORKERS = 2  # keep total worker pool predictable on A100-40

def make_eval_loader(root, img_size, batch=128, workers=EVAL_WORKERS):
    tx = T.Compose([
        T.Resize(int(img_size * 1.05)),
        T.CenterCrop(img_size),
        T.ToTensor(),
        NORM,
    ])
    ds = ImageFolder(str(root), transform=tx)
    if len(ds) == 0:
        raise RuntimeError(f'Eval folder is empty: {root}. Run §4 data fetch first.')
    dl = DataLoader(ds, batch_size=batch, shuffle=False,
                    num_workers=workers, pin_memory=True)
    return ds, dl

@torch.inference_mode()
def evaluate(model, dl, class_names, device, name=''):
    model.eval()
    y_true, y_pred = [], []
    for x, y in dl:
        x = x.to(device, non_blocking=True)
        logits = model(x)
        y_pred.extend(logits.argmax(1).cpu().tolist())
        y_true.extend(y.tolist())
    y_true = np.asarray(y_true); y_pred = np.asarray(y_pred)
    disease_idx = [i for i, n in enumerate(class_names)
                   if n not in ('healthy', 'not_a_plant')]
    macro_f1_disease = f1_score(
        y_true, y_pred, labels=disease_idx, average='macro', zero_division=0)
    per_class_recall = recall_score(
        y_true, y_pred, labels=list(range(len(class_names))),
        average=None, zero_division=0)
    healthy_idx = class_names.index('healthy') if 'healthy' in class_names else None
    healthy_pred_share = float((y_pred == healthy_idx).mean()) if healthy_idx is not None else 0.0
    healthy_true_share = float((y_true == healthy_idx).mean()) if healthy_idx is not None else 0.0
    healthy_bias = (healthy_pred_share / max(healthy_true_share, 1e-9)) if healthy_idx is not None else 0.0
    cm = confusion_matrix(y_true, y_pred, labels=list(range(len(class_names))))
    print(f'[{name}] disease_macro_f1={macro_f1_disease:.3f}  '
          f'healthy_bias={healthy_bias:.2f}  n={len(y_true)}')
    for cls, r in zip(class_names, per_class_recall):
        print(f'    recall[{cls:30s}] = {r:.3f}')
    return {
        'name': name,
        'disease_macro_f1': float(macro_f1_disease),
        'per_class_recall': {c: float(r) for c, r in zip(class_names, per_class_recall)},
        'healthy_bias': float(healthy_bias),
        'confusion_matrix': cm.tolist(),
    }""")


# 4. Public-data acquisition -----------------------------------------
md("""## 4. Public-data acquisition

Three real public sources, no manual upload:

* **iNaturalist API** — `https://api.inaturalist.org/v1/observations`. Open, no key needed for moderate use. Filter `quality_grade=research` + `field:Plant Part Observed=leaf`. Used for `healthy`, `powdery_mildew`, `gray_mold_botrytis`, `leaf_spot_fungal`, `viral_disease`.
* **COCO val2017** — direct download from `images.cocodataset.org`. After filtering out images tagged with plant categories (`potted plant`, `vase`, `broccoli`, `carrot`), the remainder feed the `not_a_plant` rejection class.
* **Roboflow Universe** *(optional, env-driven via `ROBOFLOW_API_KEY`)* — covers the classes iNat is weak on (`bacterial_leaf_spot`, `edema`, `sunburn_or_scorch`, `leaf_scorch_underwatering`, `nutrient_deficiency`) and the Stage 1 indoor leaf detector dataset.
* **Money Plant Disease Atlas** *(Biswas 2024, Mendeley Data)* — optional Pothos slice, supplements `bacterial_leaf_spot` and `leaf_spot_fungal`.

Every downloaded crop gets a `CropAnnotation` sidecar JSON automatically, so group-aware splits and error analysis work out of the box.""")

code("""# ---- shared download helpers ----------------------------------------------
import os, re, time, json, shutil, hashlib, random, zipfile
from pathlib import Path
import requests
from tqdm import tqdm

UA = {'User-Agent': 'PlantVision/2.0 (research; contact: github.com/pmixay/plantdiseases)'}

def http_get_json(url, params=None, timeout=30, retries=3):
    last = None
    for _ in range(retries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise last

def http_download(url, dest, timeout=60):
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    with requests.get(url, headers=UA, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk: f.write(chunk)
    return dest""")

code("""# ---- iNaturalist ----------------------------------------------------------
INAT_BASE = 'https://api.inaturalist.org/v1'

def inat_resolve_taxon(name: str, rank: str = 'genus') -> int | None:
    \"\"\"Resolve a taxon name → numeric taxon_id (genus/species/order/family).\"\"\"
    data = http_get_json(f'{INAT_BASE}/taxa', {'q': name, 'rank': rank})
    res = data.get('results') or []
    return res[0]['id'] if res else None

def inat_observations(taxon_id: int, max_n: int, leaf_only: bool = True):
    \"\"\"Fetch research-grade leaf observations for a taxon.\"\"\"
    rows, page, per = [], 1, 100
    while len(rows) < max_n:
        params = {
            'taxon_id': taxon_id,
            'quality_grade': 'research',
            'photos': 'true',
            'per_page': per, 'page': page,
            'order': 'desc', 'order_by': 'created_at',
        }
        if leaf_only:
            # iNat field IDs vary by language; the keyword is more robust.
            params['term_value'] = 'leaf'
        data = http_get_json(f'{INAT_BASE}/observations', params)
        batch = data.get('results') or []
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < per:
            break
        page += 1
        time.sleep(1.0)  # be polite — iNat rate limits at ~1 rps for anonymous
    return rows[:max_n]

def _photo_url(photo: dict, size: str = 'medium') -> str | None:
    raw = photo.get('url') or ''
    if not raw.startswith('http'):
        return None
    # iNat URLs come back as .../square.jpg by default; rewrite to medium/large.
    return re.sub(r'/square\\.', f'/{size}.', raw)

def download_inat_class(taxon_ids: list[int], dest: Path, klass: str,
                        max_per_taxon: int = 200, leaf_only: bool = True) -> int:
    \"\"\"Download iNat photos for `klass` from a list of taxa, with sidecar JSON.\"\"\"
    dest.mkdir(parents=True, exist_ok=True)
    n_kept = 0
    for tx in taxon_ids:
        try:
            obs_list = inat_observations(tx, max_per_taxon, leaf_only=leaf_only)
        except Exception as e:
            print(f'  iNat taxon {tx} failed: {e}'); continue
        for obs in obs_list:
            for ph in (obs.get('photos') or [])[:1]:
                url = _photo_url(ph, 'medium')
                if not url: continue
                fname = f'inat_{tx}_{obs["id"]}_{ph["id"]}.jpg'
                fp = dest / fname
                if fp.exists(): continue
                try:
                    fp.write_bytes(requests.get(url, headers=UA, timeout=15).content)
                except Exception:
                    continue
                write_sidecar(fp, CropAnnotation(
                    plant_id=str(obs.get('id')),
                    species=(obs.get('taxon') or {}).get('name'),
                    diagnosis=klass,
                    severity='unknown',
                    leaf_or_whole_plant='leaf',
                    capture_session=f'inat_taxon_{tx}',
                    source='inat',
                    label_confidence=0.7,
                    bbox=[0, 0, 0, 0],
                ))
                n_kept += 1
                time.sleep(0.5)
    return n_kept

# Curated taxon-name → class map. Names are resolved at runtime so this
# notebook stays robust to iNat ID changes; if any name fails to resolve
# we just skip it with a warning.
INAT_CLASS_TAXA = {
    'healthy': [
        ('Ficus', 'genus'), ('Monstera', 'genus'),
        ('Epipremnum', 'genus'), ('Calathea', 'genus'),
        ('Spathiphyllum', 'genus'), ('Dracaena', 'genus'),
        ('Sansevieria', 'genus'), ('Philodendron', 'genus'),
    ],
    'powdery_mildew':       [('Erysiphales', 'order')],
    'gray_mold_botrytis':   [('Botrytis cinerea', 'species')],
    'leaf_spot_fungal':     [('Colletotrichum', 'genus'),
                             ('Cercospora', 'genus'),
                             ('Alternaria', 'genus')],
    'viral_disease':        [('Tobamovirus', 'genus'),
                             ('Geminiviridae', 'family')],
}

def fetch_inat_into_train(per_taxon=150):
    for klass, names in INAT_CLASS_TAXA.items():
        taxon_ids = []
        for name, rank in names:
            tid = inat_resolve_taxon(name, rank)
            if tid:
                taxon_ids.append(tid)
            else:
                print(f'  could not resolve iNat taxon {name} ({rank})')
        n = download_inat_class(taxon_ids, TRAIN_ROOT / klass, klass,
                                max_per_taxon=per_taxon,
                                leaf_only=(klass == 'healthy'))
        print(f'iNat: {klass} += {n}')""")

code("""# ---- COCO val2017 → not_a_plant ------------------------------------------
COCO_ZIP_URL = 'http://images.cocodataset.org/zips/val2017.zip'
COCO_ANN_URL = 'http://images.cocodataset.org/annotations/annotations_trainval2017.zip'
COCO_CACHE   = ROOT / 'cache_coco_val2017'

def fetch_coco_for_nap(target_n: int = 1500):
    COCO_CACHE.mkdir(parents=True, exist_ok=True)
    img_zip = COCO_CACHE / 'val2017.zip'
    ann_zip = COCO_CACHE / 'annotations.zip'
    if not (COCO_CACHE / 'val2017').exists():
        print('Downloading COCO val2017 (~1 GB)…')
        http_download(COCO_ZIP_URL, img_zip)
        with zipfile.ZipFile(img_zip) as zf: zf.extractall(COCO_CACHE)
    if not (COCO_CACHE / 'annotations').exists():
        http_download(COCO_ANN_URL, ann_zip)
        with zipfile.ZipFile(ann_zip) as zf: zf.extractall(COCO_CACHE)
    ann = json.loads((COCO_CACHE / 'annotations' / 'instances_val2017.json').read_text())
    plant_names = {'potted plant', 'vase', 'broccoli', 'carrot'}
    plant_cat_ids = {c['id'] for c in ann['categories'] if c['name'] in plant_names}
    plant_image_ids = {a['image_id'] for a in ann['annotations']
                       if a['category_id'] in plant_cat_ids}
    eligible = [im for im in ann['images'] if im['id'] not in plant_image_ids]
    rng = random.Random(42); rng.shuffle(eligible)
    chosen = eligible[:target_n]
    dest = TRAIN_ROOT / 'not_a_plant'
    dest.mkdir(parents=True, exist_ok=True)
    n_kept = 0
    for im in chosen:
        src = COCO_CACHE / 'val2017' / im['file_name']
        dst = dest / f'coco_{im["id"]}.jpg'
        if not src.exists() or dst.exists(): continue
        shutil.copy(src, dst)
        write_sidecar(dst, CropAnnotation(
            plant_id=f'coco_{im["id"]}',
            species=None, diagnosis='not_a_plant', severity='low',
            leaf_or_whole_plant='whole_plant',
            capture_session='coco_val2017',
            source='coco', label_confidence=0.99,
            bbox=[0, 0, 0, 0],
        ))
        n_kept += 1
    print(f'COCO: not_a_plant += {n_kept}')""")

code("""# ---- Roboflow Universe (optional) ----------------------------------------
# Free tier — set ROBOFLOW_API_KEY in your environment. The projects below
# are placeholders: pick concrete houseplant-disease projects from
# https://universe.roboflow.com (search 'houseplant disease', 'leaf scorch',
# 'plant edema'…). Each entry maps a Roboflow class folder name to one
# of our 11 PlantVision classes; classes not in the map are skipped.

ROBOFLOW_PROJECTS = [
    # Example — replace `workspace`/`project`/`version` with the actual
    # IDs you picked from Roboflow Universe; the per-project class map
    # converts that project's labels into our taxonomy.
    {
        'workspace': 'YOUR_WS', 'project': 'houseplant-disease', 'version': 1,
        'role': 'classifier_train',
        'class_map': {
            'bacterial_leaf_spot':       'bacterial_leaf_spot',
            'sunburn':                   'sunburn_or_scorch',
            'leaf_edema':                'edema',
            'leaf_scorch':               'leaf_scorch_underwatering',
            'nitrogen_deficiency':       'nutrient_deficiency',
            'iron_deficiency':           'nutrient_deficiency',
        },
    },
    # Stage 1 indoor leaf detector dataset (single class plant_leaf).
    {
        'workspace': 'YOUR_WS', 'project': 'indoor-houseplant-leaf', 'version': 1,
        'role': 'detector',
        'class_map': {'leaf': 'plant_leaf'},
    },
    # val_realworld holdout — a SEPARATE Roboflow project, frozen at v2.0.
    {
        'workspace': 'YOUR_WS', 'project': 'houseplant-disease-realworld-eval',
        'version': 1, 'role': 'val_realworld',
        'class_map': None,  # use Roboflow class names verbatim
    },
]

def fetch_roboflow_projects():
    api_key = os.environ.get('ROBOFLOW_API_KEY', '').strip()
    if not api_key:
        print('Skipping Roboflow — set ROBOFLOW_API_KEY to download '
              'bacterial_leaf_spot / edema / sunburn / scorch / nutrient '
              'classes and the indoor leaf detector dataset.')
        return None
    from roboflow import Roboflow
    rf = Roboflow(api_key=api_key)
    out = {}
    for spec in ROBOFLOW_PROJECTS:
        try:
            proj = rf.workspace(spec['workspace']).project(spec['project'])
            v = proj.version(spec['version'])
            tag = f"{spec['workspace']}__{spec['project']}__v{spec['version']}"
            local = ROOT / 'cache_roboflow' / tag
            local.mkdir(parents=True, exist_ok=True)
            ds = v.download('folder', location=str(local))
            out[spec['role']] = (ds, spec['class_map'])
            print(f'Roboflow ok: {tag}  ({spec["role"]})')
        except Exception as e:
            print(f'Roboflow failed for {spec["project"]}: {e}')
    return out

def materialise_roboflow_classifier(rf_out, dest_root: Path):
    \"\"\"Hardlink Roboflow images into TRAIN_ROOT / val using class_map.\"\"\"
    if rf_out is None: return
    for role, (ds, cmap) in rf_out.items():
        if role == 'classifier_train' and cmap:
            src_root = Path(getattr(ds, 'location', '')) or None
            if not src_root or not src_root.exists(): continue
            for split in ('train', 'valid', 'test'):
                split_dir = src_root / split
                if not split_dir.exists(): continue
                for sub in split_dir.iterdir():
                    if not sub.is_dir(): continue
                    target = cmap.get(sub.name.lower())
                    if not target or target not in CLASSES: continue
                    out = TRAIN_ROOT / target
                    out.mkdir(parents=True, exist_ok=True)
                    for img in sub.glob('*'):
                        if img.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'}:
                            continue
                        dst = out / f'rf_{img.stem}{img.suffix.lower()}'
                        if dst.exists(): continue
                        try:
                            os.link(img, dst)   # hardlink
                        except Exception:
                            shutil.copy(img, dst)
                        write_sidecar(dst, CropAnnotation(
                            plant_id=f'rf_{img.stem}',
                            species=None, diagnosis=target, severity='unknown',
                            leaf_or_whole_plant='leaf',
                            capture_session=f'rf_{role}',
                            source='roboflow', label_confidence=0.85,
                            bbox=[0, 0, 0, 0],
                        ))""")

code("""# ---- val_studio: PlantVillage (optional) ---------------------------------
# Use Hugging Face mirror or Mendeley directly. Drop nothing into train.
# If the dataset isn't available we just skip — val_studio is a monitor,
# never blocking.
def fetch_plantvillage_studio():
    try:
        from datasets import load_dataset  # type: ignore
    except Exception:
        print('Install `datasets` to enable PlantVillage val_studio (optional).')
        return
    try:
        ds = load_dataset('emre570/plantvillage-disease-classification', split='train',
                          streaming=False)
    except Exception as e:
        print(f'PlantVillage download failed: {e}'); return
    sample = ds.shuffle(seed=0).select(range(min(2000, len(ds))))
    for row in sample:
        label = row['label_name'] if 'label_name' in row else str(row.get('label'))
        # Keep PV's verbatim labels in val_studio so the slice is honest.
        out = VAL_STUDIO / re.sub(r'[^a-zA-Z0-9_]+', '_', label.lower())
        out.mkdir(parents=True, exist_ok=True)
        n = len(list(out.glob('*.jpg')))
        if n >= 50: continue
        img = row['image'].convert('RGB')
        img.save(out / f'pv_{n}.jpg', quality=92)
    print('PlantVillage val_studio populated.')""")

code("""# ---- Group-aware split: train → val_indomain (10%) ----------------------
def make_val_indomain(ratio=0.10, seed=0):
    rng = random.Random(seed)
    moved = 0
    for cls in CLASSES:
        cls_dir = TRAIN_ROOT / cls
        if not cls_dir.exists(): continue
        groups: dict[str, list[Path]] = {}
        for img in cls_dir.glob('*.[jp][pn]g'):
            ann = read_sidecar(img)
            pid = (ann or {}).get('plant_id', img.stem)
            groups.setdefault(pid, []).append(img)
        ids = sorted(groups.keys())
        rng.shuffle(ids)
        n_val = max(1, int(len(ids) * ratio))
        out = VAL_INDOMAIN / cls
        out.mkdir(parents=True, exist_ok=True)
        for pid in ids[:n_val]:
            for img in groups[pid]:
                dst = out / img.name
                if dst.exists(): continue
                shutil.move(str(img), str(dst))
                sc = img.with_suffix('.json')
                if sc.exists():
                    shutil.move(str(sc), str(dst.with_suffix('.json')))
                moved += 1
    print(f'val_indomain populated  (moved={moved})')

# Same idea — split a fraction of train into a conformal cal-set.
def make_conformal_cal(ratio=0.05, seed=1):
    rng = random.Random(seed)
    moved = 0
    for cls in CLASSES:
        cls_dir = TRAIN_ROOT / cls
        if not cls_dir.exists(): continue
        groups: dict[str, list[Path]] = {}
        for img in cls_dir.glob('*.[jp][pn]g'):
            ann = read_sidecar(img)
            pid = (ann or {}).get('plant_id', img.stem)
            groups.setdefault(pid, []).append(img)
        ids = sorted(groups.keys()); rng.shuffle(ids)
        n_cal = max(1, int(len(ids) * ratio))
        out = CONFORMAL_CAL / cls
        out.mkdir(parents=True, exist_ok=True)
        for pid in ids[:n_cal]:
            for img in groups[pid]:
                dst = out / img.name
                if dst.exists(): continue
                shutil.move(str(img), str(dst))
                sc = img.with_suffix('.json')
                if sc.exists():
                    shutil.move(str(sc), str(dst.with_suffix('.json')))
                moved += 1
    print(f'conformal_cal populated  (moved={moved})')""")

code("""# ---- Orchestrate the full fetch -----------------------------------------
def report_class_counts(root: Path):
    print(f'\\n{root}:')
    for cls in CLASSES:
        n = len(list((root / cls).glob('*.[jp][pn]g'))) if (root / cls).exists() else 0
        print(f'  {cls:30s} {n:5d}')

# 1. iNat — primary fetcher for the classes it covers reliably.
fetch_inat_into_train(per_taxon=150)

# 2. COCO val2017 → not_a_plant.
fetch_coco_for_nap(target_n=1500)

# 3. Roboflow Universe (optional) — covers the iNat-weak classes
#    (bacterial_leaf_spot, edema, sunburn_or_scorch, leaf_scorch_underwatering,
#    nutrient_deficiency) plus the Stage 1 detector dataset and val_realworld.
rf_out = fetch_roboflow_projects()
materialise_roboflow_classifier(rf_out, TRAIN_ROOT)

# 4. val_studio (optional, never blocking).
# fetch_plantvillage_studio()    # uncomment if `datasets` is installed.

# 5. Group-aware splits.
make_val_indomain(ratio=0.10)
make_conformal_cal(ratio=0.05)

report_class_counts(TRAIN_ROOT)
report_class_counts(VAL_INDOMAIN)

# Refuse to continue if any class is dangerously under-populated.
THRESH = 30
empty = [c for c in CLASSES
         if len(list((TRAIN_ROOT / c).glob('*.[jp][pn]g'))) < THRESH]
if empty:
    print(f'\\nWARN: classes below {THRESH} images: {empty}')
    print('Add a Roboflow Universe project that covers them, or curate '
          'manually before running the diagnostic probe / teacher fine-tune.')""")


# 5. Dedup & label-noise ---------------------------------------------
md("""## 5. Dedup & label-noise cleanup (before any training)

Cleanup ROI is higher than any scheduler tweak. Pipeline:

1. **SHA-256** dedup — exact byte-equal copies.
2. **pHash** (`imagehash.phash`, Hamming ≤ 8) — near-duplicates from re-encoded posts.
3. **DINOv2 ViT-B/14** embeddings + per-class kNN: drop crops > 2σ from class centroid.
4. **Cleanlab** label-issue ranking + **FiftyOne** embedding browser to triage top-N suspects.
5. Manual validation pass: 200–300 photos per class.""")

code("""import hashlib, imagehash
from PIL import Image as PILImage
import numpy as np

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()

def dedup_directory(root, phash_threshold=8):
    seen_sha, seen_phash = set(), []
    keep, drop = [], []
    for p in sorted(root.rglob('*')):
        if not p.is_file() or p.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'}:
            continue
        s = sha256_file(p)
        if s in seen_sha:
            drop.append((p, 'sha256_dup')); continue
        seen_sha.add(s)
        try:
            ph = imagehash.phash(PILImage.open(p).convert('RGB'))
        except Exception:
            drop.append((p, 'unreadable')); continue
        if any((ph - other) <= phash_threshold for other in seen_phash):
            drop.append((p, 'phash_dup')); continue
        seen_phash.append(ph)
        keep.append(p)
    return keep, drop

def drop_outliers_per_class(embs: np.ndarray, sigma: float = 2.0):
    centroid = embs.mean(0)
    d = np.linalg.norm(embs - centroid, axis=1)
    return d <= (d.mean() + sigma * d.std())

# Cleanlab + FiftyOne triage (pseudo-code — wire to probe outputs from §7)
# from cleanlab.rank import get_label_quality_scores
# scores = get_label_quality_scores(labels=y_true, pred_probs=probs)
# suspects = np.argsort(scores)[:200]
# import fiftyone as fo, fiftyone.brain as fob
# ds = fo.Dataset.from_dir(str(TRAIN_ROOT), dataset_type=fo.types.ImageClassificationDirectoryTree)
# fob.compute_visualization(ds, embeddings=embs, brain_key='dinov2_emb')
# fo.launch_app(ds)""")


# 6. Stage 1 — YOLO --------------------------------------------------
md("""## 6. Stage 1 — YOLOv8n single-class `plant_leaf`

The Roboflow Universe project tagged `role='detector'` in §4 is a YOLO-format dataset (image + YOLO `.txt` per crop, plus a `data.yaml`). We point Ultralytics at that YAML directly. **No PlantDoc** — its agricultural close-ups bias indoor recall.""")

code("""from ultralytics import YOLO
import yaml

def find_detector_yaml() -> Path | None:
    cache = ROOT / 'cache_roboflow'
    if not cache.exists(): return None
    candidates = list(cache.rglob('data.yaml'))
    # Prefer the project we tagged role='detector'.
    for c in candidates:
        if 'indoor-houseplant-leaf' in str(c).lower(): return c
    return candidates[0] if candidates else None

yaml_path = find_detector_yaml()
if yaml_path is None:
    print('No YOLO dataset found. Configure ROBOFLOW_API_KEY and a detector '
          'project in §4 before training Stage 1.')
else:
    print('Using YOLO dataset:', yaml_path)
    spec = yaml.safe_load(yaml_path.read_text())
    spec['names'] = ['plant_leaf']  # collapse all source classes
    spec['nc'] = 1
    patched = yaml_path.parent / 'plant_leaf.yaml'
    patched.write_text(yaml.safe_dump(spec, sort_keys=False))

    model = YOLO('yolov8n.pt')
    model.train(
        data=str(patched),
        epochs=60, patience=12, batch=32, imgsz=640,
        optimizer='AdamW', lr0=1e-3, cos_lr=True, amp=True,
        mosaic=0.5, hsv_h=0.0, hsv_s=0.4, hsv_v=0.4,
        project='runs/detect', name='plant_leaf_indoor_v1',
    )
    model.export(format='onnx', opset=13, dynamic=False)""")


# 7. DINOv2 probe ----------------------------------------------------
md("""## 7. Diagnostic baseline — DINOv2-S frozen + linear probe

Mandatory before tweaking anything else.

* Probe ≫ student → bottleneck is **data** (clean it, change sources).
* Probe ≈ chance → bottleneck is **labels / semantics** (revisit class definitions).
* Probe ≈ student → bottleneck is **capacity / augs** (now scheduler tweaks pay off).

The probe deliberately uses the **eval transform** (no augs) — linear probes are a feature-quality test, not a classifier-quality test, and adding augs would conflate the two.""")

code("""import torch, torch.nn as nn

device = 'cuda' if torch.cuda.is_available() else 'cpu'
PROBE_SIZE = 224

# Empty-data guard: refuse to load 0-image folders.
def _ensure_populated(root, name):
    n = sum(1 for _ in Path(root).rglob('*.[jp][pn]g'))
    if n == 0:
        raise RuntimeError(f'{name} is empty — re-run §4 data fetch.')
    return n

_ensure_populated(TRAIN_ROOT, 'TRAIN_ROOT')
_ensure_populated(VAL_PRIMARY, 'VAL_PRIMARY')

train_ds_probe, train_dl_probe = make_eval_loader(TRAIN_ROOT, PROBE_SIZE, batch=256)
val_ds_probe,   val_dl_probe   = make_eval_loader(VAL_PRIMARY, PROBE_SIZE, batch=256)
CLASS_NAMES = train_ds_probe.classes

backbone = torch.hub.load('facebookresearch/dinov2', 'dinov2_vits14').eval().to(device)
for p in backbone.parameters(): p.requires_grad_(False)
head = nn.Linear(384, len(CLASS_NAMES)).to(device)
opt = torch.optim.AdamW(head.parameters(), lr=3e-3, weight_decay=1e-4)
loss_fn = nn.CrossEntropyLoss(label_smoothing=0.05)

for ep in range(8):
    head.train()
    for x, y in train_dl_probe:
        x, y = x.to(device, non_blocking=True), y.to(device)
        with torch.no_grad():
            f = backbone(x)
        opt.zero_grad(); loss_fn(head(f), y).backward(); opt.step()
    head.eval(); correct = total = 0
    with torch.inference_mode():
        for x, y in val_dl_probe:
            pred = head(backbone(x.to(device))).argmax(1)
            correct += (pred == y.to(device)).sum().item(); total += y.numel()
    print(f'ep={ep+1}  val_realworld_top1={correct/total:.3f}')""")


# 8. Logit Adjustment ------------------------------------------------
md("""## 8. Loss — Logit Adjustment (Menon ICLR 2021)

Replaces the legacy *inverse-frequency CE × `HEALTHY_DOWNWEIGHT=0.25`* heuristic with `logits[y] -= τ · log π_y`, τ=1.0.""")

code("""import torch.nn.functional as F
from collections import Counter

class LogitAdjustmentLoss(nn.Module):
    def __init__(self, class_priors: torch.Tensor, tau: float = 1.0,
                 label_smoothing: float = 0.05):
        super().__init__()
        self.register_buffer('log_prior', torch.log(class_priors.clamp(min=1e-12)))
        self.tau = tau
        self.label_smoothing = label_smoothing

    def forward(self, logits, target):
        adjusted = logits - self.tau * self.log_prior
        return F.cross_entropy(adjusted, target, label_smoothing=self.label_smoothing)

def class_priors_from_imagefolder(ds) -> torch.Tensor:
    counts = Counter([y for _, y in ds.samples])
    pri = torch.tensor([counts.get(i, 0) for i in range(len(ds.classes))], dtype=torch.float32)
    pri = pri.clamp(min=1.0)  # avoid log(0) when one class is empty post-split
    return pri / pri.sum()""")


# 9. Augs ------------------------------------------------------------
md("""## 9. Indoor-targeted augmentations

WB shifts, glare, defocus/motion blur, JPEG ringing, ISP smoothing. **Disabled on purpose:** strong hue shifts and `Solarize` — colour itself is a disease signal. MixUp/CutMix kept low for the same reason.""")

code("""from PIL import ImageFilter, ImageEnhance, Image as PILImage
from io import BytesIO
import random

class WhiteBalanceShift:
    def __init__(self, p=0.5, strength=(0.8, 1.2)):
        self.p = p; self.lo, self.hi = strength
    def __call__(self, img):
        if random.random() > self.p: return img
        r_scale = random.uniform(self.lo, self.hi)
        b_scale = random.uniform(self.lo, self.hi)
        r, g, b = img.split()
        r = r.point(lambda v: max(0, min(255, int(v * r_scale))))
        b = b.point(lambda v: max(0, min(255, int(v * b_scale))))
        return PILImage.merge('RGB', (r, g, b))

class Glare:
    def __init__(self, p=0.25): self.p = p
    def __call__(self, img):
        if random.random() > self.p: return img
        return ImageEnhance.Brightness(img).enhance(random.uniform(1.05, 1.4))

class DefocusOrMotion:
    def __init__(self, p=0.3): self.p = p
    def __call__(self, img):
        if random.random() > self.p: return img
        if random.random() < 0.5:
            return img.filter(ImageFilter.GaussianBlur(radius=random.uniform(0.8, 2.4)))
        return img.filter(ImageFilter.BoxBlur(random.uniform(1.0, 2.5)))

class JpegRecompress:
    def __init__(self, p=0.4, q_range=(45, 90)):
        self.p = p; self.q_range = q_range
    def __call__(self, img):
        if random.random() > self.p: return img
        buf = BytesIO()
        img.save(buf, format='JPEG', quality=random.randint(*self.q_range))
        buf.seek(0)
        return PILImage.open(buf).convert('RGB')

indoor_train_tx = T.Compose([
    T.Resize(int(256 * 1.15)),
    T.RandomResizedCrop(256, scale=(0.7, 1.0), ratio=(0.85, 1.15)),
    T.RandomHorizontalFlip(),
    WhiteBalanceShift(p=0.5),
    Glare(p=0.25),
    DefocusOrMotion(p=0.3),
    JpegRecompress(p=0.4),
    T.ColorJitter(brightness=0.15, contrast=0.15, saturation=0.10, hue=0.0),
    T.ToTensor(),
    NORM,
    T.RandomErasing(p=0.15, scale=(0.02, 0.1)),
])""")


# 10. Teacher --------------------------------------------------------
md("""## 10. Teacher — EfficientNetV2-S

`tf_efficientnetv2_s.in21k_ft_in1k` (21 M params, 84.2 % ImageNet top-1, quantises better than ViTs we tested). **ConvNeXt-Tiny** is the accuracy challenger.

Recipe: AdamW, weight_decay=1e-4, label_smoothing=0.05, cosine + 1-epoch warmup, 256 px batch=128 **bf16 native** (no `GradScaler` — bf16 doesn't need loss scaling, fp32 fallback is automatic for ops without bf16 kernels). Head-only 3 epochs (lr=3e-3) → unfreeze last 2 stages 12–15 epochs (head lr=5e-4 / backbone lr=5e-5). MixUp 0.10, CutMix 0.10–0.20. Logit Adjustment loss (τ=1.0). Per-epoch eval on **all three** val slices; early-stop on `val_indomain` macro-F1.""")

code("""import timm
from torch.amp import autocast
from torch.optim.lr_scheduler import CosineAnnealingLR, LinearLR, SequentialLR

BATCH, IMG = 128, 256

train_ds = ImageFolder(str(TRAIN_ROOT), transform=indoor_train_tx)
if len(train_ds) == 0:
    raise RuntimeError('TRAIN_ROOT is empty — re-run §4 data fetch.')
train_dl = DataLoader(train_ds, batch_size=BATCH, shuffle=True, num_workers=8,
                      pin_memory=True, drop_last=True)

# Three independent val slices.
val_indomain_ds, val_indomain_dl   = make_eval_loader(VAL_INDOMAIN, IMG)
val_realworld_ds, val_realworld_dl = make_eval_loader(VAL_PRIMARY,  IMG)
try:
    val_studio_ds, val_studio_dl   = make_eval_loader(VAL_STUDIO,    IMG)
except RuntimeError:
    val_studio_ds = val_studio_dl = None
    print('val_studio empty — skipping. Studio is a monitor, never blocking.')

CLASS_NAMES = train_ds.classes
assert CLASS_NAMES == sorted(CLASSES), (
    f'Class folder set drift: {sorted(set(CLASS_NAMES) ^ set(CLASSES))}'
)

teacher = timm.create_model('tf_efficientnetv2_s.in21k_ft_in1k',
                            pretrained=True, num_classes=len(CLASS_NAMES)).to(device)
priors = class_priors_from_imagefolder(train_ds).to(device)
loss_fn = LogitAdjustmentLoss(priors, tau=1.0, label_smoothing=0.05).to(device)

def step_eval(model, tag):
    \"\"\"Log val_realworld + val_indomain + (optional) val_studio.\"\"\"
    out = {
        'indomain':  evaluate(model, val_indomain_dl,  CLASS_NAMES, device, f'{tag} val_indomain'),
        'realworld': evaluate(model, val_realworld_dl, CLASS_NAMES, device, f'{tag} val_realworld'),
    }
    if val_studio_dl is not None:
        out['studio'] = evaluate(model, val_studio_dl, CLASS_NAMES, device, f'{tag} val_studio')
    return out

def _make_sched(opt, n_epochs, n_steps_per_epoch):
    \"\"\"Linear warmup (1 ep) → cosine. Guarded against empty/short runs.\"\"\"
    n_steps_per_epoch = max(1, n_steps_per_epoch)
    n_total = max(2, n_epochs) * n_steps_per_epoch
    warm_iters = min(n_steps_per_epoch, max(1, n_total - 1))
    cos_iters  = max(1, n_total - warm_iters)
    warm = LinearLR(opt, start_factor=0.1, total_iters=warm_iters)
    cos  = CosineAnnealingLR(opt, T_max=cos_iters)
    return SequentialLR(opt, [warm, cos], milestones=[warm_iters])

def _train_phase(model, opt, n_epochs, scheduler, tag, early_stop=False, patience=4):
    best_f1, best_state, bad = -1.0, None, 0
    for ep in range(n_epochs):
        model.train()
        running = 0.0; n_seen = 0
        for x, y in train_dl:
            x = x.to(device, non_blocking=True); y = y.to(device, non_blocking=True)
            opt.zero_grad()
            with autocast('cuda', dtype=torch.bfloat16):
                loss = loss_fn(model(x), y)
            loss.backward()       # bf16 needs no GradScaler
            opt.step(); scheduler.step()
            running += float(loss.detach()) * x.size(0); n_seen += x.size(0)
        print(f'[{tag} ep={ep+1}/{n_epochs}] mean_loss={running/max(1,n_seen):.4f}')
        metrics = step_eval(model, f'[{tag} ep={ep+1}]')
        if early_stop:
            f1 = metrics['indomain']['disease_macro_f1']
            if f1 > best_f1:
                best_f1, best_state = f1, {k: v.detach().cpu().clone()
                                           for k, v in model.state_dict().items()}
                bad = 0
            else:
                bad += 1
                if bad >= patience:
                    print(f'  early-stop @ ep={ep+1}  (no val_indomain gain for {patience} eps)')
                    break
    if early_stop and best_state is not None:
        model.load_state_dict(best_state)

# Phase A — head only (3 epochs, lr=3e-3)
for n, p in teacher.named_parameters():
    p.requires_grad_(n.startswith('classifier'))
opt = torch.optim.AdamW([p for p in teacher.parameters() if p.requires_grad],
                        lr=3e-3, weight_decay=1e-4)
sched = _make_sched(opt, n_epochs=3, n_steps_per_epoch=len(train_dl))
_train_phase(teacher, opt, n_epochs=3, scheduler=sched, tag='head-only')

# Phase B — unfreeze last 2 stages (12–15 epochs) — early-stop on val_indomain
for n, p in teacher.named_parameters():
    p.requires_grad_(True)
head_params = [p for n, p in teacher.named_parameters() if n.startswith('classifier')]
back_params = [p for n, p in teacher.named_parameters() if not n.startswith('classifier')]
opt = torch.optim.AdamW([
    {'params': head_params, 'lr': 5e-4},
    {'params': back_params, 'lr': 5e-5},
], weight_decay=1e-4)
EPOCHS_B = 13
sched = _make_sched(opt, n_epochs=EPOCHS_B, n_steps_per_epoch=len(train_dl))
_train_phase(teacher, opt, n_epochs=EPOCHS_B, scheduler=sched,
             tag='finetune', early_stop=True, patience=4)

Path('models').mkdir(parents=True, exist_ok=True)
torch.save(teacher.state_dict(), 'models/teacher_v2s.pth')""")


# 11. KD -------------------------------------------------------------
md("""## 11. Knowledge distillation → MobileNetV3-Large student

Production artefact = MobileNetV3-Large (5.5 M) distilled from V2-S.

Loss: `α·CE(z_s, y) + (1-α)·KL(σ(z_t/τ) || σ(z_s/τ))`. We **drop the canonical τ² rescale** to keep CE and KL on the same scale — with α=0.35, τ=3.5, the τ² term made KL outweigh CE by ~22×, drowning the hard-label gradient. With α=0.5 and τ=4 (no τ²), the KL gradient stays in a reasonable band and the student actually listens to ground-truth labels. Roughly 12 epochs.""")

code("""from torchvision import models

ALPHA, TAU, EPOCHS_KD = 0.5, 4.0, 12   # see §11 — no τ² in loss

student = models.mobilenet_v3_large(weights=models.MobileNet_V3_Large_Weights.IMAGENET1K_V2)
in_f = student.classifier[-1].in_features
student.classifier[-1] = nn.Linear(in_f, len(CLASS_NAMES))
student = student.to(device)

teacher.eval()
for p in teacher.parameters(): p.requires_grad_(False)

opt   = torch.optim.AdamW(student.parameters(), lr=5e-4, weight_decay=1e-4)
sched = _make_sched(opt, n_epochs=EPOCHS_KD, n_steps_per_epoch=len(train_dl))
ce    = nn.CrossEntropyLoss(label_smoothing=0.05)

best_f1, best_state, bad, patience = -1.0, None, 0, 3
for ep in range(EPOCHS_KD):
    student.train()
    running_ce = running_kl = running = 0.0; n_seen = 0
    for x, y in train_dl:
        x = x.to(device, non_blocking=True); y = y.to(device, non_blocking=True)
        opt.zero_grad()
        with autocast('cuda', dtype=torch.bfloat16):
            with torch.no_grad():
                t_logits = teacher(x)
            s_logits = student(x)
            ce_loss  = ce(s_logits, y)
            kl_loss  = F.kl_div(
                F.log_softmax(s_logits / TAU, dim=1),
                F.softmax(t_logits / TAU, dim=1),
                reduction='batchmean',
            )
            loss = ALPHA * ce_loss + (1 - ALPHA) * kl_loss
        loss.backward(); opt.step(); sched.step()
        bs = x.size(0)
        running    += float(loss.detach()) * bs
        running_ce += float(ce_loss.detach()) * bs
        running_kl += float(kl_loss.detach()) * bs
        n_seen += bs
    print(f'[KD ep={ep+1}/{EPOCHS_KD}] mean_loss={running/n_seen:.4f}  '
          f'ce={running_ce/n_seen:.4f}  kl={running_kl/n_seen:.4f}')
    metrics = step_eval(student, f'[KD ep={ep+1}]')
    f1 = metrics['indomain']['disease_macro_f1']
    if f1 > best_f1:
        best_f1, best_state = f1, {k: v.detach().cpu().clone()
                                   for k, v in student.state_dict().items()}
        bad = 0
    else:
        bad += 1
        if bad >= patience:
            print(f'  KD early-stop @ ep={ep+1}'); break

if best_state is not None:
    student.load_state_dict(best_state)
torch.save(student.state_dict(), 'models/classifier.pth')""")


# 12. ONNX + INT8 + temp ---------------------------------------------
md("""## 12. ONNX export + **static** INT8 (QDQ) + temperature scaling

We export the student on **CPU** (so the resulting graph carries no CUDA-only ops) and then dynamic-quantize-static-style with a 500-photo `val_realworld` calibration reader. ORT does not guarantee accuracy after static quantization — this cell **re-evaluates the INT8 ONNX model on `val_realworld` and refuses to write it if disease F1 drops by more than 0.02 vs FP32**.

Temperature scaling is fitted on `val_realworld` NLL and persisted to `models/temperature.json`.""")

code("""import json, numpy as np, torch
import onnxruntime as ort
from onnxruntime.quantization import (
    quantize_static, CalibrationDataReader, QuantFormat, QuantType,
)

# 1. ONNX FP32 export — on CPU to avoid embedding CUDA-only ops in the graph.
student_cpu = student.cpu().eval()
dummy = torch.randn(1, 3, IMG, IMG)
torch.onnx.export(
    student_cpu, dummy, 'models/classifier.onnx', opset_version=18,
    input_names=['input'], output_names=['logits'],
    dynamic_axes={'input': {0: 'N'}, 'logits': {0: 'N'}},
)

# 2. Static INT8 calibration — 500 real user-photo crops from val_realworld.
class RealWorldCalibrationReader(CalibrationDataReader):
    \"\"\"Streams 500 calibration tensors through ORT in eval-transform space.\"\"\"
    def __init__(self, dataset, n=500, input_name='input'):
        self.input_name = input_name
        idx = np.random.RandomState(0).permutation(len(dataset))[:n]
        self._iter = iter([
            (dataset[i][0].unsqueeze(0).numpy().astype(np.float32),) for i in idx
        ])
    def get_next(self):
        nxt = next(self._iter, None)
        return None if nxt is None else {self.input_name: nxt[0]}

quantize_static(
    model_input='models/classifier.onnx',
    model_output='models/classifier_int8.onnx',
    calibration_data_reader=RealWorldCalibrationReader(val_realworld_ds, n=500),
    quant_format=QuantFormat.QDQ,
    activation_type=QuantType.QInt8,
    weight_type=QuantType.QInt8,
    per_channel=True,
)

# 3. Mandatory post-quantization validation on val_realworld.
def eval_onnx(path, dl, class_names):
    sess = ort.InferenceSession(path, providers=['CPUExecutionProvider'])
    in_name = sess.get_inputs()[0].name
    y_true, y_pred = [], []
    for x, y in dl:
        logits = sess.run(None, {in_name: x.numpy().astype(np.float32)})[0]
        y_pred.extend(logits.argmax(1).tolist()); y_true.extend(y.tolist())
    y_true = np.asarray(y_true); y_pred = np.asarray(y_pred)
    disease_idx = [i for i, n in enumerate(class_names)
                   if n not in ('healthy', 'not_a_plant')]
    return float(f1_score(y_true, y_pred, labels=disease_idx,
                          average='macro', zero_division=0))

f1_fp32 = eval_onnx('models/classifier.onnx',      val_realworld_dl, CLASS_NAMES)
f1_int8 = eval_onnx('models/classifier_int8.onnx', val_realworld_dl, CLASS_NAMES)
drift = f1_fp32 - f1_int8
print(f'val_realworld disease F1  fp32={f1_fp32:.4f}  int8={f1_int8:.4f}  drift={drift:+.4f}')
assert drift <= 0.02, (
    f'Static INT8 quantization dropped disease F1 by {drift:.3f} (>0.02). '
    f'Re-run with a different calibration set or fall back to FP32.'
)

# Move student back to GPU for temperature scaling.
student = student.to(device)

# 4. Temperature scaling on val_realworld NLL.
logits_val, y_val = [], []
with torch.inference_mode():
    for x, y in val_realworld_dl:
        logits_val.append(student(x.to(device)).cpu()); y_val.append(y)
logits_val = torch.cat(logits_val); y_val = torch.cat(y_val)

T_param = torch.tensor(1.0, requires_grad=True)
opt = torch.optim.LBFGS([T_param], lr=0.05, max_iter=50)
def closure():
    opt.zero_grad()
    loss = F.cross_entropy(logits_val / T_param.clamp(min=0.05), y_val)
    loss.backward(); return loss
opt.step(closure)
T_val = float(T_param.detach())
json.dump({'temperature': T_val}, open('models/temperature.json', 'w'), indent=2)
print(f'fitted T={T_val:.3f}')

id_max_logit = logits_val.max(dim=1).values.numpy()
print(f'in-dist MaxLogit  mean={id_max_logit.mean():.2f}  '
      f'p10={np.percentile(id_max_logit, 10):.2f}')""")


# 13. Conformal ------------------------------------------------------
md("""## 12a. Conformal calibration — RAPS prediction sets at 90 % coverage

`calibrate_q_hat()` is **inlined here** so the notebook is self-contained on Colab; the production server reads the resulting `models/conformal.json` via `server/conformal.py`. We use the conformal-cal slice that §4 carved out (5 % of `train/` by `plant_id`) — disjoint from the 500 photos used for the INT8 calibration reader, also disjoint from `val_indomain` and `val_realworld`.""")

code("""# Self-contained conformal calibration (mirrors server/conformal.py).
def _aps_score(p_sorted): return np.cumsum(p_sorted)
def _raps_score(p_sorted, lam, k_reg):
    cs = np.cumsum(p_sorted)
    ranks = np.arange(1, len(p_sorted) + 1, dtype=np.float64)
    return cs + lam * np.maximum(0.0, ranks - k_reg)

def calibrate_q_hat(cal_probs, cal_labels, *, method='raps', alpha=0.1,
                    lambda_reg=0.01, k_reg=1):
    if not (0.0 < alpha < 1.0): raise ValueError('alpha out of range')
    if method not in ('aps', 'raps'): raise ValueError(method)
    n = cal_probs.shape[0]
    nonconf = np.empty(n, dtype=np.float64)
    for i in range(n):
        order = np.argsort(-cal_probs[i])
        ps = cal_probs[i, order]
        s  = _aps_score(ps) if method == 'aps' else _raps_score(ps, lambda_reg, k_reg)
        rank_of_true = int(np.where(order == cal_labels[i])[0][0])
        nonconf[i] = s[rank_of_true]
    q_level = min(1.0, np.ceil((n + 1) * (1.0 - alpha)) / n)
    q_hat = float(np.quantile(nonconf, q_level, method='higher'))
    return {
        'method': method, 'alpha': alpha, 'q_hat': q_hat,
        'lambda_reg': lambda_reg, 'k_reg': k_reg,
        'calibration_n': int(n),
    }

cal_ds, cal_dl = make_eval_loader(CONFORMAL_CAL, IMG)

logits_cal, y_cal = [], []
with torch.inference_mode():
    for x, y in cal_dl:
        logits_cal.append(student(x.to(device)).cpu()); y_cal.append(y)
logits_cal = torch.cat(logits_cal); y_cal = torch.cat(y_cal).numpy()
probs_cal  = torch.softmax(logits_cal / max(T_val, 1e-3), dim=1).numpy()

cfg = calibrate_q_hat(probs_cal, y_cal, method='raps', alpha=0.10,
                      lambda_reg=0.01, k_reg=1)
json.dump(cfg, open('models/conformal.json', 'w'), indent=2)
print('conformal:', cfg)""")


# 13. Eval contract --------------------------------------------------
md("""## 13. Evaluation contract

Three independent slices, three reports per release. Logged every epoch during training and replayed once on the final ONNX INT8 artefact:

| Slice | Role | Pass criterion |
|---|---|---|
| `val_indomain` | training-loop metric — drives early stopping | non-decreasing for ≥ 4 epochs |
| `val_realworld` | **primary — ship/no-ship gate** | disease macro-F1 ∈ **0.70 – 0.78**; no rare class with recall = 0; healthy-bias < 1.5 |
| `val_studio` | transfer / background-bias monitor (Noyan 2022) | tracked, never blocking |

Engineering keepers from the legacy pipeline that we explicitly preserve: disease macro-F1 as primary metric, healthy-bias monitor each epoch, group-aware splits by `plant_id`, hardlinks for image stores, sparse checkout for big repos, ONNX sanity drift-check after every quantization step.

A v2.x model that gains primary F1 but tanks `val_studio` top-1 is fine — that means we're learning leaves, not backgrounds.

### Optional follow-up: Coreset / dataset pruning
Once the v2.0 weights land, **DeepCore** (Guo et al. 2022) is the cheapest place to look for further gains if iteration time becomes a bottleneck. Random selection stays as the no-op baseline. **Do not** invest in dataset distillation (weak ROI at our resolution / ambiguous label boundaries). Model pruning is low priority — KD into MobileNetV3-Large + static INT8 already covers ~95 % of the runtime budget.""")


# Write the notebook -------------------------------------------------
notebook = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.10"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

NB_PATH.write_text(json.dumps(notebook, indent=1))
print(f'wrote {NB_PATH}  cells={len(cells)}')
