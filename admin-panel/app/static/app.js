"use strict";

const state = {
  csrf: "",
  connection: null,
  fingerprint: "",
  users: [],
  git: null,
  selectedUserId: null,
  confirmAction: null,
};

const viewMeta = {
  dashboard: ["Overview", "Dashboard"],
  users: ["Data administration", "Users"],
  deploy: ["Operations", "Deployments"],
  console: ["Remote access", "SSH console"],
  settings: ["Configuration", "Connection"],
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (method !== "GET" && method !== "HEAD") headers["X-CSRF-Token"] = state.csrf;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : { detail: await response.text() };
  if (!response.ok) {
    const detail = typeof payload.detail === "string"
      ? payload.detail
      : payload.detail?.message || payload.error?.message || `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return payload;
}

function toast(title, message = "", type = "success") {
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  const text = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = title;
  text.appendChild(strong);
  if (message) {
    const span = document.createElement("span");
    span.textContent = message;
    text.appendChild(span);
  }
  node.appendChild(text);
  $("#toast-region").appendChild(node);
  window.setTimeout(() => node.remove(), 5200);
}

function loading(show, message = "Working…") {
  $("#loading-message").textContent = message;
  $("#loading-overlay").classList.toggle("hidden", !show);
}

function navigate(view) {
  if (!viewMeta[view]) return;
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  $$(".view").forEach((panel) => panel.classList.toggle("active", panel.dataset.viewPanel === view));
  $("#page-eyebrow").textContent = viewMeta[view][0];
  $("#page-title").textContent = viewMeta[view][1];
  window.history.replaceState(null, "", `#${view}`);
  if (state.connection) {
    if (view === "users" && state.users.length === 0) loadUsers();
    if (view === "deploy" && !state.git) loadGit(false);
  }
}

function renderConnection() {
  const connected = Boolean(state.connection);
  const pill = $("#connection-pill");
  pill.classList.toggle("connected", connected);
  $("strong", pill).textContent = connected ? state.connection.host : "Not connected";
  $("div span", pill).textContent = connected
    ? `${state.connection.username} · ${state.connection.deployment_method.replaceAll("_", " ")}`
    : "Configure SSH";

  const topChip = $("#top-connection-chip");
  topChip.className = `chip ${connected ? "success" : "neutral"}`;
  topChip.innerHTML = `<span class="status-dot"></span>${connected ? escapeHtml(state.connection.host) : "Offline"}`;

  const settingsStatus = $("#settings-status");
  settingsStatus.className = `chip ${connected ? "success" : "neutral"}`;
  settingsStatus.innerHTML = `<span class="status-dot"></span>${connected ? "Connected" : "Disconnected"}`;
  $("#disconnect-host").classList.toggle("hidden", !connected);

  $("#hero-title").textContent = connected ? `Connected to ${state.connection.host}` : "Connect your PlantDiseases server";
  $("#hero-subtitle").textContent = connected
    ? `${state.connection.repo_path} · credentials remain only in this running process.`
    : "Use a verified SSH connection to inspect data and deploy updates safely.";
  $("#hero-action").textContent = connected ? "Open deployments" : "Configure SSH";
  $("#hero-action").dataset.go = connected ? "deploy" : "settings";
  $("#terminal-host").textContent = connected
    ? `${state.connection.username}@${state.connection.host}:${state.connection.repo_path}`
    : "not-connected:~/plantdiseases";

  const summary = $("#deploy-config-summary");
  if (connected) {
    const strongs = $$(`strong`, summary);
    strongs[0].textContent = state.connection.deployment_method.replaceAll("_", " ");
    strongs[1].textContent = state.connection.health_url;
    strongs[2].textContent = state.connection.data_dir;
  }
}

function formValue(form, name) {
  const node = form.elements.namedItem(name);
  return node ? String(node.value || "").trim() : "";
}

function buildConnectionPayload() {
  const connectionForm = $("#connection-form");
  const deployForm = $("#deployment-form");
  const nullable = (value) => value || null;
  return {
    host: formValue(connectionForm, "host"),
    port: Number(formValue(connectionForm, "port") || 22),
    username: formValue(connectionForm, "username"),
    password: nullable(formValue(connectionForm, "password")),
    key_path: nullable(formValue(connectionForm, "key_path")),
    expected_fingerprint: formValue(connectionForm, "expected_fingerprint"),
    repo_path: formValue(deployForm, "repo_path"),
    data_dir: formValue(deployForm, "data_dir"),
    deployment_method: formValue(deployForm, "deployment_method"),
    service_name: formValue(deployForm, "service_name") || "plantdiseases",
    compose_file: formValue(deployForm, "compose_file") || "server/docker-compose.yml",
    custom_restart_command: nullable(formValue(deployForm, "custom_restart_command")),
    health_url: formValue(deployForm, "health_url"),
  };
}

function saveProfile(payload) {
  const safe = { ...payload };
  delete safe.password;
  delete safe.custom_restart_command;
  localStorage.setItem("plantd-admin-profile", JSON.stringify(safe));
}

function restoreProfile() {
  let profile;
  try { profile = JSON.parse(localStorage.getItem("plantd-admin-profile") || "null"); } catch { return; }
  if (!profile) return;
  const forms = [$("#connection-form"), $("#deployment-form")];
  Object.entries(profile).forEach(([name, value]) => {
    for (const form of forms) {
      const node = form.elements.namedItem(name);
      if (node && value !== null && name !== "password") node.value = value;
    }
  });
  if (profile.expected_fingerprint) {
    state.fingerprint = profile.expected_fingerprint;
    showFingerprint(profile.expected_fingerprint, "Previously trusted fingerprint");
  }
  updateMethodVisibility();
}

function showFingerprint(fingerprint, title = "Verify this host key") {
  const box = $("#fingerprint-box");
  box.classList.add("verified");
  $("strong", box).textContent = title;
  $("code", box).textContent = fingerprint;
  $("#connection-form").elements.namedItem("expected_fingerprint").value = fingerprint;
  state.fingerprint = fingerprint;
}

function clearFingerprint() {
  state.fingerprint = "";
  const box = $("#fingerprint-box");
  box.classList.remove("verified");
  $("strong", box).textContent = "Host key not checked";
  $("code", box).textContent = "Probe the server and verify its fingerprint before connecting.";
  $("#connection-form").elements.namedItem("expected_fingerprint").value = "";
}

async function probeHost() {
  const form = $("#connection-form");
  const host = formValue(form, "host");
  const port = Number(formValue(form, "port") || 22);
  if (!host) return toast("Host required", "Enter the SSH hostname or IP address.", "error");
  loading(true, "Reading SSH host key…");
  try {
    const result = await api("/api/connection/probe", { method: "POST", body: { host, port } });
    showFingerprint(result.fingerprint, `${result.algorithm} host key`);
    toast("Host key received", "Compare the SHA256 fingerprint with the value shown by your server provider.");
  } catch (error) {
    toast("Could not probe SSH", error.message, "error");
  } finally {
    loading(false);
  }
}

async function connectHost(event) {
  event.preventDefault();
  const payload = buildConnectionPayload();
  if (!payload.expected_fingerprint) {
    return toast("Check the host key first", "Probe and verify the SSH fingerprint before connecting.", "error");
  }
  loading(true, "Connecting securely…");
  try {
    const result = await api("/api/connection/connect", { method: "POST", body: payload });
    state.connection = result.connection;
    saveProfile(payload);
    $("#connection-form").elements.namedItem("password").value = "";
    state.users = [];
    state.git = null;
    renderConnection();
    navigate("dashboard");
    toast("SSH connected", `${result.connection.username}@${result.connection.host}`);
    await refreshDashboard();
  } catch (error) {
    toast("SSH connection failed", error.message, "error");
  } finally {
    loading(false);
  }
}

async function disconnectHost() {
  try {
    await api("/api/connection/disconnect", { method: "POST", body: {} });
    state.connection = null;
    state.users = [];
    state.git = null;
    renderConnection();
    resetDashboard();
    toast("Disconnected", "The in-memory SSH password has been cleared.");
  } catch (error) {
    toast("Disconnect failed", error.message, "error");
  }
}

function updateMethodVisibility() {
  const method = formValue($("#deployment-form"), "deployment_method") || "docker_compose";
  $$(`[data-method]`).forEach((node) => node.classList.toggle("hidden", node.dataset.method !== method));
}

function normalizeUser(raw) {
  const profile = raw.profile || {};
  const counts = raw.counts || raw.source_counts || raw.data_counts || raw.stats || {};
  const id = raw.user_id || raw.user_pseudo_id || raw.id || "unknown";
  const lastSeen = Number(raw.last_seen_at ?? raw.last_seen ?? raw.latest_activity ?? 0) || 0;
  const firstSeen = Number(raw.first_seen_at ?? raw.first_seen ?? 0) || 0;
  return {
    ...raw,
    id,
    counts,
    lastSeen,
    firstSeen,
    active: Boolean(raw.active ?? raw.is_active),
    nickname: raw.nickname || raw.latest_nickname || profile.nickname || "",
    locale: raw.locale || raw.latest_locale || profile.locale || "",
    platform: raw.platform || raw.latest_platform || profile.platform || "",
    appVersion: raw.app_version || raw.latest_app_version || profile.app_version || "",
    modelVersion: raw.model_version || raw.latest_model_version || profile.model_version || "",
    country: raw.country || raw.latest_country || profile.country || "",
    city: raw.city || raw.region || raw.latest_city || raw.latest_region || profile.city || profile.region || "",
    manufacturer: raw.manufacturer || raw.device_manufacturer || profile.device_manufacturer || profile.manufacturer || "",
    deviceModel: raw.device_model || raw.phone_model || profile.device_model || "",
    androidApi: raw.android_api || raw.android_sdk || profile.android_api || profile.android_sdk || "",
    activitySource: raw.activity_source || raw.last_activity_source || "persisted event",
  };
}

function countStored(user) {
  const values = Object.values(user.counts || {}).filter((value) => Number.isFinite(Number(value)));
  return values.reduce((sum, value) => sum + Number(value), 0);
}

function userLabel(user) {
  return user.nickname || `Install ${String(user.id).slice(0, 8)}`;
}

function initials(user) {
  const label = user.nickname || String(user.id).replaceAll("-", "");
  return label.slice(0, 2).toUpperCase();
}

function formatDate(timestamp) {
  if (!timestamp) return "Never recorded";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(timestamp * 1000));
}

