const tabs = [
  "Overview",
  "Tasks",
  "Activity",
  "Memory",
  "Skills",
  "Access",
  "Settings",
];
const params = new URLSearchParams(location.search);
let variant = ["overview", "workspace", "profile"].includes(
  params.get("variant"),
)
  ? params.get("variant")
  : "overview";
let tab = tabs.includes(params.get("tab")) ? params.get("tab") : "Overview";
let paused = false;
let scenario = "ready";
let filter = "All tasks";
const tasks = [
  {
    title: "Review the household setup",
    description:
      "Check the residents, permissions, and work that needs attention.",
    state: "Completed",
    at: "Today, 14:32",
    result:
      "The household is ready for work. Reader has read access to the shared notes folder. No pending permissions need attention. A setup note was added to the journal.",
  },
  {
    title: "Prepare a weekly reading routine",
    description:
      "Suggest a schedule and a useful format for the weekly report.",
    state: "Queued",
    at: "Today, 14:28",
    result: null,
  },
  {
    title: "Check available management tools",
    description: "Review which tools are available to this resident.",
    state: "Failed",
    at: "Today, 14:10",
    result:
      "The agent could not start. Container absence was later confirmed, and this attempt was settled with zero usage.",
  },
];
const paths = {
  home: '<path d="m3 10 9-7 9 7v10H3Z"/><path d="M9 20v-7h6v7"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  task: '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 3h6v4H9zM9 12h6M9 16h4"/>',
  pulse: '<path d="M2 12h5l3-7 4 14 3-7h5"/>',
  memory:
    '<path d="M4 4h6a3 3 0 0 1 3 3v14a4 4 0 0 0-4-2H4zM13 7a3 3 0 0 1 3-3h4v15h-3a4 4 0 0 0-4 2"/>',
  skill:
    '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"/>',
  shield:
    '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
  settings:
    '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
};
const icon = (name) =>
  `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.task}</svg>`;
const escape = (s) =>
  s.replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const status = () =>
  paused
    ? "Paused"
    : scenario === "blocked"
      ? "Needs attention"
      : scenario === "working"
        ? "Working"
        : "Ready for work";
const badge = (s) =>
  `<span class="badge ${["Failed", "Needs attention"].includes(s) ? "warning" : s === "Queued" || s === "Paused" ? "neutral" : s === "Working" ? "working" : "success"}"><i></i>${s}</span>`;
const button = (text, action, primary = false) =>
  `<button class="${primary ? "primary" : "button"}" data-action="${action}">${text}</button>`;
const go = (name, text = `View ${name.toLowerCase()}`) =>
  `<button class="text-link" data-tab="${name}">${text}${icon("arrow")}</button>`;
const tabsMarkup = () =>
  `<nav class="tabs" role="tablist" aria-orientation="${variant === "workspace" && innerWidth > 600 ? "vertical" : "horizontal"}" aria-label="Resident details">${tabs.map((t, i) => `<button role="tab" id="tab-${t}" aria-controls="panel" aria-selected="${t === tab}" tabindex="${t === tab ? 0 : -1}" data-tab="${t}">${icon(["grid", "task", "pulse", "memory", "skill", "shield", "settings"][i])}<span>${t}</span>${t === "Tasks" ? "<small>3</small>" : ""}</button>`).join("")}</nav>`;
const cardHead = (title, side = "") =>
  `<div class="card-head"><h2>${title}</h2>${side}</div>`;
