"use strict";

const S = { meta: null, token: "", record: null, form: {}, dirty: false, started: Date.now(), timer: null, saving: false };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const THEME_KEY = "gendered-visibility-review-theme";

function savedTheme() {
  try { return window.localStorage.getItem(THEME_KEY); } catch { return null; }
}

function applyTheme(theme, persist = false) {
  const dark = theme === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  const button = $("#theme");
  if (button) {
    button.textContent = dark ? "☀ 亮色" : "☾ 暗色";
    button.setAttribute("aria-pressed", String(dark));
    button.title = dark ? "切换到亮色模式" : "切换到暗色模式";
  }
  if (persist) {
    try { window.localStorage.setItem(THEME_KEY, dark ? "dark" : "light"); } catch { /* Display preference is optional. */ }
  }
}

const colorScheme = window.matchMedia("(prefers-color-scheme: dark)");
applyTheme(savedTheme() || (colorScheme.matches ? "dark" : "light"));
colorScheme.addEventListener?.("change", (event) => {
  if (!savedTheme()) applyTheme(event.matches ? "dark" : "light");
});

function syncHeaderHeight() {
  const header = $("header");
  if (header) document.documentElement.style.setProperty("--header-height", `${header.offsetHeight}px`);
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
  node.timer = setTimeout(() => { node.className = ""; }, 3500);
}

function emptyForm() {
  return Object.fromEntries(S.meta.fields.map((field) => [field.id, field.type === "multi" ? [] : ""]));
}

function reviewTitle() {
  const reviewerName = S.meta.coder_id === "reviewer_2" ? "第二位审核员" : "第一位审核员";
  return `论文核心构念人工盲审（${reviewerName}，${S.meta.summary.total}条）`;
}

