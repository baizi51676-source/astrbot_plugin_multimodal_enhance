/* 多模态理解增强控制台（总览/配置按 bot 分页；依赖 window.AstrBotPluginPage bridge） */
const bridge = window.AstrBotPluginPage;
const $ = (sel) => document.querySelector(sel);

let providerOptions = [];
let configMeta = [];
let globalConfig = {};
let botsList = [];
let overviewData = null;
let workspace = { configTab: "default", botDraft: null };
let sseSubId = null;
let installPolling = null;

function toast(message, ok = true) {
  const el = $("#toast");
  el.textContent = message;
  el.style.borderColor = ok ? "var(--ok)" : "var(--danger)";
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 2800);
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text ?? "");
  return div.innerHTML;
}

function badge(on) {
  return on
    ? '<span class="badge ok">已启用</span>'
    : '<span class="badge off">未启用</span>';
}

/* ---------------- 顶层标签页 ---------------- */
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("#panel-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "overview") loadOverview();
    if (btn.dataset.tab === "config") loadConfig();
    if (btn.dataset.tab === "logs") loadLogs();
    if (btn.dataset.tab === "env") loadEnv();
  });
});

/* ---------------- 总览（按 bot 分页） ---------------- */
async function loadOverview() {
  try {
    overviewData = await bridge.apiGet("state");
    renderOverview();
  } catch (error) {
    toast("读取总览失败：" + error.message, false);
  }
}