function formatRelative(timestamp) {
  if (!timestamp) return "No timestamp";
  const seconds = Math.round(timestamp - Date.now() / 1000);
  const absolute = Math.abs(seconds);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (absolute < 60) return formatter.format(seconds, "second");
  if (absolute < 3600) return formatter.format(Math.round(seconds / 60), "minute");
  if (absolute < 86400) return formatter.format(Math.round(seconds / 3600), "hour");
  return formatter.format(Math.round(seconds / 86400), "day");
}

function renderDashboardUsers() {
  const target = $("#dashboard-users");
  if (!state.connection) {
    target.innerHTML = '<div class="empty-inline">Connect to load users.</div>';
    return;
  }
  const users = [...state.users].sort((a, b) => b.lastSeen - a.lastSeen).slice(0, 5);
  if (!users.length) {
    target.innerHTML = '<div class="empty-inline">No pseudonymous user records were found.</div>';
    return;
  }
  target.innerHTML = users.map((user) => `
    <div class="user-row">
      <span class="avatar">${escapeHtml(initials(user))}</span>
      <div class="user-row-main"><strong>${escapeHtml(userLabel(user))}</strong><span>${escapeHtml(user.id)} · ${escapeHtml(formatRelative(user.lastSeen))}</span></div>
      <span class="chip ${user.active ? "success" : "neutral"}">${user.active ? "Recent" : "Stored"}</span>
    </div>`).join("");
}

