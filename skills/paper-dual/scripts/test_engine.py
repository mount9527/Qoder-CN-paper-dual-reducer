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
EDIT3 = ("系统首先采集数据，其次清洗数据，最后输出结果。"
         "效率在一方面得到提升，成本在另一方面得以降低。")
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


def make_localized_fixture(path: Path) -> None:
    make_fixture(path)
    with zipfile.ZipFile(path) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    styles = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{WNS}">
<w:style w:type="paragraph" w:styleId="1"><w:name w:val="heading 1"/></w:style>
<w:style w:type="paragraph" w:styleId="2"><w:name w:val="Custom Title"/><w:basedOn w:val="1"/></w:style>
</w:styles>'''
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for info, data in items:
            if info.filename == "word/document.xml":
                text = data.decode("utf-8")
                text = text.replace('w:pStyle w:val="Heading1"', 'w:pStyle w:val="1"', 1)
                text = text.replace('w:pStyle w:val="Heading1"', 'w:pStyle w:val="2"', 1)
                data = text.encode("utf-8")
            z.writestr(info, data)
        z.writestr("word/styles.xml", styles)


def make_unknown_style_fixture(path: Path) -> None:
    make_fixture(path)
    with zipfile.ZipFile(path) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for info, data in items:
            if info.filename == "word/document.xml":
                text = data.decode("utf-8").replace(p(BODY4), p(BODY4, style="GhostStyle"))
                data = text.encode("utf-8")
            z.writestr(info, data)


def make_edge_fixture(path: Path) -> None:
    make_fixture(path)
    with zipfile.ZipFile(path) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    nested = (f'<w:p><w:smartTag><w:r><w:rPr><w:b/></w:rPr><w:t>{BODY4}</w:t></w:r>'
              '<w:r><w:t>该段包含智能标签容器，必须保守锁定以避免格式丢失。</w:t></w:r></w:smartTag></w:p>')
    manual_refs = ('<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>参考文献</w:t></w:r></w:p>'
                   '<w:p><w:r><w:t>手动加粗标题下的文献条目也必须锁定，不能进入改写任务清单。</w:t></w:r></w:p>'
                   '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>中文文献</w:t></w:r></w:p>'
                   '<w:p><w:r><w:t>子分类标题之后的文献条目同样必须保持锁定，不能被误判为新正文。</w:t></w:r></w:p>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for info, data in items:
            if info.filename == "word/document.xml":
                text = data.decode("utf-8")
                text = text.replace(p(BODY1), p(BODY1).replace(' xml:space="preserve"', ""), 1)
                text = text.replace(p(BODY2), nested + p(BODY2), 1)
                text = text.replace(p("参考文献", style="Heading1"), manual_refs, 1)
                data = text.encode("utf-8")
            z.writestr(info, data)


def write_edits(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="paper-dual-test-"))
    os.environ["PAPER_DUAL_STATE_DIR"] = str(tmp / "state")
    paper = tmp / "测试论文.docx"
    make_fixture(paper)

    doctor = run("doctor", expect_ok=True)
    check("AIGC 未配置状态明确", doctor["aigc"]["status"] == "not_configured", str(doctor["aigc"]))
    os.environ["PAPER_DUAL_AIGC_MODEL"] = "configured-but-unsupported"
    configured = run("doctor", expect_ok=True)
    del os.environ["PAPER_DUAL_AIGC_MODEL"]
    check("AIGC 配置错误不与未配置混淆", configured["aigc"]["status"] == "configuration_error", str(configured["aigc"]))
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
    check("实体校验保留重复次数", engine.entities("2020年与2021年")["数字"]["2020"] == 1
          and engine.entities("2020年与2020年")["数字"]["2020"] == 2)
    positive = engine.Paragraph("unit", None, "该模型不断进步，实验方法保持稳定。", False, False, [], "")
    advanced = engine.Paragraph("unit", None, "该模型持续发展，实验方法保持稳定。", False, False, [], "")
    check("普通副词不触发否定门禁", not any(g["gate"] == "语义代理" for g in engine.lint_edit(positive, advanced.text, "O8")))
    negative = engine.Paragraph("unit", None, "该方法不成立，需要继续验证。", False, False, [], "")
    affirmative = engine.Paragraph("unit", None, "该方法成立，需要继续验证。", False, False, [], "")
    check("显式否定消失仍被拦截", any("显式否定" in g["detail"] for g in engine.lint_edit(negative, affirmative.text, "O8")))
    loose = "系统先采集数据，然后对数据进行清洗，最终输出结果。效率提升的同时，成本也降了下来。"
    loose_para = engine.Paragraph("unit", None, BODY3, False, False, [], "")
    check("语义代理下限调整为 0.40", 0.30 < engine.bigram_overlap(engine.norm(BODY3), engine.norm(loose)) < engine.BIGRAM_MIN
          and any(g["gate"] == "语义代理" for g in engine.lint_edit(loose_para, loose, "O1")))
    check("重复率容忍 0.002 边界", engine.repeat_acceptable(0.5, 0.502) and engine.repeat_acceptable(0.0, 0.0)
          and not engine.repeat_acceptable(0.5, 0.5021))
    edge = tmp / "edge.docx"
    make_edge_fixture(edge)
    edge_doc = engine.Docx(edge)
    edge_nested = next(p for p in edge_doc.paras if "智能标签" in p.text)
    check("smartTag 嵌套文本保守锁定", edge_nested.locked and "嵌套文本容器" in edge_nested.reasons, str(edge_nested.reasons))
    check("手动加粗参考文献标题生效", any("参考文献" in p.reasons for p in edge_doc.paras if "手动加粗标题下" in p.text))
    check("文献子分类不解除锁定", any("参考文献" in p.reasons for p in edge_doc.paras if "子分类标题之后" in p.text))
    edge_base = next(p for p in edge_doc.paras if p.text == BODY1)
    edge_out = tmp / "edge-output.docx"
    edge_doc.save(edge_doc.apply_edits({edge_base.pid: EDIT1}), edge_out)
    edge_written = engine.Docx(edge_out)
    check("无 xml:space 段落保形回填", all(edge_written.by_pid()[p.pid].sig == p.sig for p in edge_doc.paras))
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
    lint_proc = subprocess.run([sys.executable, str(ENGINE), "lint", "--path", str(paper), "--edits", str(badf)],
                               capture_output=True, text=True, encoding="utf-8")
    check("lint 失败返回非零退出码", lint_proc.returncode == 2, str(lint_proc.returncode))
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
    check("finish：三态为通过", f["check_status"] == "passed", str(f["check_status"]))
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
    r2 = run("rewrite", "--path", str(paper), "--mode", "dual", expect_ok=True)
    current = engine.Docx(Path(r2["base"]))
    current_by = current.by_pid()
    worse_rows = [{"pid": t["pid"], "text": current_by[t["pid"]].text, "op": "O1"} for t in r2["tasks"]]
    worse_rows[0]["text"] += "综上所述，该方法具有重要意义。"
    write_edits(worse, worse_rows)
    run("save-edits", "--path", str(paper), "--file", str(worse), expect_ok=True)
    f2 = run("finish", "--path", str(paper))
    check("finish：恶化稿被拒", f2.get("ok") is False and f2["verdict"] == "rejected", str(f2))
    check("finish：候选稿篇级失败为未通过", f2["check_status"] == "not_passed", str(f2["check_status"]))
    check("finish：拒绝原因含风险分", any("风险分" in x for x in f2["reasons"]), str(f2["reasons"]))
    st = run("status", "--path", str(paper), expect_ok=True)
    check("回退：基础稿仍是第 1 轮", st["base"] == str(out_docx), st["base"])
    check("回退：被拒稿留档", (sd / "rounds" / "r2" / "rejected.docx").exists())

    accepted_state = json.loads((sd / "state.json").read_text(encoding="utf-8"))
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
    archived_dir = next((tmp / "state" / "archive").iterdir())
    archived_base = engine.base_info(accepted_state, archived_dir)[0]
    check("归档状态基础稿路径可重定位", archived_base.exists() and archived_base != out_docx, str(archived_base))

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

    localized = tmp / "localized.docx"
    make_localized_fixture(localized)
    ld = run("diagnose", "--path", str(localized), expect_ok=True)
    check("本地化标题样式锁定", ld["doc"]["locked_reasons"].get("标题") == 2, str(ld["doc"]["locked_reasons"]))
    check("样式继承标题锁定", ld["doc"]["rewritable"] == 4, str(ld["doc"]))

    unknown = tmp / "unknown-style.docx"
    make_unknown_style_fixture(unknown)
    unknown_doc = engine.Docx(unknown)
    unknown_para = next(p for p in unknown_doc.paras if p.text == BODY4)
    check("未知样式保守锁定", unknown_para.locked and "未知样式" in unknown_para.reasons, str(unknown_para.reasons))

    tx = tmp / "transaction.docx"
    make_fixture(tx)
    run("diagnose", "--path", str(tx), expect_ok=True)
    before_tx = run("status", "--path", str(tx), expect_ok=True)
    tx.write_bytes(b"not a docx")
    invalid = run("diagnose", "--path", str(tx))
    check("无效重新诊断被拒", invalid.get("ok") is False)
    after_tx = run("status", "--path", str(tx), expect_ok=True)
    check("无效重新诊断保留旧状态", after_tx["state_dir"] == before_tx["state_dir"]
          and after_tx["base_metrics"] == before_tx["base_metrics"], str(after_tx))
    check("诊断失败无临时状态残留", not any((tmp / "state" / "docs").glob(".*")))

    concurrent = tmp / "concurrent.docx"
    make_fixture(concurrent)
    run("diagnose", "--path", str(concurrent), expect_ok=True)
    procs = [subprocess.Popen([sys.executable, str(ENGINE), "rewrite", "--path", str(concurrent), "--mode", "dual"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
             for _ in range(12)]
    concurrent_out = [p.communicate() for p in procs]
    concurrent_results = [json.loads(out.strip().splitlines()[-1]) for out, _ in concurrent_out]
    check("12 个并发 rewrite 全部成功", all(p.returncode == 0 and j.get("ok") is True
          for p, j in zip(procs, concurrent_results)), str(concurrent_results))
    check("并发 rewrite 只生成一轮", all(j.get("round") == 1 for j in concurrent_results))
    check("并发后状态可读", run("status", "--path", str(concurrent), expect_ok=True)["pending"] is True)

    partial = tmp / "partial.docx"
    make_fixture(partial)
    run("diagnose", "--path", str(partial), expect_ok=True)
    pr = run("rewrite", "--path", str(partial), "--mode", "dual", expect_ok=True)
    pdoc = engine.Docx(Path(pr["state_dir"]) / "original.docx")
    replacements = {BODY1: EDIT1, BODY2: EDIT2, BODY3: EDIT3}
    pby = pdoc.by_pid()
    partial_rows = []
    for t in pr["tasks"]:
        text = pby[t["pid"]].text
        if text in replacements:
            partial_rows.append({"pid": t["pid"], "text": replacements[text], "op": "O1"})
    check("部分完成夹具至少两段", len(partial_rows) >= 2, str(partial_rows))
    subset = tmp / "subset.jsonl"
    write_edits(subset, partial_rows[:2])
    pl = run("lint", "--path", str(partial), "--edits", str(subset))
    check("lint 报告未提交任务", pl.get("ok") is False and any(g["gate"] == "任务覆盖"
          for r in pl["rejected"] for g in r["gates"]), str(pl))
    strict = run("finish", "--path", str(partial), "--edits", str(subset))
    check("默认拒绝部分完成", strict.get("ok") is False and strict["verdict"] == "rejected"
          and strict["docx"] is None, str(strict))
    check("不完整任务集三态为拒绝", strict["check_status"] == "rejected", str(strict["check_status"]))
    run("rewrite", "--path", str(partial), "--mode", "dual", expect_ok=True)
    accepted_partial = run("finish", "--path", str(partial), "--edits", str(subset), "--allow-partial")
    check("显式允许部分完成", accepted_partial.get("ok") is True and accepted_partial.get("partial") is True,
          str(accepted_partial))
    check("部分完成仍是本地门禁通过", accepted_partial["check_status"] == "passed", str(accepted_partial))
    check("部分完成报告标记", json.loads(Path(accepted_partial["comparison"]).read_text(encoding="utf-8"))["partial"] is True)

    old_entries = engine.MAX_ZIP_ENTRIES
    engine.MAX_ZIP_ENTRIES = 1
    try:
        try:
            engine.Docx(partial)
            check("ZIP 条目预算拒绝", False)
        except ValueError as e:
            check("ZIP 条目预算拒绝", "条目数超过预算" in str(e), str(e))
    finally:
        engine.MAX_ZIP_ENTRIES = old_entries
    old_xml = engine.MAX_XML_BYTES
    engine.MAX_XML_BYTES = 10
    try:
        try:
            engine.Docx(partial)
            check("XML 读取预算拒绝", False)
        except ValueError as e:
            check("XML 读取预算拒绝", "超过预算" in str(e), str(e))
        try:
            pdoc.save(pdoc.xml.encode("utf-8"), tmp / "over-budget.docx")
            check("XML 写入预算拒绝", False)
        except ValueError as e:
            check("XML 写入预算拒绝", "超过预算" in str(e), str(e))
    finally:
        engine.MAX_XML_BYTES = old_xml

    print(f"\nALL {PASS} ASSERTIONS PASSED")
    print("fixture/state:", tmp)


if __name__ == "__main__":
    main()