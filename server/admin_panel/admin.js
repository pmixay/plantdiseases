// PlantDiseases desktop moderation panel. A client of the existing APIs only:
// Yandex ID sign-in in the browser, the account service's exchange/refresh/
// logout, then /api/community/admin/... with the account access token.
// Every permission check is the server's. Member content is only ever set as
// text nodes, never parsed as markup, and the session lives in this tab's
// sessionStorage.
"use strict";

(() => {
  const API = "/api";
  const YANDEX_AUTHORIZE = "https://oauth.yandex.ru/authorize";
  const KEY_SESSION = "pd_admin_session";
  const KEY_INSTALL = "pd_admin_installation";
  const KEY_STATE = "pd_admin_oauth_state";
  const KEY_LANG = "pd_admin_lang";
  const REFRESH_MARGIN_MS = 60 * 1000;

  const REASONS = [
    "spam", "harassment", "dangerous_advice", "off_topic",
    "inappropriate_image", "privacy", "duplicate", "other_rules",
  ];
  const POST_ACTIONS = ["remove", "restore", "lock", "unlock", "pin", "unpin", "move"];
  const QUEUE_KINDS = ["reports", "hidden", "appeals", "rewards", "label_review"];
  const SANCTIONS = ["warn", "mute", "unmute", "ban", "unban"];

  const TEXT = {
    en: {
      title: "PlantDiseases moderation",
      intro: "Sign in with the Yandex ID account you use in the app. Only staff roles see anything here.",
      sign_in: "Sign in with Yandex ID",
      sign_out: "Sign out",
      not_configured: "The panel is not set up: ADMIN_PANEL_YANDEX_CLIENT_ID is empty in the server config.",
      signing_in: "Signing in...",
      bad_state: "Sign-in was refused: the reply did not match this tab. Try again.",
      no_account: "This Yandex ID has no PlantDiseases account yet. Sign in once in the app first.",
      no_roles: "This account has no staff role.",
      signed_out: "Signed out.",
      session_ended: "The session ended. Sign in again.",
      loading: "Loading...",
      done: "Done.",
      error: "Refused",
      tab_queue: "Queue",
      tab_member: "Member",
      tab_audit: "Audit log",
      tab_roles: "Roles",
      kind_reports: "Reports",
      kind_hidden: "Hidden",
      kind_appeals: "Appeals",
      kind_rewards: "Frozen discounts",
      kind_label_review: "Label review",
      refresh: "Refresh",
      empty: "Nothing here.",
      open_post: "Open post",
      open_member: "Member",
      dismiss: "Dismiss reports",
      uphold: "Uphold",
      overturn: "Overturn",
      unfreeze: "Clear the freeze",
      freeze: "Freeze discount",
      reports: "reports",
      back: "Back",
      post: "Post",
      answers: "Answers",
      state: "State",
      space: "Space",
      author: "Author",
      action: "Action",
      reason: "Reason",
      note: "Note for the log (optional)",
      apply: "Apply",
      remove: "Remove",
      restore: "Restore",
      verify: "Mark verified",
      unverify: "Clear verified",
      verified: "verified",
      exclude_training: "Exclude from training",
      excluded: "excluded from training",
      consented: "training consent",
      ban_image: "Ban this image",
      photos: "Photos",
      pending_reports: "Pending reports",
      author_token: "Author token",
      look_up: "Look up",
      posts: "Posts",
      removed: "removed",
      sanction: "Sanction",
      hours: "Hours (mute)",
      active_sanction: "Active sanction",
      history: "History",
      remove_avatar: "Remove profile photo",
      ban_avatar: "also refuse the same picture later",
      older: "Older",
      when: "When",
      by: "By",
      target: "Target",
      automatic: "automatic",
      role: "Role",
      scope: "Scope (* or a space)",
      grant: "Grant",
      revoke: "Revoke",
      granted: "Granted",
      confirm: "Are you sure?",
      none: "none",
    },
    ru: {
      title: "Модерация PlantDiseases",
      intro: "Войдите тем Яндекс ID, которым входите в приложении. Здесь что-то видно только участникам с ролью модерации.",
      sign_in: "Войти с Яндекс ID",
      sign_out: "Выйти",
      not_configured: "Панель не настроена: в конфигурации сервера пуст ADMIN_PANEL_YANDEX_CLIENT_ID.",
      signing_in: "Вход...",
      bad_state: "Вход отклонён: ответ не относится к этой вкладке. Попробуйте ещё раз.",
      no_account: "У этого Яндекс ID ещё нет аккаунта PlantDiseases. Сначала войдите в приложении.",
      no_roles: "У этого аккаунта нет роли модерации.",
      signed_out: "Вы вышли.",
      session_ended: "Сеанс завершён. Войдите снова.",
      loading: "Загрузка...",
      done: "Готово.",
      error: "Отказано",
      tab_queue: "Очередь",
      tab_member: "Участник",
      tab_audit: "Журнал",
      tab_roles: "Роли",
      kind_reports: "Жалобы",
      kind_hidden: "Скрытое",
      kind_appeals: "Апелляции",
      kind_rewards: "Замороженные скидки",
      kind_label_review: "Проверка меток",
      refresh: "Обновить",
      empty: "Пусто.",
      open_post: "Открыть пост",
      open_member: "Участник",
      dismiss: "Отклонить жалобы",
      uphold: "Оставить решение",
      overturn: "Отменить решение",
      unfreeze: "Снять заморозку",
      freeze: "Заморозить скидку",
      reports: "жалоб",
      back: "Назад",
      post: "Пост",
      answers: "Ответы",
      state: "Состояние",
      space: "Раздел",
      author: "Автор",
      action: "Действие",
      reason: "Причина",
      note: "Заметка для журнала (необязательно)",
      apply: "Применить",
      remove: "Удалить",
      restore: "Восстановить",
      verify: "Отметить как проверенный",
      unverify: "Снять отметку",
      verified: "проверен",
      exclude_training: "Исключить из обучения",
      excluded: "исключён из обучения",
      consented: "согласие на обучение",
      ban_image: "Запретить это фото",
      photos: "Фото",
      pending_reports: "Открытые жалобы",
      author_token: "Токен автора",
      look_up: "Найти",
      posts: "Посты",
      removed: "удалено",
      sanction: "Санкция",
      hours: "Часы (для mute)",
      active_sanction: "Действующая санкция",
      history: "История",
      remove_avatar: "Удалить фото профиля",
      ban_avatar: "и запретить это фото в будущем",
      older: "Раньше",
      when: "Когда",
      by: "Кто",
      target: "Цель",
      automatic: "автоматически",
      role: "Роль",
      scope: "Область (* или раздел)",
      grant: "Выдать",
      revoke: "Отозвать",
      granted: "Выдана",
      confirm: "Вы уверены?",
      none: "нет",
    },
  };

  let lang = loadLang();
  let config = null;
  let roles = [];
  let refreshing = null;

  // ── small helpers ─────────────────────────────────────────────────────

  function t(key) {
    return (TEXT[lang] && TEXT[lang][key]) || TEXT.en[key] || key;
  }

  function loadLang() {
    try {
      const saved = localStorage.getItem(KEY_LANG);
      if (saved === "en" || saved === "ru") return saved;
    } catch (_) { /* storage blocked */ }
    return (navigator.language || "ru").toLowerCase().startsWith("ru") ? "ru" : "en";
  }

  /** Build an element. Strings and numbers become text nodes, never HTML. */
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (name === "onclick") node.addEventListener("click", value);
      else if (name === "onchange") node.addEventListener("change", value);
      else if (name === "class") node.className = value;
      else node.setAttribute(name, value === true ? "" : String(value));
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function select(options, current, labelOf) {
    return el("select", {}, options.map((value) =>
      el("option", { value, selected: value === current }, labelOf ? labelOf(value) : value)));
  }

  function when(seconds) {
    if (!seconds) return "";
    return new Date(seconds * 1000).toLocaleString(lang === "ru" ? "ru-RU" : "en-GB");
  }

  function status(message, isError) {
    const node = document.getElementById("status");
    node.textContent = message || "";
    node.classList.toggle("error", Boolean(isError));
  }

  function view(...children) {
    const node = document.getElementById("view");
    node.replaceChildren(...children.flat().filter(Boolean));
  }

  function storage(key, value) {
    try {
      if (value === undefined) return sessionStorage.getItem(key);
      if (value === null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, value);
    } catch (_) { /* storage blocked: the session lasts until reload */ }
    return null;
  }

  function randomHex(bytes) {
    const values = crypto.getRandomValues(new Uint8Array(bytes));
    return Array.from(values, (b) => b.toString(16).padStart(2, "0")).join("");
  }

  function installationId() {
    let id = storage(KEY_INSTALL);
    if (!id) {
      id = crypto.randomUUID();
      storage(KEY_INSTALL, id);
    }
    return id;
  }

  // ── session ───────────────────────────────────────────────────────────

  function session() {
    try { return JSON.parse(storage(KEY_SESSION) || "null"); } catch (_) { return null; }
  }

  function saveSession(reply) {
    storage(KEY_SESSION, JSON.stringify({
      access: reply.access_token,
      refresh: reply.refresh_token,
      expiresAt: Date.now() + reply.expires_in * 1000,
      nickname: reply.profile && reply.profile.nickname,
    }));
  }

  async function account(path, body) {
    const response = await fetch(config.account_base_path + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      credentials: "omit",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw requestError(response.status, data);
    return data;
  }

  function requestError(code, data) {
    const detail = data && data.detail;
    const text = typeof detail === "string" ? detail : (detail && detail.code) || String(code);
    const error = new Error(text);
    error.status = code;
    return error;
  }

  async function refreshSession() {
    if (!refreshing) {
      refreshing = (async () => {
        const current = session();
        if (!current) throw requestError(401, { detail: "auth_required" });
        const reply = await account("/v1/auth/refresh", {
          refresh_token: current.refresh,
          rotation_id: crypto.randomUUID(),
        });
        saveSession(reply);
      })().finally(() => { refreshing = null; });
    }
    return refreshing;
  }

  async function api(path, options = {}) {
    let current = session();
    if (!current) throw requestError(401, { detail: "auth_required" });
    if (Date.now() > current.expiresAt - REFRESH_MARGIN_MS) {
      await refreshSession();
      current = session();
    }
    const send = (token) => fetch(API + path, {
      method: options.method || "GET",
      headers: Object.assign(
        { Authorization: "Bearer " + token },
        options.body ? { "Content-Type": "application/json" } : {},
      ),
      body: options.body ? JSON.stringify(options.body) : undefined,
      credentials: "omit",
    });
    let response = await send(current.access);
    if (response.status === 401) {
      try {
        await refreshSession();
      } catch (_) {
        endSession(t("session_ended"));
        throw requestError(401, { detail: "auth_required" });
      }
      response = await send(session().access);
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw requestError(response.status, data);
    return data;
  }

  /** Run a write, report the result, then redraw. */
  async function act(promiseFactory, after, confirmFirst) {
    if (confirmFirst && !window.confirm(t("confirm"))) return;
    status(t("loading"));
    try {
      await promiseFactory();
      status(t("done"));
      if (after) await after();
    } catch (error) {
      status(t("error") + ": " + error.message, true);
    }
  }

  async function show(loader) {
    status(t("loading"));
    try {
      await loader();
      status("");
    } catch (error) {
      status(t("error") + ": " + error.message, true);
    }
  }

  // ── sign-in ───────────────────────────────────────────────────────────

  function startSignIn() {
    const state = randomHex(16);
    storage(KEY_STATE, state);
    const query = new URLSearchParams({
      response_type: "token",
      client_id: config.yandex_client_id,
      redirect_uri: window.location.origin + "/admin/",
      state,
    });
    window.location.assign(YANDEX_AUTHORIZE + "?" + query.toString());
  }

  /** The Yandex reply arrives in the URL fragment; take it and wipe the URL. */
  async function finishSignIn() {
    const hash = window.location.hash.replace(/^#/, "");
    if (!hash) return false;
    const params = new URLSearchParams(hash);
    window.history.replaceState(null, "", window.location.pathname);
    const token = params.get("access_token");
    if (!token) return false;
    const expected = storage(KEY_STATE);
    storage(KEY_STATE, null);
    if (!expected || params.get("state") !== expected) {
      status(t("bad_state"), true);
      return false;
    }
    status(t("signing_in"));
    const reply = await account("/v1/auth/yandex/exchange", {
      provider_access_token: token,
      installation_id: installationId(),
    });
    if (reply.status !== "authenticated") {
      status(t("no_account"), true);
      return false;
    }
    saveSession(reply);
    status("");
    return true;
  }

  async function signOut() {
    const current = session();
    if (current) {
      try { await account("/v1/auth/logout", { refresh_token: current.refresh }); } catch (_) { /* best effort */ }
    }
    endSession(t("signed_out"));
  }

  function endSession(message) {
    storage(KEY_SESSION, null);
    roles = [];
    render();
    status(message);
  }

  // ── staff roles ───────────────────────────────────────────────────────

  function isAdmin() {
    return roles.some((r) => r.role === "admin");
  }

  async function loadRoles() {
    const me = await api("/community/admin/me");
    roles = me.roles || [];
  }

  // ── views ─────────────────────────────────────────────────────────────

  const TABS = [
    { key: "queue", label: "tab_queue", open: () => queueView("reports") },
    { key: "member", label: "tab_member", open: () => memberView("") },
    { key: "audit", label: "tab_audit", open: () => auditView(null), admin: true },
    { key: "roles", label: "tab_roles", open: () => rolesView(), admin: true },
  ];

  function renderTabs(active) {
    const tabs = document.getElementById("tabs");
    tabs.replaceChildren(...TABS.filter((tab) => !tab.admin || isAdmin()).map((tab) =>
      el("button", {
        type: "button",
        class: tab.key === active ? "active" : "",
        onclick: () => { renderTabs(tab.key); tab.open(); },
      }, t(tab.label))));
  }

  function authorLink(author) {
    if (!author || !author.author_token) return null;
    return el("button", {
      type: "button",
      class: "ghost",
      onclick: () => { renderTabs("member"); memberView(author.author_token); },
    }, (author.nickname || "?") + " (" + t("open_member") + ")");
  }

  function postButton(postId) {
    if (!postId) return null;
    return el("button", { type: "button", onclick: () => postView(postId) }, t("open_post"));
  }

  // Queue

  async function queueView(kind) {
    const kinds = select(QUEUE_KINDS, kind, (k) => t("kind_" + k));
    kinds.addEventListener("change", () => queueView(kinds.value));
    const header = el("div", { class: "row" }, kinds,
      el("button", { type: "button", class: "ghost", onclick: () => queueView(kind) }, t("refresh")));
    view(header);
    await show(async () => {
      const queue = await api("/community/admin/queue?kind=" + encodeURIComponent(kind));
      const items = queue.items || [];
      view(header, items.length ? items.map((item) => queueCard(kind, item)) : el("p", {}, t("empty")));
    });
  }

  function queueCard(kind, item) {
    const reload = () => queueView(kind);
    if (kind === "appeals") {
      return el("div", { class: "card" },
        el("h3", {}, (item.action || "") + (item.reason_code ? ", " + item.reason_code : "")),
        el("div", { class: "meta" }, when(item.created_at)),
        item.text ? el("div", { class: "body" }, item.text) : null,
        el("div", { class: "row" },
          authorLink({ nickname: item.nickname, author_token: item.author_token }),
          postButton(item.post_id),
          el("button", { type: "button", onclick: () => act(() => resolveAppeal(item.appeal_id, "uphold"), reload) }, t("uphold")),
          el("button", { type: "button", class: "danger", onclick: () => act(() => resolveAppeal(item.appeal_id, "overturn"), reload, true) }, t("overturn"))));
    }
    if (kind === "rewards") {
      return el("div", { class: "card" },
        el("h3", {}, item.nickname || "?"),
        el("div", { class: "meta" }, [item.reason, when(item.created_at)].filter(Boolean).join(", ")),
        el("div", { class: "row" },
          authorLink({ nickname: item.nickname, author_token: item.author_token }),
          el("button", { type: "button", onclick: () => act(() => setFrozen(item.author_token, false), reload) }, t("unfreeze"))));
    }
    if (kind === "label_review") {
      return el("div", { class: "card" },
        el("h3", {}, item.title || item.post_id || ""),
        el("div", { class: "meta" },
          "AI: " + (item.ai_disease || "?") +
          (item.ai_confidence != null ? " " + Math.round(item.ai_confidence * 100) + "%" : "") +
          ", community: " + (item.community_label || t("none")) +
          ", not helpful: " + (item.ai_not_helpful || 0)),
        el("div", { class: "row" }, postButton(item.post_id)));
    }
    return el("div", { class: "card" + (item.state && item.state !== "visible" ? " removed" : "") },
      el("h3", {}, (item.target_type || "") + " " + (item.report_count ? item.report_count + " " + t("reports") : "")),
      el("div", { class: "meta" },
        [item.space, item.state, (item.reasons || []).join(", ")].filter(Boolean).join(", ")),
      item.excerpt ? el("div", { class: "body" }, item.excerpt) : null,
      el("div", { class: "row" },
        authorLink(item.author),
        postButton(item.post_id),
        kind === "reports" && item.target_type && item.target_id
          ? el("button", { type: "button", class: "ghost", onclick: () => act(() => api("/community/admin/reports/dismiss", {
            method: "POST", body: { target_type: item.target_type, target_id: item.target_id },
          }), reload) }, t("dismiss"))
          : null));
  }

  function resolveAppeal(appealId, decision) {
    return api("/community/admin/appeals/" + encodeURIComponent(appealId) + "/resolve", {
      method: "POST", body: { decision },
    });
  }

  function setFrozen(authorToken, frozen) {
    return api("/community/admin/actors/" + encodeURIComponent(authorToken) + "/rewards", {
      method: "POST", body: { frozen },
    });
  }

  // Post

  async function postView(postId) {
    await show(async () => {
      const [post, spaces] = await Promise.all([
        api("/community/admin/posts/" + encodeURIComponent(postId)),
        fetch(API + "/community/spaces", { credentials: "omit" }).then((r) => r.json()).catch(() => ({ items: [] })),
      ]);
      const reload = () => postView(postId);
      const slugs = (spaces.items || []).map((s) => s.slug);
      view(
        el("div", { class: "row" },
          el("button", { type: "button", class: "ghost", onclick: () => { renderTabs("queue"); queueView("reports"); } }, t("back"))),
        postCard(post, slugs, reload),
        el("h2", {}, t("answers")),
        (post.answers || []).map((answer) => answerCard(answer, reload)));
    });
  }

  function postCard(post, slugs, reload) {
    const action = select(POST_ACTIONS, "remove");
    const reason = select(["", ...REASONS], "spam", (r) => r || t("none"));
    const space = select(slugs, post.space);
    const note = el("textarea", { maxlength: 1000, placeholder: t("note") });
    const flags = [
      post.state, post.locked ? "locked" : null, post.pinned ? "pinned" : null,
      post.training_consent ? t("consented") : null, post.training_excluded ? t("excluded") : null,
    ].filter(Boolean).join(", ");
    return el("div", { class: "card" + (post.state !== "visible" ? " removed" : "") },
      el("h3", {}, post.title || t("post")),
      el("div", { class: "meta" }, [post.kind, post.space, flags, when(post.created_at)].filter(Boolean).join(", ")),
      el("div", { class: "row" }, authorLink(post.author)),
      el("div", { class: "body" }, post.body || ""),
      (post.media || []).length ? el("div", {},
        el("div", { class: "meta" }, t("photos")),
        el("div", { class: "thumbs" }, post.media.map((media) => el("figure", {},
          el("img", { src: API + "/community/media/" + encodeURIComponent(media.media_id), alt: "", loading: "lazy" }),
          isAdmin() ? el("button", { type: "button", class: "danger", onclick: () => act(() => api("/community/admin/images/ban", {
            method: "POST", body: { media_id: media.media_id },
          }), reload, true) }, t("ban_image")) : null)))) : null,
      (post.pending_reports || []).length ? el("div", { class: "meta" },
        t("pending_reports") + ": " + post.pending_reports.map((r) => r.reason + " x" + r.count).join(", ")) : null,
      el("div", { class: "row" },
        el("label", {}, t("action") + " ", action),
        el("label", {}, t("reason") + " ", reason),
        el("label", {}, t("space") + " ", space)),
      note,
      el("div", { class: "row" },
        el("button", {
          type: "button",
          onclick: () => act(() => api("/community/admin/posts/" + encodeURIComponent(post.id) + "/action", {
            method: "POST",
            body: {
              action: action.value,
              reason_code: reason.value || null,
              note: note.value.trim() || null,
              space: action.value === "move" ? space.value : null,
            },
          }), reload, action.value === "remove"),
        }, t("apply")),
        isAdmin() && !post.training_excluded ? el("button", {
          type: "button", class: "ghost",
          onclick: () => act(() => api("/community/admin/posts/" + encodeURIComponent(post.id) + "/exclude-training", {
            method: "POST",
          }), reload, true),
        }, t("exclude_training")) : null));
  }

  function answerCard(answer, reload) {
    const reason = select(REASONS, "spam");
    const base = "/community/admin/answers/" + encodeURIComponent(answer.id);
    return el("div", { class: "card" + (answer.state !== "visible" ? " removed" : "") },
      el("div", { class: "meta" }, [
        answer.is_ai ? "AI" : null, answer.state, answer.diagnosis,
        answer.verified ? t("verified") : null, when(answer.created_at),
      ].filter(Boolean).join(", ")),
      el("div", { class: "row" }, authorLink(answer.author)),
      el("div", { class: "body" }, answer.body || ""),
      answer.is_ai ? null : el("div", { class: "row" },
        el("label", {}, t("reason") + " ", reason),
        answer.state === "visible"
          ? el("button", { type: "button", class: "danger", onclick: () => act(() => api(base + "/action", {
            method: "POST", body: { action: "remove", reason_code: reason.value },
          }), reload, true) }, t("remove"))
          : el("button", { type: "button", onclick: () => act(() => api(base + "/action", {
            method: "POST", body: { action: "restore" },
          }), reload) }, t("restore")),
        el("button", { type: "button", class: "ghost", onclick: () => act(() => api(base + "/verify", {
          method: "POST", body: { verified: !answer.verified },
        }), reload) }, answer.verified ? t("unverify") : t("verify"))));
  }

  // Member

  async function memberView(authorToken) {
    const input = el("input", { size: 36, value: authorToken, placeholder: t("author_token") });
    const header = el("div", { class: "row" }, input,
      el("button", { type: "button", onclick: () => memberView(input.value.trim()) }, t("look_up")));
    view(header);
    if (!authorToken) return;
    await show(async () => {
      const actor = await api("/community/admin/actors/" + encodeURIComponent(authorToken));
      view(header, memberCard(actor, () => memberView(authorToken)));
    });
  }

  function memberCard(actor, reload) {
    const token = actor.author_token;
    const kind = select(SANCTIONS, "warn");
    const hours = el("input", { type: "number", min: 1, max: 8760, value: 24, size: 6 });
    const reason = select(["", ...REASONS], "", (r) => r || t("none"));
    const note = el("textarea", { maxlength: 1000, placeholder: t("note") });
    const banAvatar = el("input", { type: "checkbox", checked: true });
    const active = actor.active_sanction;
    return el("div", { class: "card" },
      el("h3", {}, actor.nickname || "?"),
      el("div", { class: "meta" }, el("code", {}, token)),
      el("p", {},
        t("posts") + ": " + actor.posts + " (" + t("removed") + " " + actor.posts_removed + "), " +
        t("answers") + ": " + actor.answers + " (" + t("removed") + " " + actor.answers_removed + ")"),
      el("p", {}, t("active_sanction") + ": " +
        (active ? active.kind + (active.until ? " " + when(active.until) : "") : t("none"))),
      (actor.sanctions || []).length ? el("div", {},
        el("div", { class: "meta" }, t("history")),
        el("ul", {}, actor.sanctions.map((s) => el("li", {},
          [s.kind, s.reason_code, when(s.created_at), s.revoked ? "revoked" : null].filter(Boolean).join(", "))))) : null,
      el("div", { class: "row" },
        el("label", {}, t("sanction") + " ", kind),
        el("label", {}, t("hours") + " ", hours),
        el("label", {}, t("reason") + " ", reason)),
      note,
      el("div", { class: "row" },
        el("button", {
          type: "button", class: "danger",
          onclick: () => act(() => api("/community/admin/actors/" + encodeURIComponent(token) + "/sanction", {
            method: "POST",
            body: {
              kind: kind.value,
              reason_code: reason.value || null,
              hours: kind.value === "mute" ? Number(hours.value) : null,
              note: note.value.trim() || null,
            },
          }), reload, true),
        }, t("apply")),
        el("button", {
          type: "button", class: "ghost",
          onclick: () => act(() => api("/community/admin/actors/" + encodeURIComponent(token) + "/avatar/remove", {
            method: "POST", body: { ban_image: banAvatar.checked },
          }), reload, true),
        }, t("remove_avatar")),
        el("label", {}, banAvatar, " " + t("ban_avatar"))),
      isAdmin() ? el("div", { class: "row" },
        el("button", { type: "button", class: "ghost", onclick: () => act(() => setFrozen(token, true), reload, true) }, t("freeze")),
        el("button", { type: "button", class: "ghost", onclick: () => act(() => setFrozen(token, false), reload) }, t("unfreeze"))) : null);
  }

  // Audit

  async function auditView(before) {
    await show(async () => {
      const query = "?limit=100" + (before ? "&before=" + encodeURIComponent(before) : "");
      const audit = await api("/community/admin/audit" + query);
      const rows = (audit.items || []).map((item) => el("tr", {},
        el("td", {}, when(item.created_at)),
        el("td", {}, item.action, item.reason_code ? ", " + item.reason_code : ""),
        el("td", {}, item.target_type || "", item.post_id ? el("div", {}, postButton(item.post_id)) : null),
        el("td", {}, item.automatic ? t("automatic") : el("code", {}, item.moderator_token || "")),
        el("td", {}, item.target_token ? authorLink({ nickname: "", author_token: item.target_token }) : null),
        el("td", { class: "body" }, item.note || "")));
      view(
        el("table", {},
          el("thead", {}, el("tr", {},
            el("th", {}, t("when")), el("th", {}, t("action")), el("th", {}, t("target")),
            el("th", {}, t("by")), el("th", {}, t("author")), el("th", {}, t("note")))),
          el("tbody", {}, rows)),
        audit.next_before ? el("div", { class: "row" },
          el("button", { type: "button", class: "ghost", onclick: () => auditView(audit.next_before) }, t("older"))) : null);
    });
  }

  // Roles

  async function rolesView() {
    await show(async () => {
      const holders = await api("/community/admin/roles");
      const token = el("input", { size: 36, placeholder: t("author_token") });
      const role = select(["moderator", "expert"], "moderator");
      const scope = el("input", { size: 14, value: "*" });
      const change = (grant) => act(() => api("/community/admin/roles", {
        method: "POST",
        body: { author_token: token.value.trim(), role: role.value, scope: scope.value.trim() || "*", grant },
      }), rolesView, !grant);
      view(
        el("table", {},
          el("thead", {}, el("tr", {},
            el("th", {}, t("author")), el("th", {}, t("role")), el("th", {}, t("scope")), el("th", {}, t("granted")))),
          el("tbody", {}, (holders.items || []).map((item) => el("tr", {},
            el("td", {}, authorLink(item)),
            el("td", {}, item.role),
            el("td", {}, item.scope),
            el("td", {}, when(item.granted_at)))))),
        el("div", { class: "card" },
          el("div", { class: "row" }, token,
            el("label", {}, t("role") + " ", role),
            el("label", {}, t("scope") + " ", scope)),
          el("div", { class: "row" },
            el("button", { type: "button", onclick: () => change(true) }, t("grant")),
            el("button", { type: "button", class: "danger", onclick: () => change(false) }, t("revoke")))));
    });
  }

  // ── page ──────────────────────────────────────────────────────────────

  function translateStatic() {
    document.documentElement.lang = lang;
    document.title = t("title");
    document.querySelectorAll("[data-t]").forEach((node) => { node.textContent = t(node.dataset.t); });
    document.getElementById("lang").textContent = lang === "ru" ? "EN" : "RU";
  }

  function render() {
    translateStatic();
    const current = session();
    const signedIn = Boolean(current) && roles.length > 0;
    document.getElementById("signed-out").hidden = Boolean(current);
    document.getElementById("signed-in").hidden = !signedIn;
    document.getElementById("sign-out").hidden = !current;
    document.getElementById("who").textContent = current
      ? (current.nickname || "") + (roles.length ? " (" + roles.map((r) => r.role + ":" + r.scope).join(", ") + ")" : "")
      : "";
    document.getElementById("sign-in").disabled = !(config && config.available);
  }

  async function start() {
    document.getElementById("lang").addEventListener("click", () => {
      lang = lang === "ru" ? "en" : "ru";
      try { localStorage.setItem(KEY_LANG, lang); } catch (_) { /* ignore */ }
      render();
      if (roles.length) { renderTabs("queue"); queueView("reports"); }
    });
    document.getElementById("sign-in").addEventListener("click", startSignIn);
    document.getElementById("sign-out").addEventListener("click", signOut);

    config = await fetch("/admin/config.json", { credentials: "omit" }).then((r) => r.json());
    render();
    if (!config.available) {
      status(t("not_configured"), true);
      return;
    }
    try {
      await finishSignIn();
    } catch (error) {
      status(t("error") + ": " + error.message, true);
    }
    if (!session()) { render(); return; }
    try {
      await loadRoles();
    } catch (error) {
      if (error.status === 401) { endSession(t("session_ended")); return; }
      status(t("error") + ": " + error.message, true);
    }
    render();
    if (!roles.length) { status(t("no_roles"), true); return; }
    renderTabs("queue");
    queueView("reports");
  }

  document.addEventListener("DOMContentLoaded", start);
})();
