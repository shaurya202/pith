/* The window.
   The server owns the edit. This file draws it, sends small changes
   (a threshold, a typo, a restored pause), and plays the video.
   Word text is typed into textContent or input.value, never innerHTML,
   because a transcript can contain whatever was in the file. */

const state = {
  meta: null,
  project: null,
  plan: null,
  mode: "source", // source | preview | export
  skipCuts: true,
  media: { preview: false, export: false },
  polling: false,
};

const $ = (id) => document.getElementById(id);

function clock(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(value / 60);
  const rest = (value - minutes * 60).toFixed(1).padStart(4, "0");
  return `${minutes}:${rest}`;
}

function toast(message) {
  const node = $("toast");
  if (!message) {
    node.hidden = true;
    return;
  }
  node.hidden = false;
  node.textContent = message;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => {
    node.hidden = true;
  }, 6000);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let body = {};
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { error: text.slice(0, 240) };
    }
  }
  if (!response.ok) {
    throw new Error(body.error || `Request failed (${response.status})`);
  }
  return body;
}

function showEmpty() {
  $("empty").hidden = false;
  $("editor").hidden = true;
  $("project-meta").textContent = "";
}

function showEditor() {
  $("empty").hidden = true;
  $("editor").hidden = false;
}

async function boot() {
  state.meta = await api("/api/meta");
  const languages = $("language");
  for (const code of state.meta.languages) {
    const option = document.createElement("option");
    option.value = code;
    option.textContent = code === "auto" ? "Auto" : code;
    languages.append(option);
  }
  const font = $("font");
  for (const name of state.meta.fonts) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    font.append(option);
  }
  const presets = $("presets");
  for (const preset of state.meta.presets) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.preset = preset.preset;
    const name = document.createElement("small");
    name.textContent = preset.preset;
    button.append(name, document.createTextNode(presetLabel(preset.preset)));
    button.addEventListener("click", () => applyPreset(preset.preset));
    presets.append(button);
  }
  const listed = await api("/api/projects");
  renderRecent(listed.projects || []);
  const existing = new URLSearchParams(location.search).get("project");
  if (existing) {
    await loadProject(existing);
  }
  requestAnimationFrame(tick);
}

function presetLabel(name) {
  return { hormozi: "Bold highlight", minimal: "Clean", karaoke: "Karaoke", boxed: "Boxed" }[name] || name;
}

function renderRecent(projects) {
  const list = $("recent");
  list.replaceChildren();
  $("recent-wrap").hidden = projects.length === 0;
  for (const project of projects) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `${project.name} · ${clock(project.duration)}`;
    button.addEventListener("click", () => loadProject(project.id));
    item.append(button);
    list.append(item);
  }
}

async function loadProject(id) {
  const body = await api(`/api/projects/${id}`);
  adopt(body, { fill: true });
}

function adopt(body, { fill } = { fill: false }) {
  state.project = body.project;
  state.plan = body.plan;
  showEditor();
  $("project-meta").textContent = `${state.project.name} · ${clock(state.project.duration)} · ${state.project.width}×${state.project.height}`;
  if (fill) {
    fillControls(state.project);
    setSource();
    probeMedia();
  }
  renderPlan();
  watchJob();
}

function fillControls(project) {
  const style = project.style;
  $("threshold").value = project.pause_threshold;
  $("padding").value = project.padding;
  $("threshold-value").textContent = `${Number(project.pause_threshold).toFixed(2)}s`;
  $("padding-value").textContent = `${Number(project.padding).toFixed(2)}s`;
  $("font").value = style.font;
  $("font-size").value = style.font_size;
  $("font-size-value").textContent = style.font_size;
  $("position").value = style.position;
  $("words-per-line").value = style.words_per_line;
  $("words-value").textContent = style.words_per_line;
  $("text-color").value = style.text_color;
  $("highlight-color").value = style.highlight_color;
  $("uppercase").checked = style.uppercase;
  for (const input of document.querySelectorAll('input[name="aspect"]')) {
    input.checked = input.value === project.aspect;
  }
  $("aspect-note").hidden = project.aspect !== "9:16";
  for (const button of $("presets").querySelectorAll("button")) {
    button.classList.toggle("on", button.dataset.preset === style.preset);
  }
  const preset = (state.meta.presets || []).find((item) => item.preset === style.preset);
  $("preset-blurb").textContent = preset ? preset.blurb : "Custom. These settings no longer match a preset.";
  renderReference(project.reference);
}

