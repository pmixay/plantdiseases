"""
Model bundle fetcher for the always-online inference server.

The trained weights + calibration artefacts are **not** committed to the
repository. On startup the server ensures the model bundle is present
under the models directory, downloading and extracting it from a
configured URL (``MODEL_BUNDLE_URL``) whenever a required artefact is
missing. This keeps large, frequently-regenerated binaries out of git
while letting a fresh deploy self-provision from a single URL.

Bundle format
-------------
A ``.tar`` / ``.tar.gz`` / ``.tgz`` / ``.zip`` archive of the ``models/``
directory contents::

    detector.{pt,onnx}
    classifier.{onnx,_int8.onnx,pth}
    classes.json  temperature.json  conformal.json
    model_card.json  training_metrics.json  ...

Members may sit at the archive root or under a single top-level
directory (e.g. ``models/``); both layouts are flattened into the
target directory.

Sources
-------
* Any direct-download URL (S3 / GCS / your own host) — streamed with
  exponential backoff.
* A Google Drive share/file URL — the large-file "confirm token"
  interstitial is handled transparently.

Contract
--------
* **Idempotent** — the download is skipped when the required artefacts
  already exist on disk (a restart of the always-online server does not
  re-download).
* **Fail-loud** — if a fetch leaves the required artefacts missing it
  raises :class:`ModelFetchError`, so the fail-fast pipeline boot still
  aborts. The product contract is "never serve a half-provisioned
  model".
"""

from __future__ import annotations

import logging
import re
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# Required artefact groups — the server can boot iff at least one file
# from every group is present. ``temperature.json`` / ``conformal.json``
# are intentionally NOT required: the pipeline degrades gracefully (T=1.0,
# no prediction set) when they are absent.
REQUIRED_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("classifier", ("classifier_int8.onnx", "classifier.onnx", "classifier.pth")),
    ("detector", ("detector.onnx", "detector.pt")),
    ("metadata", ("classes.json",)),
)

_RETRY_STATUS = (429, 500, 502, 503, 504)
_CHUNK = 1 << 20  # 1 MiB
_PROGRESS_EVERY = 50 << 20  # log every 50 MiB


class ModelFetchError(RuntimeError):
    """Raised when the model bundle cannot be fetched / is incomplete."""


# ── presence check ───────────────────────────────────────────────────


def missing_groups(models_dir: Path) -> list[str]:
    """Return the names of required groups with no artefact on disk."""
    return [
        name
        for name, files in REQUIRED_GROUPS
        if not any((models_dir / f).exists() for f in files)
    ]


def models_present(models_dir: Path) -> bool:
    return not missing_groups(models_dir)


# ── Google Drive helpers ─────────────────────────────────────────────


def is_google_drive(url: str) -> bool:
    return "drive.google.com" in url or "drive.usercontent.google.com" in url


def google_drive_id(url: str) -> str | None:
    """Extract the file id from any Google Drive share/file/uc URL."""
    for pat in (r"/file/d/([A-Za-z0-9_-]+)", r"[?&]id=([A-Za-z0-9_-]+)"):
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def _drive_confirm_params(resp: requests.Response, session: requests.Session) -> dict | None:
    """Parse the Drive large-file interstitial → the confirmed download
    params, or ``None`` when the response is already the file itself.

    Drive serves files above ~100 MB behind an HTML "can't scan for
    viruses" page whose form carries ``confirm`` + ``uuid`` hidden
    inputs (older variants set a ``download_warning`` cookie instead).
    """
    ctype = resp.headers.get("Content-Type", "")
    if "text/html" not in ctype:
        return None  # already the binary
    head = resp.raw.read(1 << 16, decode_content=True) or b""
    token = next(
        (v for k, v in session.cookies.items() if k.startswith("download_warning")),
        None,
    )
    if not token:
        m = re.search(rb'name="confirm"\s+value="([^"]+)"', head) or re.search(
            rb"confirm=([0-9A-Za-z_-]+)", head
        )
        token = m.group(1).decode() if m else "t"
    uuid_m = re.search(rb'name="uuid"\s+value="([^"]+)"', head)
    params = {"export": "download", "confirm": token}
    if uuid_m:
        params["uuid"] = uuid_m.group(1).decode()
    return params


