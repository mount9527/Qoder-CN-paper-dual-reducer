import { spawn } from "node:child_process"
import { fileURLToPath } from "node:url"
import { join } from "node:path"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"

const ROOT = fileURLToPath(new URL("..", import.meta.url))
const SERVER = join(ROOT, "server.mjs")
const SCRIPTS = join(ROOT, "skills", "paper-dual", "scripts")
let pass = 0
function check(name, cond, detail = "") {
  if (!cond) { console.error(`FAIL: ${name} ${detail}`); process.exit(1) }
  pass++; console.log(`ok ${pass}: ${name}`)
}
function run(cmd, args, env) {
  return new Promise((res, rej) => {
    const c = spawn(cmd, args, { cwd: ROOT, env, windowsHide: true })
    let o = "", e = ""
    c.stdout.on("data", d => o += d); c.stderr.on("data", d => e += d)
    c.on("error", rej); c.on("close", () => res({ o, e }))
  })
}

const tmp = await mkdtemp(join(tmpdir(), "pd-integ-"))
const env = { ...process.env, PAPER_DUAL_STATE_DIR: join(tmp, "state"), PYTHONIOENCODING: "utf-8" }
const paper = join(tmp, "集成测试论文.docx")
const py = process.env.PAPER_DUAL_PYTHON || "python"
const fx = await run(py, ["-c", `import sys;sys.path.insert(0,r'${SCRIPTS}');from pathlib import Path;import test_engine;test_engine.make_fixture(Path(r'${paper}'))`], env)
check("夹具生成", fx.e === "" || fx.o === "", fx.e.slice(0, 200))

const child = spawn(process.execPath, [SERVER], { cwd: ROOT, env, windowsHide: true })
let buf = "", stderr = ""
const waiters = new Map()
child.stdout.on("data", d => {
  buf += d.toString("utf8"); let i
  while ((i = buf.indexOf("\n")) !== -1) {
    const line = buf.slice(0, i); buf = buf.slice(i + 1)
    if (!line.trim()) continue
    const m = JSON.parse(line); const w = waiters.get(m.id)
    if (w) { waiters.delete(m.id); clearTimeout(w.timer); w.resolve(m) }
  }
})
child.stderr.on("data", d => stderr += d)
let seq = 0
const rpc = (method, params) => new Promise((resolve, reject) => {
  const id = ++seq
  const timer = setTimeout(() => { waiters.delete(id); reject(new Error("timeout " + method)) }, 120000)
  waiters.set(id, { resolve, reject, timer })
  child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n")
})
const call = async (name, args) => (await rpc("tools/call", { name, arguments: args })).result

try {
  await rpc("initialize", { protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "integ", version: "0" } })
  const diag = await call("paper_dual_diagnose", { path: paper })
  check("诊断 ok", diag.structuredContent?.ok === true, JSON.stringify(diag).slice(0, 150))
  const detail = await call("pd_get_diagnosis", { path: paper })
  check("pd_get_diagnosis 含锁定统计", detail.structuredContent.doc.locked >= 8)
  const rw = await call("paper_dual_rewrite", { path: paper, mode: "dual", style: "plain" })
  check("rewrite ok", rw.structuredContent?.ok === true)
  const prompts = await call("pd_get_prompts", { path: paper })
  check("pd_get_prompts 含 header 与任务", prompts.structuredContent.header.includes("改写任务说明") && prompts.structuredContent.prompts.length >= 1)
  const tasks = prompts.structuredContent.prompts
  const save = await call("pd_save_edits", { path: paper, jsonl: JSON.stringify({ pid: tasks[0].pid, text: tasks[0].text, op: "O1" }) + "\n" })
  check("pd_save_edits ok", save.structuredContent?.ok === true, JSON.stringify(save).slice(0, 150))
  const edits = tasks.map(t => ({ pid: t.pid, text: t.text + "该方法具有重要意义。", op: t.ops[0] }))
  const lint = await call("paper_dual_lint", { path: paper, edits })
  check("inline edits 恒等改写过预检", lint.structuredContent.passed_count === edits.length, JSON.stringify(lint.structuredContent).slice(0, 200))
  const fin = await call("paper_dual_apply_edits", { path: paper, edits })
  check("恶化候选稿未通过且已回退", fin.structuredContent.verdict === "rejected"
    && fin.structuredContent.check_status === "not_passed" && fin.isError === true, JSON.stringify(fin.structuredContent).slice(0, 200))
  const view = await call("pd_get_view", { path: paper, round: 1 })
  check("pd_get_view 含对照、报告路径与下载分支", view.structuredContent.comparison.round === 1
    && typeof view.structuredContent.reportPath === "string" && view.structuredContent.reportDownload?.text === view.structuredContent.report)
  const rep = await call("pd_get_file", { path: paper, round: 1, kind: "report" })
  check("pd_get_file 报告含文本", rep.structuredContent.text.includes("论文双降报告"))
  const noDocx = await call("pd_get_file", { path: paper, round: 1, kind: "docx" })
  check("被拒轮无改稿可下载", noDocx.isError === true)

  const rw2 = await call("paper_dual_rewrite", { path: paper, mode: "dual", style: "plain" })
  check("重新生成待回填轮", rw2.structuredContent?.ok === true)
  const subset = tasks.slice(0, -1)
  const partialLint = await call("paper_dual_lint", { path: paper, edits: subset })
  check("lint 失败传播 isError 与结构化详情", partialLint.isError === true
    && partialLint.structuredContent?.ok === false
    && partialLint.structuredContent.rejected.some(r => r.gates.some(g => g.gate === "任务覆盖")),
    JSON.stringify(partialLint).slice(0, 200))
  const strict = await call("paper_dual_apply_edits", { path: paper, edits: subset })
  check("MCP 默认拒绝部分完成", strict.structuredContent.verdict === "rejected"
    && strict.structuredContent.check_status === "rejected"
    && strict.structuredContent.reasons.some(r => r.includes("未全部通过")), JSON.stringify(strict.structuredContent).slice(0, 200))
  await call("paper_dual_rewrite", { path: paper, mode: "dual", style: "plain" })
  const explicitPartial = await call("paper_dual_apply_edits", { path: paper, edits: subset, allowPartial: true })
  check("MCP 显式部分完成绕过覆盖闸门", !explicitPartial.structuredContent.reasons.some(r => r.includes("未全部通过"))
    && explicitPartial.structuredContent.check_status === "passed", JSON.stringify(explicitPartial.structuredContent).slice(0, 200))

  console.log(`\nALL ${pass} INTEGRATION ASSERTIONS PASSED`)
} catch (e) {
  console.error("INTEGRATION ERROR:", e.message); console.error("stderr:", stderr.slice(-800)); process.exitCode = 1
} finally {
  child.kill()
  await rm(tmp, { recursive: true, force: true })
}