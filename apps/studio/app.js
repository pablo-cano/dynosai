const $ = (id) => document.getElementById(id);
const I18N = window.DynosAII18n;
const t = (key, vars = {}) => I18N.t(key, vars);

const storage = {
  theme: "dynosai.studio.theme",
  language: "dynosai.studio.language",
  provider: "dynosai.studio.provider",
  workspace: "dynosai.studio.workspace",
  technical: "dynosai.studio.technical",
  projectParent: "dynosai.studio.projectParent",
  tutorialActive: "dynosai.studio.tutorial.active",
  tutorialStep: "dynosai.studio.tutorial.step",
  tutorialProject: "dynosai.studio.tutorial.project",
  projectPrefs: "dynosai.studio.project",
};

const projectViews = ["project-overview", "new-task", "work", "approvals", "checks", "project-settings", "governance", "diagnostics", "activity"];
const technicalViews = ["governance", "diagnostics", "activity"];
const initializedViews = ["new-task", "work", "approvals", ...technicalViews];
const pageMeta = {
  home: ["hub.eyebrow", "hub.title", "hub.subtitle"],
  "project-overview": ["project.eyebrow", "project.overviewTitle", "project.overviewSubtitle"],
  "new-task": ["new.eyebrow", "new.title", "new.intro"],
  work: ["work.eyebrow", "work.title", "work.intro"],
  approvals: ["approvals.eyebrow", "approvals.title", "approvals.intro"],
  checks: ["checks.eyebrow", "checks.title", "checks.intro"],
  "project-settings": ["projectSettings.eyebrow", "projectSettings.title", "projectSettings.intro"],
  settings: ["settings.eyebrow", "settings.title", "settings.intro"],
  help: ["help.eyebrow", "help.title", "help.intro"],
  governance: ["nav.technical", "governance.title", "governance.intro"],
  diagnostics: ["nav.technical", "diagnostics.title", "diagnostics.introSimple"],
  activity: ["nav.technical", "activity.title", "activity.intro"],
};
const phaseKeys = ["work.timelineUnderstand", "work.timelineSpecify", "work.timelinePlan", "work.timelineImplement", "work.timelineValidate", "work.timelineFinish"];
const phaseIndex = {inbox:0, discovery:0, spec_review:1, plan_review:2, ready:2, implementing:3, code_review:3, validating:4, ready_to_merge:4, done:5};

let health = null;
let overview = null;
let projects = [];
let reviews = [];
let reviewHistory = [];
let events = [];
let executions = [];
let currentView = "home";
let initialNavigationDone = false;
let folderDialogMode = null;
let folderDialogData = null;
let modelRouting = null;
let routingProvider = "codex";
let confirmResolver = null;
let routeDialogState = null;
let preferenceProjectRoot = null;
const expandedExecutionDiagnostics = new Set();
const expandedChangeRequests = new Set();
const reviewDrafts = new Map();
const activityScroll = {};
const diagnosticScroll = {};
let lastWindowScrollY = 0;
let lastTutorialScrollKey = "";
window.addEventListener("scroll", () => {
  lastWindowScrollY = window.scrollY || document.documentElement.scrollTop || 0;
}, {passive: true});

function formatStamp(value) {
  if (!value) return "";
  return String(value).replace("T", " ").replace("Z", "").replace(/\+00:00$/, " UTC").slice(0, 22);
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
}
function boolStored(key, fallback = false) {
  const raw = localStorage.getItem(key);
  return raw == null ? fallback : raw === "true";
}
async function api(path, options = {}) {
  const response = await fetch(path, {headers:{"Content-Type":"application/json"}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
  return data;
}
function showAlert(message, kind = "error") {
  const node = $("alert");
  node.textContent = message;
  node.className = `alert ${kind === "success" ? "success" : ""}`.trim();
  node.classList.remove("hidden");
  clearTimeout(showAlert.timer);
  showAlert.timer = setTimeout(() => node.classList.add("hidden"), 6000);
}
let operationDepth = 0;
function setOperationBusy(active, key = "operation.working") {
  const overlay = $("operation-overlay");
  if (!overlay) return;
  if (active) {
    operationDepth += 1;
    $("operation-title").textContent = t(key);
    $("operation-text").textContent = t("operation.wait");
    overlay.classList.remove("hidden");
    document.body.classList.add("operation-busy");
  } else {
    operationDepth = Math.max(0, operationDepth - 1);
    if (!operationDepth) { overlay.classList.add("hidden"); document.body.classList.remove("operation-busy"); }
  }
}
async function withOperation(key, action) {
  setOperationBusy(true, key);
  try { return await action(); } finally { setOperationBusy(false); }
}
function setBadge(id, value) {
  const node = $(id);
  if (!node) return;
  node.textContent = String(value);
  node.classList.toggle("hidden", !value);
}
function hasProject() { return !!health?.project_selected && !!health?.root && !!overview; }
function updateProjectActionState() {
  const create = $("create-project");
  const open = $("open-project-path");
  if (create) create.disabled = !$("new-project-name").value.trim() || !$("new-project-parent").value.trim();
  if (open) open.disabled = !$("project-path-input").value.trim();
}
function projectPreferenceKey(name) {
  const root = health?.root || "none";
  return `${storage.projectPrefs}.${encodeURIComponent(root)}.${name}`;
}
function projectPreference(name, fallback) {
  if (!health?.root) return fallback;
  return localStorage.getItem(projectPreferenceKey(name)) || fallback;
}
function setProjectPreference(name, value) {
  if (!health?.root) return;
  localStorage.setItem(projectPreferenceKey(name), value);
}
function applyProjectPreferences() {
  if (!health?.root) return;
  const providerValue = projectPreference("provider", localStorage.getItem(storage.provider) || "codex");
  const workspaceValue = projectPreference("workspace", localStorage.getItem(storage.workspace) || "interactive_branch");
  const provider = ["codex", "cursor"].includes(providerValue) ? providerValue : "codex";
  const workspace = ["interactive_branch", "isolated_worktree"].includes(workspaceValue) ? workspaceValue : "interactive_branch";
  setComboValue("project-provider", provider);
  setComboValue("project-workspace", workspace);
  if (preferenceProjectRoot !== health.root || routingProvider !== provider) { routingProvider = provider; preferenceProjectRoot = health.root; }
}

function themePreference() { return localStorage.getItem(storage.theme) || "system"; }
function resolvedTheme(preference = themePreference()) {
  if (preference === "light" || preference === "dark") return preference;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
function applyTheme(preference, persist = true) {
  const normalized = ["system", "light", "dark"].includes(preference) ? preference : "system";
  if (persist) localStorage.setItem(storage.theme, normalized);
  document.documentElement.dataset.themePreference = normalized;
  if (normalized === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = normalized;
  const resolved = resolvedTheme(normalized);
  document.documentElement.style.colorScheme = resolved;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", resolved === "dark" ? "#0b0d12" : "#ffffff");
  $("theme-icon").textContent = {system:"◐", light:"☀", dark:"☾"}[normalized];
  document.querySelectorAll("[data-theme-value]").forEach((button) => button.classList.toggle("active", button.dataset.themeValue === normalized));
}
function cycleTheme() {
  const order = ["system", "light", "dark"];
  const current = themePreference();
  applyTheme(order[(order.indexOf(current) + 1) % order.length]);
}

function comboRoot(name) { return document.querySelector(`[data-combobox="${name}"]`); }
function comboValue(name) { return $(name)?.value || ""; }
function closeCombobox(root) {
  if (!root) return;
  root.querySelector(".combobox-content")?.classList.add("hidden");
  root.querySelector(".combobox-trigger")?.setAttribute("aria-expanded", "false");
}
function closeAllComboboxes(except = null) {
  document.querySelectorAll("[data-combobox]").forEach((root) => { if (root !== except) closeCombobox(root); });
}
function setComboValue(name, value, {emit = false} = {}) {
  const root = comboRoot(name);
  const input = $(name);
  if (!root || !input) return;
  const options = [...root.querySelectorAll(".combobox-option")];
  const option = options.find((node) => node.dataset.value === value) || options[0];
  if (!option) return;
  input.value = option.dataset.value;
  options.forEach((node) => {
    const selected = node === option;
    node.setAttribute("aria-selected", selected ? "true" : "false");
    node.classList.toggle("selected", selected);
  });
  root.querySelector(".combobox-value").textContent = option.querySelector("span:first-child")?.textContent || option.dataset.value;
  if (emit) input.dispatchEvent(new Event("change", {bubbles:true}));
}
function refreshComboboxLabels() {
  document.querySelectorAll("[data-combobox]").forEach((root) => setComboValue(root.dataset.combobox, comboValue(root.dataset.combobox)));
}
function initComboboxes() {
  document.querySelectorAll("[data-combobox]").forEach((root, index) => {
    const trigger = root.querySelector(".combobox-trigger");
    const content = root.querySelector(".combobox-content");
    const options = [...root.querySelectorAll(".combobox-option")];
    const listId = `dynosai-combobox-${index}`;
    if (content) content.id = listId;
    trigger?.setAttribute("aria-controls", listId);
    const open = (focusIndex = -1) => {
      closeAllComboboxes(root);
      content?.classList.remove("hidden");
      trigger?.setAttribute("aria-expanded", "true");
      if (focusIndex >= 0 && options.length) options[Math.min(focusIndex, options.length - 1)].focus();
    };
    const choose = (option) => {
      setComboValue(root.dataset.combobox, option.dataset.value, {emit:true});
      closeCombobox(root);
      trigger?.focus();
    };
    trigger?.addEventListener("click", (event) => {
      event.stopPropagation();
      content?.classList.contains("hidden") ? open() : closeCombobox(root);
    });
    trigger?.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        open(event.key === "ArrowDown" ? 0 : options.length - 1);
      }
    });
    options.forEach((option, optionIndex) => {
      option.addEventListener("click", (event) => { event.stopPropagation(); choose(option); });
      option.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          const delta = event.key === "ArrowDown" ? 1 : -1;
          options[(optionIndex + delta + options.length) % options.length]?.focus();
        } else if (event.key === "Enter" || event.key === " ") {
          event.preventDefault(); choose(option);
        } else if (event.key === "Escape") {
          event.preventDefault(); closeCombobox(root); trigger?.focus();
        }
      });
    });
  });
  document.addEventListener("click", () => closeAllComboboxes());
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!$("folder-dialog")?.classList.contains("hidden")) closeFolderDialog();
    if (!$("new-folder-dialog")?.classList.contains("hidden")) closeNewFolderDialog();
    if (!$("route-dialog")?.classList.contains("hidden")) closeRouteDialog();
    if (!$("confirm-dialog")?.classList.contains("hidden")) closeConfirmDialog(false);
    closeAllComboboxes();
  });
}

