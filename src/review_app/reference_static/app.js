"use strict";

const S = { meta: null, position: 1, record: null, searchResults: [] };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const THEME_KEY = "gendered-visibility-ai-reference-theme";

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

async function api(path) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" } });
  const payload = await response.json();
  if (!response.ok) throw Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = error ? "show error" : "show";
  window.clearTimeout(node.timer);
  node.timer = window.setTimeout(() => { node.className = ""; }, 3000);
}

function savedTheme() {
  try { return window.localStorage.getItem(THEME_KEY); } catch { return null; }
}

function applyTheme(theme, persist = false) {
  const dark = theme === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  $("#theme").textContent = dark ? "☀ 亮色" : "☾ 暗色";
  $("#theme").setAttribute("aria-pressed", String(dark));
  if (persist) {
    try { window.localStorage.setItem(THEME_KEY, dark ? "dark" : "light"); } catch { /* Optional display preference. */ }
  }
}

function fieldHtml(field) {
  const value = S.record.reference_output[field.id];
  const option = field.options.find((item) => item.id === value);
  return `<section class="field"><div class="field-head"><h2>${escapeHtml(field.label)}</h2><code>${escapeHtml(field.id)}</code></div><div class="answer">${escapeHtml(option.label)}</div><code class="value">${escapeHtml(value)}</code><p>${escapeHtml(option.definition || "以冻结代码本字面定义为准。")}</p>${option.exclude ? `<p class="exclude"><b>排除／注意：</b>${escapeHtml(option.exclude)}</p>` : ""}</section>`;
}

function fieldById(id) {
  return S.meta.fields.find((field) => field.id === id);
}

function optionLabel(fieldId, value) {
  return fieldById(fieldId)?.options.find((option) => option.id === value)?.label || value;
}

function updateValueFilter() {
  const field = fieldById($("#fieldFilter").value);
  $("#valueFilter").disabled = !field;
  $("#valueFilter").innerHTML = '<option value="">不限取值</option>' + (field?.options || [])
    .map((option) => `<option value="${escapeHtml(option.id)}">${escapeHtml(option.label)}</option>`).join("");
}

function renderSearchResults(fieldId = "") {
  $("#resultCount").textContent = `找到 ${S.searchResults.length} 条`;
  $("#searchResults").innerHTML = S.searchResults.length
    ? S.searchResults.map((record) => `<button type="button" class="result-item ${record.position === S.position ? "current" : ""}" data-result-position="${record.position}"><span>${record.position} / ${S.meta.total}</span><strong>${escapeHtml(record.title)}</strong>${fieldId ? `<small>${escapeHtml(fieldById(fieldId).label)}：${escapeHtml(optionLabel(fieldId, record.value))}</small>` : ""}</button>`).join("")
    : '<p class="no-results">没有符合条件的标题。可以减少关键词或清除字段条件。</p>';
  $$('[data-result-position]').forEach((button) => {
    button.onclick = async () => {
      await load(Number(button.dataset.resultPosition));
      renderSearchResults(fieldId);
    };
  });
}

async function runSearch() {
  const params = new URLSearchParams();
  const query = $("#titleQuery").value.trim();
  const field = $("#fieldFilter").value;
  const value = $("#valueFilter").value;
  if (query) params.set("q", query);
  if (field) params.set("field", field);
  if (value) params.set("value", value);
  const payload = await api(`/api/search?${params}`);
  S.searchResults = payload.records;
  renderSearchResults(field);
}

function openSearch() {
  $("#searchPanel").hidden = false;
  $("#titleQuery").focus();
}

function render() {
  $("#position").textContent = `${S.record.position} / ${S.meta.total} · ${S.record.blind_id}`;
  $("#title").textContent = S.record.title;
  $("#fields").innerHTML = S.meta.fields.map(fieldHtml).join("");
  $$(".prev").forEach((button) => { button.disabled = S.position === 1; });
  $$(".next").forEach((button) => { button.disabled = S.position === S.meta.total; });
}

async function load(position) {
  const payload = await api(`/api/reference?position=${position}`);
  S.position = position;
  S.record = payload.record;
  render();
  window.requestAnimationFrame(() => window.scrollTo({ top: 0, left: 0, behavior: "auto" }));
}

async function move(delta) {
  const position = Math.min(S.meta.total, Math.max(1, S.position + delta));
  if (position !== S.position) await load(position);
}

async function init() {
  const colorScheme = window.matchMedia("(prefers-color-scheme: dark)");
  applyTheme(savedTheme() || (colorScheme.matches ? "dark" : "light"));
  S.meta = await api("/api/meta");
  $("#appTitle").textContent = S.meta.title;
  $("#warning").textContent = S.meta.warning;
  $("#fieldFilter").innerHTML += S.meta.fields.map((field) => `<option value="${escapeHtml(field.id)}">${escapeHtml(field.label)}</option>`).join("");
  await load(1);
}

$("#theme").onclick = () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
$("#searchToggle").onclick = openSearch;
$("#closeSearch").onclick = () => { $("#searchPanel").hidden = true; };
$("#fieldFilter").onchange = updateValueFilter;
$("#runSearch").onclick = () => runSearch().catch((error) => toast(error.message, true));
$("#clearSearch").onclick = () => {
  $("#titleQuery").value = "";
  $("#fieldFilter").value = "";
  updateValueFilter();
  S.searchResults = [];
  $("#resultCount").textContent = "输入标题或选择条件后查找";
  $("#searchResults").innerHTML = "";
};
$("#titleQuery").onkeydown = (event) => { if (event.key === "Enter") runSearch().catch((error) => toast(error.message, true)); };
$$('[data-field][data-value]').forEach((button) => {
  button.onclick = () => {
    $("#fieldFilter").value = button.dataset.field;
    updateValueFilter();
    $("#valueFilter").value = button.dataset.value;
    runSearch().catch((error) => toast(error.message, true));
  };
});
$$(".prev").forEach((button) => { button.onclick = () => move(-1).catch((error) => toast(error.message, true)); });
$$(".next").forEach((button) => { button.onclick = () => move(1).catch((error) => toast(error.message, true)); });
window.addEventListener("keydown", (event) => {
  if (["INPUT", "SELECT", "BUTTON"].includes(document.activeElement?.tagName)) return;
  if (event.key === "ArrowLeft") move(-1).catch((error) => toast(error.message, true));
  if (event.key === "ArrowRight") move(1).catch((error) => toast(error.message, true));
});
init().catch((error) => toast(error.message, true));
