"use strict";

const S = { meta: null, token: "", record: null, spans: [], action: "", note: "", dirty: false, saving: false, started: Date.now(), timer: null };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const THEME_KEY = "privacy-span-review-theme";

function applyTheme(theme, persist = false) {
  const dark = theme === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  $("#theme").textContent = dark ? "☀ 亮色" : "☾ 暗色";
  if (persist) window.localStorage.setItem(THEME_KEY, dark ? "dark" : "light");
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if ((options.method || "GET") !== "GET") headers["X-Review-App-Token"] = S.token;
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json();
  if (!response.ok) throw Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = error ? "show error" : "show";
  clearTimeout(node.timer);
  node.timer = setTimeout(() => { node.className = ""; }, 3200);
}

function spanKey(span) { return `${span.start}:${span.end}`; }
function isSelected(span) { return S.spans.some((item) => spanKey(item) === spanKey(span)); }
function overlaps(span) { return S.spans.some((item) => item.start < span.end && span.start < item.end && spanKey(item) !== spanKey(span)); }

function replacementOptions(selected) {
  const fragment = document.createDocumentFragment();
  S.meta.replacements.forEach((replacement) => {
    const option = document.createElement("option");
    option.value = replacement.id;
    option.textContent = replacement.label;
    option.selected = replacement.id === selected;
    fragment.append(option);
  });
  return fragment;
}

function addSpan(span) {
  const clean = { start: span.start, end: span.end, replacement: span.replacement ?? span.suggested_replacement ?? "某人", source: span.source || "candidate" };
  if (overlaps(clean)) { toast("所选片段与已有片段重叠", true); return; }
  if (!isSelected(clean)) S.spans.push(clean);
  S.spans.sort((a, b) => a.start - b.start || a.end - b.end);
  S.action = "redact";
  markDirty();
  renderSelections();
  renderCandidates();
}

function removeSpan(span) {
  S.spans = S.spans.filter((item) => spanKey(item) !== spanKey(span));
  markDirty();
  renderSelections();
  renderCandidates();
}

function renderCandidates() {
  const list = $("#candidateList");
  list.replaceChildren();
  if (!S.record.candidate_spans.length) {
    const message = document.createElement("p");
    message.textContent = "本条没有机器候选，请直接在标题中框选敏感文字。";
    list.append(message);
    return;
  }
  S.record.candidate_spans.forEach((candidate) => {
    const wrapper = document.createElement("div");
    wrapper.className = `candidate ${isSelected(candidate) ? "selected" : ""}`;
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = isSelected(candidate);
    checkbox.onchange = () => checkbox.checked ? addSpan(candidate) : removeSpan(candidate);
    const button = document.createElement("button");
    button.type = "button";
    const value = document.createElement("strong");
    value.textContent = candidate.text;
    const detail = document.createElement("small");
    detail.textContent = candidate.mentioned_in_prior_note ? "上一轮备注提及" : `字符 ${candidate.start}–${candidate.end}`;
    button.append(value, detail);
    button.onclick = () => isSelected(candidate) ? removeSpan(candidate) : addSpan(candidate);
    const select = document.createElement("select");
    const chosen = S.spans.find((item) => spanKey(item) === spanKey(candidate));
    select.append(replacementOptions(chosen?.replacement ?? candidate.suggested_replacement));
    select.disabled = !chosen;
    select.onchange = () => {
      const item = S.spans.find((span) => spanKey(span) === spanKey(candidate));
      if (item) { item.replacement = select.value; markDirty(); renderPreview(); }
    };
    wrapper.append(checkbox, button, select);
    list.append(wrapper);
  });
}

function renderPreview() {
  const preview = $("#preview");
  preview.replaceChildren();
  let offset = 0;
  S.spans.forEach((span) => {
    preview.append(document.createTextNode(S.record.title.slice(offset, span.start)));
    const deleted = document.createElement("del");
    deleted.textContent = S.record.title.slice(span.start, span.end);
    preview.append(deleted);
    if (span.replacement) {
      const inserted = document.createElement("ins");
      inserted.textContent = span.replacement;
      preview.append(inserted);
    }
    offset = span.end;
  });
  preview.append(document.createTextNode(S.record.title.slice(offset)));
}

