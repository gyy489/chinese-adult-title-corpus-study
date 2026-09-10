"use strict";

const S = { meta: null, position: 1, record: null, edits: {}, loadedEdits: {} };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const THEME_KEY = "gendered-visibility-comparison-theme";
const PERSON_FIELDS = ["objectification_target_position", "desire_holder_position", "pleasure_holder_position", "refusal_resistance_position", "attributed_speech_position"];
const PRIVACY_FIELDS = ["material_creation_visibility", "material_creation_target_position", "distribution_visibility", "distribution_actor_position", "distribution_actor_realization", "distribution_target_position", "distribution_authorization_visibility", "exposure_visibility", "exposure_target_position"];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const payload = await response.json();
  if (!response.ok) throw Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = error ? "show error" : "show";
  window.clearTimeout(node.timer);
  node.timer = window.setTimeout(() => { node.className = ""; }, 3500);
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
    try { window.localStorage.setItem(THEME_KEY, dark ? "dark" : "light"); } catch { /* preference only */ }
  }
}

function fieldDefinition(field, value) {
  return field.options.find((option) => option.id === value)?.definition || "以冻结代码本定义为准。";
}

function isChangedFromHuman(fieldId) {
  const row = S.record.fields.find((item) => item.id === fieldId);
  return S.edits[fieldId] !== row.human;
}

function hasUnsaved() {
  if (!S.record) return false;
  return Object.keys(S.edits).some((key) => S.edits[key] !== S.loadedEdits[key]) ||
    ($("#correctionReason")?.value || "") !== (S.record.correction_reason || "");
}

function optionHtml(field, selected) {
  return field.options.map((option) => `<option value="${escapeHtml(option.id)}" ${option.id === selected ? "selected" : ""}>${escapeHtml(option.label)} · ${escapeHtml(option.id)}</option>`).join("");
}

function fieldHtml(row) {
  const field = S.meta.fields.find((item) => item.id === row.id);
  if ($("#hideSameFields").checked && row.same && !isChangedFromHuman(row.id)) return "";
  const changed = isChangedFromHuman(row.id);
  return `<section class="field ${row.same ? "same" : "different"}${changed ? " corrected" : ""}" data-field-card="${escapeHtml(row.id)}">
    <div class="field-head"><div><h2>${escapeHtml(field.label)}</h2><code>${escapeHtml(row.id)}</code></div><span class="status">${changed ? "已校正" : (row.same ? "原始相同" : "原始不同")}</span></div>
    <div class="answers">
      <div class="answer ai"><span>AI标注 · 只读</span><strong>${escapeHtml(row.ai_label)}</strong><code>${escapeHtml(row.ai)}</code><p>${escapeHtml(fieldDefinition(field, row.ai))}</p><button type="button" class="copy-value" data-copy="ai" data-field="${escapeHtml(row.id)}">采用AI</button></div>
      <div class="answer human"><span>原始人工1 · 只读</span><strong>${escapeHtml(row.human_label)}</strong><code>${escapeHtml(row.human)}</code><p>${escapeHtml(fieldDefinition(field, row.human))}</p><button type="button" class="copy-value" data-copy="human" data-field="${escapeHtml(row.id)}">恢复人工</button></div>
      <div class="answer correction"><span>作者校正 · 可编辑</span><select data-correction-field="${escapeHtml(row.id)}" aria-label="${escapeHtml(field.label)}作者校正">${optionHtml(field, S.edits[row.id])}</select><code>${escapeHtml(S.edits[row.id])}</code><p>${escapeHtml(fieldDefinition(field, S.edits[row.id]))}</p></div>
    </div>
  </section>`;
}

function renderSummary() {
  $("#overall").textContent = `400条 · 16字段原始一致率 ${(S.meta.cell_agreement * 100).toFixed(2)}% · ${S.meta.different_records}条至少一个字段不同 · ${S.meta.correction_records}条已校正`;
  $("#summaryGrid").innerHTML = [...S.meta.field_summary].sort((a, b) => a.agreement - b.agreement)
    .map((field) => `<div class="summary-item"><span>${escapeHtml(field.label)}</span><strong>${(field.agreement * 100).toFixed(1)}%</strong><small>${field.different} / 400 不同</small><i><b style="width:${field.agreement * 100}%"></b></i></div>`).join("");
}

function bindFieldControls() {
  $$('[data-correction-field]').forEach((select) => {
    select.onchange = () => {
      S.edits[select.dataset.correctionField] = select.value;
      normalizeDependencies();
      render();
    };
  });
  $$(".copy-value").forEach((button) => {
    button.onclick = () => {
      const row = S.record.fields.find((item) => item.id === button.dataset.field);
      S.edits[row.id] = row[button.dataset.copy];
      normalizeDependencies();
      render();
    };
  });
}

