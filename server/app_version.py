"""The server (release) version, decoupled from the model version."""

SERVER_VERSION = "1.0"


def resolve_app_version() -> str:
    """Resolve the SERVER / release version.

    This is decoupled from the MODEL version (``models/classes.json ->
    model_version``, e.g. 2.6.8), which is surfaced separately by
    ``/api/capabilities``. Prefer the package ``__version__``; when the server runs
    as a flat module (``uvicorn main:app`` from ``server/``) that import fails, so
    fall back to the same hardcoded server version (kept in sync with
    ``__init__.py``) — never the model version, which would make the server
    string track the model.
    """
    try:
        from . import __version__ as pkg_version

        return str(pkg_version)
    except Exception:
        return SERVER_VERSION


APP_VERSION = resolve_app_version()


