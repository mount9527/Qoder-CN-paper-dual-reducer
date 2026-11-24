import { spawn } from "node:child_process"
import { createServer } from "node:http"
import { readFile, mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const tmp = await mkdtemp(join(tmpdir(), "pd-ui-review-"))
const env = { ...process.env, PAPER_DUAL_STATE_DIR: join(tmp, "state"), PYTHONIOENCODING: "utf-8" }
const paper = join(tmp, "界面测试论文.docx")
const py = process.env.PAPER_DUAL_PYTHON || "python"
const SCRIPTS = join(ROOT, "skills", "paper-dual", "scripts")
const fixture = spawn(py, ["-c", `import sys;sys.path.insert(0,r'${SCRIPTS}');from pathlib import Path;import test_engine;test_engine.make_fixture(Path(r'${paper}'))`], { env, windowsHide: true })
await new Promise((resolve, reject) => { fixture.on("error", reject); fixture.on("exit", code => code === 0 ? resolve() : reject(new Error("fixture failed"))) })

const child = spawn(process.execPath, [join(ROOT, "server.mjs")], { cwd: ROOT, env, windowsHide: true })
let buf = "", seq = 0, stderr = ""
const waiters = new Map()
child.stdout.on("data", d => {
  buf += d.toString("utf8")
  let i
  while ((i = buf.indexOf("\n")) !== -1) {
    const line = buf.slice(0, i); buf = buf.slice(i + 1)
    if (!line.trim()) continue
    const m = JSON.parse(line)
    const w = waiters.get(m.id)
    if (w) { waiters.delete(m.id); clearTimeout(w.timer); w.resolve(m) }
  }
})
child.stderr.on("data", d => { stderr += d })
const rpc = (method, params) => new Promise((resolve, reject) => {
  const id = ++seq
  const timer = setTimeout(() => { waiters.delete(id); reject(new Error("timeout " + method)) }, 120000)
  waiters.set(id, { resolve, reject, timer })
  child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n")
})
const call = async (name, args) => (await rpc("tools/call", { name, arguments: args })).result
await rpc("initialize", { protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "ui-review", version: "0" } })
child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized", params: {} }) + "\n")

