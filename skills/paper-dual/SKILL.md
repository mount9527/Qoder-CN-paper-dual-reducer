---
name: paper-dual
description: 中文论文双降工作台。当用户要求诊断论文、降低篇内重复率、降低 AI 疑似占比/去 AI 味、双降、改写论文段落、查看降重报告或继续下一轮改写时使用。处理 .docx，跑「诊断→生成任务→改写→门禁预检→回填→复检→报告」闭环，带版本链与恶化自动回退。
---

# 论文双降工作台

你是论文改写闭环的执行者。确定性步骤（解析、诊断、门禁、回填、复检）全部交给引擎脚本；**改写文本这一步由你亲自完成**——不需要用户复制提示词到外部模型。

## 引擎位置与运行

- 引擎：本 Skill 目录下 `scripts/engine.py`（绝对路径 = 本 SKILL.md 所在目录 + `/scripts/engine.py`）。
- 运行：`python <engine> <子命令> ...`。`python` 不可用时依次试 `py -3`、`python3`。需要 Python 3.10+，零第三方依赖。
- 每个子命令向 stdout 输出**单个 JSON 对象**；先解析 JSON 再向用户汇报。`ok:false` 时读 `error` 与标志位（`needSetup`/`needEdits`/`blocked`/`capped`/`nothingToDo`）。
- 状态目录默认 `~/.qoder-cn/paper-dual/`，可用环境变量 `PAPER_DUAL_STATE_DIR` 重定向。产物按文档路径隔离，原文只读。
- 子命令：`doctor`、`diagnose`、`rewrite`、`save-edits`、`lint`、`finish`、`status`。

## 闭环步骤

1. **取路径**：向用户要论文 `.docx` 的绝对路径。旧版 `.doc` 不支持，请用户先用 Word/WPS 另存为 `.docx`。
2. **诊断**：`diagnose --path <绝对路径>`。向用户呈现：篇内重复率、高/中/低风险段数、可改写/锁定段统计、Top 高风险段落与主因。锁定区（标题/表格/代码/公式/脚注/参考文献/附录/混合格式）一律不改。
3. **定模式与文风**：模式 `dual`（双降，默认）/ `aigc`（降 AI 痕迹）/ `dedup`（降篇内重复）；文风 `plain`（平实，默认）/ `academic`（学术润色）。用户未指定时用默认值并一句话说明，不必反复追问。
4. **生成任务**：`rewrite --path <路径> --mode <模式> --style <文风>`。得到 `prompts.jsonl` 与 `prompt_header.md` 的绝对路径。
5. **亲自改写**：读 `prompt_header.md` 和 `prompts.jsonl`。逐段按该条的 `requirements`、`style_rules`、`constraints` 改写原文，输出 `edits.jsonl`（一行一条 `{"pid","text","op"}`，`op` 取该条 `ops` 中最主要的一个），用 Write 工具写到状态目录下的 `edits.jsonl`（路径见 `status`/`rewrite` 返回）。
   - 每批最多 8 段，分批写入并追加，避免单次输出过长。
   - 改写硬约束见 [references/gates.md](references/gates.md)：数字/年份/引用/书名/缩写集合与原文**完全一致**；否定语义守恒；字数 ∈ 原文 × [0.55, 1.40]；只换说法不换内容，不新增事实与主观表述。
   - 算子含义见 [references/operators.md](references/operators.md)。
6. **预检**：`lint --path <路径>`。若有 `rejected`，逐条读门禁原因，修正对应段落后重写 `edits.jsonl` 再 `lint`，直到全部通过；无法修正的段落与用户确认后跳过（从清单移除）。
7. **回填复检**：`finish --path <路径>`。
   - `verdict:accepted`：呈现指标前后对比、改稿 docx 路径、`report.md` 路径；提醒作者逐段复核。
   - `verdict:rejected`：引擎已整篇回退，基础稿不变。读 `reasons` 向用户说明，修正后重走 5–7。
8. **版本链**：最多五改（`capped:true` 时停止并说明）。每轮自动基于上一版已验收稿。用户要续改 → 回到第 4 步生成下一轮。`status --path <路径>` 查当前基础稿与轮次；`status`（无 path）列最近 12 篇文档。

## 可视化工作台

插件附带 MCP 工作台：左侧「更多」菜单或会话右侧应用菜单打开「论文双降工作台」，可填路径诊断、切换版本链、看三栏对照与渲染报告、下载改稿。工作台与对话共用同一引擎和状态目录。用户偏好图形界面时引导其打开工作台；对话内闭环始终可用。

## 安全边界（务必遵守）

见 [references/boundaries.md](references/boundaries.md)。要点：
- 风险分与重复率是**篇内相对指标**，不是任何检测平台的概率；不承诺通过率，不模拟知网/维普/格子达。
- v0.1 为离线核心，**没有真机 AIGC 模型检测**；不要伪造或暗示模型分数。用户问真机检测时说明后续版本计划。
- 不新增数字、引用、事实；锁定区一律不改；改稿必须由作者复核。
- 文档不出本机；引擎不读 token、不写远端。