function renderSelections() {
  const list = $("#selectedList");
  list.replaceChildren();
  if (!S.spans.length) {
    const empty = document.createElement("p");
    empty.textContent = "尚未选择片段。";
    list.append(empty);
  }
  S.spans.forEach((span) => {
    const row = document.createElement("div");
    row.className = "selected-row";
    const text = document.createElement("code");
    text.textContent = S.record.title.slice(span.start, span.end);
    const select = document.createElement("select");
    select.append(replacementOptions(span.replacement));
    select.onchange = () => { span.replacement = select.value; markDirty(); renderPreview(); renderCandidates(); };
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "移除";
    remove.onclick = () => removeSpan(span);
    row.append(text, select, remove);
    list.append(row);
  });
  renderPreview();
}

function renderRecord() {
  const record = S.record;
  $("#position").textContent = `${record.position} / ${record.total} · 原111条中的第${record.confirmed_title_position}条 · ${record.span_review_id}`;
  $("#status").textContent = ({ unreviewed: "未审核", draft: "草稿", reviewed: "已完成" })[record.status];
  $("#selectableTitle").textContent = record.title;
  $("#priorNote").textContent = record.prior_note;
  $("#exceptionReason").textContent = record.exception_reason;
  $("#note").value = S.note;
  $("#prev").disabled = record.position === 1;
  $("#jumpPosition").value = record.position;
  $("#progressBar").style.width = `${Math.round(record.position / record.total * 100)}%`;
  renderCandidates();
  renderSelections();
}

function suggestedSpans(record) {
  return record.candidate_spans.filter((span) => span.suggested_selected).map((span) => ({
    start: span.start, end: span.end, replacement: span.suggested_replacement, source: "candidate",
  }));
}

async function loadRecord(position = 1, nextUnreviewed = false, afterPosition = 0) {
  if (S.dirty) await save("draft", false, false);
  const query = new URLSearchParams({ position: String(position) });
  if (nextUnreviewed) { query.set("next_unreviewed", "1"); query.set("after_position", String(afterPosition)); }
  const payload = await api(`/api/record?${query}`);
  if (!payload.record) { toast("9条边界项均已完成"); await showSummary(); return; }
  S.record = payload.record;
  S.spans = payload.record.status === "unreviewed" ? suggestedSpans(payload.record) : payload.record.spans;
  S.action = payload.record.status === "unreviewed" && S.spans.length ? "redact" : payload.record.action;
  S.note = payload.record.note;
  S.dirty = false;
  S.started = Date.now();
  renderRecord();
  window.scrollTo({ top: 0, behavior: "auto" });
}

function markDirty() {
  S.dirty = true;
  $("#saveState").textContent = "正在自动保存草稿……";
  clearTimeout(S.timer);
  S.timer = setTimeout(() => save("draft", false, false), 500);
}

async function save(status, moveNext = false, announce = true) {
  if (!S.record || S.saving) return false;
  clearTimeout(S.timer);
  S.saving = true;
  try {
    const result = await api("/api/review", { method: "POST", body: JSON.stringify({
      span_review_id: S.record.span_review_id, action: S.action, spans: S.spans,
      note: S.note, status, duration_ms: Date.now() - S.started,
    }) });
    S.dirty = false;
    S.record.status = status;
    $("#saveState").textContent = `${status === "reviewed" ? "已完成" : "草稿已保存"} · 修订 ${result.revision}`;
    if (announce && status === "reviewed") toast("已保存");
    if (moveNext) await loadRecord(1, true, S.record.position);
    return true;
  } catch (error) {
    toast(error.message, true);
    $("#saveState").textContent = error.message;
    return false;
  } finally { S.saving = false; }
}

function addCurrentSelection() {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount !== 1 || selection.isCollapsed) { toast("请先在标题中框选文字", true); return; }
  const range = selection.getRangeAt(0);
  const title = $("#selectableTitle");
  if (range.startContainer !== title.firstChild || range.endContainer !== title.firstChild) { toast("选区必须完全位于标题文字中", true); return; }
  let start = range.startOffset;
  let end = range.endOffset;
  while (start < end && /\s/.test(S.record.title[start])) start += 1;
  while (end > start && /\s/.test(S.record.title[end - 1])) end -= 1;
  if (start >= end) { toast("不能添加空白选区", true); return; }
  addSpan({ start, end, replacement: $("#manualReplacement").value, source: "manual" });
  selection.removeAllRanges();
}

