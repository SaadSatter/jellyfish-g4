import {drawPopulation, el, num} from "./charts.js";

const $ = id => document.getElementById(id);
const state = {catalog: null, batch: null, runs: [], selectedRun: null, chart: [], chartRequest: 0};
const histories = new Map();
const form = $("batch-form");
const completed = run => ["complete", "extinct"].includes(run?.status);
const list = value => value.trim().split(/[,\s]+/).filter(Boolean);
const scopeKey = run => [run.memory, run.lifetime, run.days].join("/");
const scopeLabel = run => run.memory + " B · " + run.lifetime + " y · " + num(run.days) + " d";
const statuses = {pending: "Pending", complete: "Complete", extinct: "Extinct", crash: "Crash", timeout: "Timeout", error: "Error"};

async function request(path, settings) {
  const response = await fetch(path, settings === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(settings),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}

function choose(id, entries, fallback) {
  const input = $(id);
  const old = input.value;
  input.replaceChildren(...entries.map(([value, label]) => new Option(label, value)));
  if (entries.some(([value]) => String(value) === old)) input.value = old;
  else if (fallback !== undefined) input.value = fallback;
  input.disabled = entries.length === 0;
}

function status(message, error = false) {
  $("form-status").textContent = message;
  $("form-status").classList.toggle("error", error);
}

function batchSize() {
  const settings = new FormData(form);
  const size = ["versions", "seeds", "memories", "lifetimes", "days"]
    .reduce((total, field) => total * new Set(list(settings.get(field))).size, 1);
  $("start").textContent = size ? "Run " + num(size) + (size === 1 ? " trial" : " trials") : "Run batch";
}

function applyPreset() {
  const preset = $("preset").value;
  if (!state.catalog) return;
  for (const [key, values] of Object.entries(state.catalog.presets[preset])) {
    form.elements.namedItem(key).value = values.join(", ");
  }
  form.elements.namedItem("label").value = $("preset").selectedOptions[0].text;
  batchSize();
}

async function loadCatalog(preferred) {
  state.catalog = await request("/api/catalog");
  choose("map", [["", "Random by seed"], ...state.catalog.maps.map(path => [path, path.split("/").at(-1)])]);
  const batches = state.catalog.batches;
  choose("batches", batches.length ? batches.map(batch => [batch.id, batch.name + " · " + batch.id.slice(0, 6)])
    : [["", "No saved batches"]]);
  if (preferred) $("batches").value = preferred;
  await loadBatch();
}

async function loadBatch() {
  const id = $("batches").value;
  if (!id) return;
  const batch = await request("/api/batches/" + id);
  if ($("batches").value !== id) return;
  const newBatch = state.batch?.manifest.id !== id;
  const updated = newBatch || batch.active !== state.batch.active
    || Object.keys(batch.results).length !== Object.keys(state.batch.results).length;
  if (!updated) return;
  state.batch = batch;
  if (newBatch) state.selectedRun = null;
  state.runs = batch.manifest.jobs.map(job => ({
    ...job, label: batch.manifest.versions[job.version].label,
    status: "pending", ...batch.results[job.id],
  }));
  renderBatch(newBatch);
}

function renderBatch(newBatch) {
  const {manifest, active, results} = state.batch;
  const count = Object.keys(results).length;
  const total = manifest.jobs.length;
  const failures = state.runs.filter(run => ["crash", "timeout", "error"].includes(run.status)).length;
  $("batch-meta").textContent = manifest.spec.label + " · J=" + num(manifest.spec.initial)
    + " · cap " + num(manifest.spec.cap) + " · simulator " + manifest.simulator_commit.slice(0, 7);
  $("recorded").textContent = num(count) + " / " + num(total);
  const phase = active ? "Running" : count === total ? "Finished" : "Interrupted";
  $("batch-status").textContent = phase + (failures ? " · " + num(failures) + " with errors" : "");
  $("start").disabled = active;
  const versions = manifest.versions.map((version, index) => [index, version.label]);
  if (newBatch) {
    for (const id of ["baseline", "candidate", "scope", "compare-seed", ...Object.keys(filters)]) {
      $(id).replaceChildren();
    }
  }
  choose("baseline", versions, 0);
  choose("candidate", versions, versions.length > 1 ? 1 : 0);
  choose("scope", [...new Map(state.runs.map(run => [scopeKey(run), scopeLabel(run)]))]);
  for (const [id, key] of Object.entries(filters)) {
    const options = key === "status"
      ? Object.entries(statuses)
      : [...new Map(state.runs.map(run => [run[key], key === "version" ? run.label : num(run[key])]))];
    choose(id, [["", "All"], ...options]);
  }
  $("export-json").href = "/api/batches/" + manifest.id + "/export";
  $("export-json").hidden = false;
  renderComparison();
  renderRuns();
}

function metric(runs, name) {
  const eligible = runs.filter(run => completed(run)
    && (name !== "replacement" || run.founders_out)
    && (name !== "growth" || state.batch.manifest.spec.cap >= 10 * state.batch.manifest.spec.initial));
  const successes = eligible.filter(run => {
    if (name === "replacement") return run.stats.final_population >= state.batch.manifest.spec.initial;
    if (name === "extinct") return run.status === "extinct";
    return run.stats.day_reached_10J != null;
  });
  return {eligible: eligible.length, successes: successes.length,
    fraction: eligible.length ? successes.length / eligible.length : null};
}

const pct = result => result.fraction == null ? "—" : num(Math.round(100 * result.fraction)) + "%";
const countLabel = result => num(result.successes) + "/" + num(result.eligible);

function comparisonRuns() {
  const inScope = state.runs.filter(run => scopeKey(run) === $("scope").value);
  return ["baseline", "candidate"].map(id => inScope.filter(run => String(run.version) === $(id).value));
}

function pairBySeed(baseline, candidate) {
  const bySeed = new Map(baseline.map(run => [run.seed, run]));
  return candidate.map(run => [bySeed.get(run.seed), run]).filter(([base]) => base);
}

function renderComparison() {
  const [baseline, candidate] = comparisonRuns();
  const pairs = pairBySeed(baseline, candidate);
  const finished = pairs.filter(pair => pair.every(completed));
  choose("compare-seed", pairs.map(([, run]) => [run.seed, num(run.seed)]));
  const changes = finished
    .map(([base, next]) => [next.seed, next.stats.final_population - base.stats.final_population]);
  const wins = changes.filter(([, delta]) => delta > 0).length;
  const losses = changes.filter(([, delta]) => delta < 0).length;
  $("matched").textContent = num(changes.length) + " / " + num(pairs.length);
  const count = (value, single, plural) => value + " " + (value === 1 ? single : plural);
  $("tradeoffs").textContent = [
    count(wins, "win", "wins"),
    count(changes.length - wins - losses, "tie", "ties"),
    count(losses, "loss", "losses"),
  ].join(" · ");
  const before = metric(finished.map(pair => pair[0]), "replacement");
  const after = metric(finished.map(pair => pair[1]), "replacement");
  $("replacement").textContent = pct(before) + " → " + pct(after);
  $("replacement-note").textContent = before.eligible || after.eligible
    ? countLabel(before) + " → " + countLabel(after) + " eligible runs"
    : "Requires a completed pair past founder lifespan";

  const rows = [...changes].sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).map(([seed, delta]) => {
    const button = el("button", null, "delta-row");
    button.type = "button";
    button.dataset.seed = String(seed);
    button.setAttribute("aria-pressed", String(seed) === $("compare-seed").value);
    const amount = el("span", (delta > 0 ? "+" : "") + num(delta),
      "delta-value " + (delta > 0 ? "positive" : delta < 0 ? "negative" : ""));
    button.append(el("span", num(seed), "seed-number"), amount);
    return button;
  });
  $("deltas").replaceChildren(...(rows.length ? rows : [el("p", "No completed pairs", "empty")]));
  renderMemory();
  updateChart(pairs.find(([, run]) => String(run.seed) === $("compare-seed").value));
}

