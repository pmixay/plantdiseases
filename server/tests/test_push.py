"""Tests for the FCM push token store + the send no-op when unconfigured.

Exercises push.py directly against an isolated temp DB; FCM itself is never
contacted (no credentials configured), mirroring the reload-per-test pattern
used by the community tests.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def push(tmp_path, monkeypatch):
    monkeypatch.setenv("PUSH_DB_PATH", str(tmp_path / "push.db"))
    monkeypatch.delenv("FCM_CREDENTIALS_FILE", raising=False)
    import push as p
    importlib.reload(p)
    return p


def test_register_and_tokens_for(push):
    push.register("user-a", "tok1")
    push.register("user-a", "tok2")
    assert sorted(push.tokens_for("user-a")) == ["tok1", "tok2"]
    assert push.tokens_for("user-b") == []


def test_register_upserts_by_token(push):
    # Same token re-registered under a new pseudo-id (e.g. reinstall) moves it.
    push.register("user-a", "tok1")
    push.register("user-b", "tok1")
    assert push.tokens_for("user-a") == []
    assert push.tokens_for("user-b") == ["tok1"]


def test_blank_args_ignored(push):
    push.register("", "tok")
    push.register("user-a", "")
    assert push.tokens_for("user-a") == []


def test_send_is_noop_without_credentials(push):
    push.register("user-a", "tok1")
    # FCM_CREDENTIALS_FILE unset ⇒ push disabled ⇒ nothing sent, no raise.
    assert push.send_opinion_push("user-a", "scan1", "leaf_spot") == 0