function setDialogOpen(id, open) {
  const node = $(id);
  if (!node) return;
  node.classList.toggle("hidden", !open);
  document.body.classList.toggle("dialog-open", open || [...document.querySelectorAll(".dialog-backdrop")].some((item) => item.id !== id && !item.classList.contains("hidden")));
}
function confirmDialog({title, text, confirmLabel, destructive = false}) {
  return new Promise((resolve) => {
    confirmResolver = resolve;
    $("confirm-dialog-title").textContent = title;
    $("confirm-dialog-text").textContent = text;
    const button = $("confirm-dialog-confirm");
    button.textContent = confirmLabel;
    button.classList.toggle("danger-button", destructive);
    button.classList.toggle("primary", !destructive);
    setDialogOpen("confirm-dialog", true);
    setTimeout(() => button.focus(), 20);
  });
}
function closeConfirmDialog(value = false) {
  setDialogOpen("confirm-dialog", false);
  const resolve = confirmResolver;
  confirmResolver = null;
  if (resolve) resolve(value);
}
function openNewFolderDialog() {
  if (!folderDialogData?.path || !folderDialogData?.writable) { showAlert(t("folders.notWritable")); return; }
  $("new-folder-parent").textContent = folderDialogData.path;
  $("new-folder-name").value = "";
  setDialogOpen("new-folder-dialog", true);
  setTimeout(() => $("new-folder-name").focus(), 20);
}
function closeNewFolderDialog() { setDialogOpen("new-folder-dialog", false); }
async function createFolderFromDialog() {
  const name = $("new-folder-name").value.trim();
  if (!name || !folderDialogData?.path) { showAlert(t("folders.folderNameRequired")); return; }
  try {
    await withOperation("operation.createFolder", async () => {
      const result = await api("/api/filesystem/mkdir", {method:"POST", body:JSON.stringify({parent:folderDialogData.path, name})});
      closeNewFolderDialog();
      folderDialogData = result.listing;
      renderFolderDialog();
      showAlert(t("folders.folderCreated", {name}), "success");
    });
  } catch (error) { showAlert(error.message); }
}

function applyPreferences() {
  applyTheme(themePreference(), false);
  const provider = ["codex", "cursor"].includes(localStorage.getItem(storage.provider)) ? localStorage.getItem(storage.provider) : "codex";
  const workspace = ["interactive_branch", "isolated_worktree"].includes(localStorage.getItem(storage.workspace)) ? localStorage.getItem(storage.workspace) : "interactive_branch";
  setComboValue("project-provider", provider);
  setComboValue("project-workspace", workspace);
  setComboValue("language", I18N.language());
  $("technical-toggle").checked = boolStored(storage.technical);
  const savedParent = localStorage.getItem(storage.projectParent);
  if (savedParent) $("new-project-parent").value = savedParent;
}

function pageTitle(view) {
  const meta = pageMeta[view] || pageMeta.home;
  if (projectViews.includes(view) && hasProject()) {
    $("top-context").textContent = overview.project || t("common.project");
    $("top-title").textContent = t(meta[1]);
    $("top-subtitle").textContent = view === "project-overview" ? (health.root || "") : t(meta[2]);
  } else {
    $("top-context").textContent = t(meta[0]);
    $("top-title").textContent = t(meta[1]);
    $("top-subtitle").textContent = t(meta[2]);
  }
}
function navigate(view, {scroll=true}={}) {
  if (!pageMeta[view]) return;
  if (projectViews.includes(view) && !hasProject()) view = "home";
  if (initializedViews.includes(view) && hasProject() && !overview?.initialized) view = "project-overview";
  if (technicalViews.includes(view) && !boolStored(storage.technical)) view = "settings";
  const changed = currentView !== view;
  currentView = view;
  document.querySelectorAll(".nav-item, .brand-button, .project-identity").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  document.querySelectorAll(".view").forEach((item) => item.classList.toggle("active", item.id === view));
  pageTitle(view);
  $("top-projects").classList.toggle("hidden", !hasProject() || view === "home");
  $("top-new-task").classList.toggle("hidden", !hasProject() || !overview?.initialized || view === "new-task");
  if (scroll && changed) window.scrollTo({top:0, behavior:"instant"});
  setTimeout(renderTutorialCoach, 50);
}
function renderShell() {
  const selected = hasProject();
  $("project-nav").classList.toggle("hidden", !selected);
  $("technical-nav").classList.toggle("hidden", !selected || !boolStored(storage.technical));
  if (selected) {
    $("sidebar-project").textContent = overview.project || health.root.split(/[\\/]/).pop();
    $("sidebar-project-path").textContent = t("project.openContext");
    $("project-identity").title = health.root || "";
  } else {
    $("project-identity").title = "";
  }
  $("version").textContent = health?.display_version || health?.version ? `v${health.display_version || health.version}` : "—";
  document.querySelectorAll(".requires-initialized").forEach((item) => {
    const disabled = selected && !overview?.initialized;
    item.disabled = disabled;
    item.setAttribute("aria-disabled", disabled ? "true" : "false");
    item.title = disabled ? t("project.initializeToContinue") : "";
  });
  if (!selected && projectViews.includes(currentView)) navigate("home");
  else if (initializedViews.includes(currentView) && selected && !overview?.initialized) navigate("project-overview");
  else pageTitle(currentView);
}

function renderRecentProjects() {
  const items = projects || [];
  $("recent-projects").innerHTML = items.length ? items.map((item) => {
    const selected = !!item.current;
    return `<div class="project-list-item ${item.exists ? "" : "missing"}"><div class="project-list-copy"><strong>${esc(item.name)}</strong><small>${esc(item.path)}</small></div><div class="project-list-actions">${selected ? `<span class="pill ok">${esc(t("hub.selected"))}</span>` : `<button class="secondary compact" type="button" data-project-open="${esc(item.path)}" ${item.exists ? "" : "disabled"}>${esc(t("common.open"))}</button>`}<button class="ghost compact" type="button" data-project-remove="${esc(item.path)}" ${selected ? "disabled" : ""}>${esc(t("common.remove"))}</button></div></div>`;
  }).join("") : `<div class="empty-state compact-empty"><p>${esc(t("hub.emptyRecent"))}</p></div>`;
}