async function updateChart(pair) {
  const generation = ++state.chartRequest;
  if (!pair) {
    state.chart = [];
    repaintChart();
    return;
  }
  const batchId = state.batch.manifest.id;
  const roles = ["baseline", "candidate"];
  try {
    const series = await Promise.all(pair.map(async (run, index) => {
      if (!completed(run)) return null;
      const key = batchId + "/" + run.id;
      if (!histories.has(key)) {
        const result = await request("/api/batches/" + batchId + "/runs/" + run.id);
        histories.set(key, result.history);
      }
      return {role: roles[index], label: run.label, history: histories.get(key)};
    }));
    if (generation !== state.chartRequest) return;
    state.chart = series.filter(Boolean);
    $("chart-caption").textContent = "Seed " + num(pair[1].seed) + " · " + scopeLabel(pair[1])
      + (state.chart.length < 2 ? " · Awaiting a complete pair" : "");
    repaintChart();
  } catch (error) {
    if (generation === state.chartRequest) status(error.message, true);
  }
}

function repaintChart() {
  const days = Number($("scope").value.split("/")[2]) || 1800;
  drawPopulation($("population-chart"), $("legend"), days, state.chart);
}

function renderMemory() {
  const [, lifetime, days] = $("scope").value.split("/").map(Number);
  const comparable = state.runs.filter(run => run.lifetime === lifetime && run.days === days);
  const memories = [...new Set(comparable.map(run => run.memory))].sort((a, b) => a - b);
  const versions = ["baseline", "candidate"].map(id => Number($(id).value));
  const name = $("memory-metric").value;
  const chart = $("memory-chart");
  if (!memories.length) {
    chart.replaceChildren(el("p", "Start a batch to compare memory sizes", "empty"));
    return;
  }
  const header = el("div", null, "memory-row");
  header.append(el("span", "Memory", "meta"), ...versions.map(index =>
    el("span", state.batch.manifest.versions[index].label, "meta")));
  const rows = memories.map(memory => {
    const row = el("div", null, "memory-row");
    row.append(el("span", num(memory) + " B", "meta"));
    const trialSets = versions.map(version =>
      comparable.filter(run => run.memory === memory && run.version === version));
    const pairs = pairBySeed(...trialSets).filter(pair => pair.every(completed));
    versions.forEach((_, index) => {
      const result = metric(pairs.map(pair => pair[index]), name);
      const cell = el("div", null, "memory-cell " + (index ? "candidate" : "baseline"));
      const bar = el("span", null, "bar");
      const fill = el("b");
      fill.style.width = (result.fraction ?? 0) * 100 + "%";
      bar.append(fill);
      bar.setAttribute("aria-hidden", "true");
      cell.append(bar, el("span", pct(result) + (result.eligible ? " (" + countLabel(result) + ")" : ""), "meta"));
      row.append(cell);
    });
    return row;
  });
  chart.replaceChildren(header, ...rows);
  if (name === "growth" && state.batch.manifest.spec.cap < 10 * state.batch.manifest.spec.initial) {
    chart.append(el("p", "The population cap is below 10J.", "empty"));
  }
}

