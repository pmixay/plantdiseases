"""Fail-closed verification for immutable PlantVision serving bundles.

Training/export belongs exclusively to ``train_notebook.ipynb``.  This module
does no conversion, calibration, download, or repair: it verifies the notebook's
``bundle_manifest.json`` and returns the exact graph paths selected there.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_KNOWN_ARTIFACT_ROLES = {
    "aqua_classes",
    "aqua_classifier",
    "aqua_onnx",
    "classifier",
    "classes",
    "conformal",
    "data_manifest",
    "detector",
    "ood_embedding",
    "run_manifest",
    "temperature",
}
_TOP_LEVEL_FIELDS = {
    "artifacts",
    "bundle_id",
    "bundle_id_algorithm",
    "classifier_selection",
    "data_manifest",
    "manifest_file",
    "model_family",
    "model_version",
    "run_id",
    "schema_version",
    "serving_contract",
    "train_mode",
}


class BundleLoadError(RuntimeError):
    """A serving bundle is missing, mixed, corrupt, or contract-incompatible."""


@dataclass(frozen=True)
class VerifiedBundle:
    root: Path
    manifest: dict
    classifier_path: Path
    detector_path: Path
    manifest_sha256: str
    serving_contract_sha256: str


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_json(path: Path) -> dict:
    def _reject_constant(value: str):
        raise ValueError(f"non-standard JSON constant {value}")

    try:
        parsed = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise BundleLoadError(f"Invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise BundleLoadError(f"JSON artifact {path} must contain an object")
    return parsed


def _safe_artifact_path(root: Path, raw: object, *, role: str) -> Path:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise BundleLoadError(f"Manifest artifact {role!r} has an invalid path {raw!r}")
    posix = PurePosixPath(raw)
    if posix.is_absolute() or any(part in ("", ".", "..") for part in posix.parts):
        raise BundleLoadError(f"Manifest artifact {role!r} has an unsafe path {raw!r}")
    candidate = root.joinpath(*posix.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise BundleLoadError(
            f"Manifest artifact {role!r} is missing: {candidate}"
        ) from exc
    if resolved == root or root not in resolved.parents or not resolved.is_file():
        raise BundleLoadError(
            f"Manifest artifact {role!r} escapes the bundle or is not a file: {candidate}"
        )
    return resolved


def _verify_artifact(root: Path, role: str, record: object) -> Path | None:
    if record is None:
        return None
    if not isinstance(record, dict):
        raise BundleLoadError(f"Manifest artifact {role!r} must be an object or null")
    if set(record) != {"path", "sha256", "size_bytes"}:
        raise BundleLoadError(
            f"Manifest artifact {role!r} must contain only path/sha256/size_bytes"
        )
    digest = record.get("sha256")
    size = record.get("size_bytes")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise BundleLoadError(f"Manifest artifact {role!r} has an invalid SHA-256")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise BundleLoadError(f"Manifest artifact {role!r} has an invalid byte size")
    path = _safe_artifact_path(root, record.get("path"), role=role)
    actual_size = path.stat().st_size
    if actual_size != size:
        raise BundleLoadError(
            f"Manifest artifact {role!r} size mismatch: {actual_size} != {size}"
        )
    actual_digest = _sha256_file(path)
    if actual_digest != digest:
        raise BundleLoadError(
            f"Manifest artifact {role!r} SHA-256 mismatch: {actual_digest} != {digest}"
        )
    return path


def _validate_classes_contract(classes: dict, *, version: str, family: str, run_id: str) -> None:
    """Reject metadata that would make the classifier use runtime defaults."""
    if classes.get("model_version") != version:
        raise BundleLoadError("Manifest model_version does not match classes.json")
    if classes.get("model_family") != family:
        raise BundleLoadError("Manifest model_family does not match classes.json")
    if classes.get("run_id") != run_id:
        raise BundleLoadError("Manifest run_id does not match classes.json")

    names = classes.get("class_names")
    if (
        not isinstance(names, list)
        or not names
        or any(
            not isinstance(name, str) or not name or name != name.strip()
            for name in names
        )
        or len(set(names)) != len(names)
    ):
        raise BundleLoadError("classes.json -> class_names must be unique trimmed strings")
    architecture = classes.get("architecture")
    if not isinstance(architecture, str) or not architecture.strip():
        raise BundleLoadError("classes.json -> architecture must be non-empty")
    if classes.get("detector_classes") != ["plant_leaf"]:
        raise BundleLoadError("classes.json -> detector_classes must equal ['plant_leaf']")

    training = classes.get("training")
    if not isinstance(training, dict):
        raise BundleLoadError("classes.json -> training must be an object")
    img_size = training.get("img_size")
    ratio = training.get("eval_resize_ratio")
    if isinstance(img_size, bool) or not isinstance(img_size, int) or not 1 <= img_size <= 4096:
        raise BundleLoadError("classes.json -> training.img_size is invalid")
    if (
        isinstance(ratio, bool)
        or not isinstance(ratio, (int, float))
        or not math.isfinite(float(ratio))
        or not 1.0 <= float(ratio) <= 4.0
    ):
        raise BundleLoadError("classes.json -> training.eval_resize_ratio is invalid")
    if training.get("architecture") != architecture:
        raise BundleLoadError("classes.json architecture fields disagree")

    inference = classes.get("inference")
    if not isinstance(inference, dict):
        raise BundleLoadError("classes.json -> inference must be an object")
    if inference.get("layout") != "NCHW":
        raise BundleLoadError("classes.json -> inference.layout must equal NCHW")
    if inference.get("tta") not in {"none", "hflip"}:
        raise BundleLoadError("classes.json -> inference.tta is unsupported")
    if inference.get("output_semantics") != "logits":
        raise BundleLoadError("classes.json -> inference.output_semantics must equal logits")
    for key, positive in (("normalize_mean", False), ("normalize_std", True)):
        values = inference.get(key)
        if (
            not isinstance(values, list)
            or len(values) != 3
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or (positive and float(value) <= 0)
                for value in values
            )
        ):
            raise BundleLoadError(f"classes.json -> inference.{key} is invalid")

    ood = classes.get("ood")
    if not isinstance(ood, dict):
        raise BundleLoadError("classes.json -> ood must be an object")
    required_ood = {
        "embedding", "max_logit_floor", "max_logit_scope", "nap_head_mass_floor",
    }
    if not required_ood.issubset(ood):
        raise BundleLoadError(
            f"classes.json -> ood is missing {sorted(required_ood - set(ood))}"
        )
    if ood.get("max_logit_scope") not in {"all_classes", "plant_only"}:
        raise BundleLoadError("classes.json -> ood.max_logit_scope is unsupported")
    for key, upper in (("max_logit_floor", None), ("nap_head_mass_floor", 1.0)):
        value = ood.get(key)
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or (upper is not None and not 0.0 <= float(value) <= upper)
        ):
            raise BundleLoadError(f"classes.json -> ood.{key} is invalid")


_SERVING_CONTRACT_SECTIONS = {
    "detector": (
        "path", "class_names", "img_size", "min_confidence", "nms_iou", "max_detections",
    ),
    "crop": (
        "max_crops", "min_size_px", "min_area_fraction", "padding_ratio",
        "fold_whole_frame_vote", "member_selection", "vote_aggregation",
        "whole_frame_vote_weight",
    ),
    "classifier": (
        "path", "img_size", "eval_resize_ratio", "resize", "crop",
        "normalize_mean", "normalize_std", "tta", "tta_aggregation",
        "output_semantics",
    ),
    "blur": (
        "enabled", "policy", "variance_laplacian_floor", "downscale_max_side",
    ),
    "ood": (
        "policy", "detector_confidence_floor", "classifier_signal_priority",
        "embedding_aggregation", "max_logit_aggregation", "nap_head_veto",
        "served_gate_enabled", "served_gate", "config_source",
    ),
}


def _validate_serving_contract_schema(contract: object) -> None:
    """Structural serving-contract check that does not load graphs.

    Live numeric levers (detector threshold, crop padding, TTA, ...) are
    bound later by the pipeline against the actual runtime. This still
    rejects a stub object that cannot possibly start the API.
    """
    if not isinstance(contract, dict) or contract.get("schema_version") != 1:
        raise BundleLoadError("bundle_manifest.json -> serving_contract.schema_version must equal 1")
    missing_sections = sorted(set(_SERVING_CONTRACT_SECTIONS) - set(contract))
    if missing_sections:
        raise BundleLoadError(
            f"serving_contract is missing sections {missing_sections}"
        )
    for section, keys in _SERVING_CONTRACT_SECTIONS.items():
        body = contract.get(section)
        if not isinstance(body, dict):
            raise BundleLoadError(f"serving_contract.{section} must be an object")
        missing = [key for key in keys if key not in body]
        if missing:
            raise BundleLoadError(
                f"serving_contract.{section} is missing {missing}"
            )
    detector = contract["detector"]
    if detector.get("path") != "detector.onnx":
        raise BundleLoadError("serving_contract.detector.path must equal detector.onnx")
    if detector.get("class_names") != ["plant_leaf"]:
        raise BundleLoadError("serving_contract.detector.class_names must equal ['plant_leaf']")
    crop = contract["crop"]
    if crop.get("member_selection") != "detector_confidence_descending":
        raise BundleLoadError("serving_contract.crop.member_selection is unsupported")
    if crop.get("vote_aggregation") != "equal_mean_probabilities":
        raise BundleLoadError("serving_contract.crop.vote_aggregation is unsupported")
    classifier = contract["classifier"]
    if classifier.get("resize") != "shorter_side" or classifier.get("crop") != "center":
        raise BundleLoadError("serving_contract.classifier preprocess is unsupported")
    if classifier.get("output_semantics") != "logits":
        raise BundleLoadError("serving_contract.classifier.output_semantics must equal logits")
    if classifier.get("tta") not in {"none", "hflip"}:
        raise BundleLoadError("serving_contract.classifier.tta is unsupported")
    blur = contract["blur"]
    if blur.get("enabled") is not True or blur.get("policy") != "abstain":
        raise BundleLoadError("serving_contract.blur must abstain when enabled")
    ood = contract["ood"]
    if ood.get("policy") != "detector_and_classifier_signal_with_nap_head_veto":
        raise BundleLoadError("serving_contract.ood.policy is unsupported")
    if ood.get("classifier_signal_priority") != "embedding_knn_else_max_logit":
        raise BundleLoadError("serving_contract.ood.classifier_signal_priority is unsupported")
    if ood.get("config_source") != "classes.json#ood":
        raise BundleLoadError("serving_contract.ood.config_source is unsupported")
    if not isinstance(ood.get("served_gate_enabled"), bool):
        raise BundleLoadError("serving_contract.ood.served_gate_enabled must be a boolean")
    if ood.get("served_gate") is not None and not isinstance(ood.get("served_gate"), dict):
        raise BundleLoadError("serving_contract.ood.served_gate must be an object or null")


def verify_bundle(model_dir: Path) -> VerifiedBundle:
    """Verify one notebook-produced serving bundle and resolve exact graphs.

    Recognised sidecars and classifier siblings may not sit outside the
    manifest.  This prevents a stale file from a reused Colab/server directory
    from changing behavior merely because a filename happens to exist.
    """
    root = Path(model_dir).resolve(strict=True)
    if not root.is_dir():
        raise BundleLoadError(f"Model bundle root is not a directory: {root}")
    manifest_path = root / "bundle_manifest.json"
    if not manifest_path.is_file():
        raise BundleLoadError(
            f"Required bundle manifest is missing: {manifest_path}. Legacy unbound "
            "artifacts are diagnostic-only and cannot start the API."
        )
    manifest = _strict_json(manifest_path)
    if set(manifest) != _TOP_LEVEL_FIELDS:
        raise BundleLoadError(
            "bundle_manifest.json has missing/unknown top-level fields: "
            f"{sorted(set(manifest) ^ _TOP_LEVEL_FIELDS)}"
        )
    if manifest.get("schema_version") != 1:
        raise BundleLoadError("bundle_manifest.json -> schema_version must equal 1")
    if manifest.get("manifest_file") != "bundle_manifest.json":
        raise BundleLoadError("bundle_manifest.json -> manifest_file must name itself")
    if manifest.get("bundle_id_algorithm") != "sha256_canonical_json_without_bundle_id":
        raise BundleLoadError("Unsupported bundle_id_algorithm")
    bundle_id = manifest.get("bundle_id")
    if not isinstance(bundle_id, str) or not _SHA256_RE.fullmatch(bundle_id):
        raise BundleLoadError("bundle_manifest.json -> bundle_id must be a SHA-256")
    identity = dict(manifest)
    identity.pop("bundle_id")
    actual_bundle_id = hashlib.sha256(_canonical_json(identity)).hexdigest()
    if actual_bundle_id != bundle_id:
        raise BundleLoadError(
            f"bundle_manifest.json identity mismatch: {actual_bundle_id} != {bundle_id}"
        )
    version = manifest.get("model_version")
    if not isinstance(version, str) or not version.strip():
        raise BundleLoadError("bundle_manifest.json -> model_version must be non-empty")
    run_id = manifest.get("run_id")
    train_mode = manifest.get("train_mode")
    model_family = manifest.get("model_family")
    if not isinstance(run_id, str) or not run_id.strip():
        raise BundleLoadError("bundle_manifest.json -> run_id must be non-empty")
    if train_mode not in {"gamma", "frontier", "combined"}:
        raise BundleLoadError("bundle_manifest.json -> train_mode is unsupported")
    if model_family not in {"gamma", "frontier"}:
        raise BundleLoadError("Server bundle model_family must be gamma or frontier")

    selection = manifest.get("classifier_selection")
    if not isinstance(selection, dict):
        raise BundleLoadError("bundle_manifest.json -> classifier_selection must be an object")
    if selection.get("fallback_allowed") is not False:
        raise BundleLoadError("Serving manifests must declare fallback_allowed=false")
    selected_path = selection.get("path")
    fit_source = selection.get("calibration_source")
    if not isinstance(fit_source, str) or not fit_source:
        raise BundleLoadError("classifier_selection.calibration_source must be non-empty")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise BundleLoadError("bundle_manifest.json -> artifacts must be an object")
    unknown = set(artifacts) - _KNOWN_ARTIFACT_ROLES
    if unknown:
        raise BundleLoadError(f"Unknown serving artifact roles: {sorted(unknown)}")
    for required in (
        "classifier",
        "detector",
        "classes",
        "temperature",
        "conformal",
        "data_manifest",
        "run_manifest",
    ):
        if required not in artifacts:
            raise BundleLoadError(f"Manifest does not explicitly declare artifact {required!r}")

    resolved = {
        role: _verify_artifact(root, role, record)
        for role, record in artifacts.items()
    }
    for required in (
        "classifier",
        "detector",
        "classes",
        "temperature",
        "conformal",
        "data_manifest",
        "run_manifest",
    ):
        if resolved.get(required) is None:
            raise BundleLoadError(f"Required serving artifact {required!r} is null")
    classifier = resolved["classifier"]
    detector = resolved["detector"]
    classes_path = resolved["classes"]
    assert classifier is not None and detector is not None and classes_path is not None
    if artifacts["classifier"].get("path") != selected_path:
        raise BundleLoadError("classifier_selection.path does not match artifacts.classifier.path")
    classifier_rel = artifacts["classifier"].get("path")
    detector_rel = artifacts["detector"].get("path")
    classes_rel = artifacts["classes"].get("path")
    if classifier_rel not in {"classifier.onnx", "classifier_int8.onnx"}:
        raise BundleLoadError("Selected classifier must be classifier.onnx or classifier_int8.onnx")
    if detector_rel != "detector.onnx" or classes_rel != "classes.json":
        raise BundleLoadError("Detector/classes artifacts must be detector.onnx/classes.json")
    for role, expected_name in (
        ("temperature", "temperature.json"),
        ("conformal", "conformal.json"),
    ):
        record = artifacts[role]
        if record is not None and record.get("path") != expected_name:
            raise BundleLoadError(f"Artifact {role!r} must be named {expected_name}")

    classes = _strict_json(classes_path)
    _validate_classes_contract(
        classes,
        version=version,
        family=model_family,
        run_id=run_id,
    )
    temperature = resolved.get("temperature")
    if temperature is not None and _strict_json(temperature).get("source") != fit_source:
        raise BundleLoadError("temperature.json was not fit for the selected classifier graph")
    conformal = resolved.get("conformal")
    if conformal is not None and _strict_json(conformal).get("fit_source") != fit_source:
        raise BundleLoadError("conformal.json was not fit for the selected classifier graph")

    emb = classes.get("ood", {}).get("embedding") if isinstance(classes.get("ood"), dict) else None
    if isinstance(emb, dict) and "bank_file" not in emb:
        # ood.py would default this to ``ood_embedding.npz`` at boot; a bound
        # bundle names its bank explicitly so preflight and boot judge the
        # same file (a bundle that verified here but failed at boot is the
        # worst outcome a preflight can have).
        raise BundleLoadError("classes.json ood.embedding must declare bank_file explicitly")
    declared_bank = emb.get("bank_file") if isinstance(emb, dict) else None
    ood_record = artifacts.get("ood_embedding")
    if declared_bank is None and ood_record is not None:
        raise BundleLoadError("Manifest lists an OOD bank that classes.json does not declare")
    if declared_bank is not None:
        if not isinstance(ood_record, dict) or ood_record.get("path") != declared_bank:
            raise BundleLoadError("classes.json OOD bank is not exactly bound by the manifest")

    lineage = manifest.get("data_manifest")
    if not isinstance(lineage, dict) or set(lineage) != {
        "path", "manifest_sha256", "dataset_sha256", "n_images",
    }:
        raise BundleLoadError("bundle_manifest.json -> data_manifest has an invalid schema")
    data_record = artifacts["data_manifest"]
    if (
        lineage.get("path") != "data_manifest.json"
        or not isinstance(data_record, dict)
        or data_record.get("path") != "data_manifest.json"
        or lineage.get("manifest_sha256") != data_record.get("sha256")
    ):
        raise BundleLoadError("Data-manifest identity is not bound to its artifact record")
    dataset_sha = lineage.get("dataset_sha256")
    n_images = lineage.get("n_images")
    if not isinstance(dataset_sha, str) or not _SHA256_RE.fullmatch(dataset_sha):
        raise BundleLoadError("data_manifest.dataset_sha256 must be a SHA-256")
    if isinstance(n_images, bool) or not isinstance(n_images, int) or n_images <= 0:
        raise BundleLoadError("data_manifest.n_images must be a positive integer")
    data_payload = _strict_json(resolved["data_manifest"])
    if (
        data_payload.get("classification_sha256") != dataset_sha
        or data_payload.get("n_images") != n_images
    ):
        raise BundleLoadError("Bound data_manifest.json does not match lineage summary")
    if classes.get("data_manifest_sha256") != dataset_sha:
        raise BundleLoadError("classes.json data identity does not match bundle lineage")

    run_record = artifacts["run_manifest"]
    if not isinstance(run_record, dict) or run_record.get("path") != "run_manifest.json":
        raise BundleLoadError("run_manifest artifact must be named run_manifest.json")
    run_payload = _strict_json(resolved["run_manifest"])
    for key, wanted in (
        ("run_id", run_id),
        ("model_version", version),
        ("train_mode", train_mode),
        ("model_family", model_family),
        ("data_identity", dataset_sha),
    ):
        if run_payload.get(key) != wanted:
            raise BundleLoadError(f"run_manifest.json -> {key} does not match bundle identity")

    aqua_roles = {"aqua_classifier", "aqua_classes", "aqua_onnx"}
    present_aqua = aqua_roles & set(artifacts)
    if present_aqua and present_aqua != aqua_roles:
        raise BundleLoadError("Aqua bundle profile must bind classifier, classes and ONNX together")
    if train_mode == "combined" and present_aqua != aqua_roles:
        raise BundleLoadError("Combined bundle is missing its current-run Aqua profile")
    for role, expected_path in (
        ("aqua_classifier", "aqua/aqua_classifier.tflite"),
        ("aqua_classes", "aqua/classes.json"),
        ("aqua_onnx", "aqua/aqua_student.onnx"),
    ):
        if role in artifacts:
            record = artifacts[role]
            if not isinstance(record, dict) or record.get("path") != expected_path:
                raise BundleLoadError(f"Manifest artifact {role!r} must be {expected_path}")

    # A recognised optional file cannot influence the filename-based runtime
    # unless it is the exact file declared above.
    bound_paths = {path for path in resolved.values() if path is not None}
    for name in (
        "classifier.onnx",
        "classifier_int8.onnx",
        "temperature.json",
        "conformal.json",
        "ood_embedding.npz",
    ):
        candidate = root / name
        if candidate.is_file() and candidate.resolve() not in bound_paths:
            raise BundleLoadError(f"Recognised serving artifact is present but unbound: {candidate}")

    contract = manifest.get("serving_contract")
    _validate_serving_contract_schema(contract)
    return VerifiedBundle(
        root=root,
        manifest=manifest,
        classifier_path=classifier,
        detector_path=detector,
        manifest_sha256=_sha256_file(manifest_path),
        serving_contract_sha256=hashlib.sha256(_canonical_json(contract)).hexdigest(),
    )
