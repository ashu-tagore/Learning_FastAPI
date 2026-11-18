// Atlas's ready-made web interface. Plain JavaScript, no build step.
// It only talks to the Atlas API, on the same origin, with fetch() and one WebSocket.

// Tokens live in sessionStorage: they disappear when the tab closes. Any script running
// on this page could read them, which is why the page loads no third-party code (lesson 4.7).
const tokens = {
  get access() { return sessionStorage.getItem("atlas.access"); },
  get refresh() { return sessionStorage.getItem("atlas.refresh"); },
  save(pair) {
    sessionStorage.setItem("atlas.access", pair.access_token);
    sessionStorage.setItem("atlas.refresh", pair.refresh_token);
  },
  clear() {
    sessionStorage.removeItem("atlas.access");
    sessionStorage.removeItem("atlas.refresh");
  },
};

const $ = (selector) => document.querySelector(selector);

// Every API call goes through here: it adds the token, and on a 401 it tries one refresh.
async function api(path, options = {}, retried = false) {
  const headers = { ...(options.headers || {}) };
  if (tokens.access) headers.Authorization = `Bearer ${tokens.access}`;
  const response = await fetch(path, { ...options, headers });

  const mayRefresh = response.status === 401 && !retried && tokens.refresh;
  if (mayRefresh && (await refreshTokens())) return api(path, options, true);
  if (response.status === 204) return null;

  const body = await response.json();
  if (!response.ok) throw new Error(body.error ? body.error.message : `HTTP ${response.status}`);
  return body;
}

async function refreshTokens() {
  const response = await fetch("/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: tokens.refresh }),
  });
  if (!response.ok) {
    tokens.clear();
    return false;
  }
  tokens.save(await response.json());
  return true;
}

function show(message, element = $("#auth-message"), isError = false) {
  element.textContent = message;
  element.classList.toggle("error", isError);
}

// Snippets mark matches with [brackets]; turn them into <mark> without trusting any HTML.
function highlighted(snippet) {
  const fragment = document.createDocumentFragment();
  snippet.split(/(\[[^\]]+\])/).forEach((part) => {
    const isMatch = part.startsWith("[") && part.endsWith("]");
    const node = isMatch ? document.createElement("mark") : document.createTextNode(part);
    if (isMatch) node.textContent = part.slice(1, -1);
    fragment.append(node);
  });
  return fragment;
}

function hitItem(hit) {
  const item = document.createElement("li");
  const location = document.createElement("div");
  location.className = "location";
  location.textContent = `${hit.path}:${hit.start_line}-${hit.end_line}`;
  const snippet = document.createElement("div");
  snippet.className = "snippet";
  snippet.append(highlighted(hit.snippet));
  item.append(location, snippet);
  return item;
}

function issueItem(issue, savedIds) {
  const item = document.createElement("li");
  const link = document.createElement("a");
  link.href = issue.url;
  link.textContent = `#${issue.number} ${issue.title}`;
  link.target = "_blank";
  link.rel = "noopener";
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = `${issue.labels.join(", ")} · opened ${issue.created_at.slice(0, 10)}`;

  const isSaved = savedIds.has(issue.id);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary";
  button.textContent = isSaved ? "Unsave" : "Save";
  button.addEventListener("click", async () => {
    await api(`/issues/${issue.id}/saved`, { method: isSaved ? "DELETE" : "PUT" });
    loadIssues(true);
  });
  item.append(link, " ", button, meta);
  return item;
}

// ---------- Signing in ----------

$("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const action = event.submitter.dataset.action;
  const email = $("#email").value;
  const password = $("#password").value;
  try {
    if (action === "register") {
      await api("/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
    }
    // The login endpoint follows OAuth2: a form with "username" and "password".
    const form = new URLSearchParams({ username: email, password });
    tokens.save(await api("/auth/login", { method: "POST", body: form }));
    await start();
  } catch (error) {
    show(error.message, $("#auth-message"), true);
  }
});

$("#logout").addEventListener("click", async () => {
  if (tokens.refresh) {
    await api("/auth/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: tokens.refresh }),
    }).catch(() => {});
  }
  tokens.clear();
  location.reload();
});

