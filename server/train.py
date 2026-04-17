"""
Unified training script for the PlantDiseases two-stage pipeline.

Trains both models sequentially:
    1. Detector   (MobileNetV3-Small)  — binary: healthy vs diseased
    2. Classifier (EfficientNet-B0)    — multi-class disease identification

Both use two-phase transfer learning:
    Phase A  — freeze backbone, train head only
    Phase B  — unfreeze top layers and fine-tune with a lower learning rate

Dataset layout expected under ``server/data``::

    data/
      train/
        healthy/
        bacterial_spot/
        ...
      val/
        healthy/
        bacterial_spot/
        ...

Usage::

    python train.py              # train both models
    python train.py --detector   # train detector only
    python train.py --classifier # train classifier only
"""

import argparse
import copy
import json
import os
import sys
import time
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
from torchvision import datasets, models, transforms

# ── Paths ────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("PLANTD_DATA_DIR", SCRIPT_DIR / "data"))
MODELS_DIR = SCRIPT_DIR / "models"
CHECKPOINT_DIR = MODELS_DIR / "checkpoints"

DETECTOR_OUTPUT = MODELS_DIR / "detector.pth"
CLASSIFIER_OUTPUT = MODELS_DIR / "classifier.pth"

# ── Hyperparameters ──────────────────────────────────────────────────
IMG_SIZE = 224
BATCH_SIZE = int(os.getenv("PLANTD_BATCH_SIZE", 32))
NUM_WORKERS = min(4, os.cpu_count() or 1)
SEED = 42

torch.manual_seed(SEED)

