// labnet web UI. Plain JavaScript, no framework and no build step.
//
// The page has four parts, each drawn by one function from the same state:
//   renderHeader()    lab picker and lab-wide buttons
//   renderGraph()     the topology (Cytoscape.js)
//   renderDetails()   the selected node or network, with its controls
//   renderScenario()  guided scenarios: task, check results, hints
// plus browser terminals (xterm.js), one tab per node.
//
// All data comes from the JSON API in labnet/web/app.py. The page polls
// /api/labs every few seconds, so changes made in a terminal show up too.

"use strict";

// ---------------------------------------------------------------- helpers

const $ = (id) => document.getElementById(id);

// Create a DOM element: h("button", {class: "x", onclick: fn}, "label").
// Text is always inserted as text, never as HTML, so node names and command
// output cannot inject markup.
function h(tag, attrs, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") element.className = value;
    else if (key.startsWith("on")) element.addEventListener(key.slice(2), value);
    else if (key === "disabled" || key === "hidden") element[key] = Boolean(value);
    else element.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    element.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return element;
}

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error([data.error, data.hint].filter(Boolean).join("\n") || response.statusText);
  }
  return data;
}

// ------------------------------------------------------------------ state

const state = {
  labs: [],             // from /api/labs
  labName: null,        // the lab being shown
  selected: null,       // {type: "node" | "network", name}
  scenarios: [],        // from /api/scenarios
  scenario: null,       // details of the current lab's scenario
  results: null,        // last check results
  hintsShown: 0,
  busy: false,
};

const currentLab = () => state.labs.find((lab) => lab.name === state.labName) || null;

let messageTimer = null;
function showMessage(kind, text) {
  const box = $("message");
  clearTimeout(messageTimer);
  box.hidden = !text;
  box.className = kind;
  box.textContent = text || "";
  if (kind === "done") messageTimer = setTimeout(() => (box.hidden = true), 4000);
}

// Run a slow action with a "working" banner, then refresh everything.
async function run(label, action, doneText) {
  if (state.busy) return undefined;
  state.busy = true;
  showMessage("busy", label + " ...");
  renderHeader();
  renderDetails();
  renderScenario(); // greys out the buttons while the action runs
  let result;
  try {
    result = await action();
    showMessage("done", doneText || "Done.");
  } catch (error) {
    showMessage("error", error.message);
  }
  state.busy = false;
  await refresh(true);
  return result;
}

async function refresh(detail) {
  let labs;
  try {
    labs = await api("GET", "/api/labs" + (detail ? "?detail=true" : ""));
  } catch (error) {
    showMessage("error", "Cannot reach the labnet server: " + error.message);
    return;
  }
  if (!detail) keepShaping(labs, state.labs);
  state.labs = labs;
  if (!currentLab()) state.labName = labs.length ? labs[0].name : null;
  await loadScenarioForLab();
  renderHeader();
  renderGraph();
  renderDetails();
  renderScenario();
}

// A quick refresh does not ask the nodes for delay/loss settings (that costs
// one command per node). Carry over what the last detailed refresh found.
function keepShaping(fresh, old) {
  const known = new Map();
  for (const lab of old)
    for (const node of lab.nodes)
      for (const iface of node.interfaces) known.set(`${lab.name}/${node.node}/${iface.name}`, iface.shaping);
  for (const lab of fresh)
    for (const node of lab.nodes)
      for (const iface of node.interfaces)
        iface.shaping = node.running ? known.get(`${lab.name}/${node.node}/${iface.name}`) || "" : "";
}

function selectLab(name) {
  state.labName = name;
  state.selected = null;
  refresh(true);
}

// ----------------------------------------------------------------- header

function renderHeader() {
  const lab = currentLab();
  const select = $("lab-select");
  select.replaceChildren(
    ...state.labs.map((item) => h("option", { value: item.name, selected: item.name === state.labName ? "" : null }, item.name)),
  );
  if (!state.labs.length) select.replaceChildren(h("option", {}, "(none)"));
  select.disabled = !state.labs.length;

  const running = lab ? lab.nodes.filter((node) => node.running).length : 0;
  $("lab-summary").textContent = lab
    ? `${running} of ${lab.nodes.length} nodes running` + (lab.scenario ? `  ·  scenario: ${state.scenario ? state.scenario.title : lab.scenario}` : "")
    : "";
  for (const id of ["btn-start", "btn-stop", "btn-snapshot", "btn-restore", "btn-delete"]) {
    $(id).disabled = !lab || state.busy;
  }
}

