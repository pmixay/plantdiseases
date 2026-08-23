"""Regression guard: fitting/exporting model state is notebook-only."""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_RUNTIME_PATTERNS = {
    "optimizer construction": re.compile(r"\btorch\.optim\b"),
    "gradient update": re.compile(r"\.backward\s*\("),
    "ONNX export": re.compile(r"\btorch\.onnx\.export\s*\("),
    "quantization export": re.compile(r"\bquantize_dynamic\s*\("),
    "conformal fitting": re.compile(r"\bcalibrate_q_hat\b"),
    "temperature fitting": re.compile(r"\bfit_temperature\b"),
    "trainer invocation": re.compile(r"\btrainer\.(?:fit|train)\s*\("),
    "model fitting": re.compile(r"\bmodel\.fit\s*\("),
    "Keras training": re.compile(r"\bcompile\s*\([^\n]*\boptimizer\s*="),
    "gradient tape": re.compile(r"\bGradientTape\s*\("),
    "checkpoint export": re.compile(r"\b(?:save_model|save_weights)\s*\("),
}

FORBIDDEN_RUNTIME_IMPORTS = {
    "accelerate",
    "keras",
    "lightning",
    "onnxruntime.quantization",
    "peft",
    "pytorch_lightning",
    "tensorflow",
    "timm",
    "torch",
    "transformers",
    "ultralytics",
}

FORBIDDEN_RUNTIME_CALLS = {
    "onnxruntime.quantization.quantize_dynamic",
    "onnxruntime.quantization.quantize_static",
    "tensorflow.lite.TFLiteConverter.from_keras_model",
    "torch.onnx.export",
}


def _qualified_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def test_server_and_app_sources_cannot_train_or_export_weights():
    """Inference/data-curation files must never grow a hidden training path."""
    sources = [
        *(
            path for path in (REPO_ROOT / "server").rglob("*.py")
            if "tests" not in path.parts
        ),
        *(REPO_ROOT / "android" / "app" / "src" / "main").rglob("*.kt"),
    ]
    violations = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for label, pattern in FORBIDDEN_RUNTIME_PATTERNS.items():
            if pattern.search(text):
                violations.append(f"{path.relative_to(REPO_ROOT)}: {label}")
        if path.suffix != ".py":
            continue
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                modules = []
            for module in modules:
                if any(
                    module == banned or module.startswith(f"{banned}.")
                    for banned in FORBIDDEN_RUNTIME_IMPORTS
                ):
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: "
                        f"training-framework import {module}"
                    )
            if isinstance(node, ast.Call):
                name = _qualified_name(node.func)
                if name in FORBIDDEN_RUNTIME_CALLS:
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: "
                        f"weight/export call {name}"
                    )

    requirements = (REPO_ROOT / "server" / "requirements.txt").read_text(
        encoding="utf-8"
    ).lower()
    for package in ("torch", "tensorflow", "keras", "ultralytics", "peft"):
        if re.search(rf"(?m)^\s*{re.escape(package)}(?:\[|\s|=|<|>|$)", requirements):
            violations.append(f"server/requirements.txt: training dependency {package}")
    assert not violations, (
        "Training/calibration fitting and weight export belong only in "
        f"server/train_notebook.ipynb; found {violations}"
    )
