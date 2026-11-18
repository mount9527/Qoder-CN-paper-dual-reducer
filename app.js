const $ = s => document.querySelector(s)
const statusEl = $("#status")
const pending = new Map()
let sequence = 0, ready = false, closed = false, busy = false
let hostCaps = {}, context = {}
let currentPath = "", mode = "dual", viewRound = 0, lastView = null

const send = m => window.parent.postMessage({ jsonrpc: "2.0", ...m }, "*")
function report(text, error = false) { statusEl.textContent = text; statusEl.dataset.error = String(error) }
function request(method, params) {
  if (closed || pending.size >= 8) return Promise.reject(new Error("应用不可用，请重新打开。"))
  const id = `pd-${++sequence}`
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error("结果未确认，请刷新状态后再继续。")) }, 180000)
    pending.set(id, { resolve, reject, timer })
    try { send({ id, method, params }) }
    catch { clearTimeout(timer); pending.delete(id); reject(new Error("无法连接宿主，请重新打开应用。")) }
  })
}
async function tool(name, args) {
  const r = await request("tools/call", { name, arguments: args })
  if (r?.isError) throw new Error(r.content?.[0]?.text || "操作失败")
  return r?.structuredContent ?? {}
}
function size() {
  if (!closed && context.displayMode === "inline")
    send({ method: "ui/notifications/size-changed", params: { height: Math.ceil(document.querySelector("main").getBoundingClientRect().height) } })
}
function applyHost(ctx) {
  context = { ...context, ...ctx }
  if (["light", "dark"].includes(context.theme)) document.documentElement.dataset.theme = context.theme
  size()
}
function gate() {
  $("#ask-agent").classList.toggle("hidden", !hostCaps.message)
  $("#open-report").classList.toggle("hidden", !hostCaps.experimental?.["qoder/files"])
  $("#dl-docx").classList.toggle("hidden", !hostCaps.downloadFile)
  $("#dl-report").classList.toggle("hidden", !hostCaps.downloadFile)
}
function controls() {
  const off = closed || !ready || busy
  for (const id of ["diagnose", "refresh", "rewrite", "save-edits", "lint", "finish", "copy-prompt", "ask-agent", "dl-docx", "dl-report", "open-report"])
    $("#" + id).disabled = off
}
const show = (id, on) => $("#" + id).classList.toggle("hidden", !on)
const pct = x => (x * 100).toFixed(1) + "%"
function bandBadge(b) { return b === "高" ? "bad" : b === "中" ? "warn" : "ok" }