function setupIcon(state) { return state === "complete" ? "✓" : state === "blocked" ? "!" : state === "ready" ? "→" : "·"; }
function localizedStep(step, data) {
  const detection = data.detection || {};
  const setup = data.setup || {};
  const id = step.id;
  let detail = step.detail || "";
  if (id === "project") detail = detection.exists ? t("setup.projectFound", {stacks:(detection.stacks || []).join(", ") || t("setup.projectFiles")}) : t("setup.projectMissing");
  if (id === "git") {
    if (step.state === "blocked") detail = t("setup.gitDirtyBefore", {count:setup.dirty_count || 0});
    else if (setup.dirty_count) detail = t("setup.gitDirtyAfter", {count:setup.dirty_count});
    else if (detection.has_git) detail = t("setup.gitClean");
    else detail = t("setup.gitCanInit");
  }
  if (id === "dynosai") detail = step.state === "complete" ? t("setup.dynosaiReady") : step.state === "blocked" ? t("setup.dynosaiBlocked") : t("setup.dynosaiCanInit");
  if (id === "checks") detail = setup.pending_checks ? t("setup.checksPending", {count:setup.pending_checks}) : setup.approved_checks ? t("setup.checksApproved", {count:setup.approved_checks}) : t("setup.checksNone");
  return {label:t(`setup.${id}`), detail};
}
function primaryAction(data) {
  const setup = data.setup || {};
  if (reviews.length) return {code:"approvals", title:t("action.approvalsTitle", {count:reviews.length}), text:t("action.approvalsText"), label:t("action.openApprovals")};
  const code = setup.primary_action?.code || "inspect";
  if (code === "clean_git") return {code:"none", title:t("action.cleanGitTitle"), text:t("action.cleanGitText"), label:""};
  if (code === "initialize") return {code:"initialize", title:t("action.initializeTitle"), text:t("action.initializeText"), label:t("action.initialize")};
  if (code === "review_checks") return {code:"checks", title:t("action.reviewChecksTitle"), text:t("action.reviewChecksText"), label:t("action.reviewChecks")};
  if (code === "new_task") return {code:"new-task", title:t("action.newChangeTitle"), text:t("action.newChangeText"), label:t("action.newChange")};
  return {code:"project-overview", title:t("project.readyTitle"), text:t("project.readyText"), label:t("nav.projectOverview")};
}
function renderSetup(data) {
  const setup = data.setup || {};
  $("setup-steps").innerHTML = (setup.steps || []).map((step) => {
    const localized = localizedStep(step, data);
    return `<div class="setup-step ${esc(step.state)}"><span class="setup-step-icon">${setupIcon(step.state)}</span><div><strong>${esc(localized.label)}</strong><small>${esc(localized.detail)}</small></div></div>`;
  }).join("");
  const primary = primaryAction(data);
  const statusClass = setup.ready ? "ok" : setup.primary_action?.code === "clean_git" ? "danger" : "warn";
  $("hero-status-dot").className = `status-circle ${statusClass}`;
  $("hero-kicker").textContent = setup.ready ? t("home.readyKicker") : t("home.finishSetup");
  $("hero-title").textContent = setup.ready ? t("home.readyTitle", {project:data.project || t("common.project")}) : primary.title;
  $("hero-message").textContent = setup.ready ? t("home.readyMessage") : primary.text;
  const action = primary.code === "initialize" ? "initialize" : primary.code;
  $("hero-actions").innerHTML = action !== "none" && primary.label ? `<button id="project-primary-action" class="primary" data-home-action="${esc(action)}">${esc(primary.label)}</button>` : "";
  $("project-summary")?.classList.toggle("hidden", !data.initialized);
}
function validationLabel(name) { const key = `validation.${name}`; return t(key) === key ? name : t(key); }
function validationReason(item) { const key = `validationReason.${item.name}`; return t(key) === key ? (item.reason || "") : t(key); }
function renderChecks(data) {
  const validation = data.validations || {candidates:[], approved:{}};
  const candidates = validation.candidates || [];
  const approved = validation.approved || {};
  if (!candidates.length) $("validation-list").innerHTML = `<div class="empty-state"><h3>${esc(t("checks.noneTitle"))}</h3><p>${esc(t("checks.noneText"))}</p></div>`;
  else $("validation-list").innerHTML = `<div class="validation-list">${candidates.map((item) => {
    const isApproved = !!approved[item.name]?.approved;
    return `<label class="validation-option ${isApproved ? "approved" : ""}"><input type="checkbox" data-validation-name="${esc(item.name)}" ${isApproved ? "checked disabled" : "checked"}/><span><strong>${esc(validationLabel(item.name))}${isApproved ? ` · ${esc(t("common.approved"))}` : ""}</strong><small>${esc(validationReason(item))}</small><code>${esc((item.command || []).join(" "))}</code></span></label>`;
  }).join("")}</div>`;
  $("approve-selected-validations").disabled = !data.initialized || !candidates.some((item) => !approved[item.name]?.approved);
}
function workflowTimeline(state) {
  const current = phaseIndex[state] ?? 0;
  return `<div class="workflow-timeline">${phaseKeys.map((key, index) => `<div class="timeline-step ${index < current || state === "done" ? "done" : index === current ? "active" : ""}">${esc(t(key))}</div>`).join("")}</div>`;
}
function stateLabel(state) { const key = `state.${state}`; return t(key) === key ? state : t(key); }
function executionFor(workId) { return (executions || []).find((item) => item.work_id === workId) || null; }
function executionStatusHtml(item, waiting) {
  if (waiting) return "";
  const run = executionFor(item.id);
  const needsHostContinue = ["validating","code_review","ready_to_merge"].includes(item.state);
  const needsAgentContinue = ["discovery","plan_review","ready","implementing"].includes(item.state);
  const continueBanner = (host) => `<div class="work-runtime failed"><div class="work-runtime-copy"><strong>${esc(t(host ? "execution.finishStep" : "execution.notRunning"))}</strong><small>${esc(t(host ? "execution.finishStepText" : "execution.notRunningText"))}</small></div><button class="secondary compact" type="button" data-execution-retry="${esc(item.id)}">${esc(t(host ? "execution.continueHost" : "execution.continue"))}</button></div>`;
  if (!run) {
    if (needsAgentContinue || needsHostContinue) return continueBanner(needsHostContinue);
    return "";
  }
  if (run.status === "running") return `<div class="work-runtime running"><span class="runtime-spinner" aria-hidden="true"></span><div class="work-runtime-copy"><strong>${esc(t("execution.running"))}</strong><small>${esc(t("execution.runningText", {provider:(run.provider || "").toUpperCase()}))}</small></div></div>`;
  if (["failed","stopped","unavailable"].includes(run.status)) {
    const detail = String(run.stderr_tail || run.stdout_tail || "").trim();
    const message = run.status === "unavailable" ? t("execution.unavailableText") : run.exit_code ? t("execution.failedText", {code:run.exit_code}) : t("execution.retryText");
    const expanded = expandedExecutionDiagnostics.has(String(item.id));
    const diagnostic = detail ? `<div class="runtime-diagnostic"><button class="runtime-diagnostic-toggle" type="button" data-diagnostic-toggle="${esc(item.id)}" aria-expanded="${expanded ? "true" : "false"}"><span class="runtime-diagnostic-chevron" aria-hidden="true">${expanded ? "▾" : "▸"}</span>${esc(t("execution.details"))}</button><div class="runtime-diagnostic-panel ${expanded ? "" : "hidden"}"><pre data-diagnostic-work="${esc(item.id)}">${esc(detail)}</pre></div></div>` : "";
    return `<div class="work-runtime failed"><div class="work-runtime-copy"><strong>${esc(t(run.status === "unavailable" ? "execution.unavailable" : "execution.interrupted"))}</strong><small>${esc(message)}</small>${diagnostic}</div><button class="secondary compact" type="button" data-execution-retry="${esc(item.id)}">${esc(t("execution.retry"))}</button></div>`;
  }
  if (needsAgentContinue || needsHostContinue) return continueBanner(needsHostContinue);
  return "";
}
function executionActivityHtml(item) {
  const run = executionFor(item.id);
  if (!run) return "";
  const provider = String(run.provider || "").toUpperCase();
  const logs = Array.isArray(run.activity_log) && run.activity_log.length
    ? run.activity_log.map((row, index) => ({text:row.text || row, at:row.at || null, kind:"provider", order:index}))
    : (run.activity || []).map((text, index) => ({text, at:null, kind:"provider", order:index}));
  const recentEvents = (events || []).filter((event) => event.work_id === item.id).map((event, index) => {
    const key = `activityEvent.${event.event_type || ""}`;
    const label = t(key) === key ? String(event.event_type || "") : t(key);
    return label ? {text:label, at:event.created_at || null, kind:"workflow", order:index} : null;
  }).filter(Boolean);
  const combined = [...recentEvents, ...logs];
  combined.sort((a, b) => {
    const aAt = a.at ? Date.parse(a.at) : NaN;
    const bAt = b.at ? Date.parse(b.at) : NaN;
    if (!Number.isNaN(aAt) && !Number.isNaN(bAt) && aAt !== bAt) return bAt - aAt;
    if (!Number.isNaN(aAt) !== !Number.isNaN(bAt)) return Number.isNaN(aAt) ? 1 : -1;
    return (b.order || 0) - (a.order || 0);
  });
  const rows = combined.slice(0, 18); // newest first
  const content = rows.length
    ? rows.map((row) => `<div class="agent-activity-line ${row.kind}"><span class="activity-dot" aria-hidden="true"></span><div class="activity-copy">${row.at ? `<time class="activity-time" datetime="${esc(row.at)}">${esc(formatStamp(row.at))}</time>` : ""}<span>${esc(row.text)}</span></div></div>`).join("")
    : `<div class="agent-activity-empty">${esc(run.status === "running" ? t("execution.activityWaiting") : t("execution.activityEmpty"))}</div>`;
  return `<div class="agent-activity"><div class="agent-activity-head"><div><strong>${esc(t("execution.activityTitle"))}</strong><small>${esc(t("execution.activityText"))}</small></div>${provider ? `<span class="pill neutral">${esc(provider)}</span>` : ""}</div><div class="agent-activity-log" data-work-id="${esc(item.id)}">${content}</div></div>`;
}
function teamSlotsHtml(item) {
  const team = item.team;
  const wave = team && team.current_wave;
  if (!wave || !(wave.leases || []).length) return "";
  const kind = wave.kind === "parallel" ? t("work.teamParallel") : t("work.teamSerial");
  const slots = (wave.leases || []).map((lease) => {
    const tasks = (lease.task_ids || []).join(", ");
    const files = (lease.scope_ceiling || lease.files || []).slice(0, 4).join(", ");
    return `<div class="team-slot"><strong>${esc(lease.role || "implementer")}</strong><small>${esc(tasks)}${files ? ` · ${files}` : ""}</small><span class="state-chip">${esc(lease.status || "open")}</span></div>`;
  }).join("");
  return `<div class="team-slots"><div class="team-slots-head"><strong>${esc(t("work.teamTitle"))}</strong><small>${esc(kind)}</small></div><div class="team-slot-grid">${slots}</div><p class="team-slots-note">${esc(t("work.teamNoSpawn"))}</p></div>`;
}
function evalCasesHtml(data) {
  const intel = data.eval_intelligence;
  const cases = (intel && intel.cases) || [];
  if (!cases.length) return "";
  const proposeAllowed = intel && intel.enabled !== false;
  const rows = cases.slice(0, 6).map((item) => {
    const layer = (item.attribution && item.attribution.layer) || "harness";
    const proposed = item.status === "proposed" || item.status === "regressed";
    const action = (!proposeAllowed || proposed) ? "" : `<button class="compact" data-eval-propose="${esc(item.case_id)}">${esc(t("eval.propose"))}</button>`;
    return `<div class="eval-case"><div><strong>${esc(item.case_id)}</strong><small>${esc(layer)} · ${esc(item.summary || "")}</small></div><span class="state-chip">${esc(item.status || "open")}</span>${action}</div>`;
  }).join("");
  return `<div class="eval-cases"><div class="eval-cases-head"><strong>${esc(t("eval.title"))}</strong><small>${esc(String(cases.length))}</small></div>${rows}<p class="eval-cases-note">${esc(t("eval.shadow"))}</p></div>`;
}
function harnessStatusHtml(data) {
  const features = (data.harness && data.harness.features) || [];
  if (!features.length) return "";
  const labels = {context_handles: "harness.contextHandles", team_scheduler: "harness.teamScheduler", eval_intelligence: "harness.evalIntelligence"};
  const rows = features.map((item) => `<div class="harness-chip"><span>${esc(t(labels[item.name] || item.name))}</span><strong>${esc(item.enabled ? t("harness.on") : t("harness.off"))}</strong></div>`).join("");
  return `<div class="harness-status"><div class="harness-status-head"><strong>${esc(t("harness.title"))}</strong></div><div class="harness-chip-row">${rows}</div></div>`;
}
function liveMatrixHtml(data) {
  const report = data.live_matrix || {};
  const cells = report.cells || {};
  const keys = ["codex.greenfield", "codex.brownfield", "cursor.greenfield", "cursor.brownfield"];
  if (!keys.some((key) => cells[key])) return "";
  const statusKey = {not_run: "matrix.notRun", run: "matrix.run", pass: "matrix.pass", fail: "matrix.fail"};
  const rows = keys.map((key) => {
    const [provider, mode] = key.split(".");
    const status = cells[key] || "not_run";
    return `<div class="harness-chip"><span>${esc(provider)} · ${esc(mode)}</span><strong>${esc(t(statusKey[status] || "matrix.notRun"))}</strong></div>`;
  }).join("");
  return `<div class="harness-status"><div class="harness-status-head"><strong>${esc(t("matrix.title"))}</strong></div><div class="harness-chip-row">${rows}</div><p class="provider-caps-note">${esc(t("matrix.note"))}</p></div>`;
}
function providerCapsHtml(data) {
  const report = data.provider_capabilities || {};
  const manifests = report.manifests || [];
  if (!manifests.length) return "";
  const rows = manifests.map((item) => {
    const detail = [item.adapter, item.transport, item.elicitation].filter(Boolean).join(" · ");
    return `<div class="provider-cap"><strong>${esc(item.provider || "")}</strong><small>${esc(detail)}</small></div>`;
  }).join("");
  return `<div class="provider-caps"><div class="provider-caps-head"><strong>${esc(t("caps.title"))}</strong><small>${esc(t("caps.certified"))}</small></div>${rows}<p class="provider-caps-note">${esc(t("caps.note"))}</p></div>`;
}
async function proposeEvalImprovement(caseId) {
  try {
    await withOperation("operation.saveSettings", async () => {
      await api("/api/eval/propose", {method:"POST", body:JSON.stringify({case_id: caseId})});
      await refresh({preserveView:true});
    });
  } catch (error) { showAlert(error.message); }
}
function renderWork(data) {
  captureActivityScroll();
  const work = (data.work || []).filter((item) => item.id !== "PROJECT");
  const active = work.filter((item) => item.state !== "done");
  const recent = work.filter((item) => item.state === "done");
  setBadge("progress-badge", active.length);
  const activeHtml = !active.length ? `<div class="empty-state"><h3>${esc(t("work.noActiveTitle"))}</h3><p>${esc(t("work.noActiveText"))}</p><button class="primary compact" data-view="new-task" ${data.initialized ? "" : "disabled"}>${esc(t("action.newChange"))}</button></div>` : active.map((item) => {
    const waiting = reviews.some((review) => review.work_id === item.id);
    const run = executionFor(item.id);
    const chip = waiting ? t("approvals.waiting") : run?.status === "running" ? t("execution.workingChip") : stateLabel(item.state);
    return `<article class="work-card"><div class="work-card-head"><div><h3>${esc(item.title)}</h3><p>${esc(item.description)}</p></div><span class="state-chip">${esc(chip)}</span></div>${workflowTimeline(item.state)}${teamSlotsHtml(item)}${executionStatusHtml(item, waiting)}${waiting ? `<div class="review-actions"><button class="primary compact" data-view="approvals">${esc(t("action.openApprovals"))}</button></div>` : ""}${executionActivityHtml(item)}</article>`;
  }).join("");
  const recentHtml = recent.length ? `<div class="list">${recent.slice(0,8).map((item) => `<div class="list-item"><strong>${esc(item.title)}</strong><small>${esc(item.description)}</small></div>`).join("")}</div>` : "";
  $("work-list").innerHTML = `${activeHtml}${recentHtml ? `<article class="panel recent-work"><h3>${esc(t("work.recentTitle"))}</h3>${recentHtml}</article>` : ""}`;
  restoreActivityScroll();
}
function captureActivityScroll() {
  document.querySelectorAll(".agent-activity-log[data-work-id]").forEach((el) => {
    activityScroll[el.dataset.workId] = {top: el.scrollTop, pin: el.scrollTop <= 12};
  });
  document.querySelectorAll("pre[data-diagnostic-work]").forEach((el) => {
    diagnosticScroll[el.dataset.diagnosticWork] = el.scrollTop;
  });
}
function restoreActivityScroll() {
  document.querySelectorAll(".agent-activity-log[data-work-id]").forEach((el) => {
    const saved = activityScroll[el.dataset.workId];
    if (!saved || saved.pin) el.scrollTop = 0;
    else el.scrollTop = saved.top;
    el.onscroll = () => {
      activityScroll[el.dataset.workId] = {top: el.scrollTop, pin: el.scrollTop <= 12};
    };
  });
  document.querySelectorAll("pre[data-diagnostic-work]").forEach((el) => {
    const top = diagnosticScroll[el.dataset.diagnosticWork];
    if (top != null) el.scrollTop = top;
  });
}
function reviewTitle(review) {
  if (review.kind === "clarification") return t("approvals.questionTitle");
  if (review.kind === "scope_extension") return t("approvals.scopeTitle");
  return ({spec:t("approvals.specTitle"), plan:t("approvals.planTitle"), code:t("approvals.codeTitle"), merge:t("approvals.mergeTitle")})[review.gate] || t("approvals.reviewRequired");
}
function reviewIntro(review) {
  if (review.kind === "clarification" || review.kind === "scope_extension") return review.message || "";
  const key = `approvals.${review.gate || "review"}Intro`;
  return t(key) === key ? t("approvals.reviewIntro") : t(key);
}
function structuredItem(item, fallback = "") {
  if (typeof item === "string") return {id:"", text:item};
  if (!item || typeof item !== "object") return {id:"", text:fallback};
  return {
    id:String(item.id || item.requirement || ""),
    text:String(item.text || item.title || item.description || item.content || fallback || ""),
  };
}
function reviewItems(titleKey, items, className = "") {
  const normalized = (items || []).map((item) => structuredItem(item)).filter((item) => item.text);
  if (!normalized.length) return "";
  return `<section class="review-section ${className}"><h4>${esc(t(titleKey))}</h4><div class="review-item-list">${normalized.map((item) => `<div class="review-item">${item.id ? `<span class="review-item-id">${esc(item.id)}</span>` : ""}<span>${esc(item.text)}</span></div>`).join("")}</div></section>`;
}
function reviewDetail(review) {
  const detail = review.detail || {};
  if (review.gate === "spec" && detail.spec) {
    return `<div class="review-detail"><section class="review-section"><h4>${esc(t("approvals.objective"))}</h4><p>${esc(detail.spec.objective || "—")}</p></section>${reviewItems("approvals.requirementsTitle", detail.spec.requirements)}${reviewItems("approvals.acceptanceTitle", detail.spec.acceptance_criteria)}</div>`;
  }
  if (review.gate === "plan" && detail.plan) {
    const tasks = (detail.plan.tasks || []).map((task) => ({id:task.id || "", text:[task.title, task.description].filter(Boolean).join(" — ")}));
    const files = (detail.plan.files || []).map((file) => ({id:file.action || "", text:[file.path, file.reason].filter(Boolean).join(" — ")}));
    const risks = (detail.plan.risks || []).map((risk) => ({text:typeof risk === "string" ? risk : (risk.text || risk.description || JSON.stringify(risk))}));
    return `<div class="review-detail"><section class="review-section"><h4>${esc(t("approvals.planSummary"))}</h4><p>${esc(detail.plan.approach || "—")}</p></section>${reviewItems("approvals.tasksTitle", tasks)}${reviewItems("approvals.filesTitle", files)}${reviewItems("approvals.risksTitle", risks)}</div>`;
  }
  if (["code","merge"].includes(review.gate) && (detail.quality || detail.diff || detail.integrity)) {
    const findings = (detail.quality?.findings || []).map((finding) => ({id:finding.code || finding.severity || "", text:finding.message || finding.text || ""}));
    const tasks = (detail.tasks || []).map((task) => ({id:task.id || "", text:[task.title, task.state].filter(Boolean).join(" — ")}));
    const files = (detail.diff?.files || []).map((file) => ({text:file}));
    const validations = (detail.validations || []).map((item) => ({id:String(item.exit_code ?? ""), text:[item.command, item.status].filter(Boolean).join(" — ")}));
    const integrity = (detail.integrity?.weak_evidence || []).map((item) => ({id:item.requirement_id || "", text:(item.reasons || []).join(", ")}));
    const score = detail.quality ? `${t("common.quality")} ${esc(detail.quality.score)}/100 · ${esc(detail.quality.blocking)} ${esc(t("common.blocking"))}` : "";
    const spec = detail.spec ? `${reviewItems("approvals.requirementsTitle", detail.spec.requirements)}${reviewItems("approvals.acceptanceTitle", detail.spec.acceptance_criteria)}` : "";
    const diffText = detail.diff?.text ? `<details class="review-diff"><summary>${esc(t("approvals.diffTitle"))}</summary><pre>${esc(detail.diff.text)}</pre></details>` : "";
    const policy = detail.execution_policy || {};
    const policyText = policy.profile
      ? `${esc(policy.profile)} · ${esc(policy.network || "")} · ${esc(policy.enforcement || "decision_only")}. ${esc(t("approvals.policyOsNote"))}`
      : "";
    const policyBlock = policyText ? `<section class="review-section review-policy"><h4>${esc(t("approvals.policyTitle"))}</h4><p>${policyText}</p></section>` : "";
    return `<div class="review-detail">${spec}<section class="review-section"><h4>${esc(t("approvals.evidenceSummary"))}</h4><p>${score || "—"}</p></section>${reviewItems("approvals.tasksTitle", tasks)}${reviewItems("approvals.filesTitle", files)}${reviewItems("approvals.validationsTitle", validations)}${reviewItems("approvals.integrityTitle", integrity)}${reviewItems("approvals.findingsTitle", findings)}${policyBlock}${diffText}</div>`;
  }
  return "";
}
function historyStatus(item) {
  if (item.source === "studio_auto") return t("approvals.historyAuto");
  if (item.status === "declined") return t("approvals.historyDeclined");
  if (item.status === "cancelled") return t("approvals.historyCancelled");
  return t("approvals.historyManual");
}
function renderReviewHistory() {
  const node = $("review-history");
  if (!node) return;
  const rows = reviewHistory || [];
  const body = rows.length
    ? `<div class="review-history-list">${rows.map((item) => {
      const when = formatStamp(item.resolved_at || item.created_at);
      const auto = item.source === "studio_auto";
      return `<article class="review-history-item ${auto ? "auto" : ""}"><div><strong>${esc(reviewTitle(item))}</strong><small>${esc(item.work_title || item.work_id || "")}${when ? ` · ${esc(when)}` : ""}</small></div><span class="state-chip">${esc(historyStatus(item))}</span></article>`;
    }).join("")}</div>`
    : `<p class="muted">${esc(t("approvals.historyEmpty"))}</p>`;
  node.innerHTML = `<div class="view-intro compact-intro"><h3>${esc(t("approvals.historyTitle"))}</h3></div>${body}`;
}
function renderReviews() {
  setBadge("reviews-badge", reviews.length);
  const activeEditor = document.activeElement;
  if (currentView === "approvals" && activeEditor instanceof HTMLTextAreaElement && activeEditor.closest("#review-list")) return;
  if (!reviews.length) {
    $("review-list").innerHTML = `<div class="empty-state"><h3>${esc(t("approvals.emptyTitle"))}</h3><p>${esc(t("approvals.emptyText"))}</p></div>`;
  } else {
    const activeIds = new Set(reviews.map((review) => String(review.id)));
    [...expandedChangeRequests].forEach((id) => { if (!activeIds.has(String(id))) expandedChangeRequests.delete(id); });
    [...reviewDrafts.keys()].forEach((id) => { if (!activeIds.has(String(id))) reviewDrafts.delete(id); });
    $("review-list").innerHTML = reviews.map((review) => {
      const reviewId = String(review.id);
      const projectName = review.work_title || review.work_id || overview?.project || t("common.project");
      if (review.kind === "clarification") {
        const draft = reviewDrafts.get(reviewId) || "";
        return `<article class="review-card" data-review-id="${esc(reviewId)}"><div class="review-type">${esc(t("approvals.question"))} · ${esc(projectName)}</div><div class="review-card-head"><div><h3>${esc(reviewTitle(review))}</h3><p>${esc(reviewIntro(review))}</p></div></div><label><span class="small muted">${esc(t("approvals.answerHelp"))}</span><textarea rows="3" data-review-answer placeholder="${esc(t("approvals.answerPlaceholder"))}">${esc(draft)}</textarea></label><div class="review-actions"><button class="primary compact" data-review-action="answer">${esc(t("approvals.answer"))}</button></div></article>`;
      }
      const changesOpen = expandedChangeRequests.has(reviewId);
      const draft = reviewDrafts.get(reviewId) || "";
      return `<article class="review-card" data-review-id="${esc(reviewId)}"><div class="review-type">${esc(reviewTitle(review))} · ${esc(projectName)}</div><div class="review-card-head"><div><h3>${esc(reviewTitle(review))}</h3><p>${esc(reviewIntro(review))}</p></div><span class="state-chip">${esc(t("approvals.waiting"))}</span></div>${reviewDetail(review)}<div class="review-actions"><button class="primary compact" data-review-action="approve">${esc(t("common.approve"))}</button><button class="secondary compact" data-review-action="changes">${esc(t("approvals.requestChanges"))}</button><button class="secondary compact" data-review-action="cancel">${esc(t("approvals.cancelTask"))}</button></div><div class="changes-box ${changesOpen ? "" : "hidden"}"><label><span class="small muted">${esc(t("approvals.changeHelp"))}</span><textarea rows="4" data-review-comments placeholder="${esc(t("approvals.changePlaceholder"))}">${esc(draft)}</textarea></label><div class="review-actions"><button class="primary compact" data-review-action="send-changes">${esc(t("approvals.sendChanges"))}</button><button class="secondary compact" data-review-action="hide-changes">${esc(t("approvals.back"))}</button></div></div></article>`;
    }).join("");
  }
  renderReviewHistory();
}
function renderRisk(data) {
  const risk = data.risk || {level:"low", score:0, signals:[], recommendations:[]};
  $("risk-card").innerHTML = `<div class="risk-summary"><strong>${esc(String(risk.level || "low").toUpperCase())} · ${esc(risk.score || 0)}/100</strong></div><div class="list">${(risk.signals || []).map((item) => `<div class="list-item"><strong>+${esc(item.weight)} · ${esc(item.code)}</strong><small>${esc(item.message)}</small></div>`).join("") || `<div class="list-item"><small>—</small></div>`}</div>`;
}
function renderEvents() {
  const rows = [...events].reverse().slice(0,50);
  $("event-list").innerHTML = rows.length ? rows.map((event) => `<div class="event-item"><time>${esc((event.created_at || "").replace("T"," ").replace("Z",""))}</time><strong>${esc((event.event_type || "Event").replace(/([a-z])([A-Z])/g,"$1 $2"))}</strong><span>${esc(event.work_id || overview?.project || "")}</span></div>`).join("") : `<div class="empty-state"><p>—</p></div>`;
  $("events-raw").textContent = JSON.stringify(events.slice(-100), null, 2);
}
function renderDiagnostics(data) {
  $("diag-version").textContent = health?.display_version || health?.version ? `v${health.display_version || health.version}` : "—";
  $("diag-project").textContent = data.project || "—";
  $("diag-initialized").textContent = data.initialized ? t("common.yes") : t("common.no");
  const summary = {dynosai_version:health?.version, project:data.project, root:health?.root, initialized:!!data.initialized, classification:data.detection?.classification, git:!!data.detection?.has_git, dirty_entries:data.detection?.dirty?.length || 0, stacks:data.detection?.stacks || [], detected_checks:data.validations?.candidates?.length || 0, approved_checks:Object.values(data.validations?.approved || {}).filter((item) => item.approved).length, pending_reviews:reviews.length};
  $("support-summary").textContent = JSON.stringify(summary, null, 2);
  $("raw-overview").textContent = JSON.stringify(data, null, 2);
}
function routeSourceLabel(source) {
  const key = `projectSettings.source.${source || "builtin_default"}`;
  return t(key) === key ? String(source || "") : t(key);
}
function phaseLabel(activity) {
  const key = activity ? `projectSettings.phase.${activity}` : "projectSettings.phase.default";
  return t(key) === key ? (activity || "Default") : t(key);
}
function effectiveProviderData() { return modelRouting?.providers?.[routingProvider] || null; }
function renderModelRouting() {
  const list = $("model-routing-list");
  if (!list) return;
  const providerLabel = $("routing-provider-label");
  if (providerLabel) providerLabel.textContent = routingProvider === "cursor" ? t("common.cursor") : t("common.codex");
  if (!overview?.initialized) { list.innerHTML = `<div class="empty-state compact-empty"><p>${esc(t("projectSettings.initializeFirst"))}</p></div>`; return; }
  const data = effectiveProviderData();
  if (!data) { list.innerHTML = `<div class="empty-state compact-empty"><p>${esc(t("common.loading"))}</p></div>`; return; }
  const rows = [{activity:null, route:data.default}, ...Object.entries(data.activities || {}).map(([activity, route]) => ({activity, route}))];
  list.innerHTML = rows.map(({activity, route}) => `<div class="route-row"><div class="route-phase"><strong>${esc(phaseLabel(activity))}</strong><small>${esc(activity ? t("projectSettings.phaseHelp." + activity) : t("projectSettings.defaultHelp"))}</small></div><div class="route-value"><strong>${esc(route?.model || "—")}</strong><small>${esc(route?.effort ? `${route.effort} · ${routeSourceLabel(route.source)}` : routeSourceLabel(route?.source))}</small></div><button class="secondary compact" type="button" data-route-edit="${esc(activity || "default")}">${esc(t("common.change"))}</button></div>`).join("");
}
async function loadModelRouting(provider = routingProvider) {
  if (!overview?.initialized || !hasProject()) { modelRouting = null; renderModelRouting(); return; }
  routingProvider = ["codex","cursor"].includes(provider) ? provider : "codex";
  try { modelRouting = await api(`/api/model-routing?provider=${encodeURIComponent(routingProvider)}`); renderModelRouting(); }
  catch (error) { modelRouting = null; renderModelRouting(); showAlert(error.message); }
}
function renderProjectSettings() {
  if (!hasProject()) return;
  setComboValue("project-provider", projectPreference("provider", "codex"));
  setComboValue("project-workspace", projectPreference("workspace", "interactive_branch"));
  const auto = $("auto-approve-toggle");
  if (auto) {
    auto.disabled = !overview?.initialized;
    auto.checked = !!overview?.auto_approve;
  }
  setComboValue("execution-profile", overview?.execution_policy?.profile || "balanced");
  const profileInput = $("execution-profile");
  if (profileInput) profileInput.disabled = !overview?.initialized;
  const byName = overview?.harness?.by_name || {};
  document.querySelectorAll("[data-harness]").forEach((input) => {
    const name = input.dataset.harness;
    const item = byName[name] || (overview?.harness?.features || []).find((row) => row.name === name);
    input.disabled = !overview?.initialized || !!(item && item.env_locked);
    input.checked = item ? !!item.enabled : true;
    input.title = item && item.env_locked ? t("harness.envLocked") : "";
  });
  renderModelRouting();
}
function recommendedModel(provider, model) {
  const data = modelRouting?.providers?.[provider];
  return (data?.recommended_models || []).find((item) => item.model === model);
}
function renderRouteEfforts(model, selected) {
  const recommendation = recommendedModel(routingProvider, model);
  const efforts = recommendation && Array.isArray(recommendation.efforts)
    ? recommendation.efforts
    : (routingProvider === "codex" ? ["none","low","medium","high","xhigh","max"] : ["low","medium","high","xhigh"]);
  if (!efforts.length) {
    $("route-effort-options").innerHTML = `<button type="button" class="active" data-route-effort="">${esc(t("projectSettings.effortAutomatic"))}</button>`;
    routeDialogState.effort = null;
    return;
  }
  const normalized = selected && efforts.includes(selected) ? selected : (recommendation?.recommended_effort || efforts[0] || "");
  $("route-effort-options").innerHTML = efforts.map((effort) => `<button type="button" class="${effort === normalized ? "active" : ""}" data-route-effort="${esc(effort)}">${esc(effort)}</button>`).join("");
  routeDialogState.effort = normalized || null;
}
function openRouteDialog(activityName) {
  const activity = activityName === "default" ? null : activityName;
  const data = effectiveProviderData();
  const route = activity ? data?.activities?.[activity] : data?.default;
  if (!route) return;
  routeDialogState = {activity, model:route.model, effort:route.effort || null};
  $("route-dialog-title").textContent = phaseLabel(activity);
  $("route-dialog-provider").textContent = routingProvider === "cursor" ? "Cursor" : "Codex";
  $("route-model-input").value = route.model || "";
  const recommended = data?.recommended_models || [];
  $("route-model-suggestions").innerHTML = recommended.map((item) => `<button type="button" class="suggestion-chip ${item.model === route.model ? "active" : ""}" data-route-model="${esc(item.model)}">${esc(item.label || item.model)}</button>`).join("");
  renderRouteEfforts(route.model, route.effort);
  $("route-dialog-reset").classList.toggle("hidden", route.scope !== "project");
  setDialogOpen("route-dialog", true);
}
function closeRouteDialog() { routeDialogState = null; setDialogOpen("route-dialog", false); }
async function saveRouteDialog() {
  if (!routeDialogState) return;
  const model = $("route-model-input").value.trim();
  if (!model) { showAlert(t("projectSettings.modelRequired")); return; }
  try {
    await withOperation("operation.saveSettings", async () => {
      await api("/api/model-routing/set", {method:"POST", body:JSON.stringify({provider:routingProvider, model, effort:routeDialogState.effort, activity:routeDialogState.activity})});
      closeRouteDialog();
      await loadModelRouting(routingProvider);
      showAlert(t("projectSettings.saved"), "success");
    });
  } catch (error) { showAlert(error.message); }
}
async function resetRouteDialog() {
  if (!routeDialogState) return;
  try {
    await withOperation("operation.saveSettings", async () => {
      await api("/api/model-routing/reset", {method:"POST", body:JSON.stringify({provider:routingProvider, activity:routeDialogState.activity})});
      closeRouteDialog();
      await loadModelRouting(routingProvider);
      showAlert(t("projectSettings.inherited"), "success");
    });
  } catch (error) { showAlert(error.message); }
}