function renderUsers() {
  const query = $("#user-search").value.trim().toLowerCase();
  const activeOnly = $("#active-only").checked;
  const users = state.users.filter((user) => {
    if (activeOnly && !user.active) return false;
    if (!query) return true;
    return [user.id, user.nickname, user.country, user.city, user.appVersion, user.deviceModel]
      .some((value) => String(value || "").toLowerCase().includes(query));
  });
  $("#user-result-count").textContent = `${users.length} install${users.length === 1 ? "" : "s"}`;
  const target = $("#users-table");
  if (!state.connection) {
    target.innerHTML = '<tr><td colspan="7" class="empty-cell">Connect to a server to inspect user data.</td></tr>';
    return;
  }
  if (!users.length) {
    target.innerHTML = '<tr><td colspan="7" class="empty-cell">No matching anonymous installs.</td></tr>';
    return;
  }
  target.innerHTML = users.map((user) => {
    const device = [user.manufacturer, user.deviceModel].filter(Boolean).join(" ") || "Not collected";
    const app = [user.platform, user.appVersion && `v${user.appVersion}`, user.androidApi && `API ${user.androidApi}`].filter(Boolean).join(" · ") || "Unknown app";
    const location = [user.city, user.country].filter(Boolean).join(", ") || "Not shared";
    const storeCount = countStored(user);
    return `<tr data-user-id="${escapeHtml(user.id)}">
      <td><span class="chip ${user.active ? "success" : "neutral"}"><span class="status-dot"></span>${user.active ? "Recent" : "Stored"}</span></td>
      <td class="id-cell"><code title="${escapeHtml(user.id)}">${escapeHtml(user.id)}</code><small>${escapeHtml(user.nickname || "No nickname")}</small></td>
      <td class="stacked"><strong>${escapeHtml(device)}</strong><span>${escapeHtml(app)}</span></td>
      <td class="stacked"><strong>${escapeHtml(location)}</strong><span>${escapeHtml(user.locale || "Locale unknown")}</span></td>
      <td class="stacked"><strong>${escapeHtml(formatRelative(user.lastSeen))}</strong><span>${escapeHtml(user.activitySource)}</span></td>
      <td class="stacked"><strong>${storeCount} rows</strong><span>${escapeHtml(Object.entries(user.counts).filter(([, value]) => Number(value)).map(([key, value]) => `${key}: ${value}`).slice(0, 3).join(" · ") || "No row counts")}</span></td>
      <td><button class="row-action" type="button" aria-label="View user"><svg viewBox="0 0 24 24"><path d="m9.29 6.71 1.42-1.42 6.7 6.71-6.7 6.71-1.42-1.42L14.59 12 9.29 6.71Z"/></svg></button></td>
    </tr>`;
  }).join("");
  $$(`tr[data-user-id]`, target).forEach((row) => row.addEventListener("click", () => openUser(row.dataset.userId)));
}