async function probeMedia() {
  state.media = { preview: false, export: false };
  for (const kind of ["preview", "export"]) {
    try {
      const response = await fetch(state.project.media[kind], { method: "HEAD" });
      state.media[kind] = response.ok;
    } catch {
      state.media[kind] = false;
    }
  }
  $("mode-preview").disabled = !state.media.preview;
  $("mode-export").disabled = !state.media.export;
}

function setSource() {
  state.mode = "source";
  const video = $("player");
  video.src = state.project.media.source;
  video.load();
  markMode();
  $("overlay").hidden = false;
  $("overlay-note").hidden = false;
}

function setRendered(kind) {
  state.mode = kind;
  const video = $("player");
  const stamp = Date.now();
  video.src = `${state.project.media[kind]}?v=${stamp}`;
  video.load();
  markMode();
  // The file already has the captions burned in.
  $("overlay").hidden = true;
  $("overlay-note").hidden = true;
}

function markMode() {
  for (const kind of ["source", "preview", "export"]) {
    $("mode-" + kind).classList.toggle("on", state.mode === kind);
  }
}

async function openFile(file) {
  if (!file) return;
  const data = new FormData();
  data.append("file", file, file.name);
  toast("");
  $("progress").hidden = false;
  $("progress-label").textContent = "Copying the video…";
  $("progress-bar").style.width = "30%";
  try {
    const body = await api("/api/projects", { method: "POST", body: data });
    $("progress").hidden = true;
    adopt(body, { fill: true });
  } catch (error) {
    $("progress").hidden = true;
    toast(error.message);
  }
}

async function openSample() {
  toast("");
  try {
    const body = await api("/api/sample", { method: "POST" });
    adopt(body, { fill: true });
  } catch (error) {
    toast(error.message);
  }
}

function stylePayload(extra) {
  const style = {
    font: $("font").value,
    font_size: Number($("font-size").value),
    position: $("position").value,
    words_per_line: Number($("words-per-line").value),
    text_color: $("text-color").value,
    highlight_color: $("highlight-color").value,
    uppercase: $("uppercase").checked,
  };
  return { style, ...extra };
}

let settingsTimer = 0;
function queueSettings(includeStyle) {
  const threshold = Number($("threshold").value);
  const padding = Number($("padding").value);
  $("threshold-value").textContent = `${threshold.toFixed(2)}s`;
  $("padding-value").textContent = `${padding.toFixed(2)}s`;
  $("font-size-value").textContent = $("font-size").value;
  $("words-value").textContent = $("words-per-line").value;
  // The live caption updates immediately. The plan comes back a moment later.
  paintOverlayStyle();
  clearTimeout(settingsTimer);
  settingsTimer = setTimeout(async () => {
    const payload = { pause_threshold: threshold, padding };
    // Sending the style on a threshold change would mark the preset "custom"
    // even though the look did not change.
    if (includeStyle) Object.assign(payload, stylePayload());
    try {
      const body = await api(`/api/projects/${state.project.id}/settings`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      adopt(body, { fill: false });
      // Preset highlight: the server may have marked it custom.
      for (const button of $("presets").querySelectorAll("button")) {
        button.classList.toggle("on", button.dataset.preset === body.project.style.preset);
      }
      const preset = (state.meta.presets || []).find((item) => item.preset === body.project.style.preset);
      $("preset-blurb").textContent = preset ? preset.blurb : "Custom. These settings no longer match a preset.";
    } catch (error) {
      toast(error.message);
    }
  }, 160);
}

async function applyPreset(name) {
  try {
    const body = await api(`/api/projects/${state.project.id}/settings`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ preset: name }),
    });
    adopt(body, { fill: true });
  } catch (error) {
    toast(error.message);
  }
}