# ── Data transforms ──────────────────────────────────────────────────
train_transforms = transforms.Compose([
    transforms.RandomResizedCrop(IMG_SIZE, scale=(0.8, 1.0)),
    transforms.RandomHorizontalFlip(),
    transforms.RandomVerticalFlip(p=0.1),
    transforms.RandomRotation(25),
    transforms.ColorJitter(brightness=0.25, contrast=0.25, saturation=0.2, hue=0.05),
    transforms.RandomAffine(degrees=0, translate=(0.1, 0.1), shear=10),
    transforms.GaussianBlur(kernel_size=3, sigma=(0.1, 1.5)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    transforms.RandomErasing(p=0.15, scale=(0.02, 0.15)),
])

val_transforms = transforms.Compose([
    transforms.Resize((IMG_SIZE, IMG_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
])


class BinaryPlantDataset(torch.utils.data.Dataset):
    """Wraps an ImageFolder and collapses all non-healthy classes to label 1."""

    def __init__(self, image_folder: datasets.ImageFolder, healthy_idx: int):
        self.dataset = image_folder
        self.healthy_idx = healthy_idx

    def __len__(self):
        return len(self.dataset)

    def __getitem__(self, idx):
        img, label = self.dataset[idx]
        binary_label = 0 if label == self.healthy_idx else 1
        return img, binary_label


def create_dataloaders(binary: bool = False):
    """Return (train_loader, val_loader, class_names)."""
    train_dir = DATA_DIR / "train"
    val_dir = DATA_DIR / "val"

    if not train_dir.exists() or not val_dir.exists():
        print(f"ERROR: training data not found at {DATA_DIR}")
        print("Set PLANTD_DATA_DIR or put the PlantVillage dataset under server/data/")
        sys.exit(1)

    train_ds = datasets.ImageFolder(train_dir, transform=train_transforms)
    val_ds = datasets.ImageFolder(val_dir, transform=val_transforms)
    found = sorted(train_ds.classes)
    print(f"  Found classes in data: {found}")

    if binary:
        healthy_idx = train_ds.class_to_idx.get("healthy", 0)
        train_ds = BinaryPlantDataset(train_ds, healthy_idx)
        val_ds_raw = datasets.ImageFolder(val_dir, transform=val_transforms)
        val_ds = BinaryPlantDataset(val_ds_raw, val_ds_raw.class_to_idx.get("healthy", 0))
        names = ["healthy", "diseased"]
    else:
        names = train_ds.classes

    train_loader = DataLoader(
        train_ds, batch_size=BATCH_SIZE, shuffle=True,
        num_workers=NUM_WORKERS, pin_memory=True,
    )
    val_loader = DataLoader(
        val_ds, batch_size=BATCH_SIZE, shuffle=False,
        num_workers=NUM_WORKERS, pin_memory=True,
    )
    return train_loader, val_loader, names


def train_one_epoch(model, loader, criterion, optimizer, device):
    model.train()
    running_loss, correct, total = 0.0, 0, 0
    for images, labels in loader:
        images, labels = images.to(device), labels.to(device)
        optimizer.zero_grad()
        outputs = model(images)
        loss = criterion(outputs, labels)
        loss.backward()
        optimizer.step()

        running_loss += loss.item() * images.size(0)
        correct += (outputs.argmax(1) == labels).sum().item()
        total += images.size(0)

    return running_loss / total, correct / total


@torch.no_grad()
def evaluate(model, loader, criterion, device):
    model.eval()
    running_loss, correct, total = 0.0, 0, 0
    for images, labels in loader:
        images, labels = images.to(device), labels.to(device)
        outputs = model(images)
        loss = criterion(outputs, labels)
        running_loss += loss.item() * images.size(0)
        correct += (outputs.argmax(1) == labels).sum().item()
        total += images.size(0)

    return running_loss / total, correct / total


def _unfreeze_top_features(model: nn.Module, top_blocks: int) -> None:
    """Unfreeze the last ``top_blocks`` children of ``model.features``."""
    feature_children = list(getattr(model, "features").children())
    for block in feature_children[-top_blocks:]:
        for p in block.parameters():
            p.requires_grad = True


def train_model(
    model: nn.Module,
    train_loader: DataLoader,
    val_loader: DataLoader,
    device: torch.device,
    epochs_freeze: int,
    epochs_finetune: int,
    lr_freeze: float,
    lr_finetune: float,
    unfreeze_top_blocks: int,
    model_name: str,
    checkpoint_path: Path,
) -> nn.Module:
    """Two-phase training with early stopping and disk checkpoints."""
    criterion = nn.CrossEntropyLoss(label_smoothing=0.1)
    best_acc = 0.0
    best_state = None
    patience_counter = 0
    patience = 7

    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\nPhase 1: training {model_name} head ({epochs_freeze} epochs)")
    optimizer = optim.AdamW(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=lr_freeze, weight_decay=1e-4,
    )
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs_freeze)

    for epoch in range(1, epochs_freeze + 1):
        t0 = time.time()
        train_loss, train_acc = train_one_epoch(model, train_loader, criterion, optimizer, device)
        val_loss, val_acc = evaluate(model, val_loader, criterion, device)
        scheduler.step()
        dt = time.time() - t0

        print(
            f"  Epoch {epoch:>2}/{epochs_freeze}  "
            f"train_loss={train_loss:.4f}  train_acc={train_acc:.3f}  "
            f"val_loss={val_loss:.4f}  val_acc={val_acc:.3f}  ({dt:.1f}s)"
        )

        if val_acc > best_acc:
            best_acc = val_acc
            best_state = copy.deepcopy(model.state_dict())
            torch.save(best_state, checkpoint_path)
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= patience:
                print(f"  Early stopping at epoch {epoch}")
                break

    if best_state:
        model.load_state_dict(best_state)

    print(f"\nPhase 2: fine-tuning {model_name} ({epochs_finetune} epochs)")
    _unfreeze_top_features(model, unfreeze_top_blocks)

    optimizer = optim.AdamW(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=lr_finetune, weight_decay=1e-4,
    )
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs_finetune)
    patience_counter = 0

    for epoch in range(1, epochs_finetune + 1):
        t0 = time.time()
        train_loss, train_acc = train_one_epoch(model, train_loader, criterion, optimizer, device)
        val_loss, val_acc = evaluate(model, val_loader, criterion, device)
        scheduler.step()
        dt = time.time() - t0

        print(
            f"  Epoch {epoch:>2}/{epochs_finetune}  "
            f"train_loss={train_loss:.4f}  train_acc={train_acc:.3f}  "
            f"val_loss={val_loss:.4f}  val_acc={val_acc:.3f}  ({dt:.1f}s)"
        )

        if val_acc > best_acc:
            best_acc = val_acc
            best_state = copy.deepcopy(model.state_dict())
            torch.save(best_state, checkpoint_path)
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= patience:
                print(f"  Early stopping at epoch {epoch}")
                break

    if best_state:
        model.load_state_dict(best_state)

    print(f"\n  Best val accuracy: {best_acc:.4f} ({best_acc*100:.1f}%)")
    return model


@torch.no_grad()
def detailed_evaluation(model, loader, class_names, device):
    import numpy as np

    model.eval()
    n_classes = len(class_names)
    confusion = np.zeros((n_classes, n_classes), dtype=int)

    for images, labels in loader:
        images, labels = images.to(device), labels.to(device)
        preds = model(images).argmax(1)
        for t, p in zip(labels.cpu().numpy(), preds.cpu().numpy()):
            confusion[t][p] += 1

    total = confusion.sum()
    correct = confusion.trace()
    accuracy = correct / total if total > 0 else 0

    print("\nDetailed evaluation")
    print(f"  Overall accuracy: {accuracy:.4f} ({accuracy*100:.1f}%)")
    print(f"  {'Class':<25} {'Accuracy':>10} {'Samples':>10}")
    for i, name in enumerate(class_names):
        row_sum = confusion[i].sum()
        if row_sum > 0:
            cls_acc = confusion[i][i] / row_sum
            print(f"  {name:<25} {cls_acc:>9.1%} {row_sum:>10}")
        else:
            print(f"  {name:<25} {'N/A':>10} {0:>10}")

    errors = []
    for i in range(n_classes):
        for j in range(n_classes):
            if i != j and confusion[i][j] > 0:
                errors.append((confusion[i][j], class_names[i], class_names[j]))
    errors.sort(reverse=True)
    if errors:
        print("  Most confused pairs:")
        for count, true_name, pred_name in errors[:10]:
            print(f"    {true_name} -> {pred_name}: {count} errors")

    return accuracy


def build_detector(device: torch.device) -> nn.Module:
    net = models.mobilenet_v3_small(weights=models.MobileNet_V3_Small_Weights.DEFAULT)
    for param in net.features.parameters():
        param.requires_grad = False
    in_features = net.classifier[-1].in_features
    net.classifier[-1] = nn.Linear(in_features, 2)
    return net.to(device)


def build_classifier(device: torch.device, num_classes: int) -> nn.Module:
    net = models.efficientnet_b0(weights=models.EfficientNet_B0_Weights.DEFAULT)
    for param in net.features.parameters():
        param.requires_grad = False
    in_features = net.classifier[-1].in_features
    net.classifier[-1] = nn.Linear(in_features, num_classes)
    return net.to(device)


def train_detector(device: torch.device):
    print("\nTRAINING STAGE 1 - DISEASE DETECTOR (MobileNetV3-Small, binary)")

    train_loader, val_loader, names = create_dataloaders(binary=True)
    print(f"  Training samples:   {len(train_loader.dataset)}")
    print(f"  Validation samples: {len(val_loader.dataset)}")

    model = build_detector(device)
    total_params = sum(p.numel() for p in model.parameters())
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"  Parameters: {total_params:,}  (trainable: {trainable:,})")

    model = train_model(
        model, train_loader, val_loader, device,
        epochs_freeze=15,
        epochs_finetune=10,
        lr_freeze=1e-3,
        lr_finetune=1e-5,
        unfreeze_top_blocks=3,
        model_name="Detector",
        checkpoint_path=CHECKPOINT_DIR / "detector_best.pth",
    )

    detailed_evaluation(model, val_loader, names, device)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), str(DETECTOR_OUTPUT))
    print(f"\nDetector saved -> {DETECTOR_OUTPUT}")
    return model