async function loadUsers(options = {}) {
  if (!state.connection) return renderUsers();
  const minutes = Number($("#activity-window").value || 15);
  if (options.overlay) loading(true, "Reading remote databases…");
  try {
    const result = await api(`/api/users?active_window_minutes=${minutes}`);
    const rows = result.users || result.items || result.data || [];
    state.users = rows.map(normalizeUser).sort((a, b) => b.lastSeen - a.lastSeen);
    const active = state.users.filter((user) => user.active).length;
    $("#metric-users").textContent = String(state.users.length);
    $("#metric-active").textContent = String(active);
    $("#nav-user-count").textContent = String(state.users.length);
    renderUsers();
    renderDashboardUsers();
  } catch (error) {
    toast("Could not load users", error.message, "error");
  } finally {
    if (options.overlay) loading(false);
  }
}

function detailValue(user, label, value) {
  return `<div class="detail-stat"><span>${escapeHtml(label)}</span><strong title="${escapeHtml(value || "—")}">${escapeHtml(value || "—")}</strong></div>`;
}

async function openUser(userId) {
  state.selectedUserId = userId;
  const known = state.users.find((user) => user.id === userId) || normalizeUser({ user_id: userId });
  $("#user-dialog-title").textContent = userLabel(known);
  $("#user-detail-content").innerHTML = '<div class="empty-inline">Loading details…</div>';
  $("#user-dialog").showModal();
  try {
    const result = await api(`/api/users/${encodeURIComponent(userId)}`);
    const raw = result.user || result.detail || result.data || result;
    const user = normalizeUser({ ...known, ...raw });
    const content = $("#user-detail-content");
    content.innerHTML = `
      <div class="detail-hero"><span class="avatar">${escapeHtml(initials(user))}</span><div><code>${escapeHtml(user.id)}</code><span>${escapeHtml(user.active ? "Recent server activity" : "Historical server record")} · ${escapeHtml(formatDate(user.lastSeen))}</span></div></div>
      <div class="detail-grid">
        ${detailValue(user, "Phone model", [user.manufacturer, user.deviceModel].filter(Boolean).join(" ") || "Not collected")}
        ${detailValue(user, "App", [user.platform, user.appVersion && `v${user.appVersion}`].filter(Boolean).join(" · "))}
        ${detailValue(user, "Locale", user.locale)}
        ${detailValue(user, "Location", [user.city, user.country].filter(Boolean).join(", "))}
        ${detailValue(user, "First seen", formatDate(user.firstSeen))}
        ${detailValue(user, "Last activity", formatDate(user.lastSeen))}
      </div>
      <pre class="detail-json"></pre>`;
    $(".detail-json", content).textContent = JSON.stringify(result, null, 2);
  } catch (error) {
    $("#user-detail-content").innerHTML = `<div class="notice danger"><div><strong>Could not load details</strong><span>${escapeHtml(error.message)}</span></div></div>`;
  }
}

