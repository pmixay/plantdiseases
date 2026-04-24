"""
Unified training script for PlantScope v3.x.

Trains both stages of the pipeline:

    1. Detector   (YOLOv8n)          — leaf / diseased_leaf localisation
    2. Classifier (EfficientNetV2-S) — 9-class houseplant disease head

Both stages expect datasets prepared under ``server/data``:

    data/
      detector/                # YOLO format
        data.yaml              # Ultralytics data config (nc=2, names=[leaf, diseased_leaf])
        images/train/*.jpg
        images/val/*.jpg
        labels/train/*.txt
        labels/val/*.txt

      classifier/              # ImageFolder format (alphabetical class order is authoritative)
        train/
          blight/
          healthy/
          leaf_mold/
          leaf_spot/
          mosaic_virus/
          not_a_plant/
          powdery_mildew/
          rust/
          spider_mites/
        val/
          ... same structure ...

Usage::

    python train.py                 # train both models
    python train.py --detector      # YOLO only
    python train.py --classifier    # EfficientNet only

The classifier uses two-phase transfer learning (freeze backbone → fine-tune).
Everything runs on CPU if CUDA is not available, but training is intended
for Google Colab (see ``train_notebook.ipynb``).
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import os
import random
import sys
import time
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim
from PIL import Image
from torch.utils.data import DataLoader, WeightedRandomSampler
from torchvision import datasets, transforms

from classifier import _build_efficientnet_v2_s


class RandomJPEGCompression:
    """Re-encode the PIL image as JPEG with a random quality level.

    Phone cameras apply aggressive JPEG compression that creates blocky
    artefacts on leaf edges and disease spots. Simulating this during
    training makes the classifier a lot more robust on real uploads.
    """

    def __init__(self, p: float = 0.5, quality_range: tuple[int, int] = (40, 95)):
        self.p = p
        self.q_min, self.q_max = quality_range

    def __call__(self, img: Image.Image) -> Image.Image:
        if random.random() > self.p:
            return img
        q = random.randint(self.q_min, self.q_max)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=q)
        buf.seek(0)
        return Image.open(buf).convert("RGB")

# ── Paths ────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("PLANTD_DATA_DIR", SCRIPT_DIR / "data"))
DETECTOR_DATA_DIR = DATA_DIR / "detector"
CLASSIFIER_DATA_DIR = DATA_DIR / "classifier"
MODELS_DIR = SCRIPT_DIR / "models"
CHECKPOINT_DIR = MODELS_DIR / "checkpoints"

DETECTOR_OUTPUT = MODELS_DIR / "detector.pt"
CLASSIFIER_OUTPUT = MODELS_DIR / "classifier.pth"
CLASSES_JSON = MODELS_DIR / "classes.json"

# ── Hyperparameters ──────────────────────────────────────────────────
CLASSIFIER_IMG_SIZE = 300  # EfficientNetV2-S
BATCH_SIZE = int(os.getenv("PLANTD_BATCH_SIZE", 32))
NUM_WORKERS = min(4, os.cpu_count() or 1)
SEED = 42
MODEL_VERSION = "3.0.0"

torch.manual_seed(SEED)


# ── Classifier transforms (augmentation tuned for real-world houseplants) ─
#
# Covers the failure modes that actually hit a houseplant app in practice:
#   • hand-held phone shake           → GaussianBlur, RandomAffine
#   • off-axis shots of leaves        → RandomPerspective, RandomRotation
#   • lighting (tungsten / daylight)  → ColorJitter
#   • fingers / pots occluding leaves → RandomErasing
#   • aggressive mobile JPEG codecs   → RandomJPEGCompression (custom)
#   • varying framing / zoom          → RandomResizedCrop
train_transforms = transforms.Compose([
    transforms.RandomResizedCrop(CLASSIFIER_IMG_SIZE, scale=(0.6, 1.0)),
    transforms.RandomHorizontalFlip(),
    transforms.RandomVerticalFlip(p=0.1),
    transforms.RandomRotation(25),
    transforms.RandomPerspective(distortion_scale=0.25, p=0.35),
    transforms.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.25, hue=0.05),
    transforms.RandomAffine(degrees=0, translate=(0.08, 0.08), shear=8),
    transforms.GaussianBlur(kernel_size=3, sigma=(0.1, 1.5)),
    RandomJPEGCompression(p=0.5, quality_range=(40, 95)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    transforms.RandomErasing(p=0.35, scale=(0.02, 0.22)),
])

val_transforms = transforms.Compose([
    transforms.Resize((CLASSIFIER_IMG_SIZE, CLASSIFIER_IMG_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
])


# ── Helpers ──────────────────────────────────────────────────────────

def _device() -> torch.device:
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _class_weights(dataset: datasets.ImageFolder) -> torch.Tensor:
    counts = [0] * len(dataset.classes)
    for _, label in dataset.samples:
        counts[label] += 1
    total = sum(counts)
    weights = [total / (c or 1) for c in counts]
    return torch.tensor(weights, dtype=torch.float32)


def _make_sampler(dataset: datasets.ImageFolder) -> WeightedRandomSampler:
    class_w = _class_weights(dataset)
    sample_weights = [class_w[label].item() for _, label in dataset.samples]
    return WeightedRandomSampler(
        weights=sample_weights,
        num_samples=len(sample_weights),
        replacement=True,
    )


@torch.no_grad()
def _print_per_class_report(
    model: nn.Module,
    val_loader: DataLoader,
    class_names: list[str],
    device: torch.device,
    use_amp: bool,
) -> None:
    """Collect predictions over the whole val set and print per-class
    precision/recall/F1 plus a confusion matrix. Required to tell whether
    the classifier actually learned disease classes or only the dominant
    `healthy` / `not_a_plant` classes."""
    model.eval()
    all_pred: list[int] = []
    all_true: list[int] = []
    for images, labels in val_loader:
        images = images.to(device, non_blocking=True)
        with torch.cuda.amp.autocast(enabled=use_amp and device.type == "cuda"):
            logits = model(images)
        all_pred.extend(logits.argmax(1).cpu().tolist())
        all_true.extend(labels.tolist())

    try:
        from sklearn.metrics import classification_report, confusion_matrix  # type: ignore
    except ImportError:
        print("[classifier] scikit-learn not installed — skipping per-class report")
        return

    print("\n[classifier] Per-class metrics on val:")
    print(classification_report(
        all_true, all_pred,
        target_names=class_names,
        digits=3,
        zero_division=0,
    ))
    cm = confusion_matrix(all_true, all_pred, labels=list(range(len(class_names))))
    print("Confusion matrix (rows=true, cols=pred):")
    header = "            " + " ".join(f"{n[:8]:>9}" for n in class_names)
    print(header)
    for name, row in zip(class_names, cm, strict=False):
        print(f"{name:>12} " + " ".join(f"{v:>9}" for v in row))


# ── Stage 2: Classifier training ─────────────────────────────────────

def train_classifier(
    epochs_head: int = 10,
    epochs_finetune: int = 8,
    lr_head: float = 1e-3,
    lr_finetune: float = 5e-5,
    balanced_sampler: bool = True,
    use_amp: bool = True,
) -> None:
    """Two-phase transfer learning on EfficientNetV2-S."""
    device = _device()
    print(f"[classifier] device={device}  epochs={epochs_head}+{epochs_finetune}")

    train_root = CLASSIFIER_DATA_DIR / "train"
    val_root = CLASSIFIER_DATA_DIR / "val"
    if not train_root.exists() or not val_root.exists():
        raise FileNotFoundError(
            f"Classifier dataset not found. Expected {train_root} and {val_root}.\n"
            f"Layout: data/classifier/{{train,val}}/<class>/*.jpg"
        )

    train_ds = datasets.ImageFolder(str(train_root), transform=train_transforms)
    val_ds = datasets.ImageFolder(str(val_root), transform=val_transforms)
    num_classes = len(train_ds.classes)
    print(f"[classifier] classes ({num_classes}): {train_ds.classes}")

    if train_ds.classes != sorted(train_ds.classes):
        raise RuntimeError(
            "ImageFolder class order is not alphabetical — this will break the "
            "classifier head. Rename the folders so sorted() matches the order."
        )

    sampler = _make_sampler(train_ds) if balanced_sampler else None
    train_loader = DataLoader(
        train_ds,
        batch_size=BATCH_SIZE,
        sampler=sampler,
        shuffle=sampler is None,
        num_workers=NUM_WORKERS,
        pin_memory=(device.type == "cuda"),
    )
    val_loader = DataLoader(
        val_ds,
        batch_size=BATCH_SIZE,
        shuffle=False,
        num_workers=NUM_WORKERS,
        pin_memory=(device.type == "cuda"),
    )

    # ── Build model ──
    net = _build_efficientnet_v2_s(num_classes)
    from torchvision.models import EfficientNet_V2_S_Weights, efficientnet_v2_s
    pretrained = efficientnet_v2_s(weights=EfficientNet_V2_S_Weights.DEFAULT)
    state = pretrained.state_dict()
    own = net.state_dict()
    for k, v in state.items():
        if k in own and own[k].shape == v.shape:
            own[k] = v
    net.load_state_dict(own, strict=False)

    # Phase A: freeze backbone
    for name, p in net.named_parameters():
        p.requires_grad = name.startswith("classifier.")
    net.to(device)

    criterion = nn.CrossEntropyLoss(label_smoothing=0.1)

    def _make_opt_sched(params, lr: float, epochs: int):
        """AdamW with linear warmup (1 epoch) → cosine decay."""
        opt = optim.AdamW(params, lr=lr, weight_decay=1e-4)
        warmup_epochs = min(1, max(epochs // 10, 0))
        if warmup_epochs > 0 and epochs > warmup_epochs:
            warm = optim.lr_scheduler.LinearLR(
                opt, start_factor=0.1, end_factor=1.0, total_iters=warmup_epochs,
            )
            cos = optim.lr_scheduler.CosineAnnealingLR(
                opt, T_max=epochs - warmup_epochs,
            )
            sch = optim.lr_scheduler.SequentialLR(
                opt, schedulers=[warm, cos], milestones=[warmup_epochs],
            )
        else:
            sch = optim.lr_scheduler.CosineAnnealingLR(opt, T_max=max(epochs, 1))
        return opt, sch

    optimizer, scheduler = _make_opt_sched(
        [p for p in net.parameters() if p.requires_grad],
        lr=lr_head, epochs=epochs_head,
    )
    scaler = torch.cuda.amp.GradScaler(enabled=use_amp and device.type == "cuda")

    best_acc = 0.0
    best_state = None
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    def _save_checkpoint(state: dict, epoch: int, phase_name: str) -> None:
        """Persist `last.pth` after every epoch so a Colab disconnect
        doesn't cost the whole run."""
        torch.save(
            {"state_dict": state, "epoch": epoch, "phase": phase_name},
            CHECKPOINT_DIR / "last.pth",
        )

    def run_epoch(model, loader, train: bool):
        model.train(train)
        total_loss = 0.0
        total, correct = 0, 0
        for images, labels in loader:
            images = images.to(device, non_blocking=True)
            labels = labels.to(device, non_blocking=True)
            with torch.cuda.amp.autocast(enabled=use_amp and device.type == "cuda"):
                logits = model(images)
                loss = criterion(logits, labels)
            if train:
                optimizer.zero_grad(set_to_none=True)
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
            total_loss += loss.item() * images.size(0)
            correct += (logits.argmax(1) == labels).sum().item()
            total += images.size(0)
        return total_loss / total, correct / total

    def run_validation(model):
        model.eval()
        with torch.no_grad():
            return run_epoch(model, val_loader, train=False)

    print("[classifier] Phase A — head only")
    for epoch in range(1, epochs_head + 1):
        t0 = time.time()
        tr_loss, tr_acc = run_epoch(net, train_loader, train=True)
        val_loss, val_acc = run_validation(net)
        scheduler.step()
        print(
            f"  epoch {epoch}/{epochs_head}  "
            f"train_loss={tr_loss:.3f} acc={tr_acc:.3f}  "
            f"val_loss={val_loss:.3f} acc={val_acc:.3f}  "
            f"lr={optimizer.param_groups[0]['lr']:.2e}  "
            f"({time.time() - t0:.1f}s)"
        )
        _save_checkpoint(net.state_dict(), epoch, "A")
        if val_acc > best_acc:
            best_acc = val_acc
            best_state = copy.deepcopy(net.state_dict())
            torch.save(best_state, CHECKPOINT_DIR / "best.pth")

    # Phase B: unfreeze top blocks
    unfreeze_layers = ("features.5", "features.6", "features.7", "classifier.")
    for name, p in net.named_parameters():
        p.requires_grad = any(name.startswith(prefix) for prefix in unfreeze_layers)
    trainable = sum(p.numel() for p in net.parameters() if p.requires_grad)
    print(f"[classifier] Phase B — fine-tune ({trainable:,} trainable params)")

    optimizer, scheduler = _make_opt_sched(
        [p for p in net.parameters() if p.requires_grad],
        lr=lr_finetune, epochs=epochs_finetune,
    )

    for epoch in range(1, epochs_finetune + 1):
        t0 = time.time()
        tr_loss, tr_acc = run_epoch(net, train_loader, train=True)
        val_loss, val_acc = run_validation(net)
        scheduler.step()
        print(
            f"  epoch {epoch}/{epochs_finetune}  "
            f"train_loss={tr_loss:.3f} acc={tr_acc:.3f}  "
            f"val_loss={val_loss:.3f} acc={val_acc:.3f}  "
            f"lr={optimizer.param_groups[0]['lr']:.2e}  "
            f"({time.time() - t0:.1f}s)"
        )
        _save_checkpoint(net.state_dict(), epoch, "B")
        if val_acc > best_acc:
            best_acc = val_acc
            best_state = copy.deepcopy(net.state_dict())
            torch.save(best_state, CHECKPOINT_DIR / "best.pth")

    # Per-class diagnostics — without these, high aggregate accuracy can
    # hide a model that just predicts `healthy` or never flags disease.
    _print_per_class_report(net, val_loader, train_ds.classes, device, use_amp)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    torch.save(best_state, CLASSIFIER_OUTPUT)
    CLASSES_JSON.write_text(
        json.dumps({
            "_comment": "Regenerated by train.py — PlantScope v3.x.",
            "model_version": MODEL_VERSION,
            "architecture": {
                "detector": "YOLOv8n",
                "classifier": "EfficientNetV2-S",
            },
            "num_classes": num_classes,
            "class_names": list(train_ds.classes),
            "detector_classes": ["leaf", "diseased_leaf"],
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[classifier] done  best_val_acc={best_acc:.3f}  saved={CLASSIFIER_OUTPUT}")


# ── Stage 1: YOLOv8n detector training ───────────────────────────────

def train_detector(
    epochs: int = 60,
    imgsz: int = 640,
    batch: int = 32,
    base_model: str = "yolov8n.pt",
) -> None:
    """Fine-tune YOLOv8n on the leaf / diseased_leaf dataset."""
    try:
        from ultralytics import YOLO  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "ultralytics is not installed. `pip install -r requirements-train.txt`"
        ) from e

    data_yaml = DETECTOR_DATA_DIR / "data.yaml"
    if not data_yaml.exists():
        raise FileNotFoundError(
            f"YOLO dataset config not found: {data_yaml}.\n"
            f"Expected layout under {DETECTOR_DATA_DIR}:\n"
            f"  data.yaml, images/train, images/val, labels/train, labels/val"
        )

    print(f"[detector] training YOLOv8n on {data_yaml} for {epochs} epochs")
    model = YOLO(base_model)
    model.train(
        data=str(data_yaml),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        project=str(MODELS_DIR / "yolo_runs"),
        name="detector",
        exist_ok=True,
        patience=15,
        cos_lr=True,
        amp=True,
        verbose=True,
    )

    best_pt = MODELS_DIR / "yolo_runs" / "detector" / "weights" / "best.pt"
    if not best_pt.exists():
        raise RuntimeError(f"Expected best.pt at {best_pt} but it's missing")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    DETECTOR_OUTPUT.write_bytes(best_pt.read_bytes())
    print(f"[detector] done  saved={DETECTOR_OUTPUT}")


# ── CLI ──────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Train PlantScope v3.x (YOLOv8n detector + EfficientNetV2-S classifier).",
    )
    parser.add_argument("--detector", action="store_true", help="train YOLOv8n only")
    parser.add_argument("--classifier", action="store_true", help="train EfficientNetV2-S only")
    parser.add_argument("--epochs-head", type=int, default=10)
    parser.add_argument("--epochs-finetune", type=int, default=8)
    parser.add_argument("--epochs-detector", type=int, default=60)
    parser.add_argument("--no-sampler", action="store_true", help="disable class-balanced sampler")
    args = parser.parse_args()

    run_det = args.detector or not args.classifier
    run_cls = args.classifier or not args.detector

    if run_det:
        try:
            train_detector(epochs=args.epochs_detector)
        except FileNotFoundError as e:
            print(f"[detector] skipped: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[detector] failed: {e}", file=sys.stderr)
            return 1

    if run_cls:
        try:
            train_classifier(
                epochs_head=args.epochs_head,
                epochs_finetune=args.epochs_finetune,
                balanced_sampler=not args.no_sampler,
            )
        except FileNotFoundError as e:
            print(f"[classifier] skipped: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[classifier] failed: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
