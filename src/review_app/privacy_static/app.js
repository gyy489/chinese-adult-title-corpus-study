"use strict";

const S = { meta: null, token: "", record: null, decision: "", note: "", dirty: false, saving: false, started: Date.now(), timer: null };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const THEME_KEY = "residual-privacy-review-theme";

function applyTheme(theme, persist = false) {
  const dark = theme === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  $("#theme").textContent = dark ? "☀ 亮色" : "☾ 暗色";
  $("#theme").setAttribute("aria-pressed", String(dark));
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
  node.timer = setTimeout(() => { node.className = ""; }, 3000);
}

function setText(selector, value) { $(selector).textContent = String(value); }

function candidateNode(match, context, index) {
  const item = document.createElement("div");
  item.className = "candidate";
  const label = document.createElement("span");
  label.textContent = `候选 ${index + 1}`;
  const value = document.createElement("strong");
  value.textContent = match;
  const contextNode = document.createElement("small");
  contextNode.textContent = context && context !== S.record.title ? `上下文：${context}` : "";
  item.append(label, value, contextNode);
  return item;
}

function renderRecord() {
  const record = S.record;
  setText("#position", `${record.position} / ${record.total} · ${record.review_id}`);
  setText("#titleText", record.title);
  setText("#recordStatus", ({ unreviewed: "未审核", draft: "草稿已保存", reviewed: "已完成" })[record.status]);
  setText("#queueBadge", record.in_probability && record.in_latin ? "概率样本 + 拉丁候选" : record.in_probability ? "概率样本" : "拉丁候选");
  const percent = Math.round((record.position / record.total) * 100);
  $("#progressBar").style.width = `${percent}%`;
  $("#progressText").textContent = `${percent}%`;
  $("#prev").disabled = record.position === 1;
  $("#jumpPosition").value = record.position;
  $("#note").value = S.note;
  $$(".decision").forEach((button) => button.classList.toggle("selected", button.dataset.decision === S.decision));

  const panel = $("#candidatePanel");
  const list = $("#candidateList");
  list.replaceChildren();
  record.latin_matches.forEach((match, index) => list.append(candidateNode(match, record.latin_contexts[index] || "", index)));
  panel.hidden = record.latin_matches.length === 0;
}

async function loadRecord(position = 1, nextUnreviewed = false, afterPosition = 0) {
  if (S.dirty) await save("draft", false, false);
  const query = new URLSearchParams({ position: String(position) });
  if (nextUnreviewed) {
    query.set("next_unreviewed", "1");
    query.set("after_position", String(afterPosition));
  }
  const payload = await api(`/api/record?${query}`);
  if (!payload.record) {
    toast("857条均已完成");
    await showSummary();
    return;
  }
  S.record = payload.record;
  S.decision = payload.record.decision;
  S.note = payload.record.note;
  S.dirty = false;
  S.started = Date.now();
  renderRecord();
  window.scrollTo({ top: 0, behavior: "auto" });
}

function scheduleDraft() {
  S.dirty = true;
  setText("#saveState", "正在自动保存草稿……");
  clearTimeout(S.timer);
  S.timer = setTimeout(() => save("draft", false, false), 450);
}

async function save(status, moveNext = false, announce = true) {
  if (!S.record || S.saving) return false;
  clearTimeout(S.timer);
  S.saving = true;
  try {
    const result = await api("/api/review", { method: "POST", body: JSON.stringify({
      review_id: S.record.review_id,
      decision: S.decision,
      note: S.note,
      status,
      duration_ms: Date.now() - S.started,
    }) });
    S.dirty = false;
    S.record.status = status;
    S.record.revision = result.revision;
    setText("#saveState", status === "reviewed" ? `已完成 · 修订 ${result.revision}` : `草稿已保存 · 修订 ${result.revision}`);
    if (announce && status === "reviewed") toast("已保存");
    if (moveNext) await loadRecord(1, true, S.record.position);
    return true;
  } catch (error) {
    toast(error.message, true);
    setText("#saveState", error.message);
    return false;
  } finally {
    S.saving = false;
  }
}