async function saveWord(id, patch) {
  const body = await api(`/api/projects/${state.project.id}/words/${id}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(patch),
  });
  adopt(body, { fill: false });
}

async function postSpan(path, start, end) {
  const body = await api(`/api/projects/${state.project.id}/${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ start: Number(start), end: Number(end) }),
  });
  adopt(body, { fill: false });
}

function editingText() {
  const node = document.activeElement;
  return node && node.closest("#transcript") && node.tagName === "INPUT";
}

function renderPlan() {
  const plan = state.plan;
  if (!plan) return;
  const stats = plan.stats || {};
  if (!plan.ready) {
    $("stats").textContent = "Not transcribed yet";
  } else if (stats.cut_count) {
    $("stats").textContent = `Will cut ${stats.cut_count} · ${stats.cut_seconds.toFixed(1)}s removed · ${stats.keep_seconds.toFixed(1)}s kept`;
  } else {
    $("stats").textContent = `${stats.keep_seconds.toFixed(1)}s kept`;
  }
  $("note").textContent = plan.note || "";
  $("energy-note").textContent = plan.energy
    ? "A long gap is left alone when it is still loud (laughter, music, a noise)."
    : "Pause detection is using word timings.";
  const ready = !!plan.ready;
  $("preview-button").disabled = !ready;
  $("export-button").disabled = !ready;
  $("time-end").textContent = clock(plan.duration);
  renderSpans($("cut-list"), plan.cuts || [], "Put back", (item) => postSpan("restore", item.start, item.end));
  renderSpans($("loud-list"), plan.loud_gaps || [], "Cut it", (item) => postSpan("force-cut", item.start, item.end));
  renderSpans($("restored-list"), plan.restored || [], "Cut again", (item) => postSpan("restore/remove", item.start, item.end), "Put back");
  renderSpans($("forced-list"), plan.forced || [], "Undo", (item) => postSpan("force-cut/remove", item.start, item.end), "Cut by you");
  if (!editingText()) renderTranscript();
  drawTimeline();
  paintOverlayStyle();
}

function renderSpans(list, items, action, onClick, prefix) {
  list.replaceChildren();
  for (const item of items) {
    const row = document.createElement("li");
    const label = document.createElement("span");
    const title = item.label || prefix || "Stretch";
    label.textContent = `${title} · ${clock(item.start)}–${clock(item.end)} · ${Number(item.seconds).toFixed(1)}s`;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = action;
    button.addEventListener("click", () => onClick(item).catch((error) => toast(error.message)));
    row.append(label, button);
    list.append(row);
  }
}

function renderTranscript() {
  const plan = state.plan;
  const root = $("transcript");
  root.replaceChildren();
  if (!plan.ready) {
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = "Transcribe to get words you can fix.";
    root.append(note);
    return;
  }
  const byId = new Map((plan.words || []).map((word) => [word.id, word]));
  const snippets = plan.snippets && plan.snippets.length ? plan.snippets : (plan.words || []).map((word) => ({ type: "word", id: word.id }));
  for (const snippet of snippets) {
    if (snippet.type === "word") {
      const word = byId.get(snippet.id);
      if (!word) continue;
      root.append(wordChip(word));
    } else if (snippet.type === "cut") {
      root.append(markerChip("cut", `${Number(snippet.seconds).toFixed(1)}s pause`, "Put back", () => postSpan("restore", snippet.start, snippet.end)));
    } else if (snippet.type === "loud") {
      root.append(markerChip("loud", `${Number(snippet.seconds).toFixed(1)}s kept · has sound`, "Cut", () => postSpan("force-cut", snippet.start, snippet.end)));
    }
  }
}

