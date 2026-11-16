/* Infinity Wallpaper Studio - front end */
"use strict";

const $ = (id) => document.getElementById(id);
const S = {
  page: 0, totalPages: 0, count: 0,
  source: "all", tag: "", color: "", order: "", q: "", minWidth: 0, minLikes: 0,
  items: [], selected: new Map(), config: null,
  jobId: null, pollTimer: null, busy: false,
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, options) {
  const res = await fetch(path, options);
  let data;
  try { data = await res.json(); } catch (_) { data = {}; }
  if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.hidden = true; }, 2600);
}

function criteria() {
  return {
    source: $("source").value, tag: $("tag").value, color: S.color,
    order: $("order").value, q: $("q").value.trim(),
    minWidth: Number($("minWidth").value || 0), minLikes: Number($("minLikes").value || 0),
  };
}

function downloadSettings() {
  return {
    outDir: $("outDir").value.trim(),
    width: Number($("width").value),
    concurrency: Number($("concurrency").value),
    skipExisting: $("skipExisting").checked,
    overwrite: $("overwrite").checked,
    saveManifest: $("saveManifest").checked,
  };
}

async function init() {
  try {
    const cfg = await api("/api/config");
    S.config = cfg;
    const fill = (sel, list) => {
      $(sel).innerHTML = list.map((o) =>
        `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
    };
    fill("source", cfg.sources);
    fill("tag", cfg.tags);
    fill("order", cfg.orders);
    fill("width", cfg.widths);
    $("outDir").value = cfg.defaultOut;
    $("colors").innerHTML = [
      `<div class="swatch none active" data-v="" title="全部颜色"></div>`,
      ...cfg.colors.filter((c) => c.value).map((c) =>
        `<div class="swatch" data-v="${c.value}" style="background:${c.hex}" title="${esc(c.label)}"></div>`),
    ].join("");
    $("colors").addEventListener("click", (e) => {
      const sw = e.target.closest(".swatch");
      if (!sw) return;
      [...$("colors").children].forEach((x) => x.classList.remove("active"));
      sw.classList.add("active");
      S.color = sw.dataset.v;
      S.page = 0;
      load();
    });
    $("statDownloaded").textContent = cfg.downloaded.toLocaleString();
  } catch (err) {
    toast("配置加载失败: " + err.message);
  }
  bindEvents();
  load();
}

function bindEvents() {
  ["source", "tag", "order"].forEach((id) =>
    $(id).addEventListener("change", () => { S.page = 0; load(); }));
  ["q", "minWidth", "minLikes"].forEach((id) =>
    $(id).addEventListener("keydown", (e) => {
      if (e.key === "Enter") { S.page = 0; load(); }
    }));
  $("btnApply").onclick = () => { S.page = 0; load(); };
  $("btnResetFilter").onclick = () => {
    $("source").value = "all"; $("tag").value = ""; $("order").value = "";
    $("q").value = ""; $("minWidth").value = 0; $("minLikes").value = 0;
    S.color = "";
    [...$("colors").children].forEach((x, i) => x.classList.toggle("active", i === 0));
    S.page = 0; load();
  };
  $("btnPrev").onclick = () => { if (S.page > 0) { S.page--; load(); } };
  $("btnNext").onclick = () => {
    if (S.totalPages && S.page + 1 >= S.totalPages) return toast("已经是最后一页");
    S.page++; load();
  };
  $("btnSelectAll").onclick = () => {
    S.items.forEach((r) => S.selected.set(r.id, r));
    updateSelection();
  };
  $("btnClear").onclick = () => { S.selected.clear(); updateSelection(); };

  $("btnDownloadSelected").onclick = () => {
    if (!S.selected.size) return toast("请先选择壁纸");
    startDownload({ items: [...S.selected.values()], ...downloadSettings() });
  };
  $("btnBatchDownload").onclick = () => {
    const pages = Math.max(1, Math.min(200, Number($("batchPages").value || 1)));
    startDownload({
      criteria: criteria(), pages, startPage: S.page,
      ...downloadSettings(),
    }, `批量任务：从第 ${S.page + 1} 页起连续 ${pages} 页`);
  };
  $("btnOpenFolder").onclick = async () => {
    try {
      await api("/api/open-folder", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ outDir: $("outDir").value.trim() }),
      });
    } catch (err) { toast(err.message); }
  };
  $("btnResetHistory").onclick = async () => {
    if (!confirm("清除本机下载去重记录？已下载的文件不会被删除。")) return;
    try {
      await api("/api/reset-history", { method: "POST", body: "{}" });
      const s = await api("/api/stats");
      $("statDownloaded").textContent = s.downloaded.toLocaleString();
      toast("去重记录已清空");
    } catch (err) { toast(err.message); }
  };
  $("btnReport").onclick = async () => {
    try {
      const data = await api("/api/findings");
      renderReport(data);
      $("reportModal").hidden = false;
    } catch (err) { toast(err.message); }
  };
  $("btnCloseReport").onclick = () => { $("reportModal").hidden = true; };
  $("reportModal").addEventListener("click", (e) => {
    if (e.target === $("reportModal")) $("reportModal").hidden = true;
  });
  $("btnCloseJob").onclick = () => { $("jobPanel").hidden = true; };
  $("btnCancel").onclick = async () => {
    if (!S.jobId) return;
    try {
      await api("/api/cancel", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: S.jobId }),
      });
      toast("已发送取消请求");
    } catch (err) { toast(err.message); }
  };
}

async function load() {
  $("loading").hidden = false;
  $("empty").hidden = true;
  $("grid").innerHTML = "";
  const c = criteria();
  const qs = new URLSearchParams({ page: String(S.page), ...c });
  try {
    const data = await api("/api/wallpapers?" + qs.toString());
    S.items = data.items || [];
    S.count = data.count || 0;
    S.totalPages = data.totalPages || 0;
    $("statTotal").textContent = S.count.toLocaleString();
    $("pageInfo").textContent = `第 ${S.page + 1} 页`;
    $("pageCount").textContent =
      `本页 ${S.items.length} 张 · 共 ${(S.totalPages || 0).toLocaleString()} 页 / ${S.count.toLocaleString()} 张`;
    $("btnPrev").disabled = S.page === 0;
    $("btnNext").disabled = !S.totalPages || S.page + 1 >= S.totalPages;
    renderGrid();
    $("empty").hidden = S.items.length > 0;
  } catch (err) {
    $("grid").innerHTML = `<div class="empty">读取失败：${esc(err.message)}</div>`;
  } finally {
    $("loading").hidden = true;
  }
}

function renderGrid() {
  const frag = document.createDocumentFragment();
  for (const r of S.items) {
    const card = document.createElement("div");
    card.className = "card" + (S.selected.has(r.id) ? " selected" : "");
    card.dataset.id = r.id;
    const dots = (r.colors || []).slice(0, 8)
      .map((c) => `<i style="background:${esc(c)}"></i>`).join("");
    card.innerHTML = `
      <div class="thumb-wrap">
        <img class="loading-img" loading="lazy" alt="${esc(r.source)}" src="/api/image?url=${encodeURIComponent(r.thumbUrl)}">
        <span class="check-badge">✓</span>
      </div>
      <div class="meta">
        <div class="row1"><span class="source">${esc(r.source || "未知来源")}</span>
          <span class="like">♥ ${r.like}</span></div>
        <div class="dim">${esc(r.dimensions || (r.width ? `${r.width} × ${r.height}` : r.imgId))}</div>
        <div class="colors">${dots}</div>
      </div>`;
    const img = card.querySelector("img");
    img.addEventListener("load", () => img.classList.remove("loading-img"));
    img.addEventListener("error", () => { img.style.opacity = .25; });
    card.addEventListener("click", () => {
      if (S.selected.has(r.id)) S.selected.delete(r.id);
      else S.selected.set(r.id, r);
      card.classList.toggle("selected", S.selected.has(r.id));
      updateSelection();
    });
    frag.appendChild(card);
  }
  $("grid").appendChild(frag);
  updateSelection();
}

function updateSelection() {
  const n = S.selected.size;
  $("statSelected").textContent = n.toLocaleString();
  $("selCount").textContent = n;
  $("btnDownloadSelected").disabled = n === 0 || S.busy;
}

async function startDownload(payload, title) {
  if (S.busy) return toast("已有任务在运行，请等待完成");
  if (!payload.outDir) return toast("请填写保存目录");
  S.busy = true;
  updateSelection();
  try {
    const job = await api("/api/download", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    S.jobId = job.id;
    $("jobPanel").hidden = false;
    $("jobTitle").textContent = title || `下载 ${job.total || payload.items?.length || ""} 张壁纸`;
    $("jobLogs").innerHTML = "";
    clearInterval(S.pollTimer);
    S.pollTimer = setInterval(pollJob, 600);
    pollJob();
  } catch (err) {
    toast("任务启动失败: " + err.message);
    S.busy = false;
    updateSelection();
  }
}

const PHASE = {
  queued: "排队中", fetching: "正在读取图库目录", downloading: "正在下载",
  completed: "已完成", cancelled: "已取消", failed: "失败",
};

async function pollJob() {
  if (!S.jobId) return;
  try {
    const j = await api("/api/jobs?id=" + encodeURIComponent(S.jobId));
    const finished = ["completed", "cancelled", "failed"].includes(j.status);
    $("jobPhase").textContent = finished
      ? (PHASE[j.status] || j.status)
      : ((PHASE[j.phase] || j.phase) + (j.phase === "fetching" ? ` · ${j.message || ""}` : ""));
    const pct = j.total ? Math.round((j.done / j.total) * 100) : 0;
    $("progressBar").style.width = pct + "%";
    $("jobCounts").textContent =
      `${j.done}/${j.total} · 成功 ${j.ok} · 跳过 ${j.skipped} · 失败 ${j.failed}` +
      (j.bytes ? ` · ${(j.bytes / 1048576).toFixed(1)} MB` : "");
    $("jobMessage").textContent = j.message || "";
    $("jobLogs").innerHTML = (j.logs || []).slice(-14)
      .map((l) => `<div>[${l.t}s] ${esc(l.m)}</div>`).join("");
    $("jobLogs").scrollTop = $("jobLogs").scrollHeight;
    $("btnCancel").disabled = ["completed", "cancelled", "failed"].includes(j.status);
    if (["completed", "cancelled", "failed"].includes(j.status)) {
      clearInterval(S.pollTimer);
      S.pollTimer = null;
      S.busy = false;
      updateSelection();
      const s = await api("/api/stats");
      $("statDownloaded").textContent = s.downloaded.toLocaleString();
      toast(j.message || "任务结束");
    }
  } catch (err) {
    clearInterval(S.pollTimer);
    S.busy = false;
    updateSelection();
    toast("进度读取失败: " + err.message);
  }
}

function renderReport(d) {
  const kv = (o) => Object.entries(o).map(([k, v]) =>
    `<b>${esc(k)}</b><code>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</code>`).join("");
  const t = d.target || {};
  const a = d.api || {};
  const html = [];
  html.push(`<div class="report-section"><h3>目标与方法</h3>
    <div class="report-kv">${kv({
      "目标包": t.package, "内部文件": t.inner, "打包格式": t.format,
      "扩展版本": t.extension_version, "内部文件数": t.inner_file_count,
    })}</div><p class="muted" style="font-size:12px;margin:10px 0 0">${esc(d.method)}</p></div>`);
  html.push(`<div class="report-section"><h3>解码出的接口</h3><div class="report-kv">${kv({
    "列表接口": a.list, "方法": a.method, "鉴权": a.auth,
    "每页": "50 条 / page 从 0 开始", "原图": a.image_original,
    "缩略图": a.image_scaled, "图库规模": a.catalog_size_observed,
  })}</div><div class="report-kv" style="margin-top:10px">${kv(a.params || {})}</div>
    <p class="muted" style="font-size:12px;margin:10px 0 0">返回字段：${esc((a.response_fields || []).join(", "))}</p></div>`);
  html.push(`<div class="report-section"><h3>证据链（${(d.evidence || []).length} 条）</h3>` +
    (d.evidence || []).map((e) => `<div class="ev">
      <div class="ev-head"><span class="ev-id">${esc(e.id)}</span>
        <span class="ev-type">${esc(e.type)}</span>
        <span class="ev-src">${esc(e.source)}</span></div>
      <code>${esc(e.quote)}</code><p>${esc(e.note)}</p></div>`).join("") + `</div>`);
  html.push(`<div class="report-section"><h3>结论</h3><ul class="report-list">` +
    (d.findings || []).map((f) => `<li>${esc(f)}</li>`).join("") + `</ul></div>`);
  html.push(`<div class="report-section"><h3>未验证 / 使用边界</h3><div class="report-warn">` +
    (d.unknowns || []).map((u) => esc(u)).join("<br>") + `</div></div>`);
  $("reportBody").innerHTML = html.join("");
}

init();