function renderOverview() {
  const bots = (overviewData || {}).bots || [];
  const bar = ['<button class="subtab active" data-t="default">默认</button>']
    .concat(bots.map((b, i) =>
      `<button class="subtab" data-t="${i}">${escapeHtml(b.name || ("Bot " + (i + 1)))}</button>`))
    .join("");
  $("#overview-subtabs").innerHTML = bar;
  $("#overview-subtabs").querySelectorAll(".subtab").forEach((btn) => {
    btn.onclick = () => {
      $("#overview-subtabs").querySelectorAll(".subtab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      renderOverviewBody(btn.dataset.t);
    };
  });
  renderOverviewBody("default");
}

function renderOverviewBody(tab) {
  const data = overviewData || {};
  const bots = data.bots || [];
  const isDefault = tab === "default";
  const bot = isDefault ? null : (bots[Number(tab)] || {});
  const f = isDefault ? (data.features || {}) : (bot.features || {});
  const cards = [
    ["当前视图", isDefault ? "默认配置（全局）" : escapeHtml(bot.name || "")],
    ["总开关", badge(data.enabled)],
    ["图片增强", badge(f.image)],
    ["音频理解", badge(f.audio)],
    ["视频理解", badge(f.video)],
    ["分析中提示", badge(f.notice)],
    ["图片转述注入", data.caption_patch_installed ? "已安装" : "未安装"],
    ["图转文模型", escapeHtml(data.caption_provider || "（未配置）")],
    ["语音转文本", data.stt_configured ? "已配置" : "（未配置）"],
    ["音频深度分析", data.librosa ? "librosa 可用" : "轻量模式（未装 librosa）"],
    ["进行中的解析", String((data.pipeline || {}).active_tasks ?? 0)],
    ["版本", escapeHtml(data.version || "-")],
  ];
  if (!isDefault) {
    cards.push(["匹配目标", escapeHtml((bot.targets || []).join("、") || "（未设置）")]);
    cards.push(["覆写项数量", String(Object.keys(bot.overrides || {}).length)]);
  }
  $("#overview-cards").innerHTML = cards
    .map(([k, v]) => `<div class="card"><div class="k">${escapeHtml(k)}</div><div class="v">${v}</div></div>`)
    .join("");

  const box = $("#overview-overrides");
  if (isDefault) {
    box.innerHTML = '<li class="muted">默认视图无覆写项（每个 Bot 页可见其覆写清单）。</li>';
  } else {
    const ov = bot.overrides || {};
    const keys = Object.keys(ov);
    box.innerHTML = keys.length
      ? keys.map((k) => `<li><code>${escapeHtml(k)}</code> = ${escapeHtml(JSON.stringify(ov[k]))}</li>`).join("")
      : '<li class="muted">未覆写任何项（全部跟随默认配置）。</li>';
  }

  const errors = data.recent_errors || [];
  $("#overview-errors").innerHTML = errors.length
    ? errors.map((e) => `<li>[${escapeHtml(e.time)}] ${escapeHtml(e.msg)}</li>`).join("")
    : '<li class="muted">暂无</li>';
}

/* ---------------- 配置（默认 + Bot 分页） ---------------- */
async function loadConfig() {
  try {
    const [cfg, prov] = await Promise.all([
      bridge.apiGet("config"),
      bridge.apiGet("providers"),
    ]);
    configMeta = cfg.meta || [];
    globalConfig = cfg.config || {};
    botsList = cfg.bots || [];
    providerOptions = prov.providers || [];
    if (workspace.configTab !== "default" &&
        Number(workspace.configTab) >= botsList.length) {
      workspace.configTab = "default";
    }
    renderConfigPage();
  } catch (error) {
    toast("读取配置失败：" + error.message, false);
  }
}

function renderConfigPage() {
  const bar = ['<button class="subtab' + (workspace.configTab === "default" ? " active" : "") + '" data-t="default">默认配置</button>']
    .concat(botsList.map((b, i) =>
      `<button class="subtab${workspace.configTab === String(i) ? " active" : ""}" data-t="${i}">${escapeHtml(b.name || ("Bot " + (i + 1)))}</button>`))
    .concat(['<button class="subtab add" id="btn-add-bot">＋新增 Bot</button>'])
    .join("");
  $("#config-subtabs").innerHTML = bar;
  $("#config-subtabs").querySelectorAll(".subtab[data-t]").forEach((btn) => {
    btn.onclick = () => {
      workspace.configTab = btn.dataset.t;
      workspace.botDraft = null;
      renderConfigPage();
    };
  });
  $("#btn-add-bot").onclick = addBot;
  $("#save-config").onclick = () => {
    if (workspace.configTab === "default") saveGlobalConfig();
    else saveBotConfig();
  };
  $("#reload-config").onclick = loadConfig;

  if (workspace.configTab === "default") renderGlobalForm();
  else renderBotForm(Number(workspace.configTab));
}

function controlHtml(item, value, disabled, id) {
  const dis = disabled ? " disabled" : "";
  if (item.type === "bool") {
    return `<div class="row"><input type="checkbox" id="${id}" ${value ? "checked" : ""}${dis}/></div>`;
  }
  if (item.type === "int") {
    return `<input type="number" id="${id}" value="${Number(value ?? 0)}"${dis}/>`;
  }
  if (item.type === "select" || item.type === "provider") {
    let options;
    if (item.type === "select") {
      options = item.options || [];
    } else {
      options = [{ value: "", label: "（跟随 AstrBot 设置）" }]
        .concat(providerOptions.map((p) => ({ value: p.id, label: p.label })));
    }
    const opts = options
      .map((o) => `<option value="${escapeHtml(o.value)}" ${String(value ?? "") === String(o.value) ? "selected" : ""}>${escapeHtml(o.label)}</option>`)
      .join("");
    return `<select id="${id}"${dis}>${opts}</select>`;
  }
  if (item.type === "textarea" || item.type === "json") {
    const isJson = item.type === "json";
    const text = isJson ? JSON.stringify(value ?? [], null, 2) : String(value ?? "");
    return `<textarea id="${id}"${dis}>${escapeHtml(text)}</textarea>`;
  }
  return `<input type="text" id="${id}" value="${escapeHtml(String(value ?? ""))}"${dis}/>`;
}

function renderGlobalForm() {
  const groups = new Map();
  for (const item of configMeta) {
    if (!groups.has(item.group)) groups.set(item.group, []);
    groups.get(item.group).push(item);
  }
  const html = [];
  for (const [group, items] of groups) {
    html.push(`<div class="field-group-title">${escapeHtml(group)}</div>`);
    for (const item of items) {
      const id = "gf-" + item.key;
      const hint = item.hint ? `<div class="fhint">${escapeHtml(item.hint)}</div>` : "";
      html.push(
        `<div class="field" data-key="${item.key}" data-type="${item.type}">
          <label for="${id}">${escapeHtml(item.label)}</label>${hint}
          ${controlHtml(item, globalConfig[item.key], false, id)}
        </div>`);
    }
  }
  $("#config-body").innerHTML = html.join("");
}

async function saveGlobalConfig() {
  const patch = {};
  const fields = Array.from(document.querySelectorAll("#config-body .field"));
  for (const field of fields) {
    const key = field.dataset.key;
    const type = field.dataset.type;
    let control = field.querySelector("input,select,textarea");
    if (!control) continue;
    if (type === "bool" || control.type === "checkbox") {
      patch[key] = control.checked;
    } else if (type === "int") {
      patch[key] = parseInt(control.value || "0", 10) || 0;
    } else {
      patch[key] = control.value;
    }
  }
  try {
    const res = await bridge.apiPost("config", { patch });
    toast(res.saved ? "默认配置已保存并同步" : "配置已更新（落盘失败，仅本次生效）", !!res.saved);
    await loadConfig();
  } catch (error) {
    toast("保存失败：" + error.message, false);
  }
}

function renderBotForm(index) {
  const bot = botsList[index];
  if (!bot) { workspace.configTab = "default"; renderConfigPage(); return; }
  if (!workspace.botDraft || workspace.botDraft.index !== index) {
    workspace.botDraft = {
      index,
      name: bot.name || "",
      targets: (bot.targets || []).join(", "),
      overrides: JSON.parse(JSON.stringify(bot.overrides || {})),
    };
  }
  const draft = workspace.botDraft;
  const header = `
    <div class="bot-header">
      <div class="field"><label for="bot-name">备注名</label>
        <input type="text" id="bot-name" value="${escapeHtml(draft.name)}" placeholder="例如：主账号"/></div>
      <div class="field"><label for="bot-targets">匹配目标（平台实例 ID / QQ号，逗号分隔）</label>
        <input type="text" id="bot-targets" value="${escapeHtml(draft.targets)}" placeholder="例如：snowluma, 1234567890"/></div>
    </div>
    <div class="btn-row" style="margin-bottom:12px">
      <button class="btn danger" id="bot-delete" type="button">删除此 Bot 配置</button>
    </div>`;
  const html = [header];
  for (const item of configMeta) {
    if (item.scope === "system") continue;
    const key = item.key;
    const overridden = Object.prototype.hasOwnProperty.call(draft.overrides, key);
    const value = overridden ? draft.overrides[key] : globalConfig[key];
    const id = "bf-" + key;
    const hint = item.hint ? `<div class="fhint">${escapeHtml(item.hint)}</div>` : "";
    html.push(
      `<div class="field" data-key="${key}" data-type="${item.type}" data-overridden="${overridden ? "1" : "0"}">
        <label for="${id}">${escapeHtml(item.label)}
          <span class="src">${overridden ? "已覆写" : "跟随默认"}</span>
          <button class="btn tiny" type="button" data-act="toggle" data-key="${key}">${overridden ? "恢复默认" : "覆写此值"}</button>
        </label>${hint}
        ${controlHtml(item, value, !overridden, id)}
      </div>`);
  }
  $("#config-body").innerHTML = html.join("");
  document.querySelectorAll('#config-body [data-act="toggle"]').forEach((btn) => {
    btn.onclick = () => toggleBotKey(btn.dataset.key);
  });
  const del = $("#bot-delete");
  if (del) del.onclick = deleteBot;
}

function collectBotHeader() {
  if (!workspace.botDraft) return;
  const nameEl = $("#bot-name");
  const targetEl = $("#bot-targets");
  if (nameEl) workspace.botDraft.name = nameEl.value;
  if (targetEl) workspace.botDraft.targets = targetEl.value;
}

function toggleBotKey(key) {
  collectBotHeader();
  const draft = workspace.botDraft;
  if (Object.prototype.hasOwnProperty.call(draft.overrides, key)) {
    delete draft.overrides[key];
  } else {
    draft.overrides[key] = globalConfig[key];
  }
  renderBotForm(draft.index);
}

async function saveBotConfig() {
  collectBotHeader();
  const draft = workspace.botDraft;
  const overrides = {};
  const fields = Array.from(document.querySelectorAll("#config-body .field"));
  for (const field of fields) {
    if (field.dataset.overridden !== "1") continue;
    const key = field.dataset.key;
    const type = field.dataset.type;
    const control = field.querySelector("input,select,textarea");
    if (!control) continue;
    if (type === "bool" || control.type === "checkbox") {
      overrides[key] = control.checked;
    } else if (type === "int") {
      overrides[key] = parseInt(control.value || "0", 10) || 0;
    } else {
      overrides[key] = control.value;
    }
  }
  const targets = String(draft.targets || "")
    .split(/[,，]/).map((s) => s.trim()).filter(Boolean);
  try {
    const res = await bridge.apiPost("bots/save", {
      index: draft.index,
      name: draft.name,
      bots: targets,
      overrides,
    });
    toast(res.saved ? "Bot 配置已保存并同步" : "已更新（落盘失败，仅本次生效）", !!res.saved);
    workspace.botDraft = null;
    await loadConfig();
    workspace.configTab = String(draft.index);
    renderConfigPage();
  } catch (error) {
    toast("保存失败：" + error.message, false);
  }
}

async function addBot() {
  try {
    const res = await bridge.apiPost("bots/add", { name: "新Bot", bots: [] });
    await loadConfig();
    workspace.configTab = String(res.index ?? Math.max(0, botsList.length - 1));
    workspace.botDraft = null;
    renderConfigPage();
    toast("已新增 Bot 配置，请填写备注名与匹配目标");
  } catch (error) {
    toast("新增失败：" + error.message, false);
  }
}

async function deleteBot() {
  const name = (workspace.botDraft && workspace.botDraft.name) || "该 Bot";
  if (!window.confirm(`确认删除「${name}」的覆写配置？`)) return;
  try {
    await bridge.apiPost("bots/delete", { index: workspace.botDraft.index });
    workspace.botDraft = null;
    workspace.configTab = "default";
    await loadConfig();
    toast("已删除");
  } catch (error) {
    toast("删除失败：" + error.message, false);
  }
}

/* ---------------- 插件日志 ---------------- */
function formatLogs(entries) {
  return entries.map((e) => `[${e.time}][${e.level}] ${e.msg}`).join("\n");
}

async function loadLogs() {
  try {
    const data = await bridge.apiGet("logs", { n: 400 });
    const view = $("#log-view");
    view.textContent = (data.logs || []).length ? formatLogs(data.logs) : "（暂无日志）";
    view.scrollTop = view.scrollHeight;
  } catch (error) {
    toast("读取日志失败：" + error.message, false);
  }
}

async function toggleStream() {
  const btn = $("#logs-stream-toggle");
  if (sseSubId) {
    try { await bridge.unsubscribeSSE(sseSubId); } catch (error) { /* ignore */ }
    sseSubId = null;
    btn.textContent = "开启实时流";
    return;
  }
  try {
    sseSubId = await bridge.subscribeSSE("logs/stream", {
      onMessage(ev) {
        const entry = ev && typeof ev.parsed === "object" && ev.parsed ? ev.parsed : null;
        if (!entry || !entry.time) return;
        const view = $("#log-view");
        if (view.textContent === "（暂无日志）") view.textContent = "";
        view.textContent += (view.textContent ? "\n" : "")
          + `[${entry.time}][${entry.level}] ${entry.msg}`;
        view.scrollTop = view.scrollHeight;
      },
      onError() {
        btn.textContent = "开启实时流";
        sseSubId = null;
      },
    }, {});
    btn.textContent = "停止实时流";
    toast("实时日志流已开启");
  } catch (error) {
    toast("实时流开启失败：" + error.message, false);
  }
}

/* ---------------- 环境配置 ---------------- */
function envRow(name, ok, detail) {
  return `<div class="env-item">
    <div><div class="name">${escapeHtml(name)}</div><div class="detail">${escapeHtml(detail || "未找到")}</div></div>
    <span class="badge ${ok ? "ok" : "warn"}">${ok ? "可用" : "缺失"}</span></div>`;
}

function renderEnv(data) {
  const mods = data.modules || {};
  const moduleRows = [
    ["Python: numpy（频谱分析必需）", mods.numpy],
    ["Python: librosa（音频深度分析）", mods.librosa],
    ["Python: scipy", mods.scipy],
    ["Python: soundfile", mods.soundfile],
  ];
  const html = [
    envRow("ffmpeg", !!(data.ffmpeg || {}).ok,
      ((data.ffmpeg || {}).path || "") + " · " + ((data.ffmpeg || {}).version || "")),
    envRow("ffprobe", !!(data.ffprobe || {}).ok, ((data.ffprobe || {}).path || "")),
    envRow("yt-dlp（B站解析）", !!(data.ytdlp || {}).ok, ((data.ytdlp || {}).version || "")),
    ...moduleRows.map(([label, ok]) => envRow(label, !!ok, ok ? "已安装" : "")),
    envRow("磁盘剩余 / 工作目录", true,
      `${(data.disk || {}).free_human || "?"} / ${(data.disk || {}).total_human || "?"} · ${data.workdir || ""}`),
  ];
  $("#env-status").innerHTML = html.join("");
}

function renderInstall(state) {
  const out = $("#install-output");
  if (!state || (!state.running && !state.started_at)) {
    out.textContent = "（暂无安装任务）";
    return;
  }
  const statusText = state.running ? "安装中…" : (state.ok ? "安装成功" : "安装失败");
  const head = `开始时间：${state.started_at || "-"} · 状态：${statusText}\n包：${(state.packages || []).join(", ")}\n\n`;
  out.textContent = head + (state.output || "");
  out.scrollTop = out.scrollHeight;
}

function startInstallPolling() {
  if (installPolling) return;
  installPolling = setInterval(async () => {
    try {
      const state = await bridge.apiGet("env/install/status");
      renderInstall(state);
      if (!state.running) {
        clearInterval(installPolling);
        installPolling = null;
        loadEnv();
        toast(state.ok ? "依赖安装完成" : "依赖安装失败，详见输出", !!state.ok);
      }
    } catch (error) {
      clearInterval(installPolling);
      installPolling = null;
    }
  }, 2500);
}

async function loadEnv() {
  try {
    const data = await bridge.apiGet("env/check");
    renderEnv(data);
    renderInstall(data.install || {});
    if ((data.install || {}).running) startInstallPolling();
  } catch (error) {
    toast("环境检测失败：" + error.message, false);
  }
}

async function startInstall(packages) {
  try {
    const res = await bridge.apiPost("env/install", { packages });
    toast(res.message || "已开始安装");
    startInstallPolling();
  } catch (error) {
    toast("启动安装失败：" + error.message, false);
  }
}

/* ---------------- 事件绑定 ---------------- */
$("#refresh-overview").onclick = loadOverview;
$("#logs-refresh").onclick = loadLogs;
$("#logs-stream-toggle").onclick = toggleStream;
$("#logs-export").onclick = async () => {
  try {
    await bridge.download("logs/export", {}, "multimodal_enhance_logs.txt");
    toast("导出已开始下载");
  } catch (error) {
    toast("导出失败：" + error.message, false);
  }
};
$("#env-refresh").onclick = loadEnv;
$("#env-clean").onclick = async () => {
  try {
    const res = await bridge.apiPost("env/clean", {});
    toast(`已清理 ${res.removed_files || 0} 个文件（${res.removed_human || "0B"}）`);
    loadEnv();
  } catch (error) {
    toast("清理失败：" + error.message, false);
  }
};
async function startOneClick() {
  try {
    const res = await bridge.apiPost("env/install", { mode: "one_click" });
    toast(res.message || "一键配置已启动");
    startInstallPolling();
  } catch (error) {
    toast("启动失败：" + error.message, false);
  }
}

$("#install-all").onclick = () => startOneClick();

/* ---------------- 初始化 ---------------- */
(async function init() {
  if (!bridge) {
    document.body.innerHTML = '<p style="padding:24px">请从 AstrBot 插件页面打开本控制台。</p>';
    return;
  }
  try {
    const ctx = await bridge.ready();
    if (ctx && ctx.pageTitle) document.title = ctx.pageTitle;
  } catch (error) { /* ignore */ }
  $("#status-line").textContent = "已连接";
  loadOverview();
  window.addEventListener("beforeunload", () => {
    if (sseSubId) { try { bridge.unsubscribeSSE(sseSubId); } catch (error) { /* ignore */ } }
  });
})();