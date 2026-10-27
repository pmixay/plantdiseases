from __future__ import annotations

import os

import config


def _clear(monkeypatch, key: str) -> None:
    monkeypatch.setattr(config, "_FILE_DATA", {})
    for name in ("ENVIRONMENT", key, f"{key}_FILE"):
        monkeypatch.delenv(name, raising=False)


def test_secret_file_is_the_only_supported_production_channel(tmp_path, monkeypatch):
    key = "TEST_VENDOR_SECRET"
    _clear(monkeypatch, key)
    secret_file = tmp_path / "vendor.key"
    secret_file.write_text("test-secret\n", encoding="utf-8")
    os.chmod(secret_file, 0o600)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(f"{key}_FILE", str(secret_file))

    assert config.get_secret(key) == "test-secret"

    monkeypatch.setenv(key, "direct-secret")
    assert config.get_secret(key) == ""


def test_plantnet_key_uses_the_production_file_channel(tmp_path, monkeypatch):
    key = "PLANTNET_API_KEY"
    _clear(monkeypatch, key)
    secret_file = tmp_path / "plantnet-api.key"
    secret_file.write_text("plantnet-test-key\n", encoding="utf-8")
    os.chmod(secret_file, 0o400)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PLANTNET_API_KEY_FILE", str(secret_file))

    assert config.get_secret("PLANTNET_API_KEY") == "plantnet-test-key"


def test_production_direct_or_overpermissive_secret_fails_closed(
    tmp_path,
    monkeypatch,
):
    key = "TEST_VENDOR_SECRET"
    _clear(monkeypatch, key)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(key, "direct-secret")
    assert config.get_secret(key) == ""

    monkeypatch.delenv(key)
    secret_file = tmp_path / "vendor.key"
    secret_file.write_text("test-secret", encoding="utf-8")
    os.chmod(secret_file, 0o644)
    monkeypatch.setenv(f"{key}_FILE", str(secret_file))
    assert config.get_secret(key) == ""


def test_direct_secret_requires_explicit_local_environment(monkeypatch):
    key = "TEST_VENDOR_SECRET"
    _clear(monkeypatch, key)
    monkeypatch.setenv(key, "direct-secret")

    assert config.get_secret(key) == ""

    monkeypatch.setenv("ENVIRONMENT", "staging")
    assert config.get_secret(key) == ""


def test_secret_file_rejects_symlink(tmp_path, monkeypatch):
    key = "TEST_VENDOR_SECRET"
    _clear(monkeypatch, key)
    secret_file = tmp_path / "vendor.key"
    secret_file.write_text("test-secret", encoding="utf-8")
    os.chmod(secret_file, 0o600)
    secret_link = tmp_path / "vendor-link.key"
    secret_link.symlink_to(secret_file)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(f"{key}_FILE", str(secret_link))

    assert config.get_secret(key) == ""


def test_local_direct_secret_remains_available_but_whitespace_is_rejected(monkeypatch):
    key = "TEST_VENDOR_SECRET"
    _clear(monkeypatch, key)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv(key, "local-secret")
    assert config.get_secret(key) == "local-secret"

    monkeypatch.setenv(key, "not a key")
    assert config.get_secret(key) == ""
