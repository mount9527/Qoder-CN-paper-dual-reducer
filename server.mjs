import { spawn } from "node:child_process"
import { readFile, writeFile, mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join, isAbsolute, basename } from "node:path"
import { fileURLToPath } from "node:url"

const ROOT = fileURLToPath(new URL(".", import.meta.url))
const ENGINE = join(ROOT, "skills", "paper-dual", "scripts", "engine.py")
const resourceUri = "ui://paper-dual/main.html"

const pageScript = (await readFile(new URL("./app.js", import.meta.url), "utf8")).replaceAll("</script", "<\\/script")
const html = (await readFile(new URL("./app.html", import.meta.url), "utf8")).replace("/* APP_SCRIPT */", () => pageScript)

// ---- Python engine bridge -------------------------------------------------

let pythonCmd = null
function runRaw(cmd, args, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { cwd: ROOT, windowsHide: true })
    let out = "", err = "", done = false
    const timer = setTimeout(() => { if (!done) { done = true; child.kill(); reject(new Error("引擎执行超时")) } }, timeoutMs)
    child.stdout.on("data", d => { out += d })
    child.stderr.on("data", d => { err += d })
    child.on("error", e => { if (!done) { done = true; clearTimeout(timer); reject(e) } })
    child.on("close", code => {
      if (done) return
      done = true; clearTimeout(timer)
      resolve({ code, stdout: out, stderr: err })
    })
  })
}
async function findPython() {
  if (pythonCmd) return pythonCmd
  const candidates = process.env.PAPER_DUAL_PYTHON
    ? [[process.env.PAPER_DUAL_PYTHON, []]]
    : [["python", []], ["py", ["-3"]], ["python3", []]]
  for (const [cmd, pre] of candidates) {
    try {
      const r = await runRaw(cmd, [...pre, "-c", "import sys;print(sys.version_info>=(3,10))"], 15000)
      if (r.stdout.trim() === "True") { pythonCmd = [cmd, ...pre]; return pythonCmd }
    } catch { /* try next */ }
  }
  throw new Error("未找到 Python 3.10+。请安装 Python，或设置环境变量 PAPER_DUAL_PYTHON 指向解释器。")
}
async function engine(args, timeoutMs = 180000) {
  const [cmd, ...pre] = await findPython()
  const r = await runRaw(cmd, [...pre, ENGINE, ...args], timeoutMs)
  const lines = r.stdout.trim().split("\n").filter(Boolean)
  const last = lines[lines.length - 1]
  if (!last) throw new Error("引擎无输出：" + (r.stderr.slice(-500) || `exit ${r.code}`))
  try { return JSON.parse(last) }
  catch { throw new Error("引擎输出非 JSON：" + last.slice(0, 300)) }
}
const stateDirCache = new Map()
async function stateDir(path) {
  if (stateDirCache.has(path)) return stateDirCache.get(path)
  const st = await engine(["status", "--path", path])
  if (!st.ok) throw new Error(st.error || "无法读取状态")
  stateDirCache.set(path, st.state_dir)
  return st.state_dir
}
function validPath(path) {
  if (typeof path !== "string" || !path.trim()) throw new Error("需要论文 .docx 的绝对路径")
  if (!isAbsolute(path)) throw new Error("路径必须是绝对路径")
  return path
}
async function tempEdits(edits) {
  if (!Array.isArray(edits) || edits.length === 0) throw new Error("edits 必须是非空数组")
  const lines = edits.map(e => {
    if (!e || typeof e.pid !== "string" || typeof e.text !== "string") throw new Error("每条 edit 需含 pid 与 text 字符串")
    return JSON.stringify({ pid: e.pid, text: e.text, op: typeof e.op === "string" ? e.op : "O1" })
  })
  const dir = await mkdtemp(join(tmpdir(), "paper-dual-"))
  const file = join(dir, "edits.jsonl")
  await writeFile(file, lines.join("\n") + "\n", "utf8")
  return { dir, file }
}
const ok = (structured, text) => ({ content: [{ type: "text", text }], structuredContent: structured })
const fail = msg => ({ isError: true, content: [{ type: "text", text: msg }] })
const brief = j => j.ok
  ? (j.verdict ? `第 ${j.round} 轮 ${j.verdict}；通过 ${j.passed_count ?? 0} 段。` : (j.error ? j.error : "完成。"))
  : (j.error || "操作失败")