function wordChip(word) {
  const chip = document.createElement("span");
  chip.className = "word";
  chip.dataset.id = String(word.id);
  if (word.deleted || !word.included) chip.classList.add(word.deleted ? "deleted" : "out");
  if (word.probability < 0.45) chip.classList.add("unsure");
  const input = document.createElement("input");
  input.value = word.text;
  input.size = Math.max(3, Math.min(22, word.text.length + 1));
  input.setAttribute("aria-label", `Word ${word.text}`);
  input.addEventListener("change", () => {
    const next = input.value.trim();
    if (next && next !== word.text) {
      saveWord(word.id, { text: next }).catch((error) => toast(error.message));
    }
  });
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = word.deleted ? "Put back" : "Cut";
  button.addEventListener("click", () => {
    saveWord(word.id, { deleted: !word.deleted }).catch((error) => toast(error.message));
  });
  chip.append(input, button);
  return chip;
}

function markerChip(kind, label, action, onClick) {
  const chip = document.createElement("span");
  chip.className = `chip ${kind}`;
  const text = document.createElement("span");
  text.textContent = label;
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = action;
  button.addEventListener("click", () => onClick().catch((error) => toast(error.message)));
  chip.append(text, button);
  return chip;
}

function drawTimeline() {
  const canvas = $("timeline");
  const plan = state.plan;
  const project = state.project;
  if (!plan || !project) return;
  const width = canvas.clientWidth || 600;
  const height = 84;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.floor(width * ratio);
  canvas.height = Math.floor(height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#0c0b09";
  ctx.fillRect(0, 0, width, height);
  const duration = plan.duration || project.duration || 1;
  const peaks = project.peaks || [];
  const barWidth = width / Math.max(peaks.length, 1);
  peaks.forEach((peak, index) => {
    const barHeight = Math.max(2, peak * 64);
    ctx.fillStyle = "#d9cbb8";
    ctx.globalAlpha = 0.85;
    ctx.fillRect(index * barWidth, (height - barHeight) / 2, Math.max(1, barWidth - 1), barHeight);
  });
  ctx.globalAlpha = 1;
  for (const cut of plan.cuts || []) {
    const x = (cut.start / duration) * width;
    const w = Math.max(2, ((cut.end - cut.start) / duration) * width);
    ctx.fillStyle = "rgba(255, 77, 28, 0.28)";
    ctx.fillRect(x, 0, w, height);
    ctx.strokeStyle = "rgba(255, 77, 28, 0.9)";
    ctx.beginPath();
    for (let line = -height; line < w + height; line += 8) {
      ctx.moveTo(x + line, height);
      ctx.lineTo(x + line + 10, 0);
    }
    ctx.stroke();
  }
  const video = $("player");
  const playhead = state.mode === "source" ? video.currentTime || 0 : 0;
  if (state.mode === "source") {
    const x = (playhead / duration) * width;
    ctx.fillStyle = "#ff4d1c";
    ctx.fillRect(x, 0, 2, height);
  }
}

function paintOverlayStyle() {
  const style = state.project && state.project.style;
  const overlay = $("overlay");
  if (!style) return;
  overlay.classList.toggle("boxed", !!style.boxed);
  overlay.classList.remove("top", "middle", "bottom");
  overlay.classList.add(style.position || "bottom");
  const frame = $("frame");
  const px = Math.max(14, frame.clientHeight * (Number(style.font_size) / 1920));
  overlay.style.fontFamily = `"${style.font}", Impact, sans-serif`;
  overlay.style.fontWeight = style.bold ? "700" : "400";
  overlay.style.fontSize = `${px}px`;
  overlay.style.letterSpacing = style.uppercase ? "0.01em" : "0";
  overlay.style.textTransform = style.uppercase ? "uppercase" : "none";
}

function updateOverlay() {
  const overlay = $("overlay");
  if (!state.plan || state.mode !== "source" || !state.plan.ready) {
    if (state.mode === "source") overlay.replaceChildren();
    return;
  }
  const time = $("player").currentTime || 0;
  const lines = state.plan.lines || [];
  let line = null;
  for (const candidate of lines) {
    if (time >= candidate.start - 0.05 && time <= candidate.end + 0.12) {
      line = candidate;
      break;
    }
  }
  paintOverlayStyle();
  overlay.replaceChildren();
  if (!line) return;
  const row = document.createElement("div");
  row.className = "line";
  const style = state.project.style;
  line.words.forEach((word, index) => {
    const span = document.createElement("span");
    span.className = "w";
    span.textContent = word.text;
    const active = time >= word.start && (index === line.words.length - 1 ? time <= word.end + 0.05 : time < line.words[index + 1].start);
    let hot = false;
    if (style.highlight_mode === "cumulative") hot = time >= word.start;
    else if (style.highlight_mode === "active") hot = active;
    span.style.color = hot ? style.highlight_color : style.text_color;
    if (index) row.append(document.createTextNode(" "));
    row.append(span);
  });
  overlay.append(row);
  for (const chip of $("transcript").querySelectorAll(".word")) {
    const id = Number(chip.dataset.id);
    const word = line.words.find((item) => item.id === id);
    const on = word && time >= word.start && time < word.end + 0.05;
    chip.classList.toggle("on", !!on);
  }
}

function tick() {
  const video = $("player");
  if (state.project && state.mode === "source" && state.skipCuts && video && !video.paused && state.plan) {
    const time = video.currentTime;
    const cut = (state.plan.cuts || []).find((item) => time >= item.start + 0.03 && time < item.end - 0.02);
    if (cut) {
      if (cut.end >= (state.plan.duration || 0) - 0.08) video.pause();
      else video.currentTime = cut.end + 0.01;
    }
  }
  if (video && state.plan && state.mode === "source") {
    $("time-label").textContent = clock(video.currentTime || 0);
    drawTimeline();
    updateOverlay();
  }
  requestAnimationFrame(tick);
}

function seekTimeline(event) {
  if (state.mode !== "source" || !state.plan) return;
  const rect = $("timeline").getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
  const time = ratio * (state.plan.duration || 0);
  const video = $("player");
  const cut = (state.plan.cuts || []).find((item) => time >= item.start && time < item.end);
  if (cut) video.pause();
  video.currentTime = time;
}

async function transcribe() {
  try {
    const body = await api(`/api/projects/${state.project.id}/transcribe`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: $("model").value, language: $("language").value }),
    });
    adopt(body, { fill: false });
  } catch (error) {
    toast(error.message);
  }
}