const filters = {
  "filter-version": "version", "filter-seed": "seed", "filter-memory": "memory",
  "filter-life": "lifetime", "filter-days": "days", "filter-status": "status",
};
function filteredRuns() {
  return state.runs.filter(run => Object.entries(filters).every(([id, key]) => {
    const value = $(id).value;
    return !value || String(run[key]) === value;
  }));
}

function renderRuns() {
  const runs = filteredRuns();
  $("run-count").textContent = num(runs.length) + " of " + num(state.runs.length) + " runs";
  $("csv").disabled = !runs.length;
  const rows = runs.map(run => {
    const tr = el("tr");
    const label = el("td");
    const button = el("button", run.label);
    button.dataset.run = String(run.id);
    button.setAttribute("aria-label", "Inspect " + run.label + " on seed " + run.seed);
    button.setAttribute("aria-pressed", run.id === state.selectedRun);
    label.append(button);
    const stats = completed(run) ? run.stats : {};
    tr.append(label, ...[run.seed, run.memory, run.lifetime, run.days, stats.final_population,
      stats.births, stats.deaths_predator].map(value => el("td", num(value), "num")),
      el("td", statuses[run.status], ["crash", "error", "timeout"].includes(run.status) ? "error" : ""));
    return tr;
  });
  if (!rows.length) {
    const empty = el("td", state.batch ? "No runs match these filters" : "Runs appear here after you start a batch");
    empty.colSpan = 9;
    const row = el("tr");
    row.append(empty);
    rows.push(row);
  }
  $("run-rows").replaceChildren(...rows);
  showRun(runs.find(run => run.id === state.selectedRun));
}