// ---- tools ----------------------------------------------------------------

const emptySchema = { type: "object", properties: {}, additionalProperties: false }
const pathProp = { path: { type: "string", description: "论文 .docx 绝对路径" } }
const tools = [
  { name: "open_workbench", title: "论文双降工作台", description: "打开论文双降工作台页面并读取当前状态（可传 path 定位文档）。",
    inputSchema: { type: "object", properties: { ...pathProp }, additionalProperties: false },
    annotations: { readOnlyHint: true },
    _meta: { ui: { resourceUri }, "qoder/ui": { entrypoints: [{ type: "global" }, { type: "thread" }] } } },
  { name: "paper_dual_status", title: "查询双降状态", description: "查询某篇论文的诊断/轮次/基础稿状态；不传 path 则列出最近 12 篇文档。",
    inputSchema: { type: "object", properties: { ...pathProp }, additionalProperties: false },
    annotations: { readOnlyHint: true } },
  { name: "paper_dual_diagnose", title: "诊断论文", description: "只读诊断一篇 .docx：9 维痕迹 + 篇内重复 + 锁定区统计。原文被快照，不改原件。",
    inputSchema: { type: "object", properties: { ...pathProp }, required: ["path"], additionalProperties: false },
    annotations: { readOnlyHint: false, destructiveHint: false } },
  { name: "paper_dual_rewrite", title: "生成改写任务", description: "为已诊断论文生成下一轮改写任务 prompts.jsonl。mode=dual|aigc|dedup，style=plain|academic。",
    inputSchema: { type: "object", properties: { ...pathProp, mode: { type: "string", enum: ["dual", "aigc", "dedup"] }, style: { type: "string", enum: ["plain", "academic"] } }, required: ["path"], additionalProperties: false },
    annotations: { readOnlyHint: false, destructiveHint: false } },
  { name: "paper_dual_lint", title: "预检改写清单", description: "对 edits.jsonl 逐段跑门禁。可传 edits 数组（{pid,text,op}）直接预检，否则用状态目录内清单。",
    inputSchema: { type: "object", properties: { ...pathProp, edits: { type: "array", items: { type: "object" } } }, required: ["path"], additionalProperties: false },
    annotations: { readOnlyHint: false, destructiveHint: false } },
  { name: "paper_dual_apply_edits", title: "回填并复检", description: "写入清单→门禁→保形回填→篇级复检；恶化整篇回退。可传 edits 数组，否则用状态目录内清单。",
    inputSchema: { type: "object", properties: { ...pathProp, edits: { type: "array", items: { type: "object" } } }, required: ["path"], additionalProperties: false },
    annotations: { readOnlyHint: false, destructiveHint: false } },
  { name: "pd_save_edits", title: "保存清单（页面）", description: "页面粘贴的 JSONL 文本校验后保存到状态目录。",
    inputSchema: { type: "object", properties: { ...pathProp, jsonl: { type: "string" } }, required: ["path", "jsonl"], additionalProperties: false },
    annotations: { readOnlyHint: false, destructiveHint: false }, _meta: { ui: { visibility: ["app"] } } },
  { name: "pd_get_diagnosis", title: "读取诊断（页面）", description: "读取诊断详情 JSON。",
    inputSchema: { type: "object", properties: { ...pathProp }, required: ["path"], additionalProperties: false },
    annotations: { readOnlyHint: true }, _meta: { ui: { visibility: ["app"] } } },
  { name: "pd_get_prompts", title: "读取任务（页面）", description: "读取当前轮 prompts.jsonl 与任务说明。",
    inputSchema: { type: "object", properties: { ...pathProp }, required: ["path"], additionalProperties: false },
    annotations: { readOnlyHint: true }, _meta: { ui: { visibility: ["app"] } } },
  { name: "pd_get_view", title: "读取对照与报告（页面）", description: "读取某轮三栏对照 comparison.json 与报告 report.md。",
    inputSchema: { type: "object", properties: { ...pathProp, round: { type: "integer", minimum: 1 } }, required: ["path"], additionalProperties: false },
    annotations: { readOnlyHint: true }, _meta: { ui: { visibility: ["app"] } } },
  { name: "pd_get_file", title: "读取文件用于下载（页面）", description: "返回某轮改稿 docx 或报告 md 的 base64，供页面下载。",
    inputSchema: { type: "object", properties: { ...pathProp, round: { type: "integer", minimum: 1 }, kind: { type: "string", enum: ["docx", "report"] } }, required: ["path", "kind"], additionalProperties: false },
    annotations: { readOnlyHint: true }, _meta: { ui: { visibility: ["app"] } } },
]