const html = await readFile(join(ROOT, "app.html"), "utf8")
const appJs = await readFile(join(ROOT, "app.js"), "utf8")
const app = html.replace("/* APP_SCRIPT */", () => appJs)
const replacement = {
  "随着人工智能技术的快速发展，深度学习模型在图像识别领域发挥着重要作用。该模型被广泛应用于医疗影像分析。该模型被广泛应用于工业质检场景。综上所述，本文旨在提出一种改进方法，值得注意的是，该方法具有重要意义。": "人工智能技术快速发展之后，深度学习模型在图像识别领域发挥着重要作用。医疗影像分析广泛采用该模型，工业质检场景也大量使用它。总的来看，本文提出一种改进方法，该方法具有实际价值。",
  "深度学习模型在图像识别领域发挥着重要作用。本研究将其用于文本分类。": "这类深度网络在图像识别任务中承担着关键角色。本研究将其用于文本分类。",
  "首先，系统采集数据。其次，系统清洗数据。最后，系统输出结果。一方面，效率得到提升。另一方面，成本得以降低。": "首先，系统采集数据；其次，系统清洗数据；最后，系统输出结果。一方面，效率得到提升；另一方面，成本得以降低。"
}
let prepared = null, autoResult = null
async function goodEdits() {
  if (!prepared) {
    await call("paper_dual_diagnose", { path: paper })
    await call("paper_dual_rewrite", { path: paper, mode: "dual", style: "plain" })
    const p = (await call("pd_get_prompts", { path: paper })).structuredContent
    prepared = p.prompts.map(t => ({ pid: t.pid, text: replacement[t.text] || t.text, op: t.ops[0] }))
  }
  return prepared
}
const page = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>paper-dual UI review</title><style>body{margin:0;background:#e9eef3;font:13px system-ui}header{padding:10px 16px;background:#142746;color:white;display:flex;gap:10px;align-items:center}header b{font-size:15px}header span{opacity:.8}button{padding:6px 10px;border:0;border-radius:6px;background:#d9f3e9;color:#142746;cursor:pointer}iframe{display:block;width:100%;height:calc(100vh - 49px);border:0;background:white}</style></head><body><header><b>论文双降工作台 · 本地真实 MCP 桥接预览</b><span>此截图来自独立浏览器测试宿主，不等同于 Qoder 宿主验收。</span><button id="good">填入测试改稿</button><button id="bad">填入错误 JSONL</button><button id="dark">切换深色</button></header><iframe src="/app"></iframe><script>
const frame=document.querySelector('iframe');let dark=false;
frame.addEventListener('load',()=>{frame.contentWindow.document.querySelector('#path').value=${JSON.stringify(paper)}});
window.addEventListener('message',async e=>{if(e.source!==frame.contentWindow)return;const m=e.data;if(!m||m.jsonrpc!=='2.0')return;let result,error;
if(m.method==='ui/initialize'){result={protocolVersion:'2026-01-26',hostCapabilities:{serverTools:{listChanged:false}},hostContext:{displayMode:'fullscreen',theme:dark?'dark':'light'}}}
else if(m.method==='tools/call'||m.method==='resources/read'){try{result=await bridge(m.method,m.params)}catch(x){error={code:-32000,message:x.message}}}
else if(m.method==='ui/download-file'){error={code:-32001,message:'测试宿主不提供下载'}}
else if(m.id===undefined){return}else{error={code:-32601,message:'Method not supported'}}
frame.contentWindow.postMessage({jsonrpc:'2.0',id:m.id,...(error?{error}:{result})},'*')});
async function bridge(method,params){const r=await fetch('/bridge',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({method,params})});const j=await r.json();if(j.error)throw new Error(j.error.message);return j.result}
async function fillGood(){const r=await fetch('/good-edits');const rows=await r.json();const w=frame.contentWindow.document.querySelector('#edits');w.value=rows.map(x=>JSON.stringify(x)).join('\\n');w.dispatchEvent(new frame.contentWindow.Event('input',{bubbles:true}))};
document.querySelector('#good').onclick=fillGood;
document.querySelector('#bad').onclick=()=>{const w=frame.contentWindow.document.querySelector('#edits');w.value='{这不是合法 JSON}\\n';w.dispatchEvent(new frame.contentWindow.Event('input',{bubbles:true}))};
document.querySelector('#dark').onclick=()=>{dark=!dark;frame.contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/host-context-changed',params:{theme:dark?'dark':'light'}},'*')};
if(new URLSearchParams(location.search).get('autotest')==='1')setTimeout(()=>runUiTest().catch(async e=>{const r={ok:false,error:e.message,status:frame.contentWindow.document.querySelector('#status')?.textContent};await fetch('/autotest-result',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(r)})}),300);
async function runUiTest(){
 const w=frame.contentWindow,d=w.document,q=s=>d.querySelector(s),wait=async(fn,label)=>{for(let i=0;i<300;i++){if(fn())return;await new Promise(r=>setTimeout(r,100))}throw new Error('timeout '+label)};
 const result={ok:true,steps:[]},record=(name,detail)=>{result.steps.push({name,...detail});console.log('UI_TEST',name,JSON.stringify(detail))};
 await wait(()=>q('#status')?.textContent.includes('已连接')&&!q('#diagnose')?.disabled,'initialize');
 record('initialize',{status:q('#status').textContent});
 q('#diagnose').click();await wait(()=>q('#overview')&&!q('#overview').classList.contains('hidden')&&q('#status').textContent==='就绪。','diagnose');
 record('diagnose',{status:q('#status').textContent,cards:[...q('#cards').querySelectorAll('.card')].map(x=>x.innerText)});
 q('#rewrite').click();await wait(()=>q('#task')&&!q('#task').classList.contains('hidden')&&q('#status').textContent.includes('任务已生成')&&!q('#lint').disabled,'rewrite');
 record('rewrite',{status:q('#status').textContent,round:q('#round-badge').textContent});
 document.querySelector('#bad').click();q('#lint').click();await wait(()=>q('#lint-result').textContent.includes('预检拒绝')&&q('#lint-result').textContent.includes('清单错误')&&!q('#lint').disabled,'bad lint');
 record('malformed_jsonl',{status:q('#status').textContent,lint:q('#lint-result').innerText});
 await fillGood();q('#lint').click();await wait(()=>q('#lint-result').textContent.includes('预检通过')&&q('#status').textContent==='预检通过。'&&!q('#finish').disabled,'good lint');
 record('valid_lint',{status:q('#status').textContent,lint:q('#lint-result').innerText});
 q('#finish').click();await wait(()=>q('#versions').textContent.includes('v1 通过')&&q('#status').textContent.includes('回填通过')&&!q('#refresh').disabled,'finish');
 record('accepted_round',{status:q('#status').textContent,versions:q('#versions').innerText,verdict:q('#verdict').innerText});
 document.querySelector('#dark').click();await wait(()=>d.documentElement.dataset.theme==='dark','dark theme');record('dark_theme',{theme:d.documentElement.dataset.theme});
 q('#rewrite').click();await wait(()=>q('#status').textContent.includes('第 2 轮任务已生成')&&!q('#save-edits').disabled,'second rewrite');
 await fillGood();const lines=q('#edits').value.trim().split('\\n');q('#edits').value=lines.slice(0,-1).join('\\n');q('#allow-partial').checked=true;q('#save-edits').click();await wait(()=>q('#status').textContent.includes('已保存')&&!q('#finish').disabled,'partial save');
 q('#finish').click();await wait(()=>q('#versions').textContent.includes('v2 通过（部分完成）')&&q('#status').textContent.includes('部分完成并通过')&&!q('#refresh').disabled,'partial finish');
 record('partial_round',{status:q('#status').textContent,versions:q('#versions').innerText,verdict:q('#verdict').innerText});
 document.querySelector('#dark').click();await wait(()=>d.documentElement.dataset.theme==='light','light theme');
 const response=await fetch('/autotest-result',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(result)});autoResult=await response.json();
}
</script></body></html>`
const server = createServer(async (req, res) => {
  const path = new URL(req.url, "http://localhost").pathname
  try {
    if (path === "/") { res.setHeader("content-type", "text/html; charset=utf-8"); res.end(page); return }
    if (path === "/app") { res.setHeader("content-type", "text/html; charset=utf-8"); res.end(app); return }
    if (path === "/good-edits") { res.setHeader("content-type", "application/json; charset=utf-8"); res.end(JSON.stringify(await goodEdits())); return }
    if (path === "/autotest-result" && req.method === "GET") { res.setHeader("content-type", "application/json; charset=utf-8"); res.end(JSON.stringify(autoResult || { running: true })); return }
    if (path === "/autotest-result" && req.method === "POST") {
      let body = ""
      for await (const d of req) body += d
      autoResult = JSON.parse(body)
      console.log("AUTOTEST_RESULT", JSON.stringify(autoResult))
      res.setHeader("content-type", "application/json; charset=utf-8"); res.end(JSON.stringify({ received: true })); return
    }
    if (path === "/bridge" && req.method === "POST") {
      let body = ""
      for await (const d of req) body += d
      const { method, params } = JSON.parse(body)
      const m = await rpc(method, params)
      console.log("BRIDGE", method, params?.name || "", JSON.stringify(m.error || m.result?.structuredContent || m.result || {}).slice(0, 240))
      res.setHeader("content-type", "application/json; charset=utf-8")
      res.end(JSON.stringify(m.error ? { error: m.error } : { result: m.result })); return
    }
    res.statusCode = 404; res.end("not found")
  } catch (e) { res.statusCode = 500; res.end(JSON.stringify({ error: { message: e.message } })) }
})
server.listen(0, "127.0.0.1", () => {
  const address = server.address()
  console.log(JSON.stringify({ url: `http://127.0.0.1:${address.port}/`, paper, state: env.PAPER_DUAL_STATE_DIR }))
})
let closing = false
async function cleanup() {
  if (closing) return
  closing = true
  server.close()
  child.kill()
  await rm(tmp, { recursive: true, force: true })
}
process.on("SIGINT", async () => { await cleanup(); process.exit(0) })
process.on("SIGTERM", async () => { await cleanup(); process.exit(0) })
child.on("exit", code => { if (!closing) { console.error("MCP_EXIT", code, stderr.slice(-1000)); cleanup().then(() => process.exit(code || 1)) } })