def train_classifier_model(device: torch.device):
    train_loader, val_loader, names = create_dataloaders(binary=False)
    num_cls = len(names)

    print(f"\nTRAINING STAGE 2 - DISEASE CLASSIFIER (EfficientNet-B0, {num_cls} classes)")
    print(f"  Training samples:   {len(train_loader.dataset)}")
    print(f"  Validation samples: {len(val_loader.dataset)}")

    model = build_classifier(device, num_cls)
    total_params = sum(p.numel() for p in model.parameters())
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"  Parameters: {total_params:,}  (trainable: {trainable:,})")

    model = train_model(
        model, train_loader, val_loader, device,
        epochs_freeze=20,
        epochs_finetune=15,
        lr_freeze=1e-3,
        lr_finetune=5e-6,
        unfreeze_top_blocks=3,
        model_name="Classifier",
        checkpoint_path=CHECKPOINT_DIR / "classifier_best.pth",
    )

    detailed_evaluation(model, val_loader, names, device)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), str(CLASSIFIER_OUTPUT))

    meta_path = MODELS_DIR / "classes.json"
    meta = {"class_names": list(names), "num_classes": num_cls}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    print(f"\nClassifier saved -> {CLASSIFIER_OUTPUT}")
    print(f"Class mapping  -> {meta_path}")
    return model


def main():
    parser = argparse.ArgumentParser(description="Train PlantDiseases pipeline models")
    parser.add_argument("--detector", action="store_true", help="Train detector only")
    parser.add_argument("--classifier", action="store_true", help="Train classifier only")
    args = parser.parse_args()

    train_det = args.detector or not (args.detector or args.classifier)
    train_cls = args.classifier or not (args.detector or args.classifier)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    if device.type == "cuda":
        print(f"  GPU: {torch.cuda.get_device_name(0)}")
        print(f"  Memory: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")

    if train_det:
        train_detector(device)
    if train_cls:
        train_classifier_model(device)

    print("\nTRAINING COMPLETE")
    if train_det:
        print(f"  Detector   -> {DETECTOR_OUTPUT}")
    if train_cls:
        print(f"  Classifier -> {CLASSIFIER_OUTPUT}")
    print("\nRestart the server to load trained models.")


if __name__ == "__main__":
    main()
