/* 多模态理解增强控制台（依赖 window.AstrBotPluginPage bridge） */
const bridge = window.AstrBotPluginPage;
const $ = (sel) => document.querySelector(sel);

let providerOptions = [];
let configMeta = [];
let currentConfig = {};
let lastSeq = 0;
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

/* ---------------- 标签页 ---------------- */
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

/* ---------------- 总览 ---------------- */
async function loadOverview() {
  try {
    const data = await bridge.apiGet("state");
    const f = data.features || {};
    const pipeline = data.pipeline || {};
    const cards = [
      ["总开关", badge(data.enabled)],
      ["图片增强", badge(f.image)],
      ["音频理解", badge(f.audio)],
      ["视频理解", badge(f.video)],
      ["分析中提示", badge(f.notice)],
      ["图片转述注入", data.caption_patch_installed ? "已安装" : "未安装"],
      ["图转文模型", escapeHtml(data.caption_provider || "（未配置）")],
      ["语音转文本", data.stt_configured ? "已配置" : "（未配置）"],
      ["音频深度分析", data.librosa ? "librosa 可用" : "轻量模式（未装 librosa）"],
      ["进行中的解析", String(pipeline.active_tasks ?? 0)],
      ["版本", escapeHtml(data.version || "-")],
    ];
    $("#overview-cards").innerHTML = cards
      .map(([k, v]) => `<div class="card"><div class="k">${escapeHtml(k)}</div><div class="v">${v}</div></div>`)
      .join("");
    const errors = data.recent_errors || [];
    $("#overview-errors").innerHTML = errors.length
      ? errors.map((e) => `<li>[${escapeHtml(e.time)}] ${escapeHtml(e.msg)}</li>`).join("")
      : '<li class="muted">暂无</li>';
  } catch (error) {
    toast("读取总览失败：" + error.message, false);
  }
}

/* ---------------- 配置 ---------------- */
async function loadConfig() {
  try {
    const [cfg, prov] = await Promise.all([
      bridge.apiGet("config"),
      bridge.apiGet("providers"),
    ]);
    configMeta = cfg.meta || [];
    currentConfig = cfg.config || {};
    providerOptions = prov.providers || [];
    renderConfigForm();
  } catch (error) {
    toast("读取配置失败：" + error.message, false);
  }
}

function renderConfigForm() {
  const groups = new Map();
  for (const item of configMeta) {
    if (!groups.has(item.group)) groups.set(item.group, []);
    groups.get(item.group).push(item);
  }
  const html = [];
  for (const [group, items] of groups) {
    html.push(`<div class="field-group-title">${escapeHtml(group)}</div>`);
    for (const item of items) html.push(renderField(item));
  }
  $("#config-form").innerHTML = html.join("");
  $("#save-config").onclick = saveConfig;
  $("#reload-config").onclick = loadConfig;
}

function renderField(item) {
  const value = currentConfig[item.key];
  const id = "cfg-" + item.key;
  const hint = item.hint ? `<div class="fhint">${escapeHtml(item.hint)}</div>` : "";
  const label = `<label for="${id}">${escapeHtml(item.label)}</label>`;
  if (item.type === "bool") {
    return `<div class="field" data-key="${item.key}" data-type="bool">${label}${hint}
      <div class="row"><input type="checkbox" id="${id}" ${value ? "checked" : ""}/></div></div>`;
  }
  if (item.type === "int") {
    return `<div class="field" data-key="${item.key}" data-type="int">${label}${hint}
      <input type="number" id="${id}" value="${Number(value ?? 0)}"/></div>`;
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
    return `<div class="field" data-key="${item.key}" data-type="string">${label}${hint}
      <select id="${id}">${opts}</select></div>`;
  }
  if (item.type === "textarea" || item.type === "json") {
    const isJson = item.type === "json";
    const text = isJson ? JSON.stringify(value ?? [], null, 2) : String(value ?? "");
    return `<div class="field" data-key="${item.key}" data-type="${isJson ? "json" : "string"}">${label}${hint}
      <textarea id="${id}">${escapeHtml(text)}</textarea></div>`;
  }
  return `<div class="field" data-key="${item.key}" data-type="string">${label}${hint}
    <input type="text" id="${id}" value="${escapeHtml(String(value ?? ""))}"/></div>`;
}

async function saveConfig() {
  const patch = {};
  const fields = Array.from(document.querySelectorAll("#config-form .field"));
  for (const field of fields) {
    const key = field.dataset.key;
    const type = field.dataset.type;
    if (type === "bool") {
      patch[key] = field.querySelector("input[type=checkbox]").checked;
    } else if (type === "int") {
      patch[key] = parseInt(field.querySelector("input").value || "0", 10) || 0;
    } else if (type === "json") {
      const raw = (field.querySelector("textarea").value || "").trim() || "[]";
      try {
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) throw new Error("需要 JSON 列表");
        patch[key] = parsed;
      } catch (error) {
        toast(`「${key}」JSON 无效：${error.message}`, false);
        return;
      }
    } else {
      const el = field.querySelector("input,select,textarea");
      patch[key] = el ? el.value : "";
    }
  }
  try {
    const res = await bridge.apiPost("config", { patch });
    currentConfig = res.config || currentConfig;
    toast(res.saved ? "配置已保存并同步" : "配置已更新（落盘失败，仅本次生效）", !!res.saved);
  } catch (error) {
    toast("保存失败：" + error.message, false);
  }
}

/* ---------------- 插件日志 ---------------- */
function formatLogs(entries) {
  return entries.map((e) => `[${e.time}][${e.level}] ${e.msg}`).join("\n");
}

async function loadLogs() {
  try {
    const data = await bridge.apiGet("logs", { n: 400 });
    lastSeq = data.last_seq || 0;
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
    envRow("ffmpeg", !!(data.ffmpeg || {}).ok, ((data.ffmpeg || {}).path || "") + " · " + ((data.ffmpeg || {}).version || "")),
    envRow("ffprobe", !!(data.ffprobe || {}).ok, ((data.ffprobe || {}).path || "")),
    envRow("yt-dlp（B站解析）", !!(data.ytdlp || {}).ok, ((data.ytdlp || {}).version || "")),
    ...moduleRows.map(([label, ok]) => envRow(label, !!ok, ok ? "已安装" : "")),
    envRow("磁盘剩余 / 工作目录", true, `${(data.disk || {}).free_human || "?"} / ${(data.disk || {}).total_human || "?"} · ${data.workdir || ""}`),
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
$("#install-optional").onclick = () => startInstall(["librosa", "scipy", "soundfile"]);
$("#install-numpy").onclick = () => startInstall(["numpy"]);

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