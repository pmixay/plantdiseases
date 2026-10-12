"""Deterministic training survives §3's detector training.

Ultralytics' trainer calls ``unset_deterministic()`` when ``detector.train()``
returns — ``use_deterministic_algorithms(False)``, ``cudnn.deterministic =
False`` and ``CUBLAS_WORKSPACE_CONFIG`` popped — so the 2.7.5 run trained §4/§5
non-deterministically. The notebook's ``assert_determinism`` helper restores
all three at the top of §4, §5 and §6, and records every op that has no
deterministic kernel into run_manifest.json.

The helper is executed here for real, against a stub ``torch``: no GPU or
torch install is needed to check that it undoes what Ultralytics does.
"""

from __future__ import annotations

import json
import re
import types
import warnings
from pathlib import Path

import pytest

NB_PATH = Path(__file__).resolve().parents[1] / "train_notebook.ipynb"


def _cells() -> list[str]:
    nb = json.loads(NB_PATH.read_text(encoding="utf-8"))
    return ["".join(c.get("source", [])) for c in nb["cells"] if c["cell_type"] == "code"]


def _section(number: int) -> str:
    matches = [c for c in _cells() if c.startswith(f"# §{number} ")]
    assert matches, f"no code cell opens with '# §{number} '"
    return matches[0]


def _helper_block() -> str:
    src = _section(1)
    start = src.index("# ── Determinism ──")
    end = src.index("assert_determinism('s1_setup')")
    return src[start:end]


class _StubTorch:
    """Just the surface the helper touches."""

    def __init__(self):
        self.backends = types.SimpleNamespace(
            cudnn=types.SimpleNamespace(benchmark=True, deterministic=False),
        )
        self._det = False
        self._warn_only = False

    def use_deterministic_algorithms(self, mode, *, warn_only=False):
        self._det, self._warn_only = bool(mode), bool(warn_only)

    def are_deterministic_algorithms_enabled(self):
        return self._det

    def is_deterministic_algorithms_warn_only_enabled(self):
        return self._warn_only


@pytest.fixture
def helper(monkeypatch):
    stub = _StubTorch()
    fake_os = types.SimpleNamespace(environ={"CUBLAS_WORKSPACE_CONFIG": ":4096:8"})
    ns = {"torch": stub, "os": fake_os, "_CUBLAS_WORKSPACE_CONFIG": ":4096:8"}
    saved_showwarning = warnings.showwarning
    saved_filters = list(warnings.filters)
    try:
        exec(compile(_helper_block(), "train_notebook §1 determinism", "exec"), ns)
        yield ns, stub, fake_os
    finally:
        warnings.showwarning = saved_showwarning
        warnings.filters[:] = saved_filters


def test_assert_determinism_undoes_ultralytics_unset(helper):
    ns, stub, fake_os = helper
    # What ultralytics.utils.torch_utils.unset_deterministic() does.
    stub.use_deterministic_algorithms(False)
    stub.backends.cudnn.deterministic = False
    fake_os.environ.pop("CUBLAS_WORKSPACE_CONFIG")

    state = ns["assert_determinism"]("s4_head_training")

    assert stub.are_deterministic_algorithms_enabled()
    assert stub.is_deterministic_algorithms_warn_only_enabled()
    assert stub.backends.cudnn.deterministic is True
    assert stub.backends.cudnn.benchmark is False
    assert fake_os.environ["CUBLAS_WORKSPACE_CONFIG"] == ":4096:8"
    assert state == {
        "deterministic_algorithms": True,
        "warn_only": True,
        "cudnn_deterministic": True,
        "cudnn_benchmark": False,
        "cublas_workspace_config": ":4096:8",
    }


def test_nondeterministic_ops_are_recorded_per_phase(helper):
    ns, _stub, _os = helper
    ns["assert_determinism"]("s5_finalize_distill")
    # A library 'ignore' filter installed after the assert must not hide it.
    warnings.filterwarnings("ignore", category=UserWarning)
    ns["assert_determinism"]("s5_finalize_distill")
    with warnings.catch_warnings():
        warnings.simplefilter("default")
        warnings.warn_explicit(
            "adaptive_avg_pool2d_backward_cuda does not have a deterministic "
            "implementation, but you set 'torch.use_deterministic_algorithms(True, "
            "warn_only=True)'.",
            UserWarning, "x.py", 1,
        )
    assert ns["DETERMINISM_WARNINGS"] == {
        "adaptive_avg_pool2d_backward_cuda": ["s5_finalize_distill"],
    }


def test_determinism_is_reasserted_before_every_training_phase():
    s4, s5, s6 = _section(4), _section(5), _section(6)
    # §4: before the optimiser is built and the first backward pass.
    assert "DETERMINISM_AT_TRAINING = assert_determinism('s4_head_training')" in s4
    assert s4.index("assert_determinism(") < s4.index("optim.AdamW(") < s4.index(".backward(")
    # §5: before the teacher is frozen and before the student's backward pass.
    s5_assert = s5.index("assert_determinism('s5_finalize_distill')")
    assert s5_assert < s5.index("model.eval()") < s5.index(".backward(")
    assert "assert_determinism('s6_export')" in s6
    # §3 is where Ultralytics turns it off; the recipe must not rely on §1 alone.
    assert "detector.train(" in _section(3)


def test_run_manifest_and_card_seal_the_training_state():
    s6 = _section(6)
    manifest = s6[s6.index("_run_manifest_payload = {"):s6.index("Path('models/run_manifest.json')")]
    assert re.search(r"'determinism':\s*\{\s*'at_training': DETERMINISM_AT_TRAINING", manifest)
    assert "'nondeterministic_ops':" in manifest
    assert "'training_determinism': DETERMINISM_AT_TRAINING" in s6
