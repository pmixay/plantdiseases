"""Production scan recording (/api/analyze → labeling store, anonymized)."""

from __future__ import annotations

import io
import time

import pytest
from PIL import Image

import labeling
import main as server_main
from tests.conftest import FakePipeline

_seed = 0


def _jpeg(size=(640, 480), exif: bool = False) -> bytes:
    """Distinct bytes each call (sha-unique index must not collapse scans)."""
    global _seed
    _seed += 1
    img = Image.new("RGB", size, (40, 150, 60))
    img.putpixel((1, 1), (_seed % 251, (_seed * 7) % 251, 99))
    buf = io.BytesIO()
    kwargs = {}
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "TestMake"      # Make
        ex[0x0131] = "TestSoftware"  # Software
        kwargs["exif"] = ex
    img.save(buf, format="JPEG", **kwargs)
    return buf.getvalue()


class ConfidentPipeline(FakePipeline):
    """Singleton conformal set == top-1 ⇒ route_scan says auto_yes."""

    def __init__(self, class_name="healthy", confidence=0.95):
        super().__init__()
        self._cls = class_name
        self._conf = confidence

    def analyze(self, img):
        r = super().analyze(img)
        r["class_name"] = self._cls
        r["confidence"] = self._conf
        r["prediction_set"] = {"classes": [self._cls], "coverage": 0.9}
        return r


class NapPipeline(FakePipeline):
    def analyze(self, img):
        r = super().analyze(img)
        r["class_name"] = "not_a_plant"
        r["warnings"] = ["blurry_image"]
        return r


@pytest.fixture
def recdb(monkeypatch):
    """Fresh labeling DB + recording enabled with test-friendly caps."""
    if labeling.LABELING_DB_PATH.exists():
        labeling.LABELING_DB_PATH.unlink()
    labeling._initialised = False
    labeling.init_db()
    monkeypatch.setattr(server_main, "SCAN_RECORDING_ENABLED", True)
    monkeypatch.setattr(server_main, "SCAN_COMMON_KEEP_RATE", 1.0)
    monkeypatch.setattr(server_main, "SCAN_DAILY_CAP_PER_CLASS", 50)
    return labeling


def _rows():
    with labeling._connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM candidates ORDER BY id").fetchall()]


def _analyze(client, body):
    r = client.post("/api/analyze", files={"image": ("leaf.jpg", body, "image/jpeg")})
    assert r.status_code == 200, r.text
    return r.json()


def test_scan_recorded_with_source_status_scan_id(client, recdb):
    # FakePipeline: healthy, low confidence, no conformal set → pending
    data = _analyze(client, _jpeg())
    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "user_scan"
    assert row["status"] == "pending"          # low confidence = suspicious
    assert row["scan_id"] == data["scan_id"]
    assert row["class"] == "healthy"
    assert row["model_pred"] == "healthy"


def test_confident_singleton_set_routes_auto_yes(client, recdb, monkeypatch):
    monkeypatch.setattr(server_main, "pipeline", ConfidentPipeline("pests", 0.97))
    _analyze(client, _jpeg())
    rows = _rows()
    assert len(rows) == 1 and rows[0]["status"] == "auto_yes"


def test_nap_and_blur_not_recorded(client, recdb, monkeypatch):
    monkeypatch.setattr(server_main, "pipeline", NapPipeline())
    _analyze(client, _jpeg())
    assert _rows() == []


def test_cache_hit_records_once(client, recdb):
    body = _jpeg()
    _analyze(client, body)
    second = _analyze(client, body)
    assert second["is_repeat"] is True
    assert len(_rows()) == 1


def test_recording_disabled_no_row(client, recdb, monkeypatch):
    monkeypatch.setattr(server_main, "SCAN_RECORDING_ENABLED", False)
    _analyze(client, _jpeg())
    assert _rows() == []


def test_stored_image_exif_stripped_and_downscaled(client, recdb):
    _analyze(client, _jpeg(size=(2048, 1536), exif=True))
    rows = _rows()
    assert len(rows) == 1
    stored = labeling.LABELING_IMG_DIR / rows[0]["image_path"]
    img = Image.open(stored)
    assert max(img.size) <= server_main.SCAN_IMG_MAX_SIDE
    assert not img.getexif()  # EXIF (incl. GPS) dropped by re-encode