const FIELD_EXAMPLES = {
  record_validity: "“私密影片第12集”→有效标题记录；“404 Not Found”→技术噪声。",
  privacy_chain_relevance: "“她的私密视频被上传”→相关；“她主动赴约”→不相关。",
  material_creation_visibility: "“他偷偷录下她的私密视频”→可见；“她的视频被上传”→制作阶段缺席。",
  material_creation_target_position: "“他偷偷拍摄她”→制作对象为女性位置。",
  distribution_visibility: "“他把私密视频发到群里”→可见；只写“偷偷拍摄”→分发阶段缺席。",
  distribution_actor_position: "“她把视频上传”→女性位置；“视频被上传”→行动者不可见。",
  distribution_actor_realization: "“他上传视频”→显式有性别人物；“视频被上传”→被动式省略行动者。",
  distribution_target_position: "“他上传她的私密视频”→分发对象为女性位置，不是观看者。",
  distribution_authorization_visibility: "“经她同意后发布”→明确授权；“未经她同意上传”→明确未授权；只写“上传她的视频”→无可见授权语言。",
  exposure_visibility: "“她的私密视频被同事看见”→暴露结果可见。",
  exposure_target_position: "“大家看到了他的私密视频”→暴露对象为男性位置。",
  objectification_target_position: "“把她当作可供挑选的商品”→女性位置；仅写“她与他亲密”不足以判定物化。",
  desire_holder_position: "“她说自己想要”→欲望持有者为女性位置；仅参与行为不等于有欲望。",
  pleasure_holder_position: "“他明确说自己很享受”→愉悦持有者为男性位置。",
  refusal_resistance_position: "“她拒绝并要求停止”→拒绝／抵抗者为女性位置。",
  attributed_speech_position: "“她说‘不要上传’”→被归属话语者为女性位置。",
  needs_adjudication: "代词指向有两种同样合理的解读→需要裁决；只有一种稳定编码→不需要裁决。",
  evidence_notes: "只记录最短必要片段，例如“未经她同意”，并说明为何存在边界问题。",
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function fieldHtml(field, sectionChanged) {
  const heading = sectionChanged ? `<h2 class="section-heading">${escapeHtml(field.section || "编码")}</h2>` : "";
  const help = field.help ? `<p class="field-help">${escapeHtml(field.help)}</p>` : "";
  const rule = field.decision_rule ? `<p class="decision-rule"><b>怎么选：</b>${escapeHtml(field.decision_rule)}</p>` : "";
  const examples = field.examples?.length
    ? `<div class="field-examples"><b>具体例子（虚构，不是400条答案）</b>${field.examples.map((item) => `<div class="example-row"><q>${escapeHtml(item.title)}</q><span>选：${escapeHtml(item.answer)}</span><small>${escapeHtml(item.why)}</small></div>`).join("")}</div>`
    : `<p class="field-example"><b>合成判定例（不来自400条样本）：</b>${escapeHtml(FIELD_EXAMPLES[field.id])}</p>`;
  let input;
  if (field.type === "text") {
    input = `<textarea data-text="${field.id}" placeholder="必要时记录最短证据和判断理由">${escapeHtml(S.form[field.id] || "")}</textarea>`;
  } else {
    const buttons = field.options.map((option) => {
      const selected = field.type === "multi" ? (S.form[field.id] || []).includes(option.id) : S.form[field.id] === option.id;
      return `<button type="button" class="choice ${selected ? "selected" : ""}" data-field="${field.id}" data-value="${option.id}">${escapeHtml(option.label)}</button>`;
    }).join("");
    input = `<div class="choices">${buttons}</div>`;
  }
  return `${heading}<section class="field ${field.type}" data-fieldbox="${field.id}"><h3>${escapeHtml(field.label)}${field.type === "multi" ? "（可多选）" : ""}</h3>${help}${rule}${examples}${input}</section>`;
}

function render() {
  const record = S.record;
  $("#appTitle").textContent = reviewTitle();
  $("#title").textContent = record.title;
  $("#position").textContent = `${record.position} / ${record.total} · ${record.blind_id}`;
  $("#status").textContent = ({ unreviewed: "未审核", draft: "草稿", reviewed: "已完成" })[record.status];
  $("#prev").disabled = record.position === 1;
  const percent = Math.round((record.position / record.total) * 100);
  $("#positionProgress").style.width = `${percent}%`;
  $("#positionProgress").setAttribute("aria-valuenow", String(percent));
  let previousSection = null;
  $("#form").innerHTML = S.meta.fields.map((field) => {
    const changed = field.section !== previousSection;
    previousSection = field.section;
    return fieldHtml(field, changed);
  }).join("");
  $$(".choice").forEach((button) => { button.onclick = () => choose(button.dataset.field, button.dataset.value); });
  $$("textarea").forEach((textarea) => {
    textarea.oninput = () => {
      S.form[textarea.dataset.text] = textarea.value;
      markDirty();
      scheduleAutoAdvance();
    };
  });
  syncHeaderHeight();
}

const PRIVACY_FIELDS = [
  "material_creation_visibility", "material_creation_target_position", "distribution_visibility",
  "distribution_actor_position", "distribution_actor_realization", "distribution_target_position",
  "distribution_authorization_visibility", "exposure_visibility", "exposure_target_position",
];
const SEMANTIC_FIELDS = [
  "privacy_chain_relevance", ...PRIVACY_FIELDS, "objectification_target_position", "desire_holder_position",
  "pleasure_holder_position", "refusal_resistance_position", "attributed_speech_position",
];

function applyMechanicalRules(id, value) {
  if (id === "record_validity" && value !== "valid") {
    SEMANTIC_FIELDS.forEach((field) => { if (field in S.form) S.form[field] = "not_applicable"; });
  }
  if (id === "record_validity" && value === "valid") {
    SEMANTIC_FIELDS.forEach((field) => { if (S.form[field] === "not_applicable") S.form[field] = ""; });
  }
  if (id === "privacy_chain_relevance" && value === "not_relevant") {
    PRIVACY_FIELDS.forEach((field) => { S.form[field] = "not_applicable"; });
  }
  if (id === "privacy_chain_relevance" && value === "unclear") {
    PRIVACY_FIELDS.forEach((field) => { S.form[field] = "unclear"; });
    S.form.needs_adjudication = "yes";
  }
  const pair = { material_creation_visibility: "material_creation_target_position", exposure_visibility: "exposure_target_position" }[id];
  if (pair && value === "absent") S.form[pair] = "not_visible";
  if (pair && value === "unclear") S.form[pair] = "unclear";
  if (id === "distribution_visibility" && value === "absent") {
    S.form.distribution_actor_position = "not_visible";
    S.form.distribution_target_position = "not_visible";
    S.form.distribution_actor_realization = "not_applicable";
    S.form.distribution_authorization_visibility = "not_applicable";
  }
  if (id === "distribution_visibility" && value === "unclear") {
    ["distribution_actor_position", "distribution_target_position", "distribution_actor_realization", "distribution_authorization_visibility"].forEach((field) => { S.form[field] = "unclear"; });
  }
  if (value === "unclear") S.form.needs_adjudication = "yes";
}

function optionRequiresNote(field, value) {
  if (field.type === "multi") {
    return (value || []).some((item) => field.options.some((option) => option.id === item && option.requires_note));
  }
  return field.options?.some((option) => option.id === value && option.requires_note) || false;
}

function noteIsRequired() {
  return S.meta.fields.some((field) => field.type !== "text" && optionRequiresNote(field, S.form[field.id]))
    || S.form.needs_adjudication === "yes";
}

function fieldIsComplete(field) {
  const value = S.form[field.id];
  if (field.type === "text") return !noteIsRequired() || Boolean(String(value || "").trim());
  if (field.type === "multi") return Array.isArray(value) && value.length > 0;
  return Boolean(value);
}

function formIsComplete() {
  return S.meta.fields.every(fieldIsComplete);
}

function scrollToNextUnit(id) {
  const index = S.meta.fields.findIndex((field) => field.id === id);
  const next = S.meta.fields.slice(index + 1).find((field) => !fieldIsComplete(field));
  if (!next) return;
  const target = document.querySelector(`[data-fieldbox="${next.id}"]`);
  if (!target) return;
  const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  target.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  target.classList.add("next-unit");
  window.setTimeout(() => target.classList.remove("next-unit"), 650);
}

function scheduleAutoAdvance() {
  if (!formIsComplete() || S.saving) return;
  clearTimeout(S.timer);
  $("#saveState").textContent = "本条已填完，正在保存并进入下一条……";
  S.timer = window.setTimeout(() => save("reviewed", true), 450);
}

function choose(id, value) {
  const field = S.meta.fields.find((item) => item.id === id);
  if (field.type === "multi") {
    const selected = new Set(S.form[id] || []);
    const exclusive = new Set(field.exclusive_options || []);
    if (exclusive.has(value)) S.form[id] = selected.has(value) ? [] : [value];
    else {
      exclusive.forEach((item) => selected.delete(item));
      selected.has(value) ? selected.delete(value) : selected.add(value);
      S.form[id] = [...selected];
    }
  } else S.form[id] = value;
  applyMechanicalRules(id, value);
  const option = field.options.find((item) => item.id === value);
  $("#detail").innerHTML = `<strong>${escapeHtml(field.label)}：${escapeHtml(option.label)}</strong><p>${escapeHtml(option.definition || "按按钮字面含义选择。")}</p>${option.exclude ? `<p><b>排除／注意：</b>${escapeHtml(option.exclude)}</p>` : ""}`;
  markDirty();
  render();
  window.requestAnimationFrame(() => scrollToNextUnit(id));
  scheduleAutoAdvance();
}

function markDirty() {
  S.dirty = true;
  $("#saveState").textContent = "正在自动保存……";
  clearTimeout(S.timer);
  S.timer = setTimeout(() => save("draft"), 700);
}

async function load(position = null, next = false) {
  if (S.dirty && !confirm("放弃尚未保存的修改？")) return;
  const query = new URLSearchParams();
  if (position) query.set("position", position);
  if (next) query.set("next_unreviewed", "1");
  const payload = await api(`/api/record?${query}`);
  if (!payload.record) { toast(`${S.meta.summary.total}条已全部完成`); await showSummary(); return; }
  S.record = payload.record;
  S.form = { ...emptyForm(), ...payload.record.annotation };
  S.dirty = false;
  S.started = Date.now();
  render();
  window.requestAnimationFrame(() => window.scrollTo({ top: 0, left: 0, behavior: "auto" }));
}

async function save(status, move = false) {
  if (S.saving || !S.record) return;
  S.saving = true;
  let preserveAsDraft = false;
  try {
    const payload = await api("/api/annotation", { method: "POST", body: JSON.stringify({
      record_id: S.record.record_id, annotation: S.form, status, duration_ms: Date.now() - S.started,
    }) });
    S.dirty = false;
    S.record.status = status;
    S.record.revision = payload.revision;
    $("#saveState").textContent = `已保存修订 ${payload.revision}`;
    if (move) await load(null, true);
    return true;
  } catch (error) {
    toast(error.message, true);
    preserveAsDraft = status === "reviewed" && S.dirty;
    $("#saveState").textContent = "请修正红色提示后继续；当前修改将保存为草稿";
    return false;
  } finally {
    S.saving = false;
    if (preserveAsDraft) window.setTimeout(() => save("draft"), 0);
  }
}

async function clearCurrentRecord() {
  if (!S.record || S.saving) {
    if (S.saving) toast("正在保存，请稍后再清空", true);
    return;
  }
  if (!confirm("确定清空本条记录的所有选择和证据备注吗？\n\n只会影响当前这一条，其他记录不变。")) return;
  clearTimeout(S.timer);
  S.form = emptyForm();
  S.dirty = true;
  render();
  $("#detail").textContent = "当前记录已清空，可以重新从第一项开始选择。";
  $("#saveState").textContent = "正在清空并保存为草稿……";
  if (await save("draft")) toast("已清空本条的所有选择和备注");
}

async function showSummary() {
  const summary = await api("/api/summary");
  $("#review").hidden = true;
  $("#summary").hidden = false;
  const reviewed = summary.status_counts.reviewed || 0;
  const draft = summary.status_counts.draft || 0;
  const unreviewed = summary.status_counts.unreviewed || 0;
  $("#cards").innerHTML = [["已完成", reviewed], ["草稿", draft], ["未审核", unreviewed]].map(([label, count]) => `<div class="card"><span>${label}</span><strong>${count}</strong></div>`).join("");
  $("#summaryText").textContent = `已完成 ${reviewed} / ${summary.total}（${Math.round((reviewed / summary.total) * 100)}%）`;
}

function searchableFields() {
  return S.meta.fields.filter((field) => field.type !== "text" && field.options?.length);
}

function renderSearchOptions() {
  const field = searchableFields().find((item) => item.id === $("#searchField").value);
  $("#searchOption").innerHTML = (field?.options || []).map((option) =>
    `<option value="${escapeHtml(option.id)}">${escapeHtml(option.label)}</option>`
  ).join("");
}

function prepareSearchControls() {
  const fields = searchableFields();
  const current = $("#searchField").value;
  $("#searchField").innerHTML = fields.map((field) =>
    `<option value="${escapeHtml(field.id)}">${escapeHtml(field.label)}</option>`
  ).join("");
  if (fields.some((field) => field.id === current)) $("#searchField").value = current;
  renderSearchOptions();
}

async function showSearch() {
  $("#review").hidden = true;
  $("#summary").hidden = true;
  $("#search").hidden = false;
  const summary = await api("/api/summary");
  const reviewed = summary.status_counts.reviewed || 0;
  const unlocked = reviewed === summary.total;
  [$("#searchField"), $("#searchOption"), $("#searchTitle"), $("#runSearch")].forEach((node) => { node.disabled = !unlocked; });
  if (!unlocked) {
    $("#searchState").textContent = `当前已完成 ${reviewed} / ${summary.total}。完成全部审核后才开放查找，避免中途查看类别分布影响盲审。`;
    $("#searchResults").innerHTML = "";
    return;
  }
  prepareSearchControls();
  $("#searchState").textContent = "请选择一个标注字段和选项。";
}

async function openSearchResult(position) {
  $$('[data-tab]').forEach((item) => item.classList.toggle("active", item.dataset.tab === "review"));
  $("#search").hidden = true;
  $("#summary").hidden = true;
  $("#review").hidden = false;
  await load(position);
}

async function runSearch() {
  const query = new URLSearchParams({
    field: $("#searchField").value,
    option: $("#searchOption").value,
    title: $("#searchTitle").value.trim(),
  });
  $("#searchState").textContent = "正在查找……";
  const payload = await api(`/api/search?${query}`);
  $("#searchState").textContent = `找到 ${payload.count} 条。点击标题进入修改。`;
  $("#searchResults").innerHTML = payload.results.map((record) =>
    `<button class="search-result" type="button" data-search-position="${record.position}"><small>${record.position} / ${S.meta.summary.total} · ${escapeHtml(record.blind_id)}</small>${escapeHtml(record.title)}</button>`
  ).join("");
  $$('[data-search-position]').forEach((button) => {
    button.onclick = () => openSearchResult(Number(button.dataset.searchPosition)).catch((error) => toast(error.message, true));
  });
}

async function init() {
  S.meta = await api("/api/meta");
  S.token = S.meta.request_token;
  $("#appTitle").textContent = reviewTitle();
  $("#identity").textContent = `${S.meta.coder_id} · v${S.meta.version} · 本机独立 SQLite`;
  $("#instructions").innerHTML = S.meta.instructions.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  await load(null, true);
}

window.addEventListener("resize", syncHeaderHeight);
if (window.ResizeObserver) new ResizeObserver(syncHeaderHeight).observe($("header"));

$("#prev").onclick = () => load(S.record.position - 1);
$("#clearCurrent").onclick = () => clearCurrentRecord().catch((error) => toast(error.message, true));
$("#searchField").onchange = renderSearchOptions;
$("#runSearch").onclick = () => runSearch().catch((error) => toast(error.message, true));
$("#searchTitle").onkeydown = (event) => {
  if (event.key === "Enter") { event.preventDefault(); runSearch().catch((error) => toast(error.message, true)); }
};
$("#theme").onclick = () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
$("#export").onclick = async () => {
  try { toast(`已导出：${(await api("/api/export", { method: "POST", body: "{}" })).export_path}`); }
  catch (error) { toast(error.message, true); }
};
$$('[data-tab]').forEach((button) => {
  button.onclick = async () => {
    $$('[data-tab]').forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    if (button.dataset.tab === "summary") { $("#search").hidden = true; await showSummary(); }
    else if (button.dataset.tab === "search") await showSearch();
    else { $("#summary").hidden = true; $("#search").hidden = true; $("#review").hidden = false; }
  };
});
window.onbeforeunload = (event) => { if (S.dirty) { event.preventDefault(); event.returnValue = ""; } };
init().catch((error) => toast(error.message, true));