// ---- markdown renderer (escape first) ----
const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
const inline = s => esc(s).replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>")
function renderMarkdown(md) {
  const lines = String(md).split("\n")
  let html = "", i = 0, para = []
  const flush = () => { if (para.length) { html += `<p>${inline(para.join(" "))}</p>`; para = [] } }
  while (i < lines.length) {
    const line = lines[i]
    if (/^\s*\|/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|?\s*$/.test(lines[i + 1])) {
      flush()
      const cells = r => r.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim())
      const head = cells(line)
      let body = []
      i += 2
      while (i < lines.length && /^\s*\|/.test(lines[i])) { body.push(cells(lines[i])); i++ }
      html += "<table><thead><tr>" + head.map(c => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>"
      html += body.map(r => "<tr>" + r.map(c => `<td>${inline(c)}</td>`).join("") + "</tr>").join("")
      html += "</tbody></table>"
      continue
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/)
    if (h) { flush(); html += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`; i++; continue }
    if (/^\s*[-*]\s+/.test(line)) {
      flush(); let items = []
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*]\s+/, "")); i++ }
      html += "<ul>" + items.map(x => `<li>${inline(x)}</li>`).join("") + "</ul>"; continue
    }
    if (/^>\s?/.test(line)) { flush(); html += `<blockquote>${inline(line.replace(/^>\s?/, ""))}</blockquote>`; i++; continue }
    if (!line.trim()) { flush(); i++; continue }
    para.push(line.trim()); i++
  }
  flush()
  return html
}

// ---- renderers ----
function renderRecent(docs) {
  const box = $("#recent-list"); box.textContent = ""
  if (!docs?.length) { show("recent", false); return }
  show("recent", true)
  for (const d of docs) {
    const b = document.createElement("button")
    b.className = "chip"; b.style.cursor = "pointer"
    b.textContent = d.doc_path.split(/[\\/]/).pop() + `（${d.rounds} 轮）`
    b.title = d.doc_path
    b.addEventListener("click", () => { $("#path").value = d.doc_path; void loadDoc(d.doc_path) })
    box.appendChild(b)
  }
}
function renderOverview(diag) {
  $("#doc-name").textContent = diag.doc.name
  const cards = [
    ["篇内重复率", pct(diag.repeat.rate), `${diag.repeat.covered_chars}/${diag.repeat.total_chars} 字`],
    ["风险 高/中/低", `${diag.risk.high}/${diag.risk.mid}/${diag.risk.low}`, `总分 ${diag.risk.total_score}`],
    ["段落 总/可改写", `${diag.doc.paragraphs}/${diag.doc.rewritable}`, `锁定 ${diag.doc.locked}`],
  ]
  $("#cards").textContent = ""
  for (const [k, v, s] of cards) {
    const c = document.createElement("div"); c.className = "card"
    c.innerHTML = `<div class="k">${esc(k)}</div><div class="v">${esc(v)} <small>${esc(s)}</small></div>`
    $("#cards").appendChild(c)
  }
  $("#dims").textContent = ""
  for (const d of diag.risk.dims) {
    const chip = document.createElement("span"); chip.className = "chip"
    chip.innerHTML = `${esc(d.name)} <b>${d.hits}</b>`
    chip.title = `${d.paras} 段命中`
    $("#dims").appendChild(chip)
  }
  const tb = $("#top-table tbody"); tb.textContent = ""
  for (const r of diag.risk.top) {
    const tr = document.createElement("tr")
    tr.innerHTML = `<td class="mono">${esc(r.pid)}</td><td><span class="badge ${bandBadge(r.band)}">${esc(r.band)}</span></td>` +
      `<td>${r.score}</td><td>${pct(r.repeat_ratio)}</td><td class="mono">${esc(r.ops.join(","))}</td><td class="wrap">${esc(r.text)}</td>`
    tb.appendChild(tr)
  }
  show("overview", true)
}
function renderVersions(st) {
  const box = $("#versions"); box.textContent = ""
  const mk = (label, round, current) => {
    const b = document.createElement("button")
    b.className = "secondary"; b.textContent = label
    b.setAttribute("aria-current", String(current))
    b.addEventListener("click", () => void loadView(round))
    box.appendChild(b)
  }
  mk("原文", 0, viewRound === 0)
  for (const r of st.rounds) mk(`v${r.n} ${r.status === "accepted" ? "✓" : r.status === "rejected" ? "✗" : "…"}`, r.n, viewRound === r.n)
  show("result", st.rounds.length > 0)
}
function renderTask(p) {
  $("#round-badge").textContent = `第 ${p.round} 轮`
  $("#task-meta").textContent = `${p.prompts.length} 段任务 · 文风与约束见任务说明`
  const tb = $("#prompt-table tbody"); tb.textContent = ""
  for (const t of p.prompts) {
    const tr = document.createElement("tr")
    tr.innerHTML = `<td class="mono">${esc(t.pid)}</td><td class="mono">${esc(t.ops.join(","))}</td><td>${t.risk_score}</td>` +
      `<td>${pct(t.repeat_ratio)}</td><td class="wrap">${esc((t.requirements || []).join("；") || "按文风规则改写")}</td>`
    tb.appendChild(tr)
  }
  show("task", true)
}
function renderLint(j) {
  const box = $("#lint-result"); box.textContent = ""
  if (!j) return
  const head = document.createElement("p")
  head.innerHTML = `<span class="badge ${j.rejected_count ? "warn" : "ok"}">通过 ${j.passed_count} · 被拒 ${j.rejected_count}</span>`
  box.appendChild(head)
  for (const r of j.rejected || []) {
    const d = document.createElement("div"); d.className = "note"
    d.textContent = `${r.pid}：` + r.gates.map(g => `${g.gate}：${g.detail}`).join("；")
    box.appendChild(d)
  }
}
function renderView(v) {
  lastView = v
  const c = v.comparison
  const vb = $("#verdict"); vb.textContent = ""
  const badge = document.createElement("span")
  badge.className = "badge " + (c.verdict === "accepted" ? "ok" : "bad")
  badge.textContent = c.verdict === "accepted" ? `第 ${c.round} 轮 · 通过` : `第 ${c.round} 轮 · 拒绝（已回退）`
  vb.appendChild(badge)
  if (c.reasons?.length) {
    const n = document.createElement("div"); n.className = "note"; n.textContent = c.reasons.join("；")
    vb.appendChild(n)
  }
  const mb = c.metrics_before, ma = c.metrics_after
  const mt = $("#metrics tbody"); mt.textContent = ""
  const rows = [["篇内重复率", pct(mb.repeat_rate), ma ? pct(ma.repeat_rate) : "—"],
    ["痕迹风险总分", mb.risk_total, ma ? ma.risk_total : "—"],
    ["高/中/低风险段", `${mb.high}/${mb.mid}/${mb.low}`, ma ? `${ma.high}/${ma.mid}/${ma.low}` : "—"]]
  for (const r of rows) {
    const tr = document.createElement("tr")
    tr.innerHTML = r.map((x, i) => `<td${i === 0 ? "" : ' class="mono"'}>${esc(x)}</td>`).join("")
    mt.appendChild(tr)
  }
  const ct = $("#compare tbody"); ct.textContent = ""
  for (const r of c.rows) {
    const tr = document.createElement("tr")
    tr.innerHTML = `<td class="mono">${esc(r.pid)}</td><td class="wrap">${esc(r.old)}</td><td class="wrap">${esc(r.new)}</td>` +
      `<td class="mono">${r.old_len}→${r.new_len}</td><td class="mono">${r.old_sents}→${r.new_sents}</td>`
    ct.appendChild(tr)
  }
  const rj = $("#rejected"); rj.textContent = ""
  if (c.rejected?.length) {
    const h = document.createElement("h3"); h.textContent = "被拒段落与门禁"; rj.appendChild(h)
    for (const r of c.rejected) {
      const d = document.createElement("div"); d.className = "note"
      d.textContent = `${r.pid}：` + r.gates.map(g => `${g.gate}：${g.detail}`).join("；")
      rj.appendChild(d)
    }
  }
  $("#report").innerHTML = renderMarkdown(v.report)
  show("result", true)
  size()
}

// ---- flows ----
async function loadDoc(path) {
  currentPath = path
  busy = true; controls(); report("读取诊断与状态…")
  try {
    const [diag, st] = await Promise.all([tool("pd_get_diagnosis", { path }), tool("paper_dual_status", { path })])
    renderOverview(diag)
    renderVersions(st)
    if (!st.source_unchanged) report("警告：原文在诊断后被修改，续改已被阻止，请重新诊断。", true)
    const pendingRound = st.rounds.length && st.rounds[st.rounds.length - 1].status === "awaiting_edits"
    if (pendingRound) {
      const p = await tool("pd_get_prompts", { path })
      renderTask(p)
    } else show("task", false)
    if (st.rounds.length) await loadView(st.rounds[st.rounds.length - 1].n, st)
    else show("result", false)
    if (st.source_unchanged) report("就绪。")
  } catch (e) { report(e.message, true) }
  finally { busy = false; controls(); size() }
}
async function loadView(round, st) {
  viewRound = round
  if (round === 0) {
    const diag = await tool("pd_get_diagnosis", { path: currentPath })
    renderVersions(st || await tool("paper_dual_status", { path: currentPath }))
    $("#verdict").innerHTML = '<span class="badge ok">原文（只读快照）</span>'
    $("#metrics tbody").textContent = ""
    $("#compare tbody").textContent = ""
    $("#rejected").textContent = ""
    $("#report").innerHTML = renderMarkdown(`# 原文诊断\n\n篇内重复率 ${pct(diag.repeat.rate)}，痕迹风险总分 ${diag.risk.total_score}，高风险段 ${diag.risk.high}。\n\n原文尚未改写。生成任务并回填后，这里显示三栏对照与报告。`)
    show("result", true); size(); return
  }
  const v = await tool("pd_get_view", { path: currentPath, round })
  renderView(v)
  renderVersions(st || await tool("paper_dual_status", { path: currentPath }))
}
async function act(fn, msg) {
  if (!ready || busy || closed) return
  busy = true; controls(); report(msg || "处理中…")
  try { await fn(); }
  catch (e) { report(e.message, true) }
  finally { busy = false; controls(); size() }
}

// ---- events ----
$("#diagnose").addEventListener("click", () => {
  const path = $("#path").value.trim()
  if (!path) { report("请先填写论文 .docx 绝对路径。", true); return }
  void act(async () => {
    const j = await tool("paper_dual_diagnose", { path })
    report(`诊断完成：重复率 ${pct(j.repeat.rate)}，高风险段 ${j.risk.high}。`)
    await loadDoc(path)
  }, "诊断中（只读原件）…")
})
$("#refresh").addEventListener("click", () => {
  const path = $("#path").value.trim()
  void act(async () => {
    if (path) await loadDoc(path)
    else { const j = await tool("paper_dual_status", {}); renderRecent(j.docs); report("已刷新最近文档。") }
  })
})
$("#mode").addEventListener("click", e => {
  const b = e.target.closest("button[data-mode]"); if (!b) return
  mode = b.dataset.mode
  for (const x of $("#mode").children) x.setAttribute("aria-pressed", String(x === b))
})
$("#rewrite").addEventListener("click", () => {
  if (!currentPath) { report("请先诊断一篇论文。", true); return }
  void act(async () => {
    const j = await tool("paper_dual_rewrite", { path: currentPath, mode, style: $("#style").value })
    const note = $("#rewrite-note")
    note.classList.toggle("hidden", !j.note); note.textContent = j.note || ""
    report(`第 ${j.round} 轮任务已生成：${j.task_count} 段。`)
    const p = await tool("pd_get_prompts", { path: currentPath })
    renderTask(p)
    renderVersions(await tool("paper_dual_status", { path: currentPath }))
  }, "生成改写任务…")
})
$("#copy-prompt").addEventListener("click", async () => {
  try {
    const p = await tool("pd_get_prompts", { path: currentPath })
    const text = p.header + "\n" + p.prompts.map(x => JSON.stringify(x)).join("\n")
    await navigator.clipboard.writeText(text)
    report("已复制整段指令到剪贴板。")
  } catch { report("复制失败，请手动从任务清单复制。", true) }
})
$("#ask-agent").addEventListener("click", () => {
  if (!currentPath) return
  const text = `请按论文双降 Skill 完成本轮改写：读取 ${currentPath} 当前轮的 prompts.jsonl 与 prompt_header.md，逐段改写并把 {"pid","text","op"} 写入状态目录的 edits.jsonl，然后 lint 预检、修正被拒段、finish 回填复检，最后汇报结果。`
  request("ui/message", { role: "user", content: [{ type: "text", text }] })
    .then(() => report("已把改写请求发送到当前会话，请查看对话。"))
    .catch(e => report(e.message, true))
})
$("#save-edits").addEventListener("click", () => {
  const jsonl = $("#edits").value.trim()
  if (!jsonl) { report("清单为空。", true); return }
  void act(async () => {
    const j = await tool("pd_save_edits", { path: currentPath, jsonl })
    report(`已保存 ${j.count} 条，可预检。`)
  }, "校验并保存清单…")
})
$("#lint").addEventListener("click", () => {
  void act(async () => { renderLint(await tool("paper_dual_lint", { path: currentPath })); report("预检完成。") }, "预检中…")
})
$("#finish").addEventListener("click", () => {
  void act(async () => {
    const j = await tool("paper_dual_apply_edits", { path: currentPath })
    report(j.verdict === "accepted" ? `第 ${j.round} 轮回填通过。` : `第 ${j.round} 轮被拒并已回退：${(j.reasons || []).join("；")}`, j.verdict !== "accepted")
    await loadDoc(currentPath)
  }, "回填并复检…")
})
async function download(kind) {
  const v = await tool("pd_get_file", { path: currentPath, round: viewRound || undefined, kind })
  const resource = v.base64
    ? { uri: `file:///${v.filename}`, mimeType: v.mimeType, blob: v.base64 }
    : { uri: `file:///${v.filename}`, mimeType: v.mimeType, text: v.text }
  const r = await request("ui/download-file", { contents: [{ type: "resource", resource }] })
  if (r?.isError) report("已取消或保存被拒绝。", true)
  else report("已保存（请在宿主对话框选择的位置查看）。")
}
$("#dl-docx").addEventListener("click", () => void act(() => download("docx"), "准备改稿…"))
$("#dl-report").addEventListener("click", () => void act(() => download("report"), "准备报告…"))
$("#open-report").addEventListener("click", () => {
  if (!lastView?.reportPath) { report("报告路径未知，请先选择一个版本。", true); return }
  request("qoder/files/open", { path: lastView.reportPath })
    .then(() => report("已请求在编辑器打开报告。"))
    .catch(e => report(e.message, true))
})

// ---- host protocol ----
function receive(event) {
  if (event.source !== window.parent || closed) return
  const m = event.data
  if (!m || m.jsonrpc !== "2.0" || typeof m !== "object") return
  if (!m.method && pending.has(m.id)) {
    const t = pending.get(m.id)
    if (!("error" in m) && !("result" in m)) return
    pending.delete(m.id); clearTimeout(t.timer)
    if (m.error) t.reject(new Error("宿主未完成请求，请刷新状态确认。"))
    else t.resolve(m.result)
    return
  }
  if (m.method === "ui/notifications/tool-result") {
    const sc = m.params?.structuredContent
    if (sc?.doc_path) { $("#path").value = sc.doc_path; void loadDoc(sc.doc_path) }
    else if (sc?.docs) renderRecent(sc.docs)
  } else if (m.method === "ui/notifications/host-context-changed") applyHost(m.params ?? {})
  else if (m.method === "ui/notifications/tool-cancelled") report("读取已取消，可刷新状态。")
  else if (m.id !== undefined) {
    if (m.method === "ping") send({ id: m.id, result: {} })
    else if (m.method === "ui/resource-teardown") { send({ id: m.id, result: {} }); dispose() }
    else send({ id: m.id, error: { code: -32601, message: "Method not supported" } })
  }
}
function dispose() {
  if (closed) return
  closed = true
  for (const t of pending.values()) { clearTimeout(t.timer); t.reject(new Error("应用已关闭。")) }
  pending.clear()
  window.removeEventListener("message", receive)
  window.removeEventListener("resize", size)
  window.removeEventListener("pagehide", dispose)
  controls(); report("应用已关闭。")
}
window.addEventListener("message", receive)
window.addEventListener("resize", size)
window.addEventListener("pagehide", dispose)
if (window.parent === window) {
  report("请在 Qoder 中打开本插件；直接打开 HTML 不会连接宿主。")
} else {
  request("ui/initialize", {
    protocolVersion: "2026-01-26", appInfo: { name: "paper-dual-qoder", version: "0.1.0" },
    appCapabilities: { availableDisplayModes: ["inline", "fullscreen"] },
  }).then(result => {
    if (closed) return
    if (result?.protocolVersion !== "2026-01-26") throw new Error("宿主协议版本不匹配，请更新客户端。")
    hostCaps = result.hostCapabilities ?? {}
    ready = Boolean(hostCaps.serverTools)
    applyHost(result.hostContext ?? {})
    gate()
    send({ method: "ui/notifications/initialized", params: {} })
    controls()
    report(ready ? "已连接。填写路径并诊断，或选择最近文档。" : "此宿主不提供工具调用，页面仅可查看。")
    if (ready) tool("paper_dual_status", {}).then(j => renderRecent(j.docs)).catch(() => {})
  }).catch(e => { if (!closed) report(e.message, true) })
}