async function render(quality) {
  try {
    const body = await api(`/api/projects/${state.project.id}/render`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ quality }),
    });
    adopt(body, { fill: false });
  } catch (error) {
    toast(error.message);
  }
}

function watchJob() {
  const job = state.project && state.project.job;
  if (!job || job.status !== "running") {
    if (job && job.status === "error") {
      $("progress").hidden = false;
      $("progress-bar").style.width = "0%";
      $("progress-label").textContent = job.error || "Something went wrong.";
    } else if (job && job.status === "done") {
      $("progress").hidden = true;
    }
    state.polling = false;
    return;
  }
  $("progress").hidden = false;
  const fraction = Math.max(0.05, Math.min(0.99, job.progress || 0));
  $("progress-bar").style.width = `${fraction * 100}%`;
  $("progress-label").textContent = job.message || "Working…";
  if (state.polling) return;
  state.polling = true;
  poll();
}

async function poll() {
  if (!state.project) return;
  try {
    const body = await api(`/api/projects/${state.project.id}`);
    const previous = state.project.job && state.project.job.status;
    adopt(body, { fill: false });
    const job = body.project.job;
    if (job && job.status === "done" && previous === "running") {
      await onJobDone(job);
    }
    if (job && job.status === "running") {
      setTimeout(poll, 500);
      return;
    }
  } catch (error) {
    toast(error.message);
  }
  state.polling = false;
}

async function onJobDone(job) {
  if (job.kind === "transcribe") {
    const count = job.result && job.result.words;
    toast(count ? `Transcript ready · ${count} words` : "Transcript ready");
    fillControls(state.project);
  }
  if (job.kind === "render" && job.result) {
    const kind = job.result.quality === "full" ? "export" : "preview";
    state.media[kind] = true;
    $("mode-preview").disabled = !state.media.preview;
    $("mode-export").disabled = !state.media.export;
    const size = job.result.bytes ? `${(job.result.bytes / 1e6).toFixed(1)} MB` : "";
    $("export-result").textContent = `${job.result.width}×${job.result.height} · ${job.result.seconds}s${size ? " · " + size : ""}`;
    if (kind === "export") {
      $("download").hidden = false;
      $("download").href = `${state.project.media.export}?download=1`;
    }
    setRendered(kind);
    $("player").play().catch(() => {});
  }
}

