#!/usr/bin/env python3
"""引擎端到端测试：夹具 docx 全链路 + 篡改反例。python test_engine.py"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE / "engine.py"
PASS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        print(f"FAIL: {name} {detail}")
        sys.exit(1)
    PASS += 1
    print(f"ok {PASS}: {name}")


def run(*args: str, expect_ok: bool | None = None) -> dict:
    proc = subprocess.run([sys.executable, str(ENGINE), *args],
                          capture_output=True, text=True, encoding="utf-8")
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        print("STDOUT:", proc.stdout)
        print("STDERR:", proc.stderr)
        raise
    if expect_ok is not None:
        check(f"CLI {args[0]} ok={expect_ok}", out.get("ok") is expect_ok, str(out)[:200])
    return out


WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MNS = "http://schemas.openxmlformats.org/officeDocument/2006/math"

BODY1 = ("随着人工智能技术的快速发展，深度学习模型在图像识别领域发挥着重要作用。"
         "该模型被广泛应用于医疗影像分析。该模型被广泛应用于工业质检场景。"
         "综上所述，本文旨在提出一种改进方法，值得注意的是，该方法具有重要意义。")
BODY2 = ("深度学习模型在图像识别领域发挥着重要作用。"
         "本研究将其用于文本分类。")
BODY3 = ("首先，系统采集数据。其次，系统清洗数据。最后，系统输出结果。"
         "一方面，效率得到提升。另一方面，成本得以降低。")
BODY4 = "本实验于2020年完成，样本量为1500例，准确率达到92.5%，相关结论参见文献[12]。"

EDIT1 = ("人工智能技术快速发展之后，深度学习模型在图像识别领域发挥着重要作用。"
         "医疗影像分析广泛采用该模型，工业质检场景也大量使用它。"
         "总的来看，本文提出一种改进方法，该方法具有实际价值。")
EDIT2 = ("这类深度网络在图像识别任务中承担着关键角色。"
         "本研究将其用于文本分类。")
EDIT3 = ("系统先采集数据，然后对数据进行清洗，最终输出结果。"
         "效率提升的同时，成本也降了下来。")
EDIT4 = "本实验于2020年完成，样本量为1500例，准确率达到92.5%，相关结论参见文献[12]。"


def p(text: str, style: str | None = None, bold_part: str | None = None,
      code: bool = False, extra: str = "") -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    rpr = '<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/></w:rPr>' if code else ""
    if bold_part:
        rest = text.replace(bold_part, "", 1)
        runs = (f'<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">{bold_part}</w:t></w:r>'
                f'<w:r><w:t xml:space="preserve">{rest}</w:t></w:r>')
    else:
        runs = f'<w:r>{rpr}<w:t xml:space="preserve">{text}</w:t></w:r>'
    return f"<w:p>{ppr}{runs}{extra}</w:p>"


def make_fixture(path: Path) -> None:
    table = ('<w:tbl><w:tr><w:tc><w:p><w:r><w:t>表内单元格文字，不参与改写。</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
    formula = (f'<w:p><m:oMath xmlns:m="{MNS}"><m:r><m:t>E=mc²</m:t></m:r></m:oMath>'
               '<w:r><w:t>公式段说明文字，不参与改写。</w:t></w:r></w:p>')
    footnote = ('<w:p><w:r><w:t>含脚注的段落文字，不参与改写。</w:t></w:r>'
                '<w:r><w:footnoteReference w:id="1"/></w:r></w:p>')
    doc = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{WNS}"><w:body>
{p("第一章 绪论", style="Heading1")}
{p(BODY1)}
{p(BODY2)}
{p(BODY3)}
{p(BODY4)}
{p("这是一段混合格式的正文，其中一部分加粗显示，其余部分保持常规字体样式。", bold_part="一部分加粗显示")}
{p("短段。")}
{p("print('hello world')  # 代码片段示例", code=True)}
{table}
{formula}
{footnote}
{p("参考文献", style="Heading1")}
{p("[1] 张三. 某项研究综述[J]. 计算机学报, 2020, 43(1): 1-12.")}
<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr>
</w:body></w:document>'''
    ct = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)


def write_edits(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="paper-dual-test-"))
    os.environ["PAPER_DUAL_STATE_DIR"] = str(tmp / "state")
    paper = tmp / "测试论文.docx"
    make_fixture(paper)

    run("doctor", expect_ok=True)
    d = run("diagnose", "--path", str(paper), expect_ok=True)
    doc = d["doc"]
    check("诊断：段落总数 13", doc["paragraphs"] == 13, str(doc))
    reasons = doc["locked_reasons"]
    check("诊断：标题锁定 2", reasons.get("标题") == 2, str(reasons))
    check("诊断：表格锁定 1", reasons.get("表格") == 1)
    check("诊断：代码锁定 1", reasons.get("代码") == 1)
    check("诊断：混合格式锁定 1", reasons.get("混合格式") == 1)
    check("诊断：参考文献锁定 1", reasons.get("参考文献") == 1)
    check("诊断：公式锁定 1", reasons.get("公式") == 1)
    check("诊断：脚注锁定 1", reasons.get("脚注") == 1)
    check("诊断：可改写段 4", doc["rewritable"] == 4, str(doc))
    check("诊断：篇内重复率 > 0", d["repeat"]["rate"] > 0, str(d["repeat"]))
    check("诊断：高风险段 ≥ 1", d["risk"]["high"] >= 1, str(d["risk"]["dims"]))
    check("诊断：D1 模板套话命中", any(x["id"] == "D1" and x["hits"] > 0 for x in d["risk"]["dims"]))

    bad = run("rewrite", "--path", str(paper), "--mode", "invalid")
    check("rewrite 拒绝非法 mode", bad.get("ok") is False)
    r = run("rewrite", "--path", str(paper), "--mode", "dual", expect_ok=True)
    check("rewrite：轮次 1", r["round"] == 1)
    check("rewrite：任务数 3（干净事实句不派任务）", r["task_count"] == 3, str(r["tasks"]))
    prompts = [json.loads(x) for x in Path(r["prompts"]).read_text(encoding="utf-8").splitlines()]
    check("rewrite：prompts 含要求与文风", all(p["requirements"] or p["repeat_ratio"] >= 0.2 for p in prompts)
          and all(len(p["style_rules"]) == 5 for p in prompts))
    check("rewrite：O9 派发给重复段", any("O9" in p["ops"] for p in prompts))
    r2 = run("rewrite", "--path", str(paper), "--mode", "dual", expect_ok=True)
    check("rewrite：待回填时原地重建不增轮次", r2["round"] == 1)

    by_text = {}
    sd = Path(d["state_dir"])
    sys.path.insert(0, str(HERE))
    import engine
    base = engine.Docx(sd / "original.docx")
    by = base.by_pid()
    for para in base.paras:
        by_text[para.text] = para.pid
    pid1, pid2, pid3, pid4 = (by_text[BODY1], by_text[BODY2], by_text[BODY3], by_text[BODY4])
    table_pid = next(p.pid for p in base.paras if "表格" in p.reasons)

    good = tmp / "good.jsonl"
    write_edits(good, [{"pid": pid1, "text": EDIT1, "op": "O5"},
                       {"pid": pid2, "text": EDIT2, "op": "O9"},
                       {"pid": pid3, "text": EDIT3, "op": "O1"}])
    run("save-edits", "--path", str(paper), "--file", str(good), expect_ok=True)
    lint = run("lint", "--path", str(paper), expect_ok=True)
    check("lint：好清单全过", lint["passed_count"] == 3 and lint["rejected_count"] == 0, str(lint))

    badf = tmp / "bad.jsonl"
    write_edits(badf, [
        {"pid": pid1, "text": EDIT1 + "实验于2021年补充完成。", "op": "O1"},
        {"pid": pid2, "text": "今天天气不错，我们一起去公园散步吧，顺便买点东西回来。", "op": "O1"},
        {"pid": pid3, "text": "短。", "op": "O1"},
        {"pid": table_pid, "text": "试图改写表格单元格文字，不参与改写。", "op": "O1"},
    ])
    lint2 = run("lint", "--path", str(paper), "--edits", str(badf))
    check("lint：坏清单全拒", lint2["passed_count"] == 0 and lint2["rejected_count"] == 4, str(lint2))
    gates = {r["pid"]: {g["gate"] for g in r["gates"]} for r in lint2["rejected"]}
    check("反例：数字篡改被实体门禁拒", "实体" in gates[pid1])
    check("反例：语义代理拒", "语义代理" in gates[pid2])
    check("反例：长度闸拒", "长度" in gates[pid3])
    check("反例：锁定段被拒", bool(gates[table_pid]))
    check("门禁单元：主观表述", any(g["gate"] == "主观表述" for g in engine.lint_edit(by[pid1], EDIT1 + "我认为这很重要。", "O1")))
    check("门禁单元：锁定区", any(g["gate"] == "锁定区" for g in engine.lint_edit(by[table_pid], "试图改写表格单元格文字，不参与改写。", "O1")))
    check("门禁单元：实体丢失", any(g["gate"] == "实体" for g in engine.lint_edit(by[pid4], EDIT4.replace("[12]", ""), "O1")))

    dup = tmp / "dup.jsonl"
    write_edits(dup, [{"pid": pid1, "text": EDIT1}, {"pid": pid1, "text": EDIT1}])
    check("save-edits：重复 pid 拒", run("save-edits", "--path", str(paper), "--file", str(dup)).get("ok") is False)

    f = run("finish", "--path", str(paper), expect_ok=True)
    check("finish：判定 accepted", f["verdict"] == "accepted", str(f["reasons"]))
    check("finish：重复率下降", f["metrics_after"]["repeat_rate"] < f["metrics_before"]["repeat_rate"],
          f'{f["metrics_before"]["repeat_rate"]} -> {f["metrics_after"]["repeat_rate"]}')
    check("finish：风险分下降", f["metrics_after"]["risk_total"] < f["metrics_before"]["risk_total"])
    out_docx = Path(f["docx"])
    check("finish：改稿存在", out_docx.exists())
    out = engine.Docx(out_docx)
    check("finish：段落数不变", len(out.paras) == len(base.paras))
    ob = out.by_pid()
    check("finish：全部格式签名不变", all(ob[p.pid].sig == p.sig for p in base.paras))
    check("finish：锁定段文本零改动", all(ob[p.pid].text == p.text for p in base.paras if p.locked))
    check("finish：改段文本已更新", ob[pid1].text == EDIT1 and ob[pid2].text == EDIT2)
    with zipfile.ZipFile(out_docx) as z:
        check("finish：zip 条目一致", z.namelist() == base.names)
    check("finish：报告存在", Path(f["report"]).exists())
    cmp_data = json.loads(Path(f["comparison"]).read_text(encoding="utf-8"))
    check("finish：三栏对照 3 行", len(cmp_data["rows"]) == 3)

    worse = tmp / "worse.jsonl"
    write_edits(worse, [{"pid": pid1, "text": EDIT1 + "综上所述，该方法具有重要意义。", "op": "O5"}])
    run("rewrite", "--path", str(paper), "--mode", "dual", expect_ok=True)
    run("save-edits", "--path", str(paper), "--file", str(worse), expect_ok=True)
    f2 = run("finish", "--path", str(paper))
    check("finish：恶化稿被拒", f2.get("ok") is False and f2["verdict"] == "rejected", str(f2))
    check("finish：拒绝原因含风险分", any("风险分" in x for x in f2["reasons"]), str(f2["reasons"]))
    st = run("status", "--path", str(paper), expect_ok=True)
    check("回退：基础稿仍是第 1 轮", st["base"] == str(out_docx), st["base"])
    check("回退：被拒稿留档", (sd / "rounds" / "r2" / "rejected.docx").exists())

    paper.write_bytes(paper.read_bytes() + b"")
    with open(paper, "ab") as fh:
        fh.write(b"")
    original_bytes = paper.read_bytes()
    paper.write_bytes(original_bytes + b"\x00")
    blocked = run("rewrite", "--path", str(paper))
    check("原文被修改后阻止续改", blocked.get("ok") is False and blocked.get("blocked") is True)
    paper.write_bytes(original_bytes)

    arch = run("diagnose", "--path", str(paper), expect_ok=True)
    check("重新诊断：旧产物归档", any((tmp / "state" / "archive").iterdir()))
    check("重新诊断：轮次清零", run("status", "--path", str(paper))["rounds"] == [])

    for i in range(5):
        rr = run("rewrite", "--path", str(paper), "--mode", "aigc", expect_ok=True)
        write_edits(good, [{"pid": pid1, "text": EDIT1, "op": "O5"}])
        run("save-edits", "--path", str(paper), "--file", str(good), expect_ok=True)
        run("finish", "--path", str(paper))
    cap = run("rewrite", "--path", str(paper), "--mode", "aigc")
    check("五改上限", cap.get("ok") is False and cap.get("capped") is True, str(cap))

    doc_file = tmp / "old.doc"
    doc_file.write_bytes(b"\xd0\xcf\x11\xe0")
    check(".doc 明确拒绝", run("diagnose", "--path", str(doc_file)).get("ok") is False)
    lst = run("status", expect_ok=True)
    check("status 列出最近文档", len(lst["docs"]) >= 1)

    print(f"\nALL {PASS} ASSERTIONS PASSED")
    print("fixture/state:", tmp)


if __name__ == "__main__":
    main()