function setupHeader() {
  $("lab-select").addEventListener("change", (event) => selectLab(event.target.value));
  const lab = () => encodeURIComponent(state.labName);
  $("btn-start").onclick = () => run("Starting the lab", () => api("POST", `/api/labs/${lab()}/start`), "Lab started.");
  $("btn-stop").onclick = () => run("Stopping the lab", () => api("POST", `/api/labs/${lab()}/stop`), "Lab stopped.");
  $("btn-snapshot").onclick = () => {
    const name = prompt("Name for the snapshot:", "snap");
    if (name) run(`Saving snapshot '${name}'`, () => api("POST", `/api/labs/${lab()}/snapshot`, { name }), `Snapshot '${name}' saved.`);
  };
  $("btn-restore").onclick = () => {
    const names = [...new Set(currentLab().nodes.flatMap((node) => node.snapshots))];
    if (!names.length) return showMessage("error", "This lab has no snapshots yet. Take one with the Snapshot button.");
    const name = prompt(`Restore which snapshot? Available: ${names.join(", ")}\nChanges since then are lost and running nodes restart.`, names[0]);
    if (name) run(`Restoring snapshot '${name}'`, () => api("POST", `/api/labs/${lab()}/restore`, { name }), `Back at snapshot '${name}'.`);
    return undefined;
  };
  $("btn-delete").onclick = () => {
    if (confirm(`Delete the lab '${state.labName}' and everything configured in it?`)) {
      closeTerminalsOf(state.labName);
      run("Deleting the lab", () => api("DELETE", `/api/labs/${lab()}`), "Lab deleted.");
    }
  };
}

// ------------------------------------------------------------------ graph

const svgIcon = (body) =>
  "data:image/svg+xml;utf8," +
  encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 64 64">${body}</svg>`);

const ICONS = {
  router: svgIcon(
    '<circle cx="32" cy="32" r="29" fill="#1f6feb"/>' +
      '<g stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" fill="none">' +
      '<path d="M32 13v38M13 32h38"/><path d="M26 19l6-6 6 6M26 45l6 6 6-6M19 26l-6 6 6 6M45 26l6 6-6 6"/></g>',
  ),
  server: svgIcon(
    '<rect x="12" y="5" width="40" height="54" rx="5" fill="#24292f"/>' +
      [11, 27, 43].map((y) => `<rect x="17" y="${y}" width="30" height="11" rx="2" fill="#57606a"/><circle cx="23" cy="${y + 5.5}" r="2.2" fill="#3fb950"/>`).join(""),
  ),
  pc: svgIcon(
    '<rect x="7" y="9" width="50" height="34" rx="4" fill="#57606a"/><rect x="12" y="14" width="40" height="24" fill="#cfe4ff"/>' +
      '<path d="M26 43h12l2 8H24z" fill="#57606a"/><rect x="17" y="51" width="30" height="5" rx="2.5" fill="#57606a"/>',
  ),
  network: svgIcon(
    '<rect x="3" y="16" width="58" height="32" rx="7" fill="#0e8a7d"/>' +
      '<g stroke="#fff" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round" fill="none">' +
      '<path d="M15 27h28M37 22l6 5-6 5"/><path d="M49 38H21M27 33l-6 5 6 5"/></g>',
  ),
  uplink: svgIcon('<path d="M17 49a13 13 0 0 1 1-26 17 17 0 0 1 32 4 11 11 0 0 1-2 22z" fill="#8250df"/>'),
};

