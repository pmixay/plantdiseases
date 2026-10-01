"""The served-gate calibration in notebook §6 must see what the server sees.

§6 fits ``classes.json -> ood.served_gate`` by re-running Stage 1 and the crop
rules inside the notebook (it cannot import ``server/``: only the notebook ships
to Colab). If that mirror drifts from ``detector.py`` / ``pipeline.py``, the
fitted thresholds describe a statistic the server never computes and the gate
is silently miscalibrated. This test executes the notebook's own helper code
and the server's on the same randomised detector outputs and requires
identical detections, best confidence and crop members, byte for byte.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import detector as detector_module
import pipeline as pipeline_module

NB_PATH = Path(__file__).resolve().parents[1] / "train_notebook.ipynb"
_BLOCK_START = "SERVED_GATE_CROP = {"
_BLOCK_END = "if EMBEDDING_OOD is not None or ML_FLOOR_PERSISTED is not None:"


@pytest.fixture(scope="module")
def notebook_mirror() -> dict:
    cells = json.loads(NB_PATH.read_text(encoding="utf-8"))["cells"]
    src = next(
        "".join(c["source"]) for c in cells
        if c["cell_type"] == "code" and _BLOCK_START in "".join(c["source"])
    )
    block = src[src.index(_BLOCK_START):src.index(_BLOCK_END)]
    ns: dict = {"np": np, "PILImage": Image}
    exec(compile(block, "train_notebook.ipynb#served_gate", "exec"), ns)  # noqa: S102
    return ns


class _FakeSession:
    def __init__(self, out: np.ndarray):
        self._out = out

    def get_inputs(self):
        return [types.SimpleNamespace(name="images")]

    def run(self, _names, _feed):
        return [self._out]


def _server_detector(sess: _FakeSession) -> detector_module.LeafDetector:
    det = detector_module.LeafDetector.__new__(detector_module.LeafDetector)
    det.model, det.input_name = sess, "images"
    det.img_size = detector_module.DETECTOR_IMG_SIZE
    det.min_confidence = detector_module.MIN_CONFIDENCE
    det.nms_iou = 0.45
    det.max_detections = detector_module.MAX_DETECTIONS
    det.class_name = "plant_leaf"
    return det


def _key(dets: list[dict]) -> list[tuple]:
    return [(d["x"], d["y"], d["width"], d["height"], round(d["confidence"], 6)) for d in dets]


def test_notebook_mirror_matches_server_detections_and_members(notebook_mirror):
    real = getattr(pipeline_module, "_RealPipelineForTests", pipeline_module.PlantDiseasePipeline)
    rng = np.random.default_rng(0)
    compared = 0
    for trial in range(120):
        w, h = int(rng.integers(200, 1600)), int(rng.integers(200, 1600))
        img = Image.fromarray(rng.integers(0, 255, (h, w, 3), dtype=np.uint8))
        n = int(rng.integers(0, 40))
        out = np.zeros((1, 5, max(n, 1)), dtype=np.float32)
        if n:
            out[0, 0, :n] = rng.uniform(0, 640, n)
            out[0, 1, :n] = rng.uniform(0, 640, n)
            out[0, 2, :n] = rng.uniform(2, 400, n)
            out[0, 3, :n] = rng.uniform(2, 400, n)
            out[0, 4, :n] = rng.uniform(0, 1, n)
        if trial % 2:
            out = out.transpose(0, 2, 1).copy()  # both Ultralytics layouts
        sess = _FakeSession(out)

        served = _server_detector(sess).detect(img)
        mirror_dets, mirror_conf = notebook_mirror["_served_detect"](sess, img)
        assert _key(mirror_dets) == _key(served["detections"]), f"trial {trial}: detections"
        assert mirror_conf == pytest.approx(served["confidence"], abs=1e-6)

        if served["detections"]:
            sized = real._size_filter(served["detections"], img.size)
            ranked = sorted(sized, key=lambda d: -d["confidence"])[: real._MAX_RECLASSIFY]
            server_members = [real._extract_crop(img, box) for box in ranked]
            if pipeline_module.FOLD_WHOLE_FRAME_VOTE:
                server_members.append(img)
        else:
            server_members = [img]  # _analyze_no_detection classifies the frame alone
        mirror_members = notebook_mirror["_served_members"](img, mirror_dets)
        assert len(mirror_members) == len(server_members), f"trial {trial}: member count"
        for a, b in zip(mirror_members, server_members, strict=True):
            assert a.size == b.size and np.array_equal(np.asarray(a), np.asarray(b)), (
                f"trial {trial}: crop member differs"
            )
        compared += len(mirror_members)
    assert compared > 200


# ── The streamed served pass (2.7.5): classifier batching, gate rule, upload
# size, ordering. Each is a statistic the Stage-1 audit, the end-to-end eval
# and the served-gate calibration all read, so each is pinned to the server.


class _RecordingClassifierSession:
    """A batch-composition-SENSITIVE fake: every logit depends on the batch mean,
    the way per-tensor dynamic INT8 activation scales do. A mirror that splits
    the original and mirrored halves into two runs, or batches photos together,
    produces different numbers and fails the comparison."""

    def __init__(self, n_classes: int = 6, dim: int = 8, embeddings: bool = True):
        rng = np.random.default_rng(1)
        self._w = rng.normal(size=(3 * 4, n_classes)).astype(np.float32)
        self._e = rng.normal(size=(3 * 4, dim)).astype(np.float32)
        self._embeddings = embeddings
        self.batch_sizes: list[int] = []

    def get_inputs(self):
        return [types.SimpleNamespace(name="pixel_values")]

    def get_outputs(self):
        names = ["logits", "embeddings"] if self._embeddings else ["logits"]
        return [types.SimpleNamespace(name=n) for n in names]

    def run(self, _names, feed):
        x = feed["pixel_values"]
        self.batch_sizes.append(int(x.shape[0]))
        n, c, h, w = x.shape
        pooled = x.reshape(n, c, 2, h // 2, 2, w // 2).mean(axis=(3, 5)).reshape(n, -1)
        pooled = pooled - 0.5 * pooled.mean(axis=0, keepdims=True)   # batch coupling
        outs = [pooled @ self._w]
        if self._embeddings:
            outs.append(pooled @ self._e)
        return outs


def _server_classifier(sess, tta: str):
    import classifier as classifier_module

    clf = classifier_module.DiseaseClassifier.__new__(classifier_module.DiseaseClassifier)
    clf._onnx_session, clf._onnx_input_name = sess, "pixel_values"
    clf._has_embedding = len(sess.get_outputs()) == 2
    clf.tta = tta
    return clf


@pytest.mark.parametrize("tta", ["hflip", "none"])
@pytest.mark.parametrize("embeddings", [True, False])
def test_notebook_classify_matches_server_onnx_forward(notebook_mirror, tta, embeddings):
    rng = np.random.default_rng(3)
    members = [Image.fromarray(rng.integers(0, 255, (16, 16, 3), dtype=np.uint8))
               for _ in range(int(rng.integers(1, 6)))]

    def tx(im):  # stand-in for the eval transform; both sides get the same tensors
        arr = np.asarray(im, dtype=np.float32).transpose(2, 0, 1) / 255.0
        return types.SimpleNamespace(numpy=lambda arr=arr: arr)

    x = np.stack([tx(m).numpy() for m in members]).astype(np.float32)
    server_sess = _RecordingClassifierSession(embeddings=embeddings)
    want_logits, want_emb = _server_classifier(server_sess, tta)._onnx_forward(x)

    mirror_sess = _RecordingClassifierSession(embeddings=embeddings)
    got_logits, got_emb = notebook_mirror["_served_classify"](mirror_sess, members, tx, tta=tta)
    assert mirror_sess.batch_sizes == server_sess.batch_sizes, "different batch composition"
    np.testing.assert_allclose(got_logits, want_logits, rtol=0, atol=1e-6)
    if embeddings:
        np.testing.assert_allclose(got_emb, want_emb, rtol=0, atol=1e-6)
    else:
        assert got_emb is None and want_emb is None


def test_notebook_gate_rule_matches_decide_nap_route(notebook_mirror):
    names = ["healthy", "leaf_spot", "not_a_plant"]
    rng = np.random.default_rng(5)
    for trial in range(400):
        n = 64
        det = rng.uniform(0, 1, n)
        d_min = rng.uniform(0, 1, n)
        d_min[rng.uniform(0, 1, n) < 0.2] = np.nan        # no embedding for this photo
        ml = rng.uniform(-2, 12, n)
        p_nap = rng.uniform(0, 0.2, n)
        emb_thr = [None, 0.3, 0.6][trial % 3]
        ml_floor = [None, 0.0, -1.0, 4.0][trial % 4]
        hm = [0.0, 0.05][trial % 2]
        got = notebook_mirror["_served_gate_fires"](
            det, d_min, ml, p_nap, emb_thr=emb_thr, ml_floor=ml_floor,
            head_mass_floor=hm, det_floor=pipeline_module.NAP_DETECTOR_CONF_FLOOR)
        for k in range(n):
            want, _ = pipeline_module._decide_nap_route(
                class_name="healthy", class_names=names, det_conf=float(det[k]),
                ood_score=None if np.isnan(d_min[k]) else float(d_min[k]),
                ood_threshold=emb_thr, ml_floor=ml_floor or 0.0, max_logit=float(ml[k]),
                head_nap=float(p_nap[k]), head_mass_floor=hm)
            assert bool(got[k]) == want, f"trial {trial} photo {k}"


@pytest.mark.parametrize("size,fmt", [((4000, 3000), "JPEG"), ((3001, 1999), "JPEG"),
                                      ((1999, 3001), "PNG"), ((1000, 700), "JPEG"),
                                      ((1024, 1024), "JPEG")])
def test_served_load_matches_the_app_upload_size(notebook_mirror, tmp_path, size, fmt):
    """android ImageUtils.prepareImageForUpload: long edge <= 1024, each side
    floor(side * 1024 / long_edge); smaller photos pass through untouched."""
    p = tmp_path / ("photo.jpg" if fmt == "JPEG" else "photo.png")
    Image.new("RGB", size, (90, 140, 60)).save(p, format=fmt)
    got = notebook_mirror["_served_load"](p)
    w, h = size
    r = min(1.0, 1024 / max(w, h))
    assert got.mode == "RGB"
    assert got.size == ((w, h) if r == 1.0 else (int(w * r), int(h * r)))
    assert notebook_mirror["SERVED_UPLOAD_MAX_SIDE"] == 1024


def test_served_pass_streams_every_photo_in_order(notebook_mirror, tmp_path):
    rng = np.random.default_rng(9)
    samples = []
    for i in range(23):
        p = tmp_path / f"{i}.jpg"
        Image.fromarray(rng.integers(0, 255, (int(rng.integers(64, 900)),
                                              int(rng.integers(64, 900)), 3),
                                     dtype=np.uint8)).save(p)
        samples.append((p, i % 5))
    det_out = np.zeros((1, 5, 3), dtype=np.float32)
    det_out[0, :, 0] = [320, 320, 300, 300, 0.9]
    det_out[0, :, 1] = [100, 100, 80, 80, 0.3]
    cls_sess = _RecordingClassifierSession(n_classes=5, embeddings=True)

    def tx(im):
        arr = np.asarray(im.resize((16, 16)), dtype=np.float32).transpose(2, 0, 1) / 255.0
        return types.SimpleNamespace(numpy=lambda arr=arr: arr)

    notebook_mirror["TTA_MODE"] = "hflip"   # the helpers read it from their own globals
    try:
        rows = list(notebook_mirror["_served_pass"](
            samples, _FakeSession(det_out), cls_sess, tx, 1.3, prefetch=4))
    finally:
        notebook_mirror.pop("TTA_MODE", None)
    assert [r["y"] for r in rows] == [y for _, y in samples]
    for r in rows:
        assert r["has_det"] and r["n"] >= 2               # >= 1 crop + the whole frame
        assert r["logits"].shape == (r["n"], 5) and r["emb"].shape[0] == r["n"]
        assert r["vote"].sum() == pytest.approx(1.0, abs=1e-5)
        assert 0.0 < r["det_conf"] <= 1.0
    # One classifier run per photo, members and mirrors together.
    assert len(cls_sess.batch_sizes) == len(samples)
    assert all(b == 2 * r["n"] for b, r in zip(cls_sess.batch_sizes, rows, strict=True))