// GitHub sign-in returns here with the tokens in the URL fragment, which no server sees.
function takeTokensFromFragment() {
  const fragment = new URLSearchParams(location.hash.slice(1));
  if (fragment.has("access_token")) {
    tokens.save({
      access_token: fragment.get("access_token"),
      refresh_token: fragment.get("refresh_token"),
    });
    history.replaceState(null, "", location.pathname);
  }
}

// ---------- Tabs ----------

document.querySelectorAll("[role=tab]").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll("[role=tab]").forEach((other) => {
      other.setAttribute("aria-selected", String(other === tab));
    });
    document.querySelectorAll("[data-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.panel !== tab.dataset.tab;
    });
    if (tab.dataset.tab === "issues") loadIssues(true);
    if (tab.dataset.tab === "saved") loadSaved();
  });
});

// ---------- Search (offset pagination) ----------

let searchOffset = 0;

async function runSearch(fromStart) {
  if (fromStart) {
    searchOffset = 0;
    $("#hits").replaceChildren();
  }
  const query = new URLSearchParams({ q: $("#query").value, offset: searchOffset, limit: 10 });
  const page = await api(`/search?${query}`);
  page.items.forEach((hit) => $("#hits").append(hitItem(hit)));
  searchOffset = page.next_offset ?? searchOffset;
  $("#more-hits").hidden = page.next_offset === null;
}

$("#search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  runSearch(true);
});
$("#more-hits").addEventListener("click", () => runSearch(false));

// ---------- Search as you type (WebSocket) ----------

let socket = null;

$("#live").addEventListener("change", (event) => {
  if (!event.target.checked) {
    socket?.close();
    socket = null;
    return;
  }
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${location.host}/search/live`);
  socket.addEventListener("message", (message) => {
    const reply = JSON.parse(message.data);
    const isCurrent = reply.query === $("#query").value.trim();
    if (!isCurrent) return; // an older query's answer arrived late: ignore it
    $("#hits").replaceChildren(...reply.items.map(hitItem));
    $("#more-hits").hidden = true;
  });
});

$("#query").addEventListener("input", () => {
  const isOpen = socket && socket.readyState === WebSocket.OPEN;
  if (isOpen) socket.send($("#query").value);
});

// ---------- Issues (cursor pagination) ----------

let issuesCursor = null;
// Two loads can overlap (open the tab, then change the label). Only the newest may draw.
let issuesLoad = 0;

async function savedIssueIds() {
  const saved = await api("/users/me/saved-issues");
  return new Set(saved.map((issue) => issue.id));
}

async function loadIssues(fromStart) {
  const thisLoad = ++issuesLoad;
  if (fromStart) issuesCursor = null;
  const query = new URLSearchParams({ limit: 20 });
  const label = $("#label").value.trim();
  if (label) query.set("label", label);
  if (issuesCursor) query.set("cursor", issuesCursor);

  const [page, savedIds] = await Promise.all([api(`/issues?${query}`), savedIssueIds()]);
  const isStale = thisLoad !== issuesLoad;
  if (isStale) return;
  const items = page.items.map((issue) => issueItem(issue, savedIds));
  if (fromStart) $("#issues").replaceChildren(...items);
  else $("#issues").append(...items);
  issuesCursor = page.next_cursor;
  $("#more-issues").hidden = page.next_cursor === null;
}

$("#label").addEventListener("change", () => loadIssues(true));
$("#more-issues").addEventListener("click", () => loadIssues(false));

async function loadSaved() {
  const saved = await api("/users/me/saved-issues");
  const ids = new Set(saved.map((issue) => issue.id));
  $("#saved").replaceChildren(...saved.map((issue) => issueItem(issue, ids)));
  if (saved.length === 0) $("#saved").textContent = "Nothing saved yet.";
}

// ---------- Profile ----------

$("#profile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await api("/users/me", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ skills_profile: $("#skills").value }),
  });
  show("Saved.", $("#profile-message"));
});

// ---------- Start ----------

async function start() {
  takeTokensFromFragment();
  if (!tokens.access) return;
  try {
    const me = await api("/users/me");
    $("#who").textContent = me.email || `@${me.github_login}`;
    $("#skills").value = me.skills_profile;
    $("#signin").hidden = true;
    $("#app").hidden = false;
    $("#account").hidden = false;
  } catch {
    tokens.clear();
  }
}

start();