const GRAPH_STYLE = [
  {
    selector: "node",
    style: {
      "background-opacity": 0, "background-image": "data(icon)", "background-fit": "contain",
      width: 50, height: 50, label: "data(label)", "font-size": 13, "font-weight": 600, color: "#1c2733",
      "text-valign": "bottom", "text-margin-y": 5, "text-wrap": "wrap",
      "text-background-color": "#f4f6f8", "text-background-opacity": 0.85, "text-background-padding": 2,
    },
  },
  { selector: "node.network", style: { width: 52, height: 40, "font-weight": 400, color: "#0b5e55" } },
  { selector: "node.uplink", style: { width: 60, height: 48, color: "#6639ba" } },
  { selector: "node.stopped", style: { opacity: 0.4 } },
  { selector: "node:selected", style: { "overlay-color": "#1f6feb", "overlay-opacity": 0.16, "overlay-padding": 7 } },
  {
    selector: "edge",
    style: {
      width: 2.5, "line-color": "#8c959f", "curve-style": "straight",
      label: "data(label)", "font-size": 10, color: "#4b5a69", "text-wrap": "wrap",
      "text-background-color": "#f4f6f8", "text-background-opacity": 1, "text-background-padding": 2,
      "source-label": "data(iface)", "source-text-offset": 40,
    },
  },
  { selector: "edge.down", style: { "line-color": "#cf222e", "line-style": "dashed", color: "#cf222e" } },
  { selector: "edge.shaped", style: { "line-color": "#b35900", width: 4, color: "#b35900" } },
];

let cy = null;
let graphSignature = "";

function graphElements(lab) {
  const elements = [];
  for (const network of lab.networks) {
    elements.push({
      group: "nodes",
      data: { id: "net:" + network.network, label: network.uplink ? `${network.network}\n(internet)` : network.network, icon: network.uplink ? ICONS.uplink : ICONS.network },
      classes: network.uplink ? "network uplink" : "network",
    });
  }
  for (const node of lab.nodes) {
    elements.push({
      group: "nodes",
      data: { id: "n:" + node.node, label: node.running ? node.node : `${node.node}\n(stopped)`, icon: ICONS[node.role] || ICONS.pc },
      classes: node.running ? "device" : "device stopped",
    });
    for (const iface of node.interfaces) {
      const down = node.running && !iface.up;
      const notes = [iface.addresses.join("\n"), down ? "unplugged" : "", iface.shaping].filter(Boolean);
      elements.push({
        group: "edges",
        data: { id: `e:${node.node}:${iface.name}`, source: "n:" + node.node, target: "net:" + iface.network, iface: iface.name, label: notes.join("\n") },
        classes: [down ? "down" : "", iface.shaping ? "shaped" : ""].join(" ").trim(),
      });
    }
  }
  return elements;
}

function renderGraph() {
  const lab = currentLab();
  $("empty").hidden = Boolean(lab);
  if (!cy) {
    cy = cytoscape({ container: $("graph"), style: GRAPH_STYLE, minZoom: 0.3, maxZoom: 2.5 });
    cy.on("tap", "node", (event) => onGraphTap(event.target.id()));
    cy.on("tap", (event) => {
      if (event.target === cy) { state.selected = null; renderDetails(); }
    });
    cy.on("dragfree", "node", savePositions);
  }
  const elements = lab ? graphElements(lab) : [];
  const signature = (lab ? lab.name : "") + "|" + elements.map((element) => element.data.id).join(",");

  if (signature !== graphSignature) {
    // The set of devices or links changed: rebuild and lay out again.
    graphSignature = signature;
    cy.elements().remove();
    cy.add(elements);
    layoutGraph(lab);
  } else {
    // Same topology: only update labels and states, keep positions.
    for (const element of elements) {
      const existing = cy.getElementById(element.data.id);
      existing.data(element.data);
      existing.classes(element.classes);
    }
  }
  cy.nodes().unselect();
  if (state.selected) cy.getElementById((state.selected.type === "node" ? "n:" : "net:") + state.selected.name).select();
}

function layoutGraph(lab) {
  if (!lab || !cy.nodes().length) return;
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem("labnet-positions:" + lab.name)); } catch { saved = null; }
  if (saved && cy.nodes().every((node) => saved[node.id()])) {
    cy.nodes().forEach((node) => node.position(saved[node.id()]));
    fitGraph();
    return;
  }
  // Layered, top-down: the internet uplink (or else the first router) at the
  // top, then whatever is one cable away, and so on down to the hosts.
  const uplinks = lab.networks.filter((network) => network.uplink).map((network) => "net:" + network.network);
  const routers = lab.nodes.filter((node) => node.role === "router").map((node) => "n:" + node.node);
  const roots = uplinks.length ? uplinks : routers.length ? [routers[0]] : [cy.nodes()[0].id()];
  cy.layout({ name: "breadthfirst", roots: roots.map((id) => "#" + CSS.escape(id)).join(","), directed: false, animate: false, spacingFactor: 1.15, avoidOverlap: true, nodeDimensionsIncludeLabels: true }).run();
  fitGraph();
}