def _download_google_drive(session: requests.Session, file_id: str, dest: Path, timeout: int) -> None:
    base = "https://drive.google.com/uc"
    with session.get(base, params={"export": "download", "id": file_id}, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        confirm = _drive_confirm_params(r, session)
        if confirm is None:
            _stream_to_file(r, dest)
            return
    confirm["id"] = file_id
    with session.get(
        "https://drive.usercontent.google.com/download", params=confirm, stream=True, timeout=timeout
    ) as r2:
        r2.raise_for_status()
        if "text/html" in r2.headers.get("Content-Type", ""):
            raise ModelFetchError(
                "Google Drive returned an HTML page instead of the file — the "
                "share link may require authentication or has hit its download "
                "quota. Host the bundle on a direct-download URL (S3/GCS/your "
                "server) and set MODEL_BUNDLE_URL to it."
            )
        _stream_to_file(r2, dest)


# ── generic streaming download ───────────────────────────────────────


def _stream_to_file(resp: requests.Response, dest: Path) -> None:
    total = int(resp.headers.get("Content-Length", 0) or 0)
    written = next_log = 0
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(_CHUNK):
            if not chunk:
                continue
            f.write(chunk)
            written += len(chunk)
            if written >= next_log:
                pct = f" ({written / total:.0%})" if total else ""
                logger.info("  …downloaded %d MiB%s", written >> 20, pct)
                next_log += _PROGRESS_EVERY
    if written == 0:
        raise ModelFetchError("downloaded 0 bytes — the bundle URL returned an empty body")
    logger.info("Download complete: %d MiB", written >> 20)


def download_bundle(url: str, dest: Path, *, timeout: int = 180, retries: int = 4) -> None:
    """Download the bundle at ``url`` to ``dest`` (exponential backoff)."""
    session = requests.Session()
    session.headers.update({"User-Agent": "PlantVision-modelfetch/1.0"})
    file_id = google_drive_id(url) if is_google_drive(url) else None
    last: Exception | None = None
    delay = 2.0
    for attempt in range(1, retries + 1):
        try:
            if file_id is not None:
                _download_google_drive(session, file_id, dest, timeout)
            else:
                with session.get(url, stream=True, timeout=timeout) as r:
                    if r.status_code in _RETRY_STATUS:
                        raise requests.HTTPError(f"{r.status_code} {r.reason}")
                    r.raise_for_status()
                    _stream_to_file(r, dest)
            return
        except (requests.RequestException, ModelFetchError) as e:
            last = e
            if attempt == retries:
                break
            logger.warning("Model download attempt %d/%d failed (%s); retrying in %.0fs",
                           attempt, retries, e, delay)
            dest.unlink(missing_ok=True)
            import time
            time.sleep(delay)
            delay *= 2
    raise ModelFetchError(f"could not download model bundle from {url}: {last}")


# ── extraction ───────────────────────────────────────────────────────


def _safe_path(target: Path, name: str) -> Path | None:
    """Resolve an archive member under ``target``; reject path traversal."""
    p = (target / name).resolve()
    if target.resolve() == p or target.resolve() in p.parents:
        return p
    return None


def _extract(archive: Path, staging: Path) -> None:
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                if _safe_path(staging, info.filename) is None:
                    raise ModelFetchError(f"unsafe path in zip bundle: {info.filename!r}")
            z.extractall(staging)
        return
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive, mode="r:*") as t:
            members = [m for m in t.getmembers() if m.isfile()]
            for m in members:
                if _safe_path(staging, m.name) is None:
                    raise ModelFetchError(f"unsafe path in tar bundle: {m.name!r}")
            try:
                t.extractall(staging, members=members, filter="data")
            except TypeError:  # Python < 3.12 has no `filter=`
                t.extractall(staging, members=members)
        return
    raise ModelFetchError(
        f"unrecognised bundle format at {archive.name} — expected a .tar/.tar.gz/.zip archive"
    )


def _flatten_into(staging: Path, models_dir: Path) -> None:
    """Move extracted files into ``models_dir``, stripping a single
    top-level wrapper directory (e.g. ``models/``) if present."""
    entries = [p for p in staging.iterdir() if not p.name.startswith(".")]
    root = entries[0] if len(entries) == 1 and entries[0].is_dir() else staging
    for src in root.rglob("*"):
        if src.is_dir():
            continue
        dst = models_dir / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))


# ── orchestrator ─────────────────────────────────────────────────────


def ensure_models(models_dir: Path, url: str, *, force: bool = False) -> bool:
    """Ensure the model bundle is present under ``models_dir``.

    Returns ``True`` when a download happened, ``False`` when the cache
    was already complete. Raises :class:`ModelFetchError` if, after the
    attempt, any required artefact group is still missing.
    """
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)

    if not force and models_present(models_dir):
        logger.info("Model artefacts already present in %s — skipping fetch", models_dir)
        return False

    if not url:
        raise ModelFetchError(
            f"model artefacts missing under {models_dir} (need one of each: "
            f"{[g[1] for g in REQUIRED_GROUPS]}) and no MODEL_BUNDLE_URL is "
            f"configured. Set MODEL_BUNDLE_URL or drop the trained files in "
            f"manually."
        )

    logger.info("Fetching model bundle from %s → %s", url, models_dir)
    with tempfile.TemporaryDirectory(prefix="plantvision_models_") as tmp:
        tmp_dir = Path(tmp)
        archive = tmp_dir / "bundle"
        download_bundle(url, archive)
        staging = tmp_dir / "staging"
        staging.mkdir()
        _extract(archive, staging)
        _flatten_into(staging, models_dir)

    still_missing = missing_groups(models_dir)
    if still_missing:
        raise ModelFetchError(
            f"model bundle from {url} did not provide artefacts for: "
            f"{still_missing}. Re-check the bundle contents (expected one of "
            f"each: {[g[1] for g in REQUIRED_GROUPS]})."
        )
    logger.info("Model bundle ready in %s", models_dir)
    return True