function taskRows(short = false) {
  return (
    tasks
      .filter((t) => filter === "All tasks" || t.state === filter)
      .slice(0, short ? 2 : 10)
      .map(
        (t) =>
          `<button class="task-row" data-task="${tasks.indexOf(t)}"><span class="task-icon">${icon(t.state === "Completed" ? "check" : t.state === "Queued" ? "clock" : "task")}</span><span class="task-copy"><strong>${escape(t.title)}</strong><span>${short ? t.at : escape(t.description)}</span></span><span class="task-right">${badge(t.state)}${!short ? `<time>${t.at}</time>` : ""}</span><span class="row-arrow">↗</span></button>`,
      )
      .join("") || '<p class="empty">No tasks match this filter.</p>'
  );
}
function events(full = false) {
  const rows = [
    [
      "success",
      "Task completed",
      "Household setup reviewed. Result ready to read.",
      "14:32",
    ],
    [
      "neutral",
      "Journal updated",
      "Saved a note about the household configuration.",
      "14:31",
    ],
    ["working", "Task started", "Review the household setup", "14:30"],
    ["neutral", "Task queued", "Prepare a weekly reading routine", "14:28"],
    [
      "neutral",
      "Recovery confirmed",
      "No agent container remained. The earlier attempt was settled at zero usage.",
      "14:18",
    ],
    [
      "warning",
      "Run failed to start",
      "Check available management tools",
      "14:10",
    ],
  ];
  return `<ol class="timeline">${rows
    .slice(0, full ? 6 : 3)
    .map(
      ([color, title, desc, at]) =>
        `<li><span class="event-dot ${color}"></span><div><strong>${title}</strong><p>${desc}</p></div><time>${at}</time></li>`,
    )
    .join("")}</ol>`;
}
function overview() {
  return `<div class="overview-intro"><div><span class="eyebrow">At a glance</span><h2>${variant === "profile" ? "A little order for your household." : "A clear view of Karen’s work."}</h2><p>Recent work, current availability, and the essentials.</p></div><span class="date">Thursday, 10 September</span></div>
 ${scenario === "blocked" ? `<div class="alert"><div>${icon("shield")}<strong>A previous run needs recovery</strong><p>New work is held until the agent’s termination is confirmed. A cancellation request alone does not release it.</p></div>${go("Activity", "Review what happened")}</div>` : paused ? `<div class="alert"><div><strong>Karen is paused</strong><p>New tasks will wait. Pausing does not cancel work already running.</p></div>${button("Resume resident", "pause")}</div>` : ""}
 <div class="stats"><div><span>Availability</span><strong class="status-value">${badge(status())}</strong><small>${scenario === "blocked" ? "1 unresolved run" : paused ? "New work is held" : scenario === "working" ? "Reviewing household setup" : "No active runs"}</small></div><div><span>Tasks today</span><strong>3 <em>tasks</em></strong><small>1 completed · 1 queued · 1 failed</small></div><div><span>Estimated usage today</span><strong>$0.06 <em>/ $10.00</em></strong><div class="meter"><i></i></div><small>API-equivalent · not a bill</small></div></div>
 <div class="overview-grid"><div class="main-column"><section class="card recent">${cardHead("Recent tasks", go("Tasks", "All tasks"))}${taskRows(true)}</section><section class="card about">${cardHead("What Karen does")}<p class="purpose">Helps you organise the household: create residents, keep instructions clear, and turn ideas into useful work.</p><div class="tags"><span>${icon("skill")} Household management</span><span>${icon("memory")} Persistent memory</span></div><div class="mini-note">Tools and actions stay within the permissions you grant.</div></section><section class="card activity-card">${cardHead("Recent activity", go("Activity", "Full activity log"))}${events()}</section></div><aside class="side-column"><section class="card quick-task"><span class="eyebrow">Make room for the next idea</span><h2>Something for Karen?</h2><p>Give her a task and follow its progress here.</p>${button(`${icon("plus")} New task`, "new-task", true)}</section><section class="card details-card">${cardHead("Resident details")}<dl><dt>Runtime</dt><dd>Codex subscription</dd><dt>Provider login</dt><dd>Household login</dd><dt>Budget day</dt><dd>Europe/Ljubljana</dd><dt>Access</dt><dd>Management tools</dd></dl>${go("Settings", "All settings")}</section><section class="card memory-card">${cardHead("Remembered context")}<p>Keep updates concise. Ask before changing another resident’s purpose.</p>${go("Memory", "Read memory")}</section></aside></div>`;
}
function panel() {
  if (tab === "Overview") return overview();
  const heads = {
    Tasks: [
      "Tasks & results",
      "Requested work and its individual attempts. Open a task to see its result or why it is waiting.",
    ],
    Activity: [
      "Activity log",
      "A timeline of agent activity, tool calls, and recovery. Each event keeps its original time.",
    ],
    Memory: [
      "Memory & journal",
      "What Karen remembers, and the notes she writes as work progresses.",
    ],
    Skills: [
      "Skills & instructions",
      "How Karen approaches her work. Instructions do not grant access to tools or data.",
    ],
    Access: [
      "Tools & permissions",
      "The boundaries you set for this resident.",
    ],
    Settings: [
      "Resident settings",
      "Identity, runtime, spending limits, and lifecycle controls.",
    ],
  };
  let content = "";
  if (tab === "Tasks")
    content = `<div class="filter-row"><label>Show <select id="task-filter">${["All tasks", "Completed", "Queued", "Failed"].map((x) => `<option ${x === filter ? "selected" : ""}>${x}</option>`).join("")}</select></label><span>3 tasks · newest first</span></div><section class="card task-list">${taskRows()}</section><p class="footnote">A task is the requested work. A run is one attempt to carry it out.</p>`;
  if (tab === "Activity")
    content = `<section class="card full-timeline">${cardHead("Today", '<span class="live"><i></i> Sample timeline</span>')}${events(true)}</section>`;
  if (tab === "Memory")
    content = `<div class="two-column"><section class="card prose">${cardHead("Current memory", '<span class="quiet-label">Revision 3</span>')}<h3>Working preferences</h3><p>Keep updates concise and show what changed. Ask before changing another resident’s purpose.</p><h3>Household context</h3><p>Reader prepares summaries from shared notes. Karen keeps the household organised and helps define new work.</p>${button("Preview memory history", "memory-history")}</section><section class="card prose">${cardHead("Journal")}<span class="eyebrow">Today · 14:31</span><h3>Household setup reviewed</h3><p>Checked resident configuration and available management tools. No changes to permissions were needed.</p><div class="mini-note">Written by Karen during “Review the household setup”.</div></section></div>`;
  if (tab === "Skills")
    content = `<section class="card prose">${cardHead("Instructions", '<span class="quiet-label">Revision 2</span>')}<p>Help the operator organise useful work. Check the current state before making changes, stay within your granted permissions, and report the outcome clearly.</p></section><div class="two-column"><section class="card prose"><span class="icon-tile">${icon("skill")}</span><h3>Household management</h3><p>Plan resident responsibilities and keep their instructions focused.</p><span class="quiet-label">Skill revision 1</span></section><section class="card prose"><span class="icon-tile">${icon("memory")}</span><h3>Continuity</h3><p>Keep useful context in memory and record meaningful work in the journal.</p><span class="quiet-label">Skill revision 2</span></section></div>`;
  if (tab === "Access")
    content = `<section class="card prose">${cardHead("Management tools", badge("Enabled"))}<p>Karen can manage residents within the operator’s grant.</p><dl class="wide-facts"><dt>Create and configure residents</dt><dd>Up to 5 managed residents</dd><dt>Read and update her own memory</dt><dd>Allowed</dd><dt>Use mounted folders</dt><dd>No folders granted</dd><dt>Provider authentication</dt><dd>Household Codex login</dd></dl><div class="mini-note">Sample permissions for this prototype. The production page will show the resident’s actual grant.</div></section>`;
  if (tab === "Settings")
    content = `<form id="settings-form" class="card prose"><div class="card-head"><h2>Identity & execution</h2><span class="quiet-label">Preview only</span></div><div class="form-grid"><label>Resident name<input name="name" value="Karen" required maxlength="80"></label><label>Daily spending limit<input name="budget" type="number" min="0.01" step="0.01" value="10.00" required></label><label>Runtime<select><option>Codex subscription</option></select></label><label>Budget timezone<select><option>Europe/Ljubljana</option><option>UTC</option></select></label><label class="span-two">Purpose<textarea rows="3">Help organise the household and turn ideas into useful work.</textarea></label></div><p class="footnote">Spending limits use API-equivalent estimates. Subscription usage is not an API bill.</p><button class="primary" type="submit">Preview saved settings</button></form><section class="card prose">${cardHead("Availability")}<p>Pausing prevents new runs. It does not cancel work already running.</p>${button(paused ? "Resume resident" : "Pause resident", "pause")}</section><details class="card technical"><summary>Technical details</summary><dl><dt>Resident ID</dt><dd>demo-karen</dd><dt>Configuration revision</dt><dd>2</dd><dt>Memory revision</dt><dd>3</dd></dl></details>`;
  return `<div class="page-heading"><div><span class="eyebrow">Karen / ${tab}</span><h2>${heads[tab][0]}</h2><p>${heads[tab][1]}</p></div>${tab === "Tasks" ? button(`${icon("plus")} New task`, "new-task", true) : ""}</div>${content}`;
}
function render() {
  document.body.dataset.variant = variant;
  document
    .querySelectorAll("button[data-variant]")
    .forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.variant === variant)),
    );
  const direction = {
    overview: "A · Overview",
    workspace: "B · Workspace",
    profile: "C · Profile",
  }[variant];
  document.title = `${direction} · Resident page · Hearth`;
  history.replaceState(null, "", `?variant=${variant}&tab=${tab}`);
  document.querySelector("#app").innerHTML =
    `<div class="app-shell"><aside class="global-rail"><a class="logo" href="/" aria-label="Hearth home">${icon("home")}<strong>hearth<span>A home for useful agents</span></strong></a><span class="rail-label">Your household</span><a href="/" aria-label="Townhall">${icon("home")}Townhall</a><a href="/#residents" aria-label="Residents" class="selected">${icon("grid")}Residents<span>2</span></a><a href="/#tasks" aria-label="Tasks">${icon("task")}Tasks</a><a href="/#inbox" aria-label="Inbox">${icon("pulse")}Inbox</a><div class="rail-footer"><span class="operator-avatar">M</span><span>Miha<small>Operator</small></span></div></aside><main><div class="breadcrumb"><a href="/#residents">Residents</a><span>/</span><span>Karen</span><span class="prototype-mark">${direction}</span></div><header class="resident-header"><div class="avatar">K<span></span></div><div class="identity"><div class="identity-line"><h1>Karen</h1>${badge(status())}</div><p>Your household organiser</p><span class="identity-meta">Codex subscription <b>·</b> Household login</span></div><div class="header-actions">${button(paused ? "Resume" : "Pause", "pause")}${button(`${icon("plus")} New task`, "new-task", true)}</div></header><div class="resident-body">${tabsMarkup()}<section id="panel" class="content" role="tabpanel" aria-labelledby="tab-${tab}" tabindex="0">${panel()}</section></div><footer class="design-note"><div><strong>${direction}</strong><span>${variant === "overview" ? "Everyday work first. Horizontal tabs keep the rest close." : variant === "workspace" ? "A persistent section rail for moving between work and configuration." : "A calmer profile with more emphasis on purpose and recent work."}</span></div><label>Preview state <select id="scenario"><option value="ready" ${scenario === "ready" ? "selected" : ""}>Ready</option><option value="working" ${scenario === "working" ? "selected" : ""}>Working</option><option value="blocked" ${scenario === "blocked" ? "selected" : ""}>Needs attention</option></select></label></footer></main></div>`;
}
function announce(text) {
  const el = document.querySelector("#announcement");
  el.textContent = text;
  el.classList.add("visible");
  clearTimeout(announce.timer);
  announce.timer = setTimeout(() => el.classList.remove("visible"), 4500);
}
let opener;
function showDialog(title, body) {
  opener = document.activeElement;
  const d = document.querySelector("#dialog");
  d.innerHTML = `<div class="dialog-head"><div><span class="eyebrow">Design preview · sample data</span><h2 id="dialog-title">${title}</h2></div><button aria-label="Close dialog" data-action="close">×</button></div>${body}`;
  d.showModal();
}
document.querySelector("#dialog").addEventListener("close", () => {
  if (opener?.isConnected) opener.focus();
});
document.addEventListener("click", (e) => {
  const variantButton = e.target.closest("button[data-variant]");
  if (variantButton) {
    variant = variantButton.dataset.variant;
    render();
    return;
  }
  const tabButton = e.target.closest("[data-tab]");
  if (tabButton) {
    tab = tabButton.dataset.tab;
    render();
    document.querySelector(`#tab-${tab}`).focus();
    return;
  }
  const task = e.target.closest("[data-task]");
  if (task) {
    const t = tasks[Number(task.dataset.task)];
    showDialog(
      escape(t.title),
      `<div class="dialog-body">${badge(t.state)}<p>${escape(t.description)}</p><h3>${t.state === "Completed" ? "Result" : t.state === "Queued" ? "Waiting to start" : "What happened"}</h3><p>${t.result || "This task is queued. It will be ready to start when the resident is available."}</p><div class="mini-note">${t.state === "Queued" ? "No run has started." : `One run · ${t.at} · ${t.state === "Failed" ? "$0.00" : "$0.06"} estimated usage`}</div></div>`,
    );
    return;
  }
  const action = e.target.closest("[data-action]")?.dataset.action;
  if (action === "close") document.querySelector("#dialog").close();
  if (action === "pause") {
    paused = !paused;
    render();
    announce(
      `${paused ? "Paused" : "Resumed"} in this preview only. Karen’s live state is unchanged.`,
    );
  }
  if (action === "new-task")
    showDialog(
      "Give Karen a task",
      `<form id="task-form" class="dialog-body"><label for="task-instructions">What would you like Karen to do?</label><textarea id="task-instructions" rows="5" required maxlength="32000" placeholder="Describe the outcome you want…"></textarea><p>Uses the resident’s configured tools and permissions.</p><div class="dialog-footer"><small>This preview does not submit real work.</small><button class="primary" type="submit">Run task ${icon("arrow")}</button></div></form>`,
    );
  if (action === "memory-history")
    showDialog(
      "Memory history",
      `<div class="dialog-body"><h3>Revision 3 · Today, 14:31</h3><p>Added household context after reviewing the setup.</p><h3>Revision 2 · Yesterday, 09:20</h3><p>Added your preference for concise updates.</p><h3>Revision 1 · Created</h3><p>Initial working preferences.</p></div>`,
    );
});
document.addEventListener("keydown", (e) => {
  if (!e.target.matches('[role="tab"]')) return;
  const vertical = variant === "workspace" && innerWidth > 600;
  const next = vertical ? "ArrowDown" : "ArrowRight",
    prev = vertical ? "ArrowUp" : "ArrowLeft";
  if (![next, prev, "Home", "End"].includes(e.key)) return;
  e.preventDefault();
  let i = tabs.indexOf(tab);
  i =
    e.key === "Home"
      ? 0
      : e.key === "End"
        ? tabs.length - 1
        : (i + (e.key === next ? 1 : -1) + tabs.length) % tabs.length;
  tab = tabs[i];
  render();
  document.querySelector(`#tab-${tab}`).focus();
});
document.addEventListener("change", (e) => {
  if (e.target.id === "task-filter") {
    filter = e.target.value;
    render();
    document.querySelector("#task-filter").focus();
  }
  if (e.target.id === "scenario") {
    scenario = e.target.value;
    paused = false;
    render();
    document.querySelector("#scenario").focus();
  }
});
document.addEventListener("submit", (e) => {
  if (e.target.id === "task-form") {
    e.preventDefault();
    document.querySelector("#dialog").close();
    announce("Task preview complete. No task was sent to Karen.");
  }
  if (e.target.id === "settings-form") {
    e.preventDefault();
    announce("Settings preview complete. No live settings were changed.");
  }
});
render();