function openDeleteDialog() {
  if (!state.selectedUserId) return;
  $("#delete-confirm-input").value = "";
  $("#confirm-delete-user").disabled = true;
  $("#user-dialog").close();
  $("#delete-dialog").showModal();
  $("#delete-confirm-input").focus();
}

async function deleteSelectedUser() {
  const userId = state.selectedUserId;
  if (!userId || $("#delete-confirm-input").value !== userId) return;
  $("#delete-dialog").close();
  loading(true, "Backing up and deleting user data…");
  try {
    const result = await api(`/api/users/${encodeURIComponent(userId)}`, {
      method: "DELETE",
      body: { confirm_user_id: userId, create_backup: true },
    });
    state.users = state.users.filter((user) => user.id !== userId);
    renderUsers();
    renderDashboardUsers();
    $("#metric-users").textContent = String(state.users.length);
    $("#nav-user-count").textContent = String(state.users.length);
    const backup = result.backup_dir || result.backup?.backup_dir || "backup created";
    toast("User data deleted", `Remote backup: ${backup}`);
  } catch (error) {
    toast("Deletion failed", error.message, "error");
  } finally {
    loading(false);
  }
}

function renderGit(snapshot) {
  state.git = snapshot;
  $("#metric-branch").textContent = snapshot.current_branch || "—";
  $("#metric-commit").textContent = snapshot.commit?.short_sha
    ? `${snapshot.commit.short_sha} · ${snapshot.commit.subject}`
    : "Commit unavailable";
  $("#deploy-current-branch").textContent = snapshot.current_branch || "—";
  $("#deploy-current-commit").textContent = snapshot.commit?.short_sha
    ? `${snapshot.commit.short_sha} · ${snapshot.commit.subject}`
    : "Commit unavailable";
  const tree = $("#tree-status");
  tree.className = `chip ${snapshot.dirty ? "warning" : "success"}`;
  tree.textContent = snapshot.dirty ? "Dirty tree" : "Clean tree";
  const select = $("#branch-select");
  const currentValue = select.value;
  select.innerHTML = (snapshot.remote_branches || []).map((branch) =>
    `<option value="${escapeHtml(branch.name)}">${escapeHtml(branch.name)} · ${escapeHtml(branch.short_sha)}</option>`
  ).join("") || '<option value="">No origin branches found</option>';
  if ([...select.options].some((option) => option.value === currentValue)) select.value = currentValue;
  else if ([...select.options].some((option) => option.value === snapshot.current_branch)) select.value = snapshot.current_branch;
  $("#repo-summary").innerHTML = `
    <div class="summary-row"><span>Remote</span><strong>${escapeHtml(state.connection?.host || "—")}</strong></div>
    <div class="summary-row"><span>Working tree</span><strong>${snapshot.dirty ? `${snapshot.dirty_files?.length || 1} changed paths` : "Clean"}</strong></div>
    <div class="summary-row"><span>Last commit</span><strong title="${escapeHtml(snapshot.commit?.subject)}">${escapeHtml(snapshot.commit?.short_sha || "—")} · ${escapeHtml(snapshot.commit?.subject || "")}</strong></div>`;
}

async function loadGit(fetch = false, options = {}) {
  if (!state.connection) return;
  if (options.overlay) loading(true, fetch ? "Fetching GitHub branches…" : "Reading repository…");
  try {
    const snapshot = await api(`/api/git/status?fetch=${fetch ? "true" : "false"}`);
    renderGit(snapshot);
  } catch (error) {
    toast("Could not inspect Git", error.message, "error");
  } finally {
    if (options.overlay) loading(false);
  }
}