function renderProject(data) {
  renderSetup(data);
  const approvedCount = Object.values(data.validations?.approved || {}).filter((item) => item.approved).length;
  const candidateCount = data.validations?.candidates?.length || 0;
  $("home-checks").textContent = approvedCount ? `${approvedCount} ${t("common.approved")}` : candidateCount ? `${candidateCount} ${t("common.detected")}` : t("common.none");
  const active = (data.work || []).filter((item) => item.id !== "PROJECT" && item.state !== "done");
  $("home-active").textContent = String(active.length);
  $("home-reviews").textContent = String(reviews.length);
  const evalRoot = $("eval-intelligence");
  if (evalRoot) evalRoot.innerHTML = evalCasesHtml(data);
  const harnessRoot = $("harness-features");
  if (harnessRoot) harnessRoot.innerHTML = harnessStatusHtml(data);
  const capsRoot = $("provider-capabilities");
  if (capsRoot) capsRoot.innerHTML = providerCapsHtml(data);
  const matrixRoot = $("live-matrix");
  if (matrixRoot) matrixRoot.innerHTML = liveMatrixHtml(data);
  renderChecks(data);
  renderWork(data);
  renderReviews();
  renderRisk(data);
  renderDiagnostics(data);
  renderEvents();
  renderStartReadiness();
  renderProjectSettings();
}

