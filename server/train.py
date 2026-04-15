"""
Training script for the PlantDiseases CNN model.

Uses transfer learning with MobileNetV2 pre-trained on ImageNet,
fine-tuned on the PlantVillage dataset for houseplant disease classification.

SETUP:
  1. Download PlantVillage dataset:
     https://github.com/spMohanty/PlantVillage-Dataset
     or from Kaggle: https://www.kaggle.com/datasets/emmarex/plantdisease

  2. Organize images into class folders under data/train/ and data/val/
     Example:
       data/
         train/
           healthy/
           bacterial_spot/
           early_blight/
           ...
         val/
           healthy/
           bacterial_spot/
           ...

  3. Run:  python train.py

  4. Model will be saved to models/plant_disease_model.h5

NOTE: The PlantVillage dataset primarily covers crop plants (tomato, potato,
pepper, etc.). For best results on houseplants, supplement with additional
houseplant images or use the model as a starting point and fine-tune further.
"""

import os
import sys
from pathlib import Path

import numpy as np
import tensorflow as tf
from tensorflow.keras import layers, models, optimizers, callbacks
from tensorflow.keras.applications import MobileNetV2
from tensorflow.keras.preprocessing.image import ImageDataGenerator

# ── Configuration ─────────────────────────────────────────────────────

IMG_SIZE = 224
BATCH_SIZE = 32
EPOCHS_TRANSFER = 15     # Frozen base training
EPOCHS_FINETUNE = 10     # Fine-tuning top layers
LEARNING_RATE = 1e-3
FINETUNE_LR = 1e-5
DATA_DIR = Path("data")
MODEL_OUTPUT = Path("models/plant_disease_model.h5")

CLASS_NAMES = [
    "healthy",
    "bacterial_spot",
    "early_blight",
    "late_blight",
    "leaf_mold",
    "septoria_leaf_spot",
    "spider_mites",
    "target_spot",
    "mosaic_virus",
    "yellow_leaf_curl",
    "powdery_mildew",
    "rust",
    "root_rot",
    "anthracnose",
    "botrytis",
]

NUM_CLASSES = len(CLASS_NAMES)


def create_data_generators():
    """Create train and validation data generators with augmentation."""
    train_datagen = ImageDataGenerator(
        rescale=1.0 / 255,
        rotation_range=30,
        width_shift_range=0.2,
        height_shift_range=0.2,
        shear_range=0.15,
        zoom_range=0.2,
        horizontal_flip=True,
        vertical_flip=False,
        brightness_range=[0.8, 1.2],
        fill_mode="nearest",
    )

    val_datagen = ImageDataGenerator(rescale=1.0 / 255)

    train_dir = DATA_DIR / "train"
    val_dir = DATA_DIR / "val"

    if not train_dir.exists():
        print(f"ERROR: Training data directory not found: {train_dir}")
        print("Please download PlantVillage dataset and organize into data/train/ folders.")
        print()
        print("Quick setup:")
        print("  1. Download from: https://www.kaggle.com/datasets/emmarex/plantdisease")
        print("  2. Extract and organize into data/train/ and data/val/ with class folders")
        print("  3. Each class folder should contain at least 100 images")
        sys.exit(1)

    train_gen = train_datagen.flow_from_directory(
        train_dir,
        target_size=(IMG_SIZE, IMG_SIZE),
        batch_size=BATCH_SIZE,
        class_mode="categorical",
        classes=CLASS_NAMES,
        shuffle=True,
    )

    val_gen = val_datagen.flow_from_directory(
        val_dir,
        target_size=(IMG_SIZE, IMG_SIZE),
        batch_size=BATCH_SIZE,
        class_mode="categorical",
        classes=CLASS_NAMES,
        shuffle=False,
    )

    return train_gen, val_gen


def build_model() -> tf.keras.Model:
    """Build transfer learning model based on MobileNetV2."""
    base_model = MobileNetV2(
        input_shape=(IMG_SIZE, IMG_SIZE, 3),
        include_top=False,
        weights="imagenet",
    )
    base_model.trainable = False  # Freeze during initial training

    model = models.Sequential([
        base_model,
        layers.GlobalAveragePooling2D(),
        layers.BatchNormalization(),
        layers.Dropout(0.3),
        layers.Dense(256, activation="relu"),
        layers.BatchNormalization(),
        layers.Dropout(0.3),
        layers.Dense(NUM_CLASSES, activation="softmax"),
    ])

    return model, base_model