async function loadService() {
  if (!state.connection) return;
  try {
    const result = await api("/api/service/status");
    const running = result.state === "running";
    $("#metric-service").textContent = running ? "Running" : result.state || "Unknown";
    $("#metric-service-detail").textContent = state.connection.deployment_method.replaceAll("_", " ");
    const chip = $("#deploy-service-chip");
    chip.className = `chip ${running ? "success" : "warning"}`;
    chip.innerHTML = `<span class="status-dot"></span>${escapeHtml(running ? "Running" : result.state || "Unknown")}`;
  } catch (error) {
    $("#metric-service").textContent = "Unavailable";
    $("#metric-service-detail").textContent = error.message;
  }
}

async function refreshDashboard() {
  if (!state.connection) return resetDashboard();
  await Promise.allSettled([loadUsers(), loadGit(false), loadService()]);
}

function resetDashboard() {
  $("#metric-users").textContent = "—";
  $("#metric-active").textContent = "—";
  $("#metric-branch").textContent = "—";
  $("#metric-commit").textContent = "No repository loaded";
  $("#metric-service").textContent = "—";
  $("#metric-service-detail").textContent = "Not checked";
  $("#nav-user-count").textContent = "—";
  renderDashboardUsers();
  renderUsers();
}

function confirmOperation(title, body, action, buttonLabel = "Continue") {
  state.confirmAction = action;
  $("#confirm-title").textContent = title;
  $("#confirm-body").textContent = body;
  $("#confirm-action").textContent = buttonLabel;
  $("#confirm-dialog").showModal();
}

async function switchSelectedBranch() {
  const branch = $("#branch-select").value;
  if (!branch) return toast("Select a branch", "Fetch origin and choose a branch first.", "error");
  confirmOperation(
    `Switch to ${branch}?`,
    "The panel will fetch origin and refuse the operation if the remote worktree is dirty. It will not restart the server.",
    async () => {
      loading(true, `Switching to ${branch}…`);
      try {
        const result = await api("/api/git/switch", { method: "POST", body: { branch } });
        renderGit(result);
        $("#operation-output").textContent = JSON.stringify(result, null, 2);
        toast("Branch switched", branch);
      } catch (error) { toast("Branch switch failed", error.message, "error"); }
      finally { loading(false); }
    },
    "Switch branch",
  );
}

async function deploySelectedBranch() {
  const branch = $("#branch-select").value;
  if (!branch) return toast("Select a branch", "Fetch origin and choose a branch first.", "error");
  confirmOperation(
    `Deploy ${branch}?`,
    "This creates online SQLite backups, fast-forwards from origin, rebuilds/restarts the configured service, and waits for its health check.",
    async () => {
      loading(true, `Deploying ${branch}…`);
      try {
        const result = await api("/api/ops/deploy", {
          method: "POST",
          body: { branch, restart: true, create_backup: true },
        });
        if (result.after) renderGit(result.after);
        $("#operation-output").textContent = JSON.stringify(result, null, 2);
        toast("Deployment healthy", `${branch} is running at ${result.after?.commit?.short_sha || "the latest commit"}.`);
        await loadService();
      } catch (error) {
        $("#operation-output").textContent = `Deployment failed\n${error.message}`;
        toast("Deployment failed", error.message, "error");
      } finally { loading(false); }
    },
    "Deploy & restart",
  );
}

async function restartOnly() {
  confirmOperation(
    "Restart the server?",
    "No Git files will change. The configured runtime will restart and the panel will wait for the health endpoint.",
    async () => {
      loading(true, "Restarting PlantDiseases server…");
      try {
        const result = await api("/api/ops/restart", { method: "POST", body: { confirm: true } });
        $("#operation-output").textContent = JSON.stringify(result, null, 2);
        toast("Server restarted", "The health endpoint is responding.");
        await loadService();
      } catch (error) {
        $("#operation-output").textContent = `Restart failed\n${error.message}`;
        toast("Restart failed", error.message, "error");
      } finally { loading(false); }
    },
    "Restart server",
  );
}