function toggleAllCandidates() {
  const candidates = S.record.candidate_spans;
  const allSelected = candidates.length && candidates.every(isSelected);
  if (allSelected) {
    const keys = new Set(candidates.map(spanKey));
    S.spans = S.spans.filter((span) => !keys.has(spanKey(span)));
  } else {
    candidates.forEach((candidate) => { if (!isSelected(candidate) && !overlaps(candidate)) S.spans.push({ start: candidate.start, end: candidate.end, replacement: candidate.suggested_replacement, source: "candidate" }); });
    S.spans.sort((a, b) => a.start - b.start || a.end - b.end);
  }
  S.action = S.spans.length ? "redact" : "";
  markDirty(); renderCandidates(); renderSelections();
}

async function commit(action) {
  if (action === "redact" && !S.spans.length) { toast("请先选择至少一个脱敏片段", true); return; }
  if ((action === "retain" || action === "exclude") && !S.note.trim()) { toast("保留不改或整条排除必须填写理由", true); $("#note").focus(); return; }
  S.action = action;
  if (action !== "redact") S.spans = [];
  renderCandidates(); renderSelections();
  await save("reviewed", true);
}

async function showSummary() {
  if (S.dirty) await save("draft", false, false);
  const summary = await api("/api/summary");
  $("#reviewView").hidden = true; $("#summaryView").hidden = false;
  $("#reviewTab").classList.remove("active"); $("#summaryTab").classList.add("active");
  const values = [["第一轮备注已推导", S.meta.note_derived_titles], ["边界项已完成", summary.status_counts.reviewed || 0], ["边界项未审核", summary.status_counts.unreviewed || 0], ["精确脱敏", summary.action_counts.redact || 0], ["保留不改", summary.action_counts.retain || 0], ["整条排除", summary.action_counts.exclude || 0]];
  const cards = $("#summaryCards"); cards.replaceChildren();
  values.forEach(([label, value]) => { const card = document.createElement("div"); card.className = "summary-item"; const name = document.createElement("span"); name.textContent = label; const count = document.createElement("strong"); count.textContent = value; card.append(name, count); cards.append(card); });
}

function showReview() { $("#summaryView").hidden = true; $("#reviewView").hidden = false; $("#summaryTab").classList.remove("active"); $("#reviewTab").classList.add("active"); }

async function initialize() {
  applyTheme(window.localStorage.getItem(THEME_KEY) || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  S.meta = await api("/api/meta"); S.token = S.meta.request_token;
  $("#manualReplacement").append(replacementOptions("某人"));
  await loadRecord(1, true, 0);
}

$("#addSelection").onclick = addCurrentSelection;
$("#toggleAll").onclick = toggleAllCandidates;
$("#note").oninput = (event) => { S.note = event.target.value; markDirty(); };
$("#apply").onclick = () => commit("redact");
$("#retain").onclick = () => commit("retain");
$("#exclude").onclick = () => commit("exclude");
$("#prev").onclick = () => loadRecord(Math.max(1, S.record.position - 1));
$("#jump").onclick = () => loadRecord(Number($("#jumpPosition").value));
$("#jumpPosition").onkeydown = (event) => { if (event.key === "Enter") $("#jump").click(); };
$("#summaryTab").onclick = showSummary;
$("#reviewTab").onclick = showReview;
$("#continueReview").onclick = async () => { showReview(); await loadRecord(1, true, 0); };
$("#theme").onclick = () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
$("#export").onclick = async () => { try { if (S.dirty) await save("draft", false, false); const result = await api("/api/export", { method: "POST", body: "{}" }); toast(`已导出：${result.export_path}`); } catch (error) { toast(error.message, true); } };
document.addEventListener("keydown", (event) => {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName) || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key.toLowerCase() === "a") { event.preventDefault(); toggleAllCandidates(); }
  if (event.key === "1") { event.preventDefault(); commit("redact"); }
  if (event.key === "2") { event.preventDefault(); commit("retain"); }
  if (event.key === "3") { event.preventDefault(); commit("exclude"); }
});
window.addEventListener("beforeunload", (event) => { if (S.dirty) { event.preventDefault(); event.returnValue = ""; } });
initialize().catch((error) => toast(error.message, true));
