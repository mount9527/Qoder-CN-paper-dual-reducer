# paper-dual-qoder · 论文双降工作台（Qoder CN 插件）

中文论文 `.docx` 的**诊断 + 门禁改写闭环 + 版本链**工作台，为 Qoder CN 重新开发。
离线核心零第三方依赖（Python 标准库）；可视化工作台用 Node stdio MCP 服务（零 npm 依赖）。

> 功能设计参考 DSH 插件 [paper-dual-reducer](https://github.com/mount9527/paper-dual-reducer)（MIT），
> 引擎代码独立重写，非移植。许可 MIT，来源说明见 `NOTICE.md`。

## 能做什么

流水线：**诊断 → 生成改写任务 → 改写 → 门禁预检 → 回填 → 复检 → 报告**。

- **诊断**：9 维痕迹诊断（模板套话/被动句/句长起伏/结构对称/层层嵌套/冒号列举/标点节奏/口语化/破折号，纯正则离线）+ 13 字指纹篇内重复定位 + 锁定区统计。
- **三种模式**：降篇内重复（dedup）、降 AI 痕迹（aigc）、双降（dual，一次改写同时注入两类要求）。
- **改写闭环**：在 Qoder 里 **agent 亲自改写**（不必复制提示词到外部模型），五道门禁逐段把关，回填后篇级复检，恶化整篇自动回退。
- **版本链**：原文 → 改稿 → 二改 … 最多五改；每轮基于上一版已验收稿；被拒稿留档不覆盖。
- **格式保形**：标题/表格/代码/公式/脚注/参考文献一律不改；段落与字符属性零漂移；zip 其他条目逐字节复制。
- **可视化工作台**：左侧「更多」菜单打开「论文双降工作台」，填路径诊断、切版本、三栏对照、渲染报告、下载改稿。

## 依赖

- **Python 3.10+**（引擎，零第三方依赖）。`python` 不在 PATH 时可用环境变量 `PAPER_DUAL_PYTHON` 指定解释器。
- **Node.js**（仅工作台 MCP 服务需要；Qoder Desktop 环境自带）。
- 真机 AIGC 模型检测（torch + transformers）**v0.1 未包含**，计划后续版本提供；未提供前不产生模型分数。

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
python skills/paper-dual/scripts/engine.py status   --path /abs/paper.docx
```

每个子命令输出单个 JSON。状态目录默认 `~/.qoder-cn/paper-dual/`，`PAPER_DUAL_STATE_DIR` 可重定向。

## 测试

```sh
python skills/paper-dual/scripts/test_engine.py   # 夹具 docx 全链路 + 篡改反例，71 项断言
npm run check                                     # 工作台静态语法检查
npm run smoke                                     # MCP 协议子进程冒烟测试（16 项）
npm run integration                               # 经 MCP 服务跑完整闭环（11 项）
```

## 边界（请先读）

- 风险分/重复率是**篇内相对指标**，不是任何平台检测概率；不模拟知网/维普/格子达，不承诺通过率。
- 只改正文，锁定区一律不动；不新增数字/引用/事实；改稿必须由作者逐段复核。
- 文档不出本机；引擎不读 token、不写远端。
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