function appendConsole(text) {
  const output = $("#terminal-output");
  output.textContent += text;
  output.scrollTop = output.scrollHeight;
}

async function runConsole(command) {
  command = String(command || "").trim();
  if (!command) return;
  if (!state.connection) return toast("Connect SSH first", "Open Connection settings and authenticate.", "error");
  appendConsole(`\n$ ${command}\n`);
  $("#console-command").value = "";
  try {
    const result = await api("/api/console/run", {
      method: "POST",
      body: { command, timeout_seconds: 60 },
    });
    if (result.stdout) appendConsole(result.stdout);
    if (result.stderr) appendConsole(`\n[stderr]\n${result.stderr}`);
    appendConsole(`\n[exit ${result.exit_code} · ${result.duration_ms} ms${result.stdout_truncated || result.stderr_truncated ? " · output truncated" : ""}]\n`);
  } catch (error) {
    appendConsole(`\n[error] ${error.message}\n`);
    toast("Console command failed", error.message, "error");
  }
}

function initEvents() {
  $$(".nav-item").forEach((item) => item.addEventListener("click", () => navigate(item.dataset.view)));
  document.addEventListener("click", (event) => {
    const go = event.target.closest("[data-go]");
    if (go) navigate(go.dataset.go);
    const closer = event.target.closest("[data-close-dialog]");
    if (closer) $(`#${closer.dataset.closeDialog}`).close();
  });

  $("#theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("plantd-admin-theme", next);
  });
  $("#probe-host").addEventListener("click", probeHost);
  $("#connection-form").addEventListener("submit", connectHost);
  $("#deployment-form").addEventListener("submit", (event) => event.preventDefault());
  $("#disconnect-host").addEventListener("click", disconnectHost);
  $("#deployment-method").addEventListener("change", updateMethodVisibility);
  ["host", "port"].forEach((name) => $("#connection-form").elements.namedItem(name).addEventListener("input", clearFingerprint));

  $("#dashboard-refresh").addEventListener("click", () => refreshDashboard());
  $("#refresh-users").addEventListener("click", () => loadUsers({ overlay: true }));
  $("#activity-window").addEventListener("change", () => loadUsers({ overlay: true }));
  $("#user-search").addEventListener("input", renderUsers);
  $("#active-only").addEventListener("change", renderUsers);
  $("#open-delete-user").addEventListener("click", openDeleteDialog);
  $("#delete-confirm-input").addEventListener("input", () => {
    $("#confirm-delete-user").disabled = $("#delete-confirm-input").value !== state.selectedUserId;
  });
  $("#confirm-delete-user").addEventListener("click", deleteSelectedUser);

  $("#fetch-branches").addEventListener("click", () => loadGit(true, { overlay: true }));
  $("#switch-branch").addEventListener("click", switchSelectedBranch);
  $("#deploy-branch").addEventListener("click", deploySelectedBranch);
  $("#restart-server").addEventListener("click", restartOnly);
  $("#clear-operation").addEventListener("click", () => { $("#operation-output").textContent = "No deployment operation has run in this browser session."; });
  $("#confirm-action").addEventListener("click", async () => {
    const action = state.confirmAction;
    state.confirmAction = null;
    $("#confirm-dialog").close();
    if (action) await action();
  });

  $("#console-form").addEventListener("submit", (event) => { event.preventDefault(); runConsole($("#console-command").value); });
  $("#console-command").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      runConsole(event.currentTarget.value);
    }
  });
  $$("[data-command]").forEach((button) => button.addEventListener("click", () => runConsole(button.dataset.command)));
  $("#clear-console").addEventListener("click", () => { $("#terminal-output").textContent = "PlantDiseases SSH console\n"; });
}

async function boot() {
  const theme = localStorage.getItem("plantd-admin-theme")
    || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = theme;
  initEvents();
  restoreProfile();
  try {
    const session = await api("/api/session");
    state.csrf = session.csrf_token;
    state.connection = session.connection || null;
    renderConnection();
    const requestedView = window.location.hash.slice(1);
    navigate(viewMeta[requestedView] ? requestedView : "dashboard");
    if (state.connection) refreshDashboard();
  } catch (error) {
    toast("Admin session unavailable", error.message, "error");
  }
}

document.addEventListener("DOMContentLoaded", boot);