// Show the whole topology, without zooming in so far that a small lab looks huge.
function fitGraph() {
  cy.resize();
  cy.fit(undefined, 60);
  if (cy.zoom() > 1.4) { cy.zoom(1.4); cy.center(); }
}

function savePositions() {
  const positions = {};
  cy.nodes().forEach((node) => (positions[node.id()] = node.position()));
  try { localStorage.setItem("labnet-positions:" + state.labName, JSON.stringify(positions)); } catch { /* storage unavailable */ }
}

function onGraphTap(id) {
  if (id.startsWith("net:")) {
    state.selected = { type: "network", name: id.slice(4) };
  } else {
    const name = id.slice(2);
    state.selected = { type: "node", name };
    const node = currentLab().nodes.find((item) => item.node === name);
    if (node && node.running) openTerminal(state.labName, name);
  }
  showTab("details");
  renderDetails();
}

// ---------------------------------------------------------------- details

// The page polls every few seconds. Rebuilding a panel that has not changed
// would lose the scroll position, any text selection, and a click that
// lands just as the rebuild happens. So each panel remembers what it was
// last drawn from and is rebuilt only when that differs.
const drawnFrom = {};
function unchanged(panel, ...inputs) {
  const signature = JSON.stringify(inputs);
  if (drawnFrom[panel] === signature) return true;
  drawnFrom[panel] = signature;
  return false;
}