function showRun(run) {
  const detail = $("run-detail");
  detail.hidden = !run;
  if (!run) return;
  const spec = state.batch.manifest.spec;
  const version = state.batch.manifest.versions[run.version];
  const stats = run.stats ?? {};
  const fields = [
    ["Status", statuses[run.status]], ["Settings", scopeLabel(run)],
    ["Source", (version.commit ?? version.sha256 ?? state.batch.manifest.simulator_sha256).slice(0, 12)],
    ["Map", spec.map_path || "Random by seed"], ["Peak population", num(stats.peak_population)],
    ["Births blocked", num(stats.births_blocked_by_cap)], ["Age deaths", num(stats.deaths_age)],
    ["Day to 10J", spec.cap < 10 * spec.initial ? "Above cap" : num(stats.day_reached_10J)],
    ["Last generation", num(stats.max_generation)], ["Elapsed seconds", num(run.elapsed_s)],
  ];
  const definition = el("dl");
  for (const [term, value] of fields) {
    const group = el("div");
    group.append(el("dt", term), el("dd", value));
    definition.append(group);
  }
  const replay = el("button", "Open simulator replay");
  replay.dataset.replay = String(run.id);
  const link = el("a", "Batch manifest");
  link.href = "/api/batches/" + state.batch.manifest.id + "/manifest";
  const actions = el("div", null, "exports");
  actions.append(replay, link, el("span", "", "meta"));
  detail.replaceChildren(el("h3", run.label + " · Seed " + num(run.seed)), definition);
  if (run.error) detail.append(el("pre", run.error));
  detail.append(actions);
}

function exportCsv() {
  const batch = state.batch.manifest;
  const columns = ["batch", "player", "commit", "sha256", "simulator_sha256", "seed", "memory",
    "lifespan_years", "days", "initial", "cap", "map", "status", "final_population", "births",
    "deaths_predator", "deaths_age", "births_blocked_by_cap", "day_reached_10J", "elapsed_s"];
  const rows = filteredRuns().map(run => {
    const version = batch.versions[run.version];
    const s = run.stats ?? {};
    return [batch.id, run.label, version.commit, version.sha256, batch.simulator_sha256, run.seed,
      run.memory, run.lifetime, run.days, batch.spec.initial, batch.spec.cap, batch.spec.map_path,
      run.status, s.final_population, s.births, s.deaths_predator, s.deaths_age,
      s.births_blocked_by_cap, s.day_reached_10J, run.elapsed_s];
  });
  const quote = value => {
    const text = String(value ?? "");
    const safe = typeof value === "string" && /^[=+\-@\t\r]/.test(text) ? "'" + text : text;
    return '"' + safe.replaceAll('"', '""') + '"';
  };
  const csv = [columns, ...rows].map(row => row.map(quote).join(",")).join("\r\n") + "\r\n";
  const url = URL.createObjectURL(new Blob([csv], {type: "text/csv"}));
  const link = el("a");
  link.href = url;
  link.download = batch.id + "-runs.csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

form.addEventListener("input", batchSize);
$("preset").addEventListener("change", applyPreset);
form.addEventListener("submit", async event => {
  event.preventDefault();
  $("start").disabled = true;
  status("Preparing batch");
  try {
    const batch = await request("/api/batches", Object.fromEntries(new FormData(form)));
    await loadCatalog(batch.id);
    status("Batch " + batch.id.slice(0, 6) + " started");
  } catch (error) {
    $("start").disabled = false;
    status(error.message, true);
  }
});
$("batches").addEventListener("change", () => loadBatch().catch(error => status(error.message, true)));
for (const id of ["baseline", "candidate", "scope", "compare-seed"]) {
  $(id).addEventListener("change", renderComparison);
}
$("deltas").addEventListener("click", event => {
  const button = event.target.closest("button[data-seed]");
  if (button) {
    $("compare-seed").value = button.dataset.seed;
    renderComparison();
  }
});
$("memory-metric").addEventListener("change", renderMemory);
for (const id of Object.keys(filters)) $(id).addEventListener("change", renderRuns);
$("run-rows").addEventListener("click", event => {
  const button = event.target.closest("button[data-run]");
  if (button) {
    state.selectedRun = Number(button.dataset.run);
    renderRuns();
    $("run-rows").querySelector('[data-run="' + state.selectedRun + '"]')?.focus();
  }
});
$("run-detail").addEventListener("click", async event => {
  const button = event.target.closest("button[data-replay]");
  if (!button) return;
  const message = button.parentElement.querySelector("span");
  try {
    const response = await request("/api/batches/" + state.batch.manifest.id + "/runs/" + button.dataset.replay + "/replay", {});
    message.textContent = response.message;
  } catch (error) {
    message.textContent = error.message;
  }
});
$("csv").addEventListener("click", exportCsv);
new ResizeObserver(repaintChart).observe($("population-chart"));
repaintChart();
renderMemory();
renderRuns();
batchSize();
loadCatalog().catch(error => status(error.message, true));
async function poll() {
  try {
    if (state.batch?.active) await loadBatch();
  } catch (error) {
    status(error.message, true);
  } finally {
    setTimeout(poll, 1000);
  }
}
setTimeout(poll, 1000);
