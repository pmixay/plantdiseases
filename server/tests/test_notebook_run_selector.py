"""The 2.7.2 run selector, exercised against the notebook's own source.

``RUN_272`` (§1) plus the §4 drift guard exist to make "one change per run"
mechanical. 2.6.9 and 2.7.0 each moved several constants at once and are
permanently uninterpretable as a result — no amount of later analysis recovers
which change did what. 2.7.1 moved one thing and is the only failure in the
record that taught anything.

These lift the two real blocks out of the notebook (not a copy — a copy would
drift) and check the three properties that make the mechanism worth having:
exactly one constant moves per run, an unrequested change stops the run, and an
unknown selector is rejected rather than silently ignored.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

NOTEBOOK = Path(__file__).resolve().parents[1] / "train_notebook.ipynb"

# The 2.7.1 configuration the selector freezes. Kept here deliberately: if
# someone changes a baseline value in the notebook without updating
# RUN_272_FROZEN, `test_frozen_baseline_matches_the_declared_constants` fails.
BASELINE = {
    "USE_LORA": True, "LORA_RANK": 16, "LORA_ALPHA": 32, "LORA_DROPOUT": 0.05,
    "LORA_LR": 1e-4, "LORA_LAST_K_BLOCKS": 8, "LA_TAU": 0.5,
    "LABEL_SMOOTHING": 0.05, "USE_WEIGHTED_SAMPLER": False,
}


def _blocks() -> tuple[str, str]:
    if not NOTEBOOK.exists():
        pytest.skip("train_notebook.ipynb not present")
    nb = json.loads(NOTEBOOK.read_text())
    sources = ["".join(c["source"]) for c in nb["cells"]]
    sel_src = next((s for s in sources if "RUN_272_PLAN = {" in s), None)
    guard_src = next((s for s in sources if "RUN_272_FROZEN = {" in s), None)
    if sel_src is None or guard_src is None:
        pytest.fail("RUN_272 selector or §4 guard not found — was it renamed?")
    sel = sel_src[sel_src.index("RUN_272 = None"):
                  sel_src.index("SEED = RUN_272_OVERRIDES.pop") + 60]
    guard = guard_src[guard_src.index("RUN_272_FROZEN = {"):
                      guard_src.index("print(f'§4 config verified")]
    return sel, guard


def _run(selector: str, guard: str, run, **drift):
    """Execute the selector + guard for one RUN_272 value, return the namespace."""
    if run is not None:
        selector = re.sub(r"^RUN_272 = None$", f"RUN_272 = {run!r}", selector,
                          count=1, flags=re.M)
    ns = dict(BASELINE, SEED=0, print=lambda *a, **k: None)
    ns.update(drift)
    exec(compile(selector, "<selector>", "exec"), ns)
    exec(compile(guard, "<guard>", "exec"), ns)
    return ns


@pytest.mark.parametrize("run,expect_seed,expect_moved", [
    (None, 0, {}),
    ("R1", 1, {}),                              # seed replicate: SEED only
    ("R2", 0, {"USE_LORA": False}),
    ("R3", 0, {"LABEL_SMOOTHING": 0.0}),
])
def test_exactly_one_thing_moves(run, expect_seed, expect_moved):
    sel, guard = _blocks()
    ns = _run(sel, guard, run)
    moved = {k: ns[k] for k in BASELINE if ns[k] != BASELINE[k]}
    assert moved == expect_moved
    assert ns["SEED"] == expect_seed
    assert len(moved) <= 1, "a run that moves two constants answers neither question"


def test_the_manifest_records_which_question_the_run_asked():
    sel, guard = _blocks()
    for run in (None, "R1", "R2", "R3"):
        ns = _run(sel, guard, run)
        m = ns["RUN_272_MANIFEST"]
        assert m["run"] == run
        assert m["seed"] == ns["SEED"]
        assert m["frozen"].keys() == BASELINE.keys(), (
            "the manifest must carry the FULL baseline, so two bundles can be "
            "diffed without the notebook that produced them")


def test_unrequested_drift_stops_the_run():
    sel, guard = _blocks()
    with pytest.raises(RuntimeError, match="SEVERAL THINGS MOVED AT ONCE"):
        _run(sel, guard, "R2", LA_TAU=0.9)


def test_drift_in_the_very_constant_being_overridden_is_allowed():
    # R3 sets LABEL_SMOOTHING; that is the requested change, not drift.
    sel, guard = _blocks()
    ns = _run(sel, guard, "R3")
    assert ns["LABEL_SMOOTHING"] == 0.0


def test_unknown_selector_is_rejected_not_ignored():
    sel, guard = _blocks()
    with pytest.raises(SystemExit, match="RX"):
        _run(sel, guard, "RX")


def test_override_of_an_undeclared_constant_is_rejected():
    """A selector entry naming a constant outside RUN_272_FROZEN would be
    applied but never drift-checked — a silent hole in the guarantee."""
    sel, guard = _blocks()
    ns = dict(BASELINE, SEED=0, print=lambda *a, **k: None)
    ns["RUN_272"] = "RZ"
    ns["RUN_272_OVERRIDES"] = {"SOME_UNDECLARED_KNOB": 1}
    with pytest.raises(RuntimeError, match="not a frozen"):
        exec(compile(guard, "<guard>", "exec"), ns)


def test_frozen_baseline_matches_the_declared_constants():
    """RUN_272_FROZEN must equal what §4 actually assigns, or the guard is
    comparing against fiction and would fire (or stay silent) for the wrong
    reasons."""
    nb = json.loads(NOTEBOOK.read_text())
    c10 = next(s for s in ("".join(c["source"]) for c in nb["cells"])
               if "RUN_272_FROZEN = {" in s)
    ns = {"print": lambda *a, **k: None}
    exec(compile(c10[c10.index("RUN_272_FROZEN = {"):
                     c10.index("_g = globals()")], "<frozen>", "exec"), ns)
    frozen = ns["RUN_272_FROZEN"]
    assert frozen == BASELINE, (
        "RUN_272_FROZEN drifted from the 2.7.1 baseline this test pins. If the "
        "notebook's constant genuinely changed, update BOTH — and note that "
        "every archived bundle's run_272.frozen block now means something else.")
    for name, want in BASELINE.items():
        m = re.search(rf"^{name} = (.+?)(?:\s+#.*)?$", c10, flags=re.M)
        assert m, f"{name} is in RUN_272_FROZEN but never assigned in §4"
        assert eval(m.group(1).strip()) == want, (
            f"§4 assigns {name} = {m.group(1).strip()} but RUN_272_FROZEN says {want!r}")
