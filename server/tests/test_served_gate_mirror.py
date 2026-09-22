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
