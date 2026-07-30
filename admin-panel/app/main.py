from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import shlex
import threading
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .models import (
    BranchRequest,
    ConnectionRequest,
    ConsoleRequest,
    DeleteUserRequest,
    DeployRequest,
    ProbeRequest,
    RestartRequest,
    validate_user_id,
)
from .security import LocalSession, LocalSessionStore
from .ssh_client import SshManager, SshOperationError, StoredConnection


LOGGER = logging.getLogger("plantdiseases.admin")
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
COOKIE_NAME = "plantd_admin_session"

sessions = LocalSessionStore()
ssh = SshManager()
operation_lock = threading.Lock()
audit_lock = threading.Lock()

app = FastAPI(
    title="PlantDiseases Admin",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class SecurityHeadersMiddleware:
    """Pure ASGI header middleware; it never buffers request/response bodies."""

    def __init__(self, wrapped_app):
        self.app = wrapped_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                    "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
                )
                if scope.get("path", "").startswith("/api/"):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


async def require_session(
    session_id: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> LocalSession:
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Admin session expired; restart the launcher")
    return session


async def require_mutation_session(
    request: Request,
    session: Annotated[LocalSession, Depends(require_session)],
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> LocalSession:
    origin = request.headers.get("origin")
    if origin:
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin.rstrip("/") != expected.rstrip("/"):
            raise HTTPException(status_code=403, detail="Cross-origin request rejected")
    if not sessions.check_csrf(session, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    return session


SessionDep = Annotated[LocalSession, Depends(require_session)]
MutationSessionDep = Annotated[LocalSession, Depends(require_mutation_session)]


def connection_for(session: LocalSession) -> StoredConnection:
    try:
        return ssh.get(session.session_id)
    except SshOperationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def checked(result: dict[str, Any], label: str) -> dict[str, Any]:
    if result["exit_code"] != 0:
        message = (result.get("stderr") or result.get("stdout") or "").strip()
        raise SshOperationError(f"{label} failed: {message[-3000:] or 'unknown error'}")
    return result


_RUNTIME_DATABASES = (
    "feedback.db",
    "community.db",
    "push.db",
    "presence.db",
    "telemetry.db",
    "labeling.db",
)
_RUNTIME_DIRECTORIES = (
    "feedback_images",
    "community_thumbs",
    "labeling_images",
    "labeling_exports",
    "plant_photo_cache",
    "ai_cache",
    "admin_backups",
)


def runtime_repo_paths(connection: StoredConnection) -> tuple[set[str], set[str]]:
    """Return known runtime paths only when the data directory is in the worktree."""
    repo = posixpath.normpath(connection.repo_path)
    data = posixpath.normpath(connection.data_dir)
    relative = posixpath.relpath(data, repo)
    if relative == ".." or relative.startswith("../"):
        return set(), set()
    prefix = "" if relative == "." else relative.rstrip("/") + "/"
    files: set[str] = set()
    for database in _RUNTIME_DATABASES:
        files.add(prefix + database)
        files.update(prefix + database + suffix for suffix in ("-wal", "-shm", "-journal"))
    directories = {prefix + name for name in _RUNTIME_DIRECTORIES}
    return files, directories


def classify_git_status(
    lines: list[str],
    connection: StoredConnection,
) -> tuple[list[str], list[str]]:
    """Separate safe untracked runtime artifacts from source-tree changes."""
    runtime_files, runtime_directories = runtime_repo_paths(connection)
    dirty: list[str] = []
    runtime: list[str] = []
    for line in lines:
        # Quoted/escaped unusual names stay blocking rather than being misclassified.
        path = line[3:].rstrip("/") if line.startswith("?? ") and not line[3:].startswith('"') else None
        is_runtime = bool(
            path
            and (
                path in runtime_files
                or any(path == directory or path.startswith(directory + "/") for directory in runtime_directories)
            )
        )
        (runtime if is_runtime else dirty).append(line)
    return dirty, runtime


def ensure_target_does_not_track_runtime(session: LocalSession, branch: str) -> None:
    """Never let a checkout overwrite a database, upload, cache, or backup path."""
    connection = connection_for(session)
    runtime_files, runtime_directories = runtime_repo_paths(connection)
    paths = sorted(runtime_files | runtime_directories)
    if not paths:
        return
    repo = shlex.quote(connection.repo_path)
    ref = shlex.quote("origin/" + branch)
    pathspecs = " ".join(shlex.quote(path) for path in paths)
    tracked = run_checked(
        session.session_id,
        f"git -C {repo} ls-tree -r --name-only {ref} -- {pathspecs}",
        "Runtime-path collision check",
        timeout=30,
    )["stdout"].splitlines()
    if tracked:
        raise SshOperationError(
            "The target branch tracks production runtime paths and could overwrite data: "
            + ", ".join(tracked[:20]),
        )


def run_checked(session_id: str, command: str, label: str, *, timeout: int = 90) -> dict[str, Any]:
    return checked(ssh.run(session_id, command, timeout=timeout), label)


def audit(event: str, session: LocalSession, **details: Any) -> None:
    connection = ssh.public_connection(session.session_id)
    row = {
        "timestamp": int(time.time()),
        "event": event,
        "host": connection.get("host") if connection else None,
        **details,
    }
    try:
        target_dir = BASE_DIR / "data"
        target_dir.mkdir(parents=True, exist_ok=True)
        with audit_lock, (target_dir / "audit.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        LOGGER.warning("Could not write local admin audit record", exc_info=True)


@app.exception_handler(SshOperationError)
async def ssh_error_handler(_request: Request, exc: SshOperationError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.get("/bootstrap", include_in_schema=False)
async def bootstrap(token: str = Query(default="")):
    if not sessions.consume_bootstrap_token(token):
        raise HTTPException(status_code=403, detail="Invalid launcher token")
    session = sessions.create()
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        session.session_id,
        httponly=True,
        samesite="strict",
        secure=False,
        max_age=sessions.ttl_seconds,
        path="/",
    )
    return response


@app.get("/", include_in_schema=False)
async def index(session_id: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
    if not sessions.get(session_id):
        return HTMLResponse(
            "<!doctype html><title>PlantDiseases Admin</title>"
            "<main style='font:16px system-ui;max-width:680px;margin:10vh auto;padding:24px'>"
            "<h1>Launch token required</h1><p>Start this panel with <code>start.sh</code> "
            "or <code>start.bat</code>, then use the browser window it opens.</p></main>",
            status_code=401,
        )
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/session")
async def session_info(session: SessionDep):
    return {
        "ok": True,
        "csrf_token": session.csrf_token,
        "connected": ssh.public_connection(session.session_id) is not None,
        "connection": ssh.public_connection(session.session_id),
    }


@app.post("/api/connection/probe")
async def probe_connection(body: ProbeRequest, _session: MutationSessionDep):
    return await run_in_threadpool(ssh.probe, body.host, body.port)


@app.post("/api/connection/connect")
async def connect(body: ConnectionRequest, session: MutationSessionDep):
    public = await run_in_threadpool(ssh.connect, session.session_id, body)
    audit("ssh_connected", session, username=body.username, port=body.port)
    return {"ok": True, "connection": public}


@app.post("/api/connection/disconnect")
async def disconnect(session: MutationSessionDep):
    audit("ssh_disconnected", session)
    ssh.disconnect(session.session_id)
    return {"ok": True}


@app.get("/api/connection")
async def connection_status(session: SessionDep):
    return {
        "connected": ssh.public_connection(session.session_id) is not None,
        "connection": ssh.public_connection(session.session_id),
    }


def database_request(session: LocalSession, action: str, **extra: Any) -> dict[str, Any]:
    connection = connection_for(session)
    return ssh.run_remote_agent(
        session.session_id,
        {"action": action, "data_dir": connection.data_dir, **extra},
    )


@app.get("/api/users")
async def users(
    session: SessionDep,
    active_window_minutes: int = Query(default=15, ge=1, le=1440),
):
    return await run_in_threadpool(
        database_request,
        session,
        "list_users",
        active_window_seconds=active_window_minutes * 60,
    )


@app.get("/api/users/{user_id}")
async def user_detail(user_id: str, session: SessionDep):
    try:
        user_id = validate_user_id(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await run_in_threadpool(database_request, session, "user_detail", user_id=user_id)


@app.delete("/api/users/{user_id}")
async def delete_user(user_id: str, body: DeleteUserRequest, session: MutationSessionDep):
    try:
        user_id = validate_user_id(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.confirm_user_id != user_id:
        raise HTTPException(status_code=409, detail="Confirmation must exactly match the user ID")
    result = await run_in_threadpool(
        database_request,
        session,
        "delete_user",
        user_id=user_id,
        create_backup=body.create_backup,
    )
    audit("user_deleted", session, user_id=user_id, result=result.get("deleted"), backup=result.get("backup_dir"))
    return result


def git_snapshot(session: LocalSession, *, fetch: bool = False) -> dict[str, Any]:
    connection = connection_for(session)
    sid = session.session_id
    repo = shlex.quote(connection.repo_path)
    root = run_checked(sid, f"git -C {repo} rev-parse --show-toplevel", "Repository check")["stdout"].strip()
    if fetch:
        run_checked(sid, f"git -C {repo} fetch --prune origin", "Git fetch", timeout=180)
    current = run_checked(sid, f"git -C {repo} branch --show-current", "Read current branch")["stdout"].strip()
    commit_line = run_checked(
        sid,
        f"git -C {repo} log -1 --pretty=format:%H%x09%h%x09%s%x09%cI",
        "Read current commit",
    )["stdout"].strip()
    commit_parts = commit_line.split("\t", 3)
    status_lines = run_checked(
        sid,
        f"git -C {repo} status --porcelain=v1 --untracked-files=normal",
        "Read working-tree status",
        timeout=30,
    )["stdout"].splitlines()
    dirty_lines, runtime_lines = classify_git_status(status_lines, connection)
    remote_text = run_checked(
        sid,
        f"git -C {repo} for-each-ref --format='%(refname:strip=3)|%(objectname)|%(objectname:short)|%(subject)' refs/remotes/origin",
        "List remote branches",
    )["stdout"]
    remotes: list[dict[str, str]] = []
    for line in remote_text.splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4 and parts[0] != "HEAD":
            remotes.append({"name": parts[0], "sha": parts[1], "short_sha": parts[2], "subject": parts[3]})
    local_text = run_checked(
        sid,
        f"git -C {repo} for-each-ref --format='%(refname:strip=2)|%(objectname:short)|%(upstream:short)' refs/heads",
        "List local branches",
    )["stdout"]
    locals_: list[dict[str, str]] = []
    for line in local_text.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            locals_.append({"name": parts[0], "short_sha": parts[1], "upstream": parts[2]})
    return {
        "ok": True,
        "repo_root": root,
        "current_branch": current or "(detached)",
        "commit": {
            "sha": commit_parts[0] if commit_parts else "",
            "short_sha": commit_parts[1] if len(commit_parts) > 1 else "",
            "subject": commit_parts[2] if len(commit_parts) > 2 else "",
            "committed_at": commit_parts[3] if len(commit_parts) > 3 else "",
        },
        "dirty": bool(dirty_lines),
        "dirty_files": dirty_lines[:100],
        "preserved_runtime_files": [line[3:] for line in runtime_lines[:100]],
        "remote_branches": remotes,
        "local_branches": locals_,
        "fetched": fetch,
    }


@app.get("/api/git/status")
async def git_status(session: SessionDep, fetch: bool = Query(default=False)):
    return await run_in_threadpool(git_snapshot, session, fetch=fetch)


def ensure_clean(snapshot: dict[str, Any]) -> None:
    if snapshot["dirty"]:
        raise SshOperationError(
            "Remote repository has source changes or unknown untracked files. Commit, stash, or remove them before switching/updating. Known production databases, media and backups are preserved automatically.",
        )


def verify_remote_branch(snapshot: dict[str, Any], branch: str) -> None:
    if branch not in {item["name"] for item in snapshot["remote_branches"]}:
        raise SshOperationError("The selected branch is not present on origin after fetch")


def switch_branch(session: LocalSession, branch: str) -> dict[str, Any]:
    connection = connection_for(session)
    sid = session.session_id
    repo = shlex.quote(connection.repo_path)
    snapshot = git_snapshot(session, fetch=True)
    ensure_clean(snapshot)
    verify_remote_branch(snapshot, branch)
    ensure_target_does_not_track_runtime(session, branch)
    branch_q = shlex.quote(branch)
    local_ref_q = shlex.quote("refs/heads/" + branch)
    exists = ssh.run(sid, f"git -C {repo} show-ref --verify --quiet {local_ref_q}", timeout=30)
    if exists["exit_code"] == 0:
        run_checked(sid, f"git -C {repo} switch {branch_q}", "Switch branch")
    else:
        run_checked(
            sid,
            f"git -C {repo} switch --track -c {branch_q} {shlex.quote('origin/' + branch)}",
            "Create tracking branch",
        )
    audit("branch_switched", session, branch=branch)
    return git_snapshot(session)


@app.post("/api/git/switch")
async def git_switch(body: BranchRequest, session: MutationSessionDep):
    return await run_in_threadpool(switch_branch, session, body.branch)


def update_current_branch(session: LocalSession) -> dict[str, Any]:
    connection = connection_for(session)
    sid = session.session_id
    repo = shlex.quote(connection.repo_path)
    snapshot = git_snapshot(session, fetch=True)
    ensure_clean(snapshot)
    branch = snapshot["current_branch"]
    if branch == "(detached)":
        raise SshOperationError("Cannot update a detached HEAD; switch to a branch first")
    verify_remote_branch(snapshot, branch)
    ensure_target_does_not_track_runtime(session, branch)
    remote_ref = shlex.quote("origin/" + branch)

    local_is_ancestor = ssh.run(
        sid,
        f"git -C {repo} merge-base --is-ancestor HEAD {remote_ref}",
        timeout=30,
    )
    if local_is_ancestor["exit_code"] not in {0, 1}:
        checked(local_is_ancestor, "Compare local and origin history")

    recovery_ref = None
    relation = "fast_forward"
    if local_is_ancestor["exit_code"] == 0:
        run_checked(
            sid,
            f"git -C {repo} merge --ff-only {remote_ref}",
            "Fast-forward update",
            timeout=120,
        )
    else:
        remote_is_ancestor = ssh.run(
            sid,
            f"git -C {repo} merge-base --is-ancestor {remote_ref} HEAD",
            timeout=30,
        )
        if remote_is_ancestor["exit_code"] not in {0, 1}:
            checked(remote_is_ancestor, "Compare origin and local history")
        relation = "local_ahead" if remote_is_ancestor["exit_code"] == 0 else "diverged"

        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        short_sha = snapshot["commit"]["short_sha"] or snapshot["commit"]["sha"][:12]
        recovery_ref = f"refs/plantd-admin/backups/{branch}/{timestamp}-{short_sha}"
        recovery_ref_q = shlex.quote(recovery_ref)
        branch_q = shlex.quote(branch)

        # Preserve the exact local commit before moving the deployment branch.
        # The following detach means the checked-out branch can be moved without
        # reset --hard; target runtime paths were collision-checked above.
        run_checked(
            sid,
            f"git -C {repo} update-ref {recovery_ref_q} HEAD",
            "Create local-history recovery ref",
        )
        run_checked(
            sid,
            f"git -C {repo} switch --detach {remote_ref}",
            "Check out origin deployment commit",
            timeout=120,
        )
        run_checked(
            sid,
            f"git -C {repo} branch --force {branch_q} {remote_ref}",
            "Align local deployment branch",
        )
        run_checked(
            sid,
            f"git -C {repo} switch {branch_q}",
            "Restore deployment branch checkout",
            timeout=120,
        )

    audit(
        "branch_updated",
        session,
        branch=branch,
        relation=relation,
        recovery_ref=recovery_ref,
    )
    result = git_snapshot(session)
    result["synchronization"] = {
        "mode": "fast_forward" if recovery_ref is None else "aligned_to_origin",
        "previous_relation": relation,
        "recovery_ref": recovery_ref,
    }
    return result


@app.post("/api/git/update")
async def git_update(session: MutationSessionDep):
    return await run_in_threadpool(update_current_branch, session)


def restart_server(session: LocalSession) -> dict[str, Any]:
    connection = connection_for(session)
    sid = session.session_id
    repo = shlex.quote(connection.repo_path)
    if connection.deployment_method == "docker_compose":
        compose_path = shlex.quote(f"{connection.repo_path}/{connection.compose_file}")
        run_checked(sid, f"docker compose -f {compose_path} config --quiet", "Docker Compose validation")
        restart_result = run_checked(
            sid,
            f"docker compose -f {compose_path} up -d --build --remove-orphans",
            "Docker deployment",
            timeout=600,
        )
    elif connection.deployment_method == "systemd":
        server_venv_python = shlex.quote(f"{connection.repo_path}/server/venv/bin/python")
        root_venv_python = shlex.quote(f"{connection.repo_path}/.venv/bin/python")
        requirements = shlex.quote(f"{connection.repo_path}/server/requirements.txt")
        run_checked(
            sid,
            f"if test -x {server_venv_python}; then python={server_venv_python}; "
            f"elif test -x {root_venv_python}; then python={root_venv_python}; "
            "else echo 'PlantDiseases virtualenv Python was not found' >&2; exit 1; fi; "
            f"\"$python\" -m pip install --disable-pip-version-check -r {requirements}",
            "systemd dependency update",
            timeout=600,
        )
        restart_result = run_checked(
            sid,
            f"sudo -n systemctl restart {shlex.quote(connection.service_name)}",
            "systemd restart",
            timeout=120,
        )
    else:
        restart_result = run_checked(
            sid,
            f"cd {repo} && {connection.custom_restart_command}",
            "Custom restart",
            timeout=600,
        )

    health_script = (
        "import json,sys,time,urllib.request\n"
        f"url={connection.health_url!r}\n"
        "deadline=time.time()+90\nlast=''\n"
        "while time.time()<deadline:\n"
        " try:\n"
        "  with urllib.request.urlopen(url,timeout=4) as response:\n"
        "   body=response.read(65536).decode('utf-8','replace')\n"
        "   if response.status==200:\n"
        "    print(body);sys.exit(0)\n"
        " except Exception as exc:last=str(exc)\n"
        " time.sleep(2)\n"
        "print(last,file=sys.stderr);sys.exit(1)\n"
    )
    health_result = checked(
        ssh.run(
            sid,
            f"{shlex.quote(connection.remote_python)} -",
            timeout=100,
            stdin_data=health_script,
        ),
        "Health check",
    )
    try:
        health = json.loads(health_result["stdout"])
    except json.JSONDecodeError:
        health = {"raw": health_result["stdout"].strip()}
    audit("server_restarted", session, method=connection.deployment_method)
    return {
        "ok": True,
        "method": connection.deployment_method,
        "restart": restart_result,
        "health": health,
    }


@app.post("/api/ops/restart")
async def restart(body: RestartRequest, session: MutationSessionDep):
    if not body.confirm:
        raise HTTPException(status_code=409, detail="Restart confirmation is required")
    if not operation_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Another deployment operation is running")
    try:
        return await run_in_threadpool(restart_server, session)
    finally:
        operation_lock.release()


def deploy(session: LocalSession, body: DeployRequest) -> dict[str, Any]:
    before = git_snapshot(session, fetch=True)
    ensure_clean(before)
    verify_remote_branch(before, body.branch)
    backup = None
    if body.create_backup:
        backup = database_request(session, "backup_all")
    switched = switch_branch(session, body.branch)
    updated = update_current_branch(session)
    restarted = restart_server(session) if body.restart else None
    audit(
        "branch_deployed",
        session,
        branch=body.branch,
        from_sha=before["commit"]["sha"],
        to_sha=updated["commit"]["sha"],
        restarted=body.restart,
    )
    return {
        "ok": True,
        "before": before,
        "switched": switched,
        "after": updated,
        "backup": backup,
        "restart": restarted,
    }


@app.post("/api/ops/deploy")
async def deploy_endpoint(body: DeployRequest, session: MutationSessionDep):
    if not operation_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Another deployment operation is running")
    try:
        return await run_in_threadpool(deploy, session, body)
    finally:
        operation_lock.release()


@app.post("/api/console/run")
async def console_run(body: ConsoleRequest, session: MutationSessionDep):
    connection = connection_for(session)
    command = f"cd {shlex.quote(connection.repo_path)} && {body.command}"
    result = await run_in_threadpool(
        ssh.run,
        session.session_id,
        command,
        timeout=body.timeout_seconds,
    )
    audit(
        "console_command",
        session,
        command_sha256=hashlib.sha256(body.command.encode("utf-8")).hexdigest(),
        exit_code=result["exit_code"],
    )
    return {"ok": result["exit_code"] == 0, **result}


@app.get("/api/service/status")
async def service_status(session: SessionDep):
    connection = connection_for(session)
    sid = session.session_id
    if connection.deployment_method == "systemd":
        result = await run_in_threadpool(
            ssh.run,
            sid,
            f"systemctl is-active {shlex.quote(connection.service_name)}",
            timeout=20,
        )
    elif connection.deployment_method == "docker_compose":
        compose_path = shlex.quote(f"{connection.repo_path}/{connection.compose_file}")
        result = await run_in_threadpool(
            ssh.run,
            sid,
            f"docker compose -f {compose_path} ps --status running --quiet",
            timeout=30,
        )
    else:
        return {"ok": True, "state": "unknown", "detail": "Custom restart mode has no status command"}
    state = "running" if result["exit_code"] == 0 and result["stdout"].strip() else "stopped"
    if connection.deployment_method == "systemd" and result["stdout"].strip() == "active":
        state = "running"
    return {"ok": result["exit_code"] == 0, "state": state, "detail": result["stdout"].strip()}


def bootstrap_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/bootstrap?token={sessions.bootstrap_token}"