function renderDetails() {
  const box = $("details");
  // Do not redraw while the user is typing a delay or loss value.
  if (box.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return;
  const lab = currentLab();
  if (unchanged("details", lab, state.selected, state.busy)) return;
  if (!lab) return box.replaceChildren(h("p", { class: "muted" }, "No lab selected."));
  if (state.selected && state.selected.type === "node") {
    const node = lab.nodes.find((item) => item.node === state.selected.name);
    if (node) return box.replaceChildren(...nodeDetails(lab, node));
  }
  if (state.selected && state.selected.type === "network") {
    const network = lab.networks.find((item) => item.network === state.selected.name);
    if (network) return box.replaceChildren(...networkDetails(lab, network));
  }
  return box.replaceChildren(...labDetails(lab));
}

function labDetails(lab) {
  return [
    h("h2", {}, "Lab ", lab.name),
    h("p", { class: "muted" }, "Click a device in the picture to open its console and see its controls. Click a switch to see what is plugged into it."),
    h("h3", {}, "Devices"),
    h("table", {},
      h("tr", {}, h("th", {}, "Node"), h("th", {}, "Role"), h("th", {}, "State"), h("th", {}, "Addresses")),
      lab.nodes.map((node) =>
        h("tr", {},
          h("td", {}, h("a", { href: "#", onclick: (event) => { event.preventDefault(); onGraphTap("n:" + node.node); renderGraph(); } }, node.node)),
          h("td", {}, node.role),
          h("td", {}, h("span", { class: "badge " + (node.running ? "running" : "stopped") }, node.running ? "running" : "stopped")),
          h("td", { class: "mono" }, node.interfaces.flatMap((iface) => iface.addresses).join(", ") || "-"),
        ),
      ),
    ),
    h("h3", {}, "Networks"),
    h("table", {},
      lab.networks.map((network) =>
        h("tr", {}, h("td", {}, network.network), h("td", { class: "muted" }, network.uplink ? `uplink to the internet, gateway ${network.gateway}` : "switch")),
      ),
    ),
  ];
}

function nodeDetails(lab, node) {
  const base = `/api/labs/${encodeURIComponent(lab.name)}/nodes/${encodeURIComponent(node.node)}`;
  const parts = [
    h("h2", {}, node.node, h("span", { class: "badge" }, node.role), h("span", { class: "badge " + (node.running ? "running" : "stopped") }, node.running ? "running" : "stopped")),
    h("div", { class: "row" },
      h("button", { class: "primary", disabled: !node.running, onclick: () => openTerminal(lab.name, node.node) }, "Open console"),
      node.running
        ? h("button", { onclick: () => run(`Stopping ${node.node}`, () => api("POST", base + "/stop"), `${node.node} stopped.`) }, "Stop node")
        : h("button", { onclick: () => run(`Starting ${node.node}`, () => api("POST", base + "/start"), `${node.node} started.`) }, "Start node"),
    ),
    h("h3", {}, "Interfaces"),
  ];
  for (const iface of node.interfaces) parts.push(interfaceCard(node, iface, base));
  parts.push(
    h("h3", {}, "Snapshots"),
    h("p", { class: node.snapshots.length ? "mono" : "muted" }, node.snapshots.join(", ") || "none"),
    h("p", { class: "muted" }, "Same console in a terminal: ", h("code", {}, `labnet shell ${lab.name}/${node.node}`)),
  );
  return parts;
}

function interfaceCard(node, iface, base) {
  const link = `${base}/links/${encodeURIComponent(iface.network)}`;
  const where = `${node.node} ${iface.name}`;
  const delay = h("input", { placeholder: "50ms", value: (iface.shaping.match(/delay (\S+)/) || [])[1] || "" });
  const loss = h("input", { placeholder: "0", value: (iface.shaping.match(/loss ([\d.]+)/) || [])[1] || "" });
  const down = node.running && !iface.up;
  return h("div", { class: "iface" },
    h("div", { class: "title" },
      iface.name, h("span", { class: "muted" }, "→ " + iface.network), h("span", { class: "spacer" }),
      node.running ? h("span", { class: "badge " + (down ? "down" : "running") }, down ? "unplugged" : "link up") : null,
    ),
    h("div", { class: "mono" }, iface.addresses.join(", ") || "no IPv4 address"),
    node.running && h("div", { class: "row" },
      down
        ? h("button", { class: "small", onclick: () => run(`Plugging in ${where}`, () => api("POST", link, { state: "up" }), "Cable plugged in.") }, "Plug cable in")
        : h("button", { class: "small", onclick: () => run(`Unplugging ${where}`, () => api("POST", link, { state: "down" }), "Cable unplugged.") }, "Unplug cable"),
    ),
    node.running && h("div", { class: "row" },
      h("label", {}, "delay", delay), h("label", {}, "loss %", loss),
      h("button", { class: "small", onclick: () => { document.activeElement.blur(); run(`Shaping ${where}`, () => api("POST", link, { delay: delay.value.trim(), loss: loss.value.trim() }), "Link settings applied."); } }, "Apply"),
      iface.shaping && h("button", { class: "small", onclick: () => run(`Clearing ${where}`, () => api("POST", link, {}), "Delay and loss removed.") }, "Clear"),
    ),
    iface.shaping && h("div", { class: "muted" }, `Applied to packets this node sends: ${iface.shaping}`),
  );
}

function networkDetails(lab, network) {
  const members = lab.nodes.flatMap((node) => node.interfaces.filter((iface) => iface.network === network.network).map((iface) => ({ node, iface })));
  return [
    h("h2", {}, network.network, h("span", { class: "badge" }, network.uplink ? "uplink" : "switch")),
    h("p", { class: "muted" },
      network.uplink
        ? `The lab's connection to the internet. A gateway at ${network.gateway} hands out addresses by DHCP, answers DNS and NATs to the outside, like an internet provider.`
        : "A plain layer-2 switch. It has no address and runs no DHCP or DNS: whatever happens on it is up to the devices plugged in.",
    ),
    h("h3", {}, "Plugged in"),
    h("table", {},
      h("tr", {}, h("th", {}, "Node"), h("th", {}, "Interface"), h("th", {}, "Address")),
      members.map(({ node, iface }) => h("tr", {}, h("td", {}, node.node), h("td", {}, iface.name), h("td", { class: "mono" }, iface.addresses.join(", ") || "-"))),
    ),
  ];
}

// --------------------------------------------------------------- scenario

async function loadScenarioForLab() {
  const lab = currentLab();
  const reference = lab ? lab.scenario : null;
  if (!reference) { state.scenario = null; return; }
  if (state.scenario && state.scenario.reference === reference) return;
  try {
    state.scenario = await api("GET", "/api/scenario?ref=" + encodeURIComponent(reference));
    state.scenario.reference = reference;
  } catch { state.scenario = null; }
  state.results = null;
  state.hintsShown = 0;
}

function renderScenario() {
  const box = $("scenario");
  const active = state.scenario;
  const open = state.labs.filter((lab) => lab.scenario).map((lab) => lab.name);
  if (unchanged("scenario", active, state.results, state.hintsShown, state.busy, open, state.scenarios.length)) return;
  const parts = [];
  const query = active ? "?ref=" + encodeURIComponent(active.reference) : "";

  if (active) {
    parts.push(
      h("h2", {}, `${active.number}. ${active.title}`),
      h("div", { class: "row" },
        h("button", { class: "primary", disabled: state.busy, onclick: checkScenario }, "Check my work"),
        h("button", { disabled: state.hintsShown >= active.hints.length, onclick: () => { state.hintsShown += 1; renderScenario(); } },
          state.hintsShown >= active.hints.length ? "No more hints" : `Show hint ${state.hintsShown + 1} of ${active.hints.length}`),
        h("button", { class: "danger", disabled: state.busy, onclick: () => {
          if (confirm("Reset the scenario? Everything you configured in it is lost.")) {
            closeTerminalsOf(active.lab);
            run("Rebuilding the scenario", () => api("POST", "/api/scenario/reset" + query), "Scenario reset.");
          }
        } }, "Reset"),
      ),
    );
    if (state.results) parts.push(resultsList(state.results));
    for (let index = 0; index < state.hintsShown; index += 1) {
      parts.push(h("div", { class: "hint" }, h("b", {}, `Hint ${index + 1}: `), active.hints[index]));
    }
    // The task text is Markdown from the scenario's own task.md file.
    const task = h("div", { class: "task" });
    task.innerHTML = marked.parse(active.task);
    parts.push(task);
  }

  parts.push(h("h3", {}, active ? "Other scenarios" : "Guided scenarios"));
  if (!active) parts.push(h("p", { class: "muted" }, "A scenario builds a lab, gives you a task, and checks whether you solved it."));
  const list = h("div", { class: "scenario-list" });
  for (const item of state.scenarios) {
    const running = state.labs.some((lab) => lab.name === item.lab && lab.scenario);
    list.append(
      h("button", { title: item.summary, disabled: state.busy, onclick: () => (running ? selectLab(item.lab) : startScenario(item)) },
        h("span", { class: "num" }, item.number), h("span", { class: "grow" }, item.title),
        running ? h("span", { class: "badge running" }, "open") : h("span", { class: "muted" }, "start"),
      ),
    );
  }
  parts.push(list);
  box.replaceChildren(...parts);
}

function resultsList(results) {
  const solved = results.passed === results.total;
  return h("div", {},
    h("p", {}, h("b", {}, solved ? `Solved: all ${results.total} checks passed.` : `${results.passed} of ${results.total} checks passed.`)),
    h("ul", { class: "results" },
      results.results.map((result) =>
        h("li", {},
          h("span", { class: "badge " + (result.passed ? "pass" : "fail") }, result.passed ? "PASS" : "FAIL"), " ", result.name,
          !result.passed && h("div", { class: "why" }, "Found: " + result.detail),
          !result.passed && result.explain && h("div", { class: "why" }, result.explain),
        ),
      ),
    ),
  );
}

async function checkScenario() {
  const reference = state.scenario.reference;
  const results = await run("Checking (some checks wait for leases or routing)", () => api("POST", "/api/scenario/check?ref=" + encodeURIComponent(reference)), "Check finished.");
  if (results) { state.results = results; renderScenario(); }
}

async function startScenario(item) {
  const started = await run(`Building the lab for '${item.title}' (the first time can take a few minutes)`, () => api("POST", "/api/scenario/start?ref=" + encodeURIComponent(item.name)), "Scenario ready. Read the task on the right.");
  if (started) { state.selected = null; state.labName = started.lab; await refresh(true); showTab("scenario"); }
}

function showTab(name) {
  for (const button of document.querySelectorAll("#tabs button")) button.classList.toggle("active", button.dataset.tab === name);
  $("details").hidden = name !== "details";
  $("scenario").hidden = name !== "scenario";
}

// -------------------------------------------------------------- terminals

const terminals = new Map(); // "lab/node" -> {term, fit, socket, tab, pane}
let activeTerminal = null;

function openTerminal(lab, node) {
  const key = `${lab}/${node}`;
  if (terminals.has(key)) return activateTerminal(key);

  const pane = h("div", { class: "term" });
  const tab = h("div", { class: "tab", onclick: () => activateTerminal(key) }, key,
    h("button", { class: "close", title: "Close this console", onclick: (event) => { event.stopPropagation(); closeTerminal(key); } }, "×"));
  $("terminals").append(pane);
  $("terminal-tabs").append(tab);
  $("terminal-pane").hidden = false;

  const term = new Terminal({ cursorBlink: true, fontSize: 13, scrollback: 5000, fontFamily: 'ui-monospace, Consolas, "DejaVu Sans Mono", monospace', theme: { background: "#101418" } });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(pane);

  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${location.host}/ws/terminal/${encodeURIComponent(lab)}/${encodeURIComponent(node)}`);
  socket.binaryType = "arraybuffer";
  const send = (message) => { if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message)); };
  socket.onopen = () => { fit.fit(); send({ type: "resize", rows: term.rows, cols: term.cols }); term.focus(); };
  socket.onmessage = (event) => term.write(typeof event.data === "string" ? event.data : new Uint8Array(event.data));
  socket.onclose = () => term.write("\r\n\x1b[90m[console closed]\x1b[0m\r\n");
  term.onData((data) => send({ type: "input", data }));
  term.onResize(({ rows, cols }) => send({ type: "resize", rows, cols }));

  terminals.set(key, { term, fit, socket, tab, pane });
  activateTerminal(key);
  fitGraph(); // the picture has less room now
  return undefined;
}

function activateTerminal(key) {
  activeTerminal = key;
  for (const [name, item] of terminals) {
    item.pane.hidden = name !== key;
    item.tab.classList.toggle("active", name === key);
  }
  const item = terminals.get(key);
  if (item) { item.fit.fit(); item.term.focus(); }
}

function closeTerminal(key) {
  const item = terminals.get(key);
  if (!item) return;
  item.socket.close();
  item.term.dispose();
  item.tab.remove();
  item.pane.remove();
  terminals.delete(key);
  const next = [...terminals.keys()].pop();
  if (next) activateTerminal(next);
  else { activeTerminal = null; $("terminal-pane").hidden = true; fitGraph(); }
}

function closeTerminalsOf(lab) {
  for (const key of [...terminals.keys()]) if (key.startsWith(lab + "/")) closeTerminal(key);
}

// ------------------------------------------------------------------ start

async function main() {
  setupHeader();
  for (const button of document.querySelectorAll("#tabs button")) button.onclick = () => showTab(button.dataset.tab);
  window.addEventListener("resize", () => { if (activeTerminal) terminals.get(activeTerminal).fit.fit(); fitGraph(); });

  // Optional deep link: /#lab=office&node=r1&tab=scenario
  const wanted = new URLSearchParams(location.hash.slice(1));
  if (wanted.get("lab")) state.labName = wanted.get("lab");

  try { state.scenarios = await api("GET", "/api/scenarios"); } catch (error) { showMessage("error", error.message); }
  await refresh(true);
  if (wanted.get("tab")) showTab(wanted.get("tab"));
  const lab = currentLab();
  if (lab && wanted.get("node") && lab.nodes.some((node) => node.node === wanted.get("node"))) {
    onGraphTap("n:" + wanted.get("node"));
    renderGraph();
    if (wanted.get("tab")) showTab(wanted.get("tab"));
  }

  let ticks = 0;
  setInterval(() => {
    if (state.busy || document.hidden) return;
    ticks += 1;
    refresh(ticks % 3 === 0);
  }, 4000);
}

main();