function renderStartReadiness() {
  const note = $("start-readiness");
  const button = $("start-work");
  if (!note || !button) return;
  if (!overview?.initialized) { note.className = "inline-note warn"; note.textContent = t("new.finishSetup"); button.disabled = true; return; }
  const workspace = projectPreference("workspace", "interactive_branch");
  const dirty = (overview.detection?.dirty || []).length > 0;
  if (dirty && workspace === "interactive_branch") { note.className = "inline-note warn"; note.textContent = t("new.dirty"); button.disabled = true; return; }
  note.className = "inline-note hidden";
  note.textContent = "";
  button.disabled = false;
}

async function refresh({preserveView = true} = {}) {
  const viewBefore = currentView;
  try {
    health = await api("/api/health");
    projects = (await api("/api/projects")).items || [];
    if (health.project_selected) {
      overview = await api("/api/overview");
      applyProjectPreferences();
      const reviewsPayload = overview.initialized ? (await api("/api/reviews")) : {items:[], history:[]};
      reviews = reviewsPayload.items || [];
      reviewHistory = reviewsPayload.history || [];
      executions = overview.initialized ? (await api("/api/execution")).items || [] : [];
      events = overview.initialized ? (await api("/api/events")).items || [] : [];
      modelRouting = overview.initialized ? await api(`/api/model-routing?provider=${encodeURIComponent(routingProvider)}`) : null;
    } else {
      overview = null; reviews = []; reviewHistory = []; executions = []; events = []; modelRouting = null; preferenceProjectRoot = null;
      setBadge("progress-badge", 0); setBadge("reviews-badge", 0);
    }
    const pageY = window.scrollY || lastWindowScrollY;
    if (health.project_selected) renderProject(overview);
    renderRecentProjects();
    renderShell();
    if (!initialNavigationDone) {
      initialNavigationDone = true;
      navigate(health.project_selected ? "project-overview" : "home");
    } else if (!preserveView || (projectViews.includes(currentView) && !health.project_selected)) navigate(health.project_selected ? "project-overview" : "home");
    else navigate(currentView, {scroll:false});
    const restoreWindow = () => {
      if (viewBefore === currentView) window.scrollTo(0, pageY);
    };
    restoreWindow();
    requestAnimationFrame(() => {
      restoreWindow();
      requestAnimationFrame(restoreWindow);
    });
    updateTutorialFromState();
    syncTutorialNavigation();
    renderTutorialCoach();
  } catch (error) { showAlert(error.message); }
}