async function choose(decision) {
  if (S.saving) return;
  S.decision = decision;
  S.dirty = true;
  renderRecord();
  if (decision === "false_positive") {
    S.note = "";
    await save("reviewed", true, false);
  } else {
    scheduleDraft();
    $("#note").focus();
    setText("#saveState", "请写一句理由，再按 Ctrl/⌘ + Enter 或点击保存。草稿会自动保存。");
  }
}

async function showSummary() {
  const summary = await api("/api/summary");
  $("#reviewView").hidden = true;
  $("#summaryView").hidden = false;
  $("#reviewTab").classList.remove("active");
  $("#summaryTab").classList.add("active");
  const reviewed = summary.status_counts.reviewed || 0;
  const unreviewed = summary.status_counts.unreviewed || 0;
  const draft = summary.status_counts.draft || 0;
  const values = [["已完成", reviewed], ["未审核", unreviewed], ["草稿", draft], ["有敏感信息", summary.decision_counts.confirmed_identifier || 0], ["不确定", summary.decision_counts.unclear || 0]];
  const cards = $("#summaryCards");
  cards.replaceChildren();
  values.forEach(([label, value]) => {
    const card = document.createElement("div");
    card.className = "summary-item";
    const name = document.createElement("span");
    name.textContent = label;
    const count = document.createElement("strong");
    count.textContent = value;
    card.append(name, count);
    cards.append(card);
  });
  const probability = summary.queues.probability;
  const latin = summary.queues.latin;
  setText("#queueProgress", `概率样本：${probability.reviewed}/${probability.total}；拉丁候选：${latin.reviewed_unique_titles}/${latin.total_unique_titles}个唯一标题（${latin.candidate_rows}个候选行）。`);
}

function showReview() {
  $("#summaryView").hidden = true;
  $("#reviewView").hidden = false;
  $("#summaryTab").classList.remove("active");
  $("#reviewTab").classList.add("active");
}

async function initialize() {
  const storedTheme = window.localStorage.getItem(THEME_KEY);
  applyTheme(storedTheme || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  S.meta = await api("/api/meta");
  S.token = S.meta.request_token;
  const instructions = $("#instructions");
  S.meta.instructions.forEach((instruction) => {
    const item = document.createElement("li");
    item.textContent = instruction;
    instructions.append(item);
  });
  await loadRecord(1, true, 0);
}

$$(".decision").forEach((button) => { button.onclick = () => choose(button.dataset.decision); });
$("#note").oninput = (event) => { S.note = event.target.value; scheduleDraft(); };
$("#save").onclick = () => save("reviewed", true);
$("#nextIncomplete").onclick = () => loadRecord(1, true, S.record?.position || 0);
$("#prev").onclick = () => loadRecord(Math.max(1, S.record.position - 1));
$("#jump").onclick = () => loadRecord(Number($("#jumpPosition").value));
$("#jumpPosition").onkeydown = (event) => { if (event.key === "Enter") $("#jump").click(); };
$("#summaryTab").onclick = async () => {
  if (S.dirty) await save("draft", false, false);
  await showSummary();
};
$("#reviewTab").onclick = showReview;
$("#continueReview").onclick = async () => { showReview(); await loadRecord(1, true, 0); };
$("#theme").onclick = () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
$("#export").onclick = async () => {
  try {
    if (S.dirty) await save("draft", false, false);
    const result = await api("/api/export", { method: "POST", body: "{}" });
    toast(`已导出：${result.export_path}`);
  } catch (error) { toast(error.message, true); }
};

document.addEventListener("keydown", (event) => {
  const editing = ["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName);
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    save("reviewed", true);
    return;
  }
  if (editing || event.ctrlKey || event.metaKey || event.altKey) return;
  const decisions = { "1": "false_positive", "2": "confirmed_identifier", "3": "unclear" };
  if (decisions[event.key]) {
    event.preventDefault();
    choose(decisions[event.key]);
  }
});

window.addEventListener("beforeunload", (event) => {
  if (S.dirty) { event.preventDefault(); event.returnValue = ""; }
});

initialize().catch((error) => toast(error.message, true));