async function callTool(name, args) {
  switch (name) {
    case "open_workbench":
    case "paper_dual_status": {
      const j = args.path ? await engine(["status", "--path", validPath(args.path)]) : await engine(["status"])
      return j.ok ? ok(j, brief(j)) : fail(j.error || "查询失败")
    }
    case "paper_dual_diagnose": {
      const j = await engine(["diagnose", "--path", validPath(args.path)])
      return j.ok ? ok(j, `诊断完成：篇内重复率 ${(j.repeat.rate * 100).toFixed(1)}%，高风险段 ${j.risk.high}。`) : fail(j.error || "诊断失败")
    }
    case "paper_dual_rewrite": {
      const a = ["rewrite", "--path", validPath(args.path)]
      if (args.mode) a.push("--mode", args.mode)
      if (args.style) a.push("--style", args.style)
      const j = await engine(a)
      return j.ok ? ok(j, `第 ${j.round} 轮任务已生成：${j.task_count} 段。`) : fail(j.error || "生成任务失败")
    }
    case "paper_dual_lint": {
      const path = validPath(args.path)
      let tmp = null
      try {
        const a = ["lint", "--path", path]
        if (args.edits) { tmp = await tempEdits(args.edits); a.push("--edits", tmp.file) }
        const j = await engine(a)
        return ok(j, `预检：通过 ${j.passed_count ?? 0}，被拒 ${j.rejected_count ?? 0}。`)
      } finally { if (tmp) await rm(tmp.dir, { recursive: true, force: true }) }
    }
    case "paper_dual_apply_edits": {
      const path = validPath(args.path)
      let tmp = null
      try {
        if (args.edits) {
          tmp = await tempEdits(args.edits)
          const sv = await engine(["save-edits", "--path", path, "--file", tmp.file])
          if (!sv.ok) return fail(sv.error || "清单保存失败")
        }
        const j = await engine(["finish", "--path", path])
        return ok(j, brief(j))
      } finally { if (tmp) await rm(tmp.dir, { recursive: true, force: true }) }
    }
    case "pd_save_edits": {
      const path = validPath(args.path)
      if (typeof args.jsonl !== "string" || !args.jsonl.trim()) return fail("清单文本为空")
      const dir = await mkdtemp(join(tmpdir(), "paper-dual-"))
      const file = join(dir, "edits.jsonl")
      try {
        await writeFile(file, args.jsonl, "utf8")
        const j = await engine(["save-edits", "--path", path, "--file", file])
        return j.ok ? ok(j, `已保存 ${j.count} 条。`) : fail(j.error || "保存失败")
      } finally { await rm(dir, { recursive: true, force: true }) }
    }
    case "pd_get_diagnosis": {
      const sd = await stateDir(validPath(args.path))
      const j = JSON.parse(await readFile(join(sd, "diagnosis.json"), "utf8"))
      return ok(j, "诊断详情")
    }
    case "pd_get_prompts": {
      const path = validPath(args.path)
      const st = await engine(["status", "--path", path])
      if (!st.ok) return fail(st.error)
      const sd = st.state_dir
      const n = st.rounds.length
      if (!n) return fail("尚无任何轮次")
      const rd = join(sd, "rounds", `r${n}`)
      const prompts = (await readFile(join(rd, "prompts.jsonl"), "utf8")).trim().split("\n").filter(Boolean).map(JSON.parse)
      const header = await readFile(join(rd, "prompt_header.md"), "utf8")
      return ok({ round: n, prompts, header }, `第 ${n} 轮任务`)
    }
    case "pd_get_view": {
      const path = validPath(args.path)
      const st = await engine(["status", "--path", path])
      if (!st.ok) return fail(st.error)
      const n = args.round || st.rounds.length
      if (!n) return fail("尚无任何轮次")
      const rd = join(st.state_dir, "rounds", `r${n}`)
      const comparison = JSON.parse(await readFile(join(rd, "comparison.json"), "utf8"))
      const report = await readFile(join(rd, "report.md"), "utf8")
      return ok({ round: n, comparison, report, reportPath: join(rd, "report.md") }, `第 ${n} 轮对照与报告`)
    }
    case "pd_get_file": {
      const path = validPath(args.path)
      const st = await engine(["status", "--path", path])
      if (!st.ok) return fail(st.error)
      const n = args.round || st.rounds.length
      if (!n) return fail("尚无任何轮次")
      const rd = join(st.state_dir, "rounds", `r${n}`)
      if (args.kind === "report") {
        const text = await readFile(join(rd, "report.md"), "utf8")
        return ok({ round: n, filename: `report-r${n}.md`, mimeType: "text/markdown", text }, "报告")
      }
      const r = st.rounds[n - 1]
      if (!r || !r.docx) return fail(`第 ${n} 轮没有已验收改稿（可能被判拒绝）`)
      const buf = await readFile(r.docx)
      if (buf.length > 12 * 1024 * 1024) return fail("改稿超过 12 MiB，超出页面下载预算")
      return ok({ round: n, filename: basename(r.docx), mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", base64: buf.toString("base64") }, "改稿")
    }
    default:
      return fail("未知工具：" + name)
  }
}

// ---- MCP stdio protocol ---------------------------------------------------

const reply = message => process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", ...message })}\n`)
async function handle(request) {
  if (!request || request.jsonrpc !== "2.0" || typeof request.method !== "string") {
    return reply({ id: null, error: { code: -32600, message: "Invalid request" } })
  }
  if (request.id === undefined) return
  const { id, method, params = {} } = request
  const failRpc = (code, message) => reply({ id, error: { code, message } })
  if (!params || typeof params !== "object" || Array.isArray(params)) return failRpc(-32602, "Parameters must be an object")
  try {
    switch (method) {
      case "initialize":
        return reply({ id, result: {
          protocolVersion: ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"].includes(params.protocolVersion) ? params.protocolVersion : "2025-11-25",
          capabilities: { tools: {}, resources: {} },
          serverInfo: { name: "paper-dual-qoder", version: "0.1.0" },
        } })
      case "ping": return reply({ id, result: {} })
      case "tools/list": return reply({ id, result: { tools } })
      case "resources/list": return reply({ id, result: { resources: [{ uri: resourceUri, name: "论文双降工作台", mimeType: "text/html;profile=mcp-app" }] } })
      case "resources/read":
        if (params.uri !== resourceUri) return failRpc(-32602, "Resource not found")
        return reply({ id, result: { contents: [{ uri: resourceUri, mimeType: "text/html;profile=mcp-app", text: html,
          _meta: { "qoder/ui": { availableDisplayModes: ["inline", "fullscreen"], preferredDisplayMode: "fullscreen" } } }] } })
      case "tools/call": {
        const args = params.arguments ?? {}
        if (!args || typeof args !== "object" || Array.isArray(args)) return failRpc(-32602, "Tool arguments must be an object")
        const tool = tools.find(t => t.name === params.name)
        if (!tool) return reply({ id, result: fail("未知工具") })
        const result = await callTool(params.name, args)
        return reply({ id, result })
      }
      default: return failRpc(-32601, "Method not supported")
    }
  } catch (e) {
    return reply({ id, result: fail(e.message || String(e)) })
  }
}

const maxLineBytes = 8 * 1024 * 1024
let buffer = Buffer.alloc(0)
process.stdin.on("data", chunk => {
  buffer = Buffer.concat([buffer, chunk])
  let end
  while ((end = buffer.indexOf(10)) !== -1) {
    const line = buffer.subarray(0, end)
    buffer = buffer.subarray(end + 1)
    if (line.length > maxLineBytes) { process.stderr.write("Request exceeds budget\n"); process.exit(1) }
    if (!line.toString("utf8").trim()) continue
    try { void handle(JSON.parse(line.toString("utf8"))) }
    catch { reply({ id: null, error: { code: -32700, message: "Invalid JSON" } }) }
  }
  if (buffer.length > maxLineBytes) { process.stderr.write("Request exceeds budget\n"); process.exit(1) }
})