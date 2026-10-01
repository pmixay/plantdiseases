"""Verify a notebook-produced serving bundle without loading model graphs.

This is a deployment preflight only.  It never trains, calibrates, converts,
downloads, or runs inference.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from bundle import BundleLoadError, verify_bundle  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify hashes and contracts in a PlantVision serving bundle."
    )
    parser.add_argument(
        "model_dir",
        nargs="?",
        type=Path,
        default=SERVER_DIR / "models",
        help="extracted bundle directory (default: server/models)",
    )
    args = parser.parse_args(argv)
    try:
        verified = verify_bundle(args.model_dir)
    except (BundleLoadError, OSError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    summary = {
        "bundle_id": verified.manifest["bundle_id"],
        "classifier": verified.classifier_path.name,
        "manifest_sha256": verified.manifest_sha256,
        "model_family": verified.manifest["model_family"],
        "model_version": verified.manifest["model_version"],
        "run_id": verified.manifest["run_id"],
        "serving_contract_sha256": verified.serving_contract_sha256,
        "status": "valid",
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