function renderReference(reference) {
  const list = $("reference-notes");
  list.replaceChildren();
  if (!reference || !reference.notes) {
    $("reference-apply").hidden = true;
    return;
  }
  for (const note of reference.notes) {
    const item = document.createElement("li");
    item.textContent = note;
    list.append(item);
  }
  $("reference-apply").hidden = false;
}

async function uploadReference(file) {
  const data = new FormData();
  data.append("file", file, file.name);
  try {
    const body = await api(`/api/projects/${state.project.id}/reference`, { method: "POST", body: data });
    adopt(body, { fill: false });
    renderReference(body.project.reference);
  } catch (error) {
    toast(error.message);
  }
}

function bind() {
  $("open-button").addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", () => openFile($("file-input").files[0]));
  $("sample-button").addEventListener("click", openSample);
  const drop = $("drop");
  drop.addEventListener("click", () => $("file-input").click());
  drop.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") $("file-input").click();
  });
  ["dragover", "dragenter"].forEach((name) => {
    window.addEventListener(name, (event) => {
      event.preventDefault();
      drop.classList.add("over");
    });
  });
  window.addEventListener("dragleave", () => drop.classList.remove("over"));
  window.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("over");
    const file = event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) openFile(file);
  });

  $("mode-source").addEventListener("click", () => state.project && setSource());
  $("mode-preview").addEventListener("click", () => state.media.preview && setRendered("preview"));
  $("mode-export").addEventListener("click", () => state.media.export && setRendered("export"));
  $("skip-cuts").addEventListener("change", () => {
    state.skipCuts = $("skip-cuts").checked;
  });
  $("player").addEventListener("loadedmetadata", () => paintOverlayStyle());
  $("timeline").addEventListener("pointerdown", seekTimeline);
  $("transcribe").addEventListener("click", transcribe);
  $("preview-button").addEventListener("click", () => render("preview"));
  $("export-button").addEventListener("click", () => render("full"));

  for (const id of ["threshold", "padding"]) {
    $(id).addEventListener("input", () => queueSettings(false));
  }
  for (const id of ["font-size", "words-per-line"]) {
    $(id).addEventListener("input", () => queueSettings(true));
  }
  for (const id of ["font", "position", "text-color", "highlight-color", "uppercase"]) {
    $(id).addEventListener("change", () => queueSettings(true));
  }
  for (const input of document.querySelectorAll('input[name="aspect"]')) {
    input.addEventListener("change", () => {
      if (!input.checked) return;
      $("aspect-note").hidden = input.value !== "9:16";
      api(`/api/projects/${state.project.id}/settings`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ aspect: input.value }),
      }).then((body) => adopt(body, { fill: false })).catch((error) => toast(error.message));
    });
  }

  $("reference-button").addEventListener("click", () => $("reference-file").click());
  $("reference-file").addEventListener("change", () => {
    if ($("reference-file").files[0]) uploadReference($("reference-file").files[0]);
  });
  $("reference-sample").addEventListener("click", async () => {
    try {
      const body = await api(`/api/projects/${state.project.id}/reference/sample`, { method: "POST" });
      adopt(body, { fill: false });
      renderReference(body.project.reference);
    } catch (error) {
      toast(error.message);
    }
  });
  $("reference-apply").addEventListener("click", async () => {
    try {
      const body = await api(`/api/projects/${state.project.id}/reference/apply`, { method: "POST" });
      adopt(body, { fill: true });
      toast("Starting point applied. Change anything you don't like.");
    } catch (error) {
      toast(error.message);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.target.closest("video, input, textarea, select")) return;
    if (event.code === "Space" && state.project) {
      event.preventDefault();
      const video = $("player");
      if (video.paused) video.play();
      else video.pause();
    }
  });
  window.addEventListener("resize", () => {
    drawTimeline();
    paintOverlayStyle();
  });
}

bind();
boot().catch((error) => toast(error.message));