async function openProject(path) {
  const value = String(path || "").trim();
  if (!value) { showAlert(t("alerts.pathRequired")); return; }
  try {
    await withOperation("operation.openProject", async () => {
      const result = await api("/api/projects/open", {method:"POST", body:JSON.stringify({path:value})});
      $("project-path-input").value = "";
      updateProjectActionState();
      showAlert(t("alerts.projectOpened", {name:result.project?.name || result.root}), "success");
      await refresh({preserveView:false});
      navigate("project-overview");
    });
  } catch (error) { showAlert(error.message); }
}
function folderJoin(parent, name) {
  if (!parent || !name) return parent || "";
  const separator = parent.includes("\\") ? "\\" : "/";
  return `${parent.replace(/[\\/]+$/, "")}${separator}${name}`;
}
function closeFolderDialog() {
  folderDialogMode = null;
  folderDialogData = null;
  $("folder-dialog").classList.add("hidden");
  document.body.classList.remove("dialog-open");
}
function renderFolderDialog() {
  if (!folderDialogData || !folderDialogMode) return;
  const data = folderDialogData;
  $("folder-dialog-current").textContent = data.path;
  $("folder-dialog-up").disabled = !data.parent;
  $("folder-dialog-new").disabled = !data.writable;
  $("folder-dialog-home").dataset.folderBrowse = data.home || "";
  $("folder-dialog-up").dataset.folderBrowse = data.parent || "";
  $("folder-dialog-title").textContent = t(folderDialogMode === "create" ? "folders.createTitle" : "folders.openTitle");
  $("folder-dialog-text").textContent = t(folderDialogMode === "create" ? "folders.createText" : "folders.openText");
  $("folder-dialog-select").textContent = t(folderDialogMode === "create" ? "folders.useLocation" : "folders.openProject");
  const roots = data.roots || [];
  $("folder-dialog-roots").innerHTML = roots.length ? roots.map((root) => `<button class="folder-root secondary compact" type="button" data-folder-browse="${esc(root.path)}">${esc(root.label)}</button>`).join("") : "";
  const directories = data.directories || [];
  $("folder-dialog-list").innerHTML = directories.length ? directories.map((item) => `<button class="folder-entry" type="button" data-folder-browse="${esc(item.path)}"><span class="folder-entry-icon">⌑</span><span>${esc(item.name)}</span><span class="folder-entry-chevron">›</span></button>`).join("") : `<div class="folder-empty">${esc(t("folders.empty"))}</div>`;
  const projectName = $("new-project-name").value.trim();
  const target = folderDialogMode === "create" && projectName ? folderJoin(data.path, projectName) : data.path;
  $("folder-dialog-selection").innerHTML = folderDialogMode === "create"
    ? `<span>${esc(t("folders.projectWillBeCreated"))}</span><code>${esc(target)}</code>`
    : `<span>${esc(t("folders.selectedProject"))}</span><code>${esc(target)}</code>`;
}
async function loadFolderDialog(path = "") {
  try {
    $("folder-dialog-list").innerHTML = `<div class="folder-empty">${esc(t("common.loading"))}</div>`;
    folderDialogData = await api("/api/filesystem/list", {method:"POST", body:JSON.stringify({path:path || null})});
    renderFolderDialog();
  } catch (error) { showAlert(error.message); }
}
async function openFolderDialog(mode) {
  folderDialogMode = mode;
  $("folder-dialog").classList.remove("hidden");
  document.body.classList.add("dialog-open");
  const initial = mode === "create"
    ? ($("new-project-parent").value.trim() || localStorage.getItem(storage.projectParent) || "")
    : ($("project-path-input").value.trim() || "");
  await loadFolderDialog(initial);
}
async function chooseFolderDialog() {
  if (!folderDialogData?.path || !folderDialogMode) return;
  const path = folderDialogData.path;
  if (folderDialogMode === "create") {
    $("new-project-parent").value = path;
    localStorage.setItem(storage.projectParent, path);
    closeFolderDialog();
    updateProjectActionState();
    $("new-project-name").focus();
    return;
  }
  $("project-path-input").value = path;
  closeFolderDialog();
  updateProjectActionState();
  $("open-project-path").focus();
}
async function browseProject() { await openFolderDialog("open"); }
async function browseNewParent() { await openFolderDialog("create"); }
async function createProject() {
  const name = $("new-project-name").value.trim();
  const parent = $("new-project-parent").value.trim();
  if (!name || !parent) { showAlert(t("alerts.createProjectFields")); return; }
  try {
    await withOperation("operation.createProject", async () => {
      localStorage.setItem(storage.projectParent, parent);
      const result = await api("/api/projects/create", {method:"POST", body:JSON.stringify({name, parent, template:"empty"})});
      $("new-project-name").value = "";
      updateProjectActionState();
      showAlert(t("alerts.projectCreated", {name:result.project?.name || name}), "success");
      if (tutorialActive()) {
        localStorage.setItem(storage.tutorialProject, result.root);
        setTutorialStep(2);
      }
      await refresh({preserveView:false});
      navigate("project-overview");
    });
  } catch (error) { showAlert(error.message); }
}
async function goHome() {
  if (hasProject()) {
    await closeProject();
    return;
  }
  navigate("home");
}
async function closeProject() {
  try {
    await withOperation("operation.closeProject", async () => {
      await api("/api/projects/close", {method:"POST", body:"{}"});
      overview = null;
      await refresh({preserveView:false});
      navigate("home");
    });
  } catch (error) { showAlert(error.message); }
}
async function removeRecentProject(path) {
  const confirmed = await confirmDialog({title:t("confirm.removeProjectTitle"), text:t("confirm.removeProject"), confirmLabel:t("common.remove"), destructive:true});
  if (!confirmed) return;
  try {
    await withOperation("operation.removeProject", async () => {
      await api("/api/projects/remove", {method:"POST", body:JSON.stringify({path})});
      showAlert(t("alerts.projectRemoved"), "success");
      await refresh();
    });
  } catch (error) { showAlert(error.message); }
}
async function initializeProject() {
  if (!overview) return;
  try {
    await withOperation("operation.initialize", async () => {
      const detection = overview.detection || {};
      const provider = projectPreference("provider", "codex");
      await api("/api/project/initialize", {method:"POST", body:JSON.stringify({agent:provider, allow_git_init:detection.classification === "NEW_CODE_NO_GIT"})});
      await refresh();
      const candidates = overview?.validations?.candidates || [];
      const approved = overview?.validations?.approved || {};
      const pendingChecks = candidates.some((item) => !approved[item.name]?.approved);
      showAlert(t(pendingChecks ? "alerts.initializedReviewChecks" : "alerts.initialized"), "success");
      if (tutorialMatchesCurrent()) { setTutorialStep(3); navigate("new-task"); }
      else navigate(pendingChecks ? "checks" : "project-overview");
    });
  } catch (error) { showAlert(error.message); }
}
async function approveSelectedValidations() {
  const names = [...document.querySelectorAll("[data-validation-name]:checked:not(:disabled)")].map((input) => input.dataset.validationName);
  if (!names.length) { showAlert(t("alerts.selectCheck")); return; }
  try {
    await withOperation("operation.approveChecks", async () => {
      await api("/api/validations/approve", {method:"POST", body:JSON.stringify({names})});
      showAlert(t("alerts.checksApproved"), "success");
      await refresh();
    });
  } catch (error) { showAlert(error.message); }
}
async function startWork() {
  const description = $("work-description").value.trim();
  if (!description) { showAlert(t("alerts.describe")); return; }
  try {
    await withOperation("operation.createChange", async () => {
      const result = await api("/api/work/start", {method:"POST", body:JSON.stringify({description, provider:projectPreference("provider", "codex"), workspace_strategy:projectPreference("workspace", "interactive_branch")})});
      $("work-description").value = "";
      showAlert(t("alerts.taskCreated", {id:result.id || ""}), "success");
      if (tutorialMatchesCurrent()) setTutorialStep(4);
      await refresh();
      navigate("work");
    });
  } catch (error) { showAlert(error.message); }
}
async function retryExecution(workId) {
  try {
    await withOperation("operation.startAgent", async () => {
      const result = await api("/api/execution/start", {method:"POST", body:JSON.stringify({work_id:workId})});
      if (result.status === "running") showAlert(t("alerts.agentStarted"), "success");
      else if (result.status === "done") showAlert(t("alerts.workFinished"), "success");
      else if (result.status === "waiting_review") showAlert(t("alerts.reviewReady"), "success");
      else showAlert(t("alerts.agentCouldNotStart"), "error");
      await refresh();
    });
  } catch (error) { showAlert(error.message); }
}