function normalizeDependencies() {
  if (S.edits.record_validity !== "valid") {
    Object.keys(S.edits).filter((id) => id !== "record_validity").forEach((id) => { S.edits[id] = "not_applicable"; });
    return;
  }
  PERSON_FIELDS.forEach((id) => { if (S.edits[id] === "not_applicable") S.edits[id] = "not_visible"; });
  if (S.edits.privacy_chain_relevance === "not_applicable") S.edits.privacy_chain_relevance = "not_relevant";
  if (S.edits.privacy_chain_relevance === "not_relevant") {
    PRIVACY_FIELDS.forEach((id) => { S.edits[id] = "not_applicable"; });
    return;
  }
  if (S.edits.privacy_chain_relevance === "unclear") {
    PRIVACY_FIELDS.forEach((id) => { S.edits[id] = "unclear"; });
    return;
  }
  [["material_creation_visibility", "material_creation_target_position"], ["exposure_visibility", "exposure_target_position"]].forEach(([visibility, target]) => {
    if (S.edits[visibility] === "not_applicable") S.edits[visibility] = "absent";
    if (S.edits[visibility] === "absent") S.edits[target] = "not_visible";
    if (S.edits[visibility] === "unclear") S.edits[target] = "unclear";
    if (S.edits[visibility] === "visible" && S.edits[target] === "not_applicable") S.edits[target] = "not_visible";
  });
  if (S.edits.distribution_visibility === "not_applicable") S.edits.distribution_visibility = "absent";
  if (S.edits.distribution_visibility === "absent") {
    S.edits.distribution_actor_position = "not_visible";
    S.edits.distribution_target_position = "not_visible";
    S.edits.distribution_actor_realization = "not_applicable";
    S.edits.distribution_authorization_visibility = "not_applicable";
    return;
  }
  if (S.edits.distribution_visibility === "unclear") {
    ["distribution_actor_position", "distribution_target_position", "distribution_actor_realization", "distribution_authorization_visibility"].forEach((id) => { S.edits[id] = "unclear"; });
    return;
  }
  if (S.edits.distribution_target_position === "not_applicable") S.edits.distribution_target_position = "not_visible";
  if (S.edits.distribution_authorization_visibility === "not_applicable") S.edits.distribution_authorization_visibility = "unclear";
  if (S.edits.distribution_actor_position === "not_applicable") S.edits.distribution_actor_position = "not_visible";
  if (S.edits.distribution_actor_position === "not_visible") S.edits.distribution_actor_realization = "no_actor_expression";
  else if (S.edits.distribution_actor_position === "unclear") S.edits.distribution_actor_realization = "unclear";
  else if (["not_applicable", "passive_agent_omitted", "nominalized_event_without_agent", "no_actor_expression", "unclear"].includes(S.edits.distribution_actor_realization)) {
    S.edits.distribution_actor_realization = ["feminine", "masculine", "gender_diverse", "mixed"].includes(S.edits.distribution_actor_position) ? "explicit_gendered_person_or_role" : "explicit_ungendered_person_or_role";
  }
}

function render() {
  const diff = S.record.difference_count;
  const changed = Object.keys(S.edits).filter(isChangedFromHuman).length;
  $("#position").textContent = `${S.record.position} / ${S.meta.total} · ${S.record.blind_id}`;
  $("#title").textContent = S.record.title;
  $("#recordStats").textContent = `${diff ? `原始 ${diff} / 16 个字段不同` : "原始16字段全部相同"}${changed ? ` · 当前校正 ${changed} 个` : ""}`;
  $("#recordStats").className = diff ? "has-difference" : "all-same";
  $("#fields").innerHTML = S.record.fields.map(fieldHtml).join("") || '<div class="empty">本条没有需要显示的差异字段。</div>';
  const notes = S.record.evidence_notes?.trim();
  $("#notes").hidden = !notes;
  $("#notes p").textContent = notes || "";
  $("#correctionState").textContent = S.record.correction_count ? `已保存 ${S.record.correction_count} 个字段 · revision ${S.record.correction_revision} · ${S.record.correction_updated_at || ""}` : `本条未校正 · 历史revision ${S.record.correction_revision}`;
  $("#saveHint").textContent = changed ? `当前相对原始人工改动 ${changed} 个字段。` : "当前与原始人工1完全相同。";
  $("#saveCorrection").disabled = changed === 0 || !$("#correctionReason").value.trim();
  $("#resetCorrection").disabled = S.record.correction_count === 0;
  bindFieldControls();
  const positions = activePositions();
  const index = positions.indexOf(S.position);
  $$(".prev").forEach((button) => { button.disabled = index <= 0; });
  $$(".next").forEach((button) => { button.disabled = index < 0 || index >= positions.length - 1; });
}