def evaluate_model(model, val_gen):
    """Evaluate model with per-class metrics and confusion matrix."""
    print("\n" + "=" * 60)
    print("DETAILED EVALUATION")
    print("=" * 60)

    # Get predictions for full validation set
    val_gen.reset()
    predictions = model.predict(val_gen, verbose=1)
    y_pred = np.argmax(predictions, axis=1)
    y_true = val_gen.classes

    # Overall accuracy
    accuracy = np.mean(y_pred == y_true)
    print(f"\nOverall Accuracy: {accuracy:.4f} ({accuracy*100:.1f}%)")

    # Per-class accuracy
    print(f"\n{'Class':<25} {'Accuracy':>10} {'Samples':>10}")
    print("-" * 50)
    for i, class_name in enumerate(CLASS_NAMES):
        mask = y_true == i
        if mask.sum() > 0:
            class_acc = np.mean(y_pred[mask] == i)
            print(f"{class_name:<25} {class_acc:>9.1%} {mask.sum():>10}")
        else:
            print(f"{class_name:<25} {'N/A':>10} {0:>10}")

    # Confusion matrix (text format)
    print(f"\nConfusion Matrix (rows=true, cols=predicted):")
    print(f"{'':>5}", end="")
    for i in range(NUM_CLASSES):
        print(f"{i:>5}", end="")
    print()
    for i in range(NUM_CLASSES):
        print(f"{i:>5}", end="")
        for j in range(NUM_CLASSES):
            count = np.sum((y_true == i) & (y_pred == j))
            print(f"{count:>5}", end="")
        print(f"  <- {CLASS_NAMES[i]}")

    # Top confused classes
    print("\nMost confused pairs:")
    confusion = np.zeros((NUM_CLASSES, NUM_CLASSES), dtype=int)
    for t, p in zip(y_true, y_pred):
        confusion[t][p] += 1

    errors = []
    for i in range(NUM_CLASSES):
        for j in range(NUM_CLASSES):
            if i != j and confusion[i][j] > 0:
                errors.append((confusion[i][j], CLASS_NAMES[i], CLASS_NAMES[j]))
    errors.sort(reverse=True)
    for count, true_name, pred_name in errors[:10]:
        print(f"  {true_name} -> {pred_name}: {count} errors")

    return accuracy


def train():
    """Full training pipeline."""
    print("=" * 60)
    print("PlantDiseases CNN Training")
    print(f"Model: MobileNetV2 + custom head ({NUM_CLASSES} classes)")
    print(f"Image size: {IMG_SIZE}x{IMG_SIZE}")
    print("=" * 60)

    # Create output dir
    MODEL_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    # Data
    print("\nLoading data...")
    train_gen, val_gen = create_data_generators()
    print(f"  Training samples:   {train_gen.samples}")
    print(f"  Validation samples: {val_gen.samples}")
    print(f"  Classes:            {NUM_CLASSES}")
    print(f"  Batch size:         {BATCH_SIZE}")

    # Model
    print("\nBuilding model (MobileNetV2 + custom head)...")
    model, base_model = build_model()
    model.summary()

    # ── Phase 1: Transfer learning (frozen base) ──────────────────
    print(f"\n{'='*60}")
    print(f"Phase 1: Training classifier head ({EPOCHS_TRANSFER} epochs)")
    print(f"{'='*60}")
    model.compile(
        optimizer=optimizers.Adam(learning_rate=LEARNING_RATE),
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )

    early_stop = callbacks.EarlyStopping(
        monitor="val_accuracy", patience=5, restore_best_weights=True
    )

    reduce_lr = callbacks.ReduceLROnPlateau(
        monitor="val_loss", factor=0.5, patience=3, min_lr=1e-6
    )

    history1 = model.fit(
        train_gen,
        epochs=EPOCHS_TRANSFER,
        validation_data=val_gen,
        callbacks=[early_stop, reduce_lr],
    )

    # ── Phase 2: Fine-tuning top layers ───────────────────────────
    print(f"\n{'='*60}")
    print(f"Phase 2: Fine-tuning top 30 layers ({EPOCHS_FINETUNE} epochs)")
    print(f"{'='*60}")

    # Unfreeze top 30 layers of base model
    base_model.trainable = True
    for layer in base_model.layers[:-30]:
        layer.trainable = False

    model.compile(
        optimizer=optimizers.Adam(learning_rate=FINETUNE_LR),
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )

    history2 = model.fit(
        train_gen,
        epochs=EPOCHS_FINETUNE,
        validation_data=val_gen,
        callbacks=[early_stop, reduce_lr],
    )

    # ── Save ──────────────────────────────────────────────────────
    model.save(str(MODEL_OUTPUT))
    print(f"\nModel saved to {MODEL_OUTPUT}")

    # ── Detailed evaluation ───────────────────────────────────────
    final_accuracy = evaluate_model(model, val_gen)

    # Final summary
    print(f"\n{'='*60}")
    print("TRAINING COMPLETE")
    print(f"{'='*60}")
    print(f"  Model saved:    {MODEL_OUTPUT}")
    print(f"  Final accuracy: {final_accuracy:.1%}")
    print(f"  Classes:        {NUM_CLASSES}")
    print(f"\nRestart the server to load the trained model:")
    print(f"  uvicorn main:app --host 0.0.0.0 --port 8000")


if __name__ == "__main__":
    # Set GPU memory growth
    gpus = tf.config.experimental.list_physical_devices("GPU")
    for gpu in gpus:
        tf.config.experimental.set_memory_growth(gpu, True)

    train()