async function resolveReview(card, action) {
  const id = card.dataset.reviewId;
  try {
    let content = null; let apiAction = "accept";
    if (action === "approve") content = {decision:"approve"};
    if (action === "cancel") {
      const confirmed = await confirmDialog({title:t("confirm.cancelTitle"), text:t("confirm.cancel"), confirmLabel:t("common.confirm")});
      if (!confirmed) return;
      apiAction = "cancel"; content = {decision:"cancel"};
    }
    if (action === "send-changes") { const comments = card.querySelector("[data-review-comments]")?.value.trim(); if (!comments) { showAlert(t("alerts.explainChanges")); return; } content = {decision:"request_changes", comments}; }
    if (action === "answer") { const answer = card.querySelector("[data-review-answer]")?.value.trim(); if (!answer) { showAlert(t("alerts.typeAnswer")); return; } content = {answer}; }
    await withOperation("operation.review", async () => {
      await api("/api/interaction/resolve", {method:"POST", body:JSON.stringify({interaction_id:id, action:apiAction, content})});
      expandedChangeRequests.delete(String(id));
      reviewDrafts.delete(String(id));
      showAlert(t(action === "approve" ? "alerts.reviewApproved" : action === "send-changes" ? "alerts.changeSent" : action === "answer" ? "alerts.answerRecorded" : "alerts.cancelled"), "success");
      await refresh();
      updateTutorialFromState();
      if (tutorialMatchesCurrent() && tutorialStep() === 5 && !reviews.length) navigate("work");
    });
  } catch (error) { showAlert(error.message); }
}

function tutorialActive() { return boolStored(storage.tutorialActive); }
function tutorialStep() { return Math.max(1, Math.min(6, Number(localStorage.getItem(storage.tutorialStep) || 1))); }
function tutorialMatchesCurrent() { return tutorialActive() && !!health?.root && health.root === localStorage.getItem(storage.tutorialProject); }
function setTutorialStep(step) { localStorage.setItem(storage.tutorialStep, String(Math.max(1, Math.min(6, step)))); renderTutorialCoach(); }
function startTutorial() {
  localStorage.setItem(storage.tutorialActive, "true");
  localStorage.setItem(storage.tutorialStep, "1");
  localStorage.removeItem(storage.tutorialProject);
  navigate("home");
  renderTutorialCoach();
}
function stopTutorial() {
  localStorage.removeItem(storage.tutorialActive);
  localStorage.removeItem(storage.tutorialStep);
  localStorage.removeItem(storage.tutorialProject);
  $("tutorial-coach").classList.add("hidden");
  clearHighlight();
}
function tutorialConfig(step) {
  if (step === 5 && !reviews.length) return {view:"work", target:"#work-list", title:"tutorial.step5ContinueTitle", text:"tutorial.step5ContinueText"};
  return {
    1:{view:"home", target:"#create-project-card", title:"tutorial.step1TitleNew", text:"tutorial.step1TextSimple"},
    2:{view:"project-overview", target:"#project-primary-action", title:"tutorial.step2Title", text:"tutorial.step2TextNew"},
    3:{view:"new-task", target:"#work-description", title:"tutorial.step3ChangeTitle", text:"tutorial.step3ChangeText", example:"tutorial.prompt"},
    4:{view:"work", target:"#work-list", title:"tutorial.step4WorkTitle", text:"tutorial.step4WorkText"},
    5:{view:"approvals", target:"#review-list", title:"tutorial.step5ApprovalTitle", text:"tutorial.step5ApprovalText"},
    6:{view:"work", target:"#work-list", title:"tutorial.step6ResultTitle", text:"tutorial.step6ResultText"},
  }[step];
}
function clearHighlight() { document.querySelectorAll(".tutorial-target").forEach((node) => node.classList.remove("tutorial-target")); }
function highlightTarget(selector, {scroll=false}={}) {
  clearHighlight();
  const node = document.querySelector(selector);
  if (!node) return;
  node.classList.add("tutorial-target");
  if (scroll) node.scrollIntoView({behavior:"smooth", block:"center"});
}
function renderTutorialCoach() {
  const coach = $("tutorial-coach");
  if (!tutorialActive()) { coach.classList.add("hidden"); clearHighlight(); return; }
  const step = tutorialStep();
  const config = tutorialConfig(step);
  coach.classList.remove("hidden");
  $("tutorial-progress").textContent = `${step} / 6`;
  $("tutorial-title").textContent = t(config.title);
  $("tutorial-text").textContent = t(config.text);
  const example = $("tutorial-example");
  if (config.example) { example.classList.remove("hidden"); $("tutorial-example-text").textContent = t(config.example); }
  else example.classList.add("hidden");
  $("tutorial-back").disabled = step <= 1;
  $("tutorial-show").textContent = step === 6 ? t("tutorial.finish") : t("tutorial.showMe");
  if (currentView === config.view) {
    const key = `${step}:${config.target}`;
    highlightTarget(config.target, {scroll: lastTutorialScrollKey !== key});
    lastTutorialScrollKey = key;
  } else clearHighlight();
}
function showTutorialTarget() {
  const step = tutorialStep();
  if (step === 6) { stopTutorial(); showAlert(t("tutorial.finished"), "success"); return; }
  const config = tutorialConfig(step);
  navigate(config.view);
  setTimeout(() => highlightTarget(config.target, {scroll:true}), 80);
}
function updateTutorialFromState() {
  if (!tutorialMatchesCurrent() || !overview) return;
  const step = tutorialStep();
  const workItems = (overview.work || []).filter((item) => item.id !== "PROJECT");
  const done = workItems.some((item) => item.state === "done");
  if (step <= 2 && overview.initialized) { setTutorialStep(3); return; }
  if (done && step >= 4 && step < 6) { setTutorialStep(6); return; }
  if (step === 4 && reviews.length > 0) { setTutorialStep(5); return; }
  if (step === 5) renderTutorialCoach();
}
function syncTutorialNavigation() {
  if (!tutorialMatchesCurrent() || !overview) return;
  const step = tutorialStep();
  if (step === 6 && currentView !== "work") navigate("work");
  else if (step === 5 && reviews.length > 0 && currentView !== "approvals") navigate("approvals");
  else if (step === 5 && !reviews.length && currentView === "approvals") navigate("work");
}

