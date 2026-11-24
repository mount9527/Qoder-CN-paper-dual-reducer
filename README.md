# paper-dual-qoder · 论文双降工作台（Qoder CN 插件）

中文论文 `.docx` 的**诊断 + 门禁改写闭环 + 版本链**工作台，为 Qoder CN 重新开发。
离线核心零第三方依赖（Python 标准库）；可视化工作台用 Node stdio MCP 服务（零 npm 依赖）。

> 功能设计参考 DSH 插件 [paper-dual-reducer](https://github.com/mount9527/paper-dual-reducer)（MIT），
> 引擎代码独立重写，非移植。许可 MIT，来源说明见 `NOTICE.md`。

## 能做什么

流水线：**诊断 → 生成改写任务 → 改写 → 门禁预检 → 回填 → 复检 → 报告**。

- **诊断**：9 个文风规则维度（模板套话/被动句/句长起伏/结构对称/层层嵌套/冒号列举/标点节奏/口语化/破折号，纯正则离线）+ 13 字指纹篇内重复定位 + 锁定区统计。
- **三种模式**：降篇内重复（dedup）、降 AI 痕迹（aigc）、双降（dual，一次改写同时注入两类要求）。
- **改写闭环**：在 Qoder 里 **agent 亲自改写**（不必复制提示词到外部模型），五道门禁逐段把关，回填后篇级复检，恶化整篇自动回退。
- **版本链**：原文 → 改稿 → 二改 … 最多五改；每轮基于上一版已验收稿；被拒稿留档不覆盖。
- **格式保形**：标题/表格/代码/公式/脚注/参考文献/附录一律不改；支持本地化标题样式与继承链，手动加粗的文献标题也锁定；智能标签等嵌套文本容器保守锁定；zip 其他条目逐字节复制。
- **可靠状态**：同文档跨进程锁、原子状态替换、重新诊断事务保护；无效新文档不会挤掉旧状态。
- **明确验收**：本轮任务默认必须全部提交并通过；只有用户明确同意后，才允许跳过被拒/未提交段。结果区分通过、未通过（候选稿已回退）、拒绝（输入或任务集不合格），不表示平台通过概率。
- **合理阈值**：字二元组语义代理下限 0.40；重复率允许上升 0.002（0.2 个百分点）；风险分持平不判失败，避免机械阈值挡住可用改稿。
- **可视化工作台**：左侧「更多」菜单打开「论文双降工作台」，填路径诊断、切版本、三栏对照、渲染报告、下载改稿；预检与回填会先保存当前粘贴的清单，避免检查或提交旧内容。

## 依赖

- **Python 3.10+**（引擎，零第三方依赖）。`python` 不在 PATH 时可用环境变量 `PAPER_DUAL_PYTHON` 指定解释器。
- **Node.js**（仅工作台 MCP 服务需要；Qoder Desktop 环境自带）。
- 真机 AIGC 模型检测（torch + transformers）**当前版本未包含**；`doctor` 区分未配置与配置了尚未支持的模型，两者均不产生模型分数。

## 安装

打包 ZIP（根目录直接含 `.qoder-plugin/plugin.json` 与 `.mcp.json`），通过 Qoder 的本地插件 ZIP 安装流程导入并启用；重启或重载插件后：

- 对话里说「诊断这篇论文 / 帮我双降 / 降重」触发 `paper-dual` Skill。
- 左侧「更多」菜单出现「论文双降工作台」。

## 用法（对话闭环）

1. 给 agent 论文 `.docx` 绝对路径（旧版 `.doc` 请先另存为 `.docx`）。
2. agent 跑诊断并汇报重复率/风险分布/锁定统计。
3. 选模式（默认双降）与文风（默认平实 plain，可选学术 academic）。
4. agent 生成任务、亲自逐段改写、预检修正、回填复检，交付改稿与报告。
5. 要续改就生成下一轮，最多五改。

## 引擎 CLI

```sh
python skills/paper-dual/scripts/engine.py doctor
python skills/paper-dual/scripts/engine.py diagnose --path /abs/paper.docx
python skills/paper-dual/scripts/engine.py rewrite  --path /abs/paper.docx --mode dual --style plain
# agent 按 prompts.jsonl 写 edits.jsonl（一行一条 {"pid","text","op"}）
python skills/paper-dual/scripts/engine.py lint     --path /abs/paper.docx
python skills/paper-dual/scripts/engine.py finish   --path /abs/paper.docx
# 仅在用户明确同意跳过被拒/未提交段时：
python skills/paper-dual/scripts/engine.py finish   --path /abs/paper.docx --allow-partial
python skills/paper-dual/scripts/engine.py status   --path /abs/paper.docx
```

每个子命令输出单个 JSON。`lint` 未通过与 `finish` 未通过/拒绝时退出码为 2，防止 shell 链误继续。状态目录默认 `~/.qoder-cn/paper-dual/`，`PAPER_DUAL_STATE_DIR` 可重定向。

## 测试

```sh
npm test                                         # 引擎回归 + 语法 + MCP 冒烟/集成
npm run test:engine                              # 夹具 docx 全链路 + 篡改反例 + 并发/事务/样式/预算，115 项断言
npm run check                                     # 工作台静态语法检查
npm run smoke                                     # MCP 协议子进程冒烟测试（23 项）
npm run integration                               # 经 MCP 服务跑完整闭环（15 项）
npm run test:ui                                   # 本地浏览器工作台回归宿主（需连接 Qoder 浏览器）
node dist/ui-harness.mjs                          # 可选：独立浏览器 + 真实 MCP 子进程验证（非 Qoder 宿主验收）
```

## 边界（请先读）

- 风险分/重复率是**篇内相对指标**，不是任何平台检测概率；不模拟知网/维普/格子达，不承诺通过率。早期说明中的 6 维是旧口径；当前 9 维是文风规则，并非独立检测模型。门禁通过不等于原稿更优，请比较后替换。
- 只改正文，锁定区一律不动；不新增数字/引用/事实；改稿必须由作者逐段复核。
- 文档不出本机；引擎不读 token、不写远端。
- DOCX 资源预算：ZIP 最多 10,000 条目、解压后总计 256 MiB、单个 XML 64 MiB；超限显式拒绝。
- 含文本框/嵌套结构的文档可能无法安全回填（诊断仍可用），引擎显式报错保护原件。

## 目录

```
.qoder-plugin/plugin.json   插件清单
.mcp.json                   工作台 MCP 服务声明（${QODER_PLUGIN_ROOT}）
LICENSE / NOTICE.md         MIT 许可 / 来源说明
server.mjs                  stdio MCP 服务（桥接 Python 引擎）
app.html / app.js           工作台页面（自包含，服务内联脚本）
skills/paper-dual/
  SKILL.md                  主 Skill：对话闭环
  references/               算子规格 / 门禁阈值 / 合规边界
  scripts/engine.py         引擎（零第三方依赖）
  scripts/test_engine.py    端到端测试
scripts/smoke.mjs           MCP 协议冒烟测试
```
