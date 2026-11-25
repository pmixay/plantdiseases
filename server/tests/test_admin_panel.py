"""The desktop moderation panel: served safely, and only calls routes that exist."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import routes_admin_panel

PANEL = Path(routes_admin_panel.PANEL_DIR)
SCRIPT = (PANEL / "admin.js").read_text(encoding="utf-8")
ACCOUNT_ROUTES = (
    Path(__file__).resolve().parents[2] / "account-server" / "app" / "api" / "routes.py"
).read_text(encoding="utf-8")


def test_page_is_served_with_a_strict_policy(client):
    page = client.get("/admin/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    policy = page.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "frame-ancestors 'none'" in policy
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["referrer-policy"] == "no-referrer"
    # Every script is a file: the policy forbids inline code.
    scripts = re.findall(r"<script\b[^>]*>", page.text)
    assert scripts and all('src="' in tag for tag in scripts)
    assert "style=" not in page.text


def test_bare_path_redirects_and_assets_have_their_types(client):
    redirect = client.get("/admin", follow_redirects=False)
    assert redirect.status_code == 308
    assert redirect.headers["location"] == "/admin/"
    script = client.get("/admin/admin.js")
    assert script.headers["content-type"].startswith("text/javascript")
    assert script.headers["content-security-policy"]
    assert client.get("/admin/admin.css").headers["content-type"].startswith("text/css")


@pytest.mark.parametrize("name", ["index.html.bak", "main.py", "..%2Fmain.py", "admin.JS"])
def test_only_the_panel_files_are_served(client, name):
    assert client.get(f"/admin/{name}").status_code == 404


def test_config_says_when_the_panel_is_not_set_up(client, monkeypatch):
    unset = client.get("/admin/config.json").json()
    assert unset == {"available": False, "yandex_client_id": None, "account_base_path": "/id"}
    monkeypatch.setattr(routes_admin_panel, "YANDEX_CLIENT_ID", "yandex-app-id")
    assert client.get("/admin/config.json").json()["yandex_client_id"] == "yandex-app-id"


def test_script_never_builds_html_from_strings():
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in SCRIPT


def test_script_calls_only_routes_that_exist(client):
    from main import app

    # Every admin route is in the schema (only the 1.9 stand-ins are not).
    templates = set(app.openapi()["paths"])
    patterns = [
        re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path)) + "$")
        for path in templates
    ]
    called = set(re.findall(r'"(/community/[a-z_/-]*)', SCRIPT))
    assert len(called) >= 10
    for literal in called:
        path = "/api" + literal.split("?")[0]
        if path.endswith("/"):
            # The script appends an id here: some route must take one next.
            assert any(t.startswith(path + "{") for t in templates), literal
        else:
            assert any(p.match(path) for p in patterns), literal
    for path in ("/v1/auth/yandex/exchange", "/v1/auth/refresh", "/v1/auth/logout"):
        assert path in SCRIPT and f'"{path}"' in ACCOUNT_ROUTES


def _text_keys(language: str) -> set[str]:
    block = re.search(rf"\n    {language}: \{{\n(.*?)\n    \}},", SCRIPT, re.S)
    assert block, language
    return set(re.findall(r"^      ([a-z_]+):", block.group(1), re.M))


def test_both_languages_carry_every_label_the_script_uses():
    english, russian = _text_keys("en"), _text_keys("ru")
    assert english == russian
    used = set(re.findall(r'\bt\("([a-z_]+)"\)', SCRIPT))
    used |= set(re.findall(r'label: "([a-z_]+)"', SCRIPT))
    used |= {"kind_" + kind for kind in re.findall(r'"([a-z_]+)"', SCRIPT.split("QUEUE_KINDS = [")[1].split("]")[0])}
    assert used <= english, used - english
    html_keys = set(re.findall(r'data-t="([a-z_]+)"', (PANEL / "index.html").read_text(encoding="utf-8")))
    assert html_keys <= english


def test_panel_text_uses_plain_punctuation():
    for path in PANEL.iterdir():
        text = path.read_text(encoding="utf-8")
        for mark in ("—", "–", "·"):
            assert mark not in text, (path.name, mark)