function applyLanguage(language, persist = true) {
  I18N.apply(language, {persist});
  setComboValue("language", I18N.language());
  refreshComboboxLabels();
  renderRecentProjects();
  if (overview) renderProject(overview);
  renderModelRouting();
  navigate(currentView, {scroll:false});
  renderTutorialCoach();
}

function wireEvents() {
  document.addEventListener("click", (event) => {
    const target = event.target.closest("button");
    if (!target || target.disabled || target.getAttribute("aria-disabled") === "true") return;
    if (target.dataset.view) {
      if (target.dataset.view === "home") { goHome(); return; }
      navigate(target.dataset.view);
    }
    if (target.dataset.folderBrowse) { loadFolderDialog(target.dataset.folderBrowse); return; }
    if (target.dataset.startTutorial) startTutorial();
    if (target.dataset.homeAction) {
      if (target.dataset.homeAction === "initialize") initializeProject();
      else navigate(target.dataset.homeAction);
    }
    if (target.classList.contains("example-chip")) {
      const key = target.dataset.exampleKey;
      $("work-description").value = t(`new.example${key === "feature" ? "Feature" : key === "bug" ? "Bug" : "Refactor"}Prompt`);
    }
    if (target.dataset.projectOpen) openProject(target.dataset.projectOpen);
    if (target.dataset.projectRemove) removeRecentProject(target.dataset.projectRemove);
    if (target.dataset.executionRetry) retryExecution(target.dataset.executionRetry);
    if (target.dataset.diagnosticToggle) {
      const workId = String(target.dataset.diagnosticToggle);
      if (expandedExecutionDiagnostics.has(workId)) expandedExecutionDiagnostics.delete(workId);
      else expandedExecutionDiagnostics.add(workId);
      if (overview) renderWork(overview);
      return;
    }
    if (target.dataset.routeEdit) openRouteDialog(target.dataset.routeEdit);
    if (target.dataset.routeModel && routeDialogState) { $("route-model-input").value = target.dataset.routeModel; routeDialogState.model = target.dataset.routeModel; document.querySelectorAll("[data-route-model]").forEach((node) => node.classList.toggle("active", node === target)); renderRouteEfforts(target.dataset.routeModel, recommendedModel(routingProvider, target.dataset.routeModel)?.recommended_effort); }
    if (target.hasAttribute("data-route-effort") && routeDialogState) { routeDialogState.effort = target.dataset.routeEffort || null; document.querySelectorAll("[data-route-effort]").forEach((node) => node.classList.toggle("active", node === target)); }
    if (target.dataset.evalPropose) { proposeEvalImprovement(target.dataset.evalPropose); return; }
    if (target.dataset.reviewAction) {
      const card = target.closest("[data-review-id]");
      if (!card) return;
      const reviewId = String(card.dataset.reviewId);
      if (target.dataset.reviewAction === "changes") {
        expandedChangeRequests.add(reviewId);
        renderReviews();
        document.querySelector(`[data-review-id="${CSS.escape(reviewId)}"] [data-review-comments]`)?.focus();
      } else if (target.dataset.reviewAction === "hide-changes") {
        expandedChangeRequests.delete(reviewId);
        renderReviews();
      } else resolveReview(card, target.dataset.reviewAction);
    }
  });
  document.addEventListener("input", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLTextAreaElement)) return;
    const card = target.closest("[data-review-id]");
    if (!card) return;
    if (target.hasAttribute("data-review-comments") || target.hasAttribute("data-review-answer")) reviewDrafts.set(String(card.dataset.reviewId), target.value);
  });
  $("theme-toggle").addEventListener("click", cycleTheme);
  document.querySelectorAll("[data-theme-value]").forEach((button) => button.addEventListener("click", () => applyTheme(button.dataset.themeValue)));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (themePreference() === "system") applyTheme("system", false); });
  $("top-new-task").addEventListener("click", () => navigate("new-task"));
  $("create-project").addEventListener("click", createProject);
  $("new-project-name").addEventListener("input", updateProjectActionState);
  $("browse-new-parent").addEventListener("click", browseNewParent);
  $("browse-project").addEventListener("click", browseProject);
  $("folder-dialog-close").addEventListener("click", closeFolderDialog);
  $("folder-dialog-cancel").addEventListener("click", closeFolderDialog);
  $("folder-dialog-select").addEventListener("click", chooseFolderDialog);
  $("folder-dialog-new").addEventListener("click", openNewFolderDialog);
  $("folder-dialog").addEventListener("click", (event) => { if (event.target === $("folder-dialog")) closeFolderDialog(); });
  $("new-folder-cancel").addEventListener("click", closeNewFolderDialog);
  $("new-folder-create").addEventListener("click", createFolderFromDialog);
  $("new-folder-name").addEventListener("keydown", (event) => { if (event.key === "Enter") createFolderFromDialog(); });
  $("new-folder-dialog").addEventListener("click", (event) => { if (event.target === $("new-folder-dialog")) closeNewFolderDialog(); });
  $("confirm-dialog-cancel").addEventListener("click", () => closeConfirmDialog(false));
  $("confirm-dialog-confirm").addEventListener("click", () => closeConfirmDialog(true));
  $("confirm-dialog").addEventListener("click", (event) => { if (event.target === $("confirm-dialog")) closeConfirmDialog(false); });
  $("route-dialog-close").addEventListener("click", closeRouteDialog);
  $("route-dialog-cancel").addEventListener("click", closeRouteDialog);
  $("route-dialog-save").addEventListener("click", saveRouteDialog);
  $("route-dialog-reset").addEventListener("click", resetRouteDialog);
  $("route-model-input").addEventListener("input", () => { if (routeDialogState) { routeDialogState.model = $("route-model-input").value.trim(); renderRouteEfforts(routeDialogState.model, routeDialogState.effort); } });
  $("route-dialog").addEventListener("click", (event) => { if (event.target === $("route-dialog")) closeRouteDialog(); });
  $("open-project-path").addEventListener("click", () => openProject($("project-path-input").value));
  $("project-path-input").addEventListener("click", browseProject);
  $("new-project-parent").addEventListener("click", browseNewParent);
  $("approve-selected-validations").addEventListener("click", approveSelectedValidations);
  $("start-work").addEventListener("click", startWork);
  $("project-provider").addEventListener("change", (event) => { setProjectPreference("provider", event.target.value); routingProvider = event.target.value; loadModelRouting(routingProvider); renderStartReadiness(); });
  $("project-workspace").addEventListener("change", (event) => { setProjectPreference("workspace", event.target.value); renderStartReadiness(); });
  $("language").addEventListener("change", (event) => applyLanguage(event.target.value, true));
  $("technical-toggle").addEventListener("change", (event) => {
    localStorage.setItem(storage.technical, String(event.target.checked));
    renderShell();
    if (!event.target.checked && technicalViews.includes(currentView)) navigate("settings");
  });
  $("auto-approve-toggle")?.addEventListener("change", async (event) => {
    const enabled = !!event.target.checked;
    try {
      await withOperation("operation.saveSettings", async () => {
        await api("/api/project-settings", {method:"POST", body:JSON.stringify({auto_approve: enabled})});
        await refresh({preserveView:true});
        showAlert(t("projectSettings.autoApproveSaved"), "success");
      });
    } catch (error) {
      event.target.checked = !enabled;
      showAlert(error.message);
    }
  });
  document.querySelectorAll("[data-harness]").forEach((input) => {
    input.addEventListener("change", async (event) => {
      const name = event.target.dataset.harness;
      const enabled = !!event.target.checked;
      try {
        await withOperation("operation.saveSettings", async () => {
          await api("/api/project-settings", {method:"POST", body:JSON.stringify({harness: {[name]: enabled}})});
          await refresh({preserveView:true});
          showAlert(t("harness.saved"), "success");
        });
      } catch (error) {
        event.target.checked = !enabled;
        showAlert(error.message);
      }
    });
  });
  $("execution-profile")?.addEventListener("change", async (event) => {
    const profile = event.target.value;
    try {
      await withOperation("operation.saveSettings", async () => {
        await api("/api/execution-profile", {method:"POST", body:JSON.stringify({profile})});
        await refresh({preserveView:true});
        showAlert(t("projectSettings.executionProfileSaved"), "success");
      });
    } catch (error) {
      setComboValue("execution-profile", overview?.execution_policy?.profile || "balanced");
      showAlert(error.message);
    }
  });
  $("copy-support").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("support-summary").textContent); showAlert(t("alerts.supportCopied"), "success"); }
    catch { showAlert(t("alerts.clipboardDenied")); }
  });
  $("tutorial-close").addEventListener("click", stopTutorial);
  $("tutorial-show").addEventListener("click", showTutorialTarget);
  $("tutorial-back").addEventListener("click", () => { if (tutorialStep() > 1) setTutorialStep(tutorialStep() - 1); });
  $("tutorial-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("tutorial-example-text").textContent); showAlert(t("tutorial.copied"), "success"); }
    catch { showAlert(t("alerts.clipboardDenied")); }
  });
  window.addEventListener("dynosai-language-changed", refreshComboboxLabels);
}

initComboboxes();
I18N.apply(I18N.language(), {persist:false});
applyPreferences();
wireEvents();
updateProjectActionState();
navigate("home");
refresh();
setInterval(() => { if (!document.hidden) refresh({preserveView:true}); }, 2500);
