"""``requirements.lock`` must stay the pinned closure of ``requirements.txt``.

CI and deploys install the lock with ``--require-hashes``; a requirement
added or re-pinned in ``requirements.txt`` without regenerating the lock
would otherwise be silently missing from (or contradicted by) what runs.
"""

from __future__ import annotations

import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

SERVER = Path(__file__).resolve().parents[1]


def _top_level_requirements() -> list[Requirement]:
    reqs = []
    for raw in (SERVER / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            reqs.append(Requirement(line))
    return reqs


def _locked_versions() -> dict[str, str]:
    pins = {}
    for line in (SERVER / "requirements.lock").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", line)
        if m:
            pins[canonicalize_name(m.group(1))] = m.group(2)
    return pins


def test_lock_pins_every_requirement_within_its_specifier():
    locked = _locked_versions()
    assert locked, "requirements.lock has no pins"
    for req in _top_level_requirements():
        name = canonicalize_name(req.name)
        assert name in locked, f"{req.name} is in requirements.txt but not in requirements.lock"
        assert req.specifier.contains(locked[name], prereleases=True), (
            f"requirements.lock pins {req.name}=={locked[name]}, outside {req.specifier}"
        )


def test_every_lock_pin_is_hash_checked():
    text = (SERVER / "requirements.lock").read_text(encoding="utf-8")
    blocks = [b for b in re.split(r"\n(?=[A-Za-z0-9])", text) if "==" in b.split("\n", 1)[0]]
    assert blocks
    for block in blocks:
        assert "--hash=sha256:" in block, block.split("\n", 1)[0]