def test_common_class_auto_yes_sampled_out(client, recdb, monkeypatch):
    # healthy is the abundant easy class → confident scans honour the keep rate
    monkeypatch.setattr(server_main, "pipeline", ConfidentPipeline("healthy", 0.97))
    monkeypatch.setattr(server_main, "SCAN_COMMON_KEEP_RATE", 0.0)
    _analyze(client, _jpeg())
    assert _rows() == []


def test_disease_class_always_kept_despite_zero_keep_rate(client, recdb, monkeypatch):
    # every disease class is high-value training signal → never sampled out
    monkeypatch.setattr(
        server_main, "pipeline", ConfidentPipeline("gray_mold_botrytis", 0.97))
    monkeypatch.setattr(server_main, "SCAN_COMMON_KEEP_RATE", 0.0)
    _analyze(client, _jpeg())
    rows = _rows()
    assert len(rows) == 1 and rows[0]["status"] == "auto_yes"


def test_suspicious_scan_kept_despite_zero_keep_rate(client, recdb, monkeypatch):
    # default FakePipeline is unconfident → pending → always kept
    monkeypatch.setattr(server_main, "SCAN_COMMON_KEEP_RATE", 0.0)
    _analyze(client, _jpeg())
    assert len(_rows()) == 1


def test_daily_cap_per_class(client, recdb, monkeypatch):
    monkeypatch.setattr(server_main, "SCAN_DAILY_CAP_PER_CLASS", 1)
    _analyze(client, _jpeg())
    _analyze(client, _jpeg())
    assert len(_rows()) == 1


def test_keep_sampling_is_deterministic_by_sha():
    sha_low = "00000001" + "0" * 56   # bucket 1 → kept at 10%
    sha_high = "ffffffff" + "0" * 56  # bucket ~9999 → dropped at 10%
    assert server_main._scan_keep_sampled(sha_low, 0.1) is True
    assert server_main._scan_keep_sampled(sha_high, 0.1) is False
    # repeat calls agree (pure function of sha)
    assert server_main._scan_keep_sampled(sha_high, 0.1) is False


def test_prune_user_scans_order(recdb):
    """auto_yes common-class rows die first; pending only past retention;
    user_feedback and human-verdict rows never."""
    now = int(time.time())
    old = now - 200 * 86400

    def seed(cls, status, source, created_at, tag):
        sha = f"{tag:0>64}"
        fn = f"{sha}.jpg"
        labeling.LABELING_IMG_DIR.mkdir(parents=True, exist_ok=True)
        (labeling.LABELING_IMG_DIR / fn).write_bytes(b"x")
        rid = labeling.insert_candidate(
            cls=cls, image_path=fn, source=source, source_url=None,
            subject_plant=None, sha=sha, phash=None, status=status,
        )
        with labeling._connect() as conn:
            conn.execute(
                "UPDATE candidates SET created_at=? WHERE id=?", (created_at, rid))
        return rid

    fresh_auto_common = seed("healthy", "auto_yes", "user_scan", now, "a1")
    old_auto_weak = seed("gray_mold_botrytis", "auto_yes", "user_scan", old, "a2")
    old_pending = seed("leaf_spot", "pending", "user_scan", old, "a3")
    fresh_pending = seed("leaf_spot", "pending", "user_scan", now, "a4")
    old_feedback = seed("leaf_spot", "pending", "user_feedback", old, "a5")
    human_yes = seed("pests", "yes", "user_scan", old, "a6")

    # retention pass only: old pending user_scan dies, others stay
    labeling.prune_user_scans(90, 1000, {"gray_mold_botrytis"})
    ids = {r["id"] for r in _rows()}
    assert old_pending not in ids
    assert {fresh_auto_common, old_auto_weak, fresh_pending,
            old_feedback, human_yes} <= ids

    # cap pass: max 3 user_scan rows remain (auto_yes common dies before weak)
    labeling.prune_user_scans(90, 3, {"gray_mold_botrytis"})
    ids = {r["id"] for r in _rows()}
    assert fresh_auto_common not in ids        # common auto_yes pruned first
    assert {old_auto_weak, fresh_pending, old_feedback, human_yes} <= ids

    # squeeze to 1: weak auto_yes goes next; pending/feedback/human survive
    labeling.prune_user_scans(90, 1, {"gray_mold_botrytis"})
    ids = {r["id"] for r in _rows()}
    assert old_auto_weak not in ids
    assert {fresh_pending, old_feedback, human_yes} <= ids