function activePositions() {
  return $("#differentOnly").checked ? S.meta.different_positions : Array.from({ length: S.meta.total }, (_, index) => index + 1);
}

async function load(position, force = false) {
  if (!force && hasUnsaved() && !window.confirm("当前有未保存的校正，确定放弃并切换记录？")) return false;
  const payload = await api(`/api/comparison?position=${position}`);
  S.position = position;
  S.record = payload.record;
  S.edits = Object.fromEntries(S.record.fields.map((row) => [row.id, row.corrected]));
  S.loadedEdits = { ...S.edits };
  $("#correctionReason").value = S.record.correction_reason || "";
  render();
  window.requestAnimationFrame(() => window.scrollTo({ top: 0, left: 0, behavior: "auto" }));
  return true;
}

async function refreshMeta() {
  S.meta = await api("/api/meta");
  renderSummary();
}

async function move(delta) {
  const positions = activePositions();
  let index = positions.indexOf(S.position);
  if (index < 0) index = delta > 0 ? -1 : positions.length;
  const nextIndex = Math.min(positions.length - 1, Math.max(0, index + delta));
  if (positions[nextIndex] && positions[nextIndex] !== S.position) await load(positions[nextIndex]);
}

async function toggleDifferentOnly() {
  if ($("#differentOnly").checked && !S.meta.different_positions.includes(S.position)) {
    await load(S.meta.different_positions.find((position) => position > S.position) || S.meta.different_positions[0]);
  } else render();
}

async function saveCorrection() {
  const payload = await api("/api/correction", { method: "POST", body: JSON.stringify({ position: S.position, annotation: S.edits, reason: $("#correctionReason").value, expected_revision: S.record.correction_revision }) });
  S.record = payload.record;
  S.edits = Object.fromEntries(S.record.fields.map((row) => [row.id, row.corrected]));
  S.loadedEdits = { ...S.edits };
  $("#correctionReason").value = S.record.correction_reason || "";
  await refreshMeta();
  render();
  toast("作者校正已保存，原始人工盲审未改动。");
}

async function resetCorrection() {
  if (!window.confirm("撤销本条当前校正、恢复原始人工1？历史事件仍会保留。")) return;
  const payload = await api("/api/correction/reset", { method: "POST", body: JSON.stringify({ position: S.position, expected_revision: S.record.correction_revision }) });
  S.record = payload.record;
  S.edits = Object.fromEntries(S.record.fields.map((row) => [row.id, row.corrected]));
  S.loadedEdits = { ...S.edits };
  $("#correctionReason").value = "";
  await refreshMeta();
  render();
  toast("已恢复原始人工1；撤销事件已留痕。");
}

async function init() {
  const colorScheme = window.matchMedia("(prefers-color-scheme: dark)");
  applyTheme(savedTheme() || (colorScheme.matches ? "dark" : "light"));
  S.meta = await api("/api/meta");
  $("#appTitle").textContent = S.meta.title;
  $("#warning").textContent = S.meta.warning;
  renderSummary();
  await load(1, true);
}

$("#theme").onclick = () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
$("#summaryToggle").onclick = () => { $("#summary").hidden = !$("#summary").hidden; };
$("#summaryClose").onclick = () => { $("#summary").hidden = true; };
$("#differentOnly").onchange = () => toggleDifferentOnly().catch((error) => toast(error.message, true));
$("#hideSameFields").onchange = render;
$("#correctionReason").oninput = render;
$("#useAiAll").onclick = () => { S.record.fields.forEach((row) => { S.edits[row.id] = row.ai; }); normalizeDependencies(); render(); };
$("#restoreHumanAll").onclick = () => { S.record.fields.forEach((row) => { S.edits[row.id] = row.human; }); render(); };
$("#saveCorrection").onclick = () => saveCorrection().catch((error) => toast(error.message, true));
$("#resetCorrection").onclick = () => resetCorrection().catch((error) => toast(error.message, true));
$$('.prev').forEach((button) => { button.onclick = () => move(-1).catch((error) => toast(error.message, true)); });
$$('.next').forEach((button) => { button.onclick = () => move(1).catch((error) => toast(error.message, true)); });
window.addEventListener("beforeunload", (event) => { if (hasUnsaved()) { event.preventDefault(); event.returnValue = ""; } });
window.addEventListener("keydown", (event) => {
  if (["INPUT", "BUTTON", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
  if (event.key === "ArrowLeft") move(-1).catch((error) => toast(error.message, true));
  if (event.key === "ArrowRight") move(1).catch((error) => toast(error.message, true));
  if (event.key.toLowerCase() === "d") { $("#differentOnly").checked = !$("#differentOnly").checked; toggleDifferentOnly().catch((error) => toast(error.message, true)); }
});
init().catch((error) => toast(error.message, true));
