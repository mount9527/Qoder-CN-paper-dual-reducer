import { spawn } from "node:child_process"
import { fileURLToPath } from "node:url"
import { join } from "node:path"

const ROOT = fileURLToPath(new URL("..", import.meta.url))
const SERVER = join(ROOT, "server.mjs")
let pass = 0
function check(name, cond, detail = "") {
  if (!cond) { console.error(`FAIL: ${name} ${detail}`); process.exit(1) }
  pass++; console.log(`ok ${pass}: ${name}`)
}

const child = spawn(process.execPath, [SERVER], { cwd: ROOT, windowsHide: true })
let buf = ""
const waiters = new Map()
child.stdout.on("data", d => {
  buf += d.toString("utf8")
  let i
  while ((i = buf.indexOf("\n")) !== -1) {
    const line = buf.slice(0, i); buf = buf.slice(i + 1)
    if (!line.trim()) continue
    const msg = JSON.parse(line)
    const w = waiters.get(msg.id)
    if (w) { waiters.delete(msg.id); clearTimeout(w.timer); w.resolve(msg) }
  }
})
let stderr = ""
child.stderr.on("data", d => { stderr += d })
let seq = 0
function rpc(method, params) {
  const id = ++seq
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { waiters.delete(id); reject(new Error(`timeout ${method}`)) }, 60000)
    waiters.set(id, { resolve, reject, timer })
    child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n")
  })
}

try {
  const init = await rpc("initialize", { protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "smoke", version: "0" } })
  check("initialize 协议版本", init.result.protocolVersion === "2025-11-25")
  check("serverInfo 名称", init.result.serverInfo.name === "paper-dual-qoder")

  const tl = await rpc("tools/list", {})
  const tools = tl.result.tools
  check("工具数量 11", tools.length === 11, String(tools.length))
  const open = tools.find(t => t.name === "open_workbench")
  check("open_workbench 声明 ui resourceUri", open?._meta?.ui?.resourceUri === "ui://paper-dual/main.html")
  check("open_workbench 全局+会话入口", JSON.stringify(open._meta["qoder/ui"].entrypoints) === JSON.stringify([{ type: "global" }, { type: "thread" }]))
  check("页面写操作仅 app 可见", tools.filter(t => t._meta?.ui?.visibility?.includes("app")).length === 5)
  check("模型工具不含 app-only", !tools.find(t => t.name === "pd_save_edits")._meta.ui.visibility.includes("model"))

  const rl = await rpc("resources/list", {})
  check("资源列表含 ui 资源", rl.result.resources[0].uri === "ui://paper-dual/main.html")
  const rr = await rpc("resources/read", { uri: "ui://paper-dual/main.html" })
  const content = rr.result.contents[0]
  check("资源 mime 正确", content.mimeType === "text/html;profile=mcp-app")
  check("页面脚本已内联", content.text.includes("ui/initialize") && !content.text.includes("/* APP_SCRIPT */"))
  check("声明全屏优先", content._meta["qoder/ui"].preferredDisplayMode === "fullscreen")

  const st = await rpc("tools/call", { name: "paper_dual_status", arguments: {} })
  check("status 桥接引擎成功", st.result.structuredContent?.ok === true, JSON.stringify(st.result).slice(0, 200))
  check("status 返回 docs 数组", Array.isArray(st.result.structuredContent.docs))

  const bad = await rpc("tools/call", { name: "paper_dual_diagnose", arguments: { path: "/nonexistent-paper-xyz.docx" } })
  check("诊断不存在文件返回 isError", bad.result.isError === true)

  const badArgs = await rpc("tools/call", { name: "paper_dual_diagnose", arguments: { path: "relative.docx" } })
  check("相对路径被拒", badArgs.result.isError === true)

  const ping = await rpc("ping", {})
  check("ping 返回 {}", JSON.stringify(ping.result) === "{}")

  console.log(`\nALL ${pass} SMOKE ASSERTIONS PASSED`)
} catch (e) {
  console.error("SMOKE ERROR:", e.message)
  console.error("server stderr:", stderr.slice(-800))
  process.exitCode = 1
} finally {
  child.kill()
}