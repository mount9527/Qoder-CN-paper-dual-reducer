#!/usr/bin/env python3
"""论文双降引擎（Qoder CN 插件自带，零第三方依赖，Python 3.10+）。

流水线：diagnose -> rewrite -> (agent 改写 edits.jsonl) -> lint -> finish -> report
所有子命令向 stdout 输出单个 JSON 对象；人类可读产物写入状态目录。
只读源文件：诊断时快照 original.docx，之后一切操作基于快照。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

ENGINE_VERSION = "0.2.1"
MAX_ROUNDS = 5
MIN_PARA_CHARS = 20
FINGERPRINT = 13
REPEAT_TASK_RATIO = 0.20
REPEAT_O9_RATIO = 0.45
REPEAT_ACCEPT_TOLERANCE = 0.002
LENGTH_LO, LENGTH_HI = 0.55, 1.40
BIGRAM_MIN = 0.40
HIGH_SCORE, MID_SCORE = 6, 3
ROUND_CAPS = {"dual": [16, 10, 6, 4, 2], "aigc": [12, 8, 5, 3, 2], "dedup": [16, 10, 6, 4, 2]}
MAX_ZIP_ENTRIES = 10000
MAX_ZIP_TOTAL = 256 * 1024 * 1024
MAX_XML_BYTES = 64 * 1024 * 1024
STATE_LOCK_TIMEOUT = 180
MODE_NAMES = {"dedup": "降低篇内重复率", "aigc": "降低 AI 疑似占比（离线痕迹口径）", "dual": "双降"}

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

for _p, _u in [
    ("w", "http://schemas.openxmlformats.org/wordprocessingml/2006/main"),
    ("m", "http://schemas.openxmlformats.org/officeDocument/2006/math"),
    ("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships"),
    ("mc", "http://schemas.openxmlformats.org/markup-compatibility/2006"),
    ("wp", "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"),
    ("a", "http://schemas.openxmlformats.org/drawingml/2006/main"),
    ("pic", "http://schemas.openxmlformats.org/drawingml/2006/picture"),
    ("v", "urn:schemas-microsoft-com:vml"),
    ("o", "urn:schemas-microsoft-com:office:office"),
    ("w14", "http://schemas.microsoft.com/office/word/2010/wordml"),
    ("w15", "http://schemas.microsoft.com/office/word/2012/wordml"),
    ("wps", "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"),
    ("wpg", "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"),
    ("w10", "urn:schemas-microsoft-com:office:word"),
    ("sl", "http://schemas.openxmlformats.org/schemaLibrary/2006/main"),
]:
    ET.register_namespace(_p, _u)

CODE_FONTS = {"consolas", "courier new", "courier", "monaco", "menlo", "lucida console", "monospace", "source code pro", "jetbrains mono"}
HEADING_STYLE = re.compile(r"^(heading\s*[1-9]|标题\s*[1-9]?|title|subtitle)$", re.I)
REFS_HEAD = re.compile(r"参考文献|参考书目|References", re.I)
APPENDIX_HEAD = re.compile(r"附\s*录|Appendix", re.I)

NEG_RE = re.compile(r"并未|并非|不是|不能|不会|不可|不应|不得|不再|从未|决不|绝不|没有|没能|未能|否认|缺乏|缺少|忽略|避免|防止|未|没|无(?!论|线|限|数|穷|奈|辜|私|畏|端|偿|微不至)|不(?!断|时|久|宜|妨|免|知|见得|得已|小心|由自主|约而同)")
SUBJ_RE = re.compile(r"我觉得|我认为|笔者觉得|个人认为|我们觉得")
NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")
CITE_RE = re.compile(r"\[\d+(?:[-–,，]\d+)*\]")
BOOK_RE = re.compile(r"《[^》]{1,60}》")
ABBR_RE = re.compile(r"[A-Z]{2,}")

OPERATORS = {
    "O1": "语序重组", "O2": "长句拆分", "O3": "短句合并", "O4": "主被动互换",
    "O5": "套话删改", "O6": "嵌套松绑", "O7": "列举改写", "O8": "同义替换", "O9": "消重改写",
}
DIM_OPS = {"D1": ["O5"], "D2": ["O4"], "D3": ["O2", "O3"], "D4": ["O1"], "D5": ["O2", "O6"],
           "D6": ["O7"], "D7": ["O2"], "D8": ["O5"], "D9": ["O2"]}

PLAIN_RULES = [
    "只换说法不换内容：调语序、换同义词、变主谓宾结构；无主语句依据上下文补主语，不引入新主体。",
    "平实文笔：删掉「我觉得／我认为」这类主观表述与夸张修饰。",
    "助词化：适当加「的、了、到、过、会、有、能、把」，句子不那么紧致，但不写成口水话。",
    "标点连贯：少用句号、多用逗号分号；长句拆短；高频词换同义词；专业词通俗化（术语与缩写不动）。",
    "结构松绑：打乱句子结构、把字句与被动句互换、调语序；核心表意与论断强度不得改变。",
]
ACADEMIC_RULES = [
    "保持学术语体：不口语化、不随意化，术语与缩写一律不动。",
    "只换说法不换内容：调语序、换同义表达、变主谓宾结构；核心表意与论断强度不得改变。",
    "删除模板套话与主观表述，合并碎句、拆分超长嵌套句，使句长有起伏。",
    "不新增数字、引用、事实与主观判断；否定与量词语义必须守恒。",
    "总字数控制在原文 ±20% 以内。",
]


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def die(msg: str, **extra) -> None:
    emit({"ok": False, "error": msg, **extra})
    sys.exit(1)


def norm(text: str) -> str:
    return re.sub(r"[\W_]", "", text, flags=re.UNICODE)


def vis_len(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"[。！？；!?;]", text) if s.strip()]


def canon(el) -> str:
    if el is None:
        return ""
    s = ET.tostring(el, encoding="unicode")
    return re.sub(r">\s+<", "><", s).strip()


def para_text(p) -> str:
    return "".join(t.text or "" for t in p.iter(W + "t"))


def para_sig(p) -> str:
    clone = ET.fromstring(ET.tostring(p))
    for t in clone.iter(W + "t"):
        t.text = ""
        t.attrib.pop(XML_SPACE, None)
    return canon(clone)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- docx 解析

class Paragraph:
    __slots__ = ("pid", "el", "text", "norm", "in_table", "locked", "reasons", "sig")

    def __init__(self, pid, el, text, in_table, locked, reasons, sig):
        self.pid, self.el, self.text, self.in_table = pid, el, text, in_table
        self.locked, self.reasons, self.sig = locked, reasons, sig
        self.norm = norm(text)


def validate_zip_budget(z: zipfile.ZipFile) -> None:
    infos = z.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise ValueError(f"DOCX 条目数超过预算：{len(infos)} > {MAX_ZIP_ENTRIES}")
    total = sum(i.file_size for i in infos)
    if total > MAX_ZIP_TOTAL:
        raise ValueError(f"DOCX 解压后体积超过预算：{total} > {MAX_ZIP_TOTAL}")
    doc = z.getinfo("word/document.xml")
    if doc.file_size > MAX_XML_BYTES:
        raise ValueError(f"document.xml 超过预算：{doc.file_size} > {MAX_XML_BYTES}")


class Docx:
    def __init__(self, path: Path):
        self.path = path
        self.styles: dict[str, tuple[str, str]] = {}
        with zipfile.ZipFile(path) as z:
            validate_zip_budget(z)
            self.xml = z.read("word/document.xml").decode("utf-8")
            self.names = z.namelist()
            if "word/styles.xml" in self.names:
                styles_xml = z.read("word/styles.xml")
                if len(styles_xml) > MAX_XML_BYTES:
                    raise ValueError(f"styles.xml 超过预算：{len(styles_xml)} > {MAX_XML_BYTES}")
                styles_root = ET.fromstring(styles_xml)
                for st in styles_root.findall(W + "style"):
                    sid = st.get(W + "styleId", "")
                    name_el = st.find(W + "name")
                    based_el = st.find(W + "basedOn")
                    if sid:
                        self.styles[sid] = (name_el.get(W + "val", "") if name_el is not None else "",
                                            based_el.get(W + "val", "") if based_el is not None else "")
        self.root = ET.fromstring(self.xml)
        self.body = self.root.find(W + "body")
        if self.body is None:
            raise ValueError("document.xml 缺少 w:body")
        self.paras: list[Paragraph] = []
        self._parse()

    def _walk(self, el, in_table, in_p):
        for child in el:
            if child.tag == W + "p":
                if not in_p:
                    yield child, in_table
                yield from self._walk(child, in_table, True)
            elif child.tag == W + "tbl":
                yield from self._walk(child, True, in_p)
            else:
                yield from self._walk(child, in_table, in_p)

    def _style_chain(self, style: str):
        seen = set()
        sid = style
        while sid and sid not in seen:
            seen.add(sid)
            name, sid = self.styles.get(sid, ("", ""))
            yield name

    def _is_heading(self, style: str, pPr) -> bool:
        if pPr is not None and pPr.find(W + "outlineLvl") is not None:
            return True
        return bool(style and (HEADING_STYLE.match(style) or any(HEADING_STYLE.match(n) for n in self._style_chain(style))))

    def _style_names(self, style: str) -> set[str]:
        return {style.lower(), *(n.lower() for n in self._style_chain(style))}

    def _parse(self):
        refs = appendix = False
        region_level = 0
        for i, (el, in_table) in enumerate(self._walk(self.body, False, False)):
            pid = f"p{i:04d}"
            text = para_text(el)
            reasons = self._classify(el, in_table, text, refs, appendix)
            pPr = el.find(W + "pPr")
            style_el = pPr.find(W + "pStyle") if pPr is not None else None
            style = style_el.get(W + "val") if style_el is not None else ""
            clean = norm(text)
            is_region_head = bool(re.fullmatch(r"(参考文献|参考书目|References|附录|Appendix)", clean, re.I))
            level = self._heading_level(style, pPr)
            if is_region_head or level:
                level = level or 1
                if REFS_HEAD.fullmatch(clean) or (not is_region_head and REFS_HEAD.search(text)):
                    refs, appendix, region_level = True, False, level
                elif APPENDIX_HEAD.fullmatch(clean) or (not is_region_head and APPENDIX_HEAD.search(text)):
                    refs, appendix, region_level = False, True, level
                elif refs or appendix:
                    if level <= region_level:
                        refs = appendix = region_level = False
                if "标题" not in reasons:
                    reasons.append("标题")
            self.paras.append(Paragraph(pid, el, text, in_table, bool(reasons), reasons, para_sig(el)))

    def _heading_level(self, style: str, pPr) -> int:
        outline = pPr.find(W + "outlineLvl") if pPr is not None else None
        if outline is not None:
            try:
                return int(outline.get(W + "val", "0")) + 1
            except ValueError:
                pass
        names = [style, *self._style_chain(style)]
        for name in names:
            m = re.search(r"(?:heading|标题)\s*([1-9])", name, re.I)
            if m:
                return int(m.group(1))
        return 1 if self._is_heading(style, pPr) else 0

    def _classify(self, el, in_table, text, refs, appendix) -> list[str]:
        r = []
        if in_table:
            r.append("表格")
        pPr = el.find(W + "pPr")
        style_el = pPr.find(W + "pStyle") if pPr is not None else None
        style = style_el.get(W + "val") if style_el is not None else ""
        if self._is_heading(style, pPr):
            r.append("标题")
        if style and style not in self.styles:
            r.append("未知样式")
        if refs:
            r.append("参考文献")
        if appendix:
            r.append("附录")
        if next(el.iter(M + "oMath"), None) is not None or next(el.iter(M + "oMathPara"), None) is not None:
            r.append("公式")
        if next(el.iter(W + "footnoteReference"), None) is not None or next(el.iter(W + "endnoteReference"), None) is not None:
            r.append("脚注")
        if next(el.iter(W + "fldChar"), None) is not None or next(el.iter(W + "instrText"), None) is not None:
            r.append("域/目录")
        if next(el.iter(W + "hyperlink"), None) is not None:
            r.append("引用链接")
        if next(el.iter(W + "drawing"), None) is not None or next(el.iter(W + "pict"), None) is not None or next(el.iter(W + "object"), None) is not None:
            r.append("图形/文本框")
        is_code = bool(self._style_names(style) & {"code", "代码", "htmlcode", "verbatimchar"})
        for rp in el.iter(W + "rPr"):
            fonts = rp.find(W + "rFonts")
            if fonts is not None:
                for attr in (W + "ascii", W + "hAnsi"):
                    if (fonts.get(attr) or "").lower() in CODE_FONTS:
                        is_code = True
        if is_code:
            r.append("代码")
        if next(el.iter(W + "tab"), None) is not None or next(el.iter(W + "br"), None) is not None:
            r.append("制表/换行")
        if any(next(el.iter(tag), None) is not None for tag in (W + "smartTag", W + "customXml", W + "sdt")):
            r.append("嵌套文本容器")
        runs = [run for run in el.iter(W + "r") if run.find(W + "t") is not None]
        if not runs or len(norm(text)) < MIN_PARA_CHARS:
            r.append("过短")
        elif len({canon(run.find(W + "rPr")) for run in runs}) > 1:
            r.append("混合格式")
        return r

    def by_pid(self) -> dict[str, Paragraph]:
        return {p.pid: p for p in self.paras}

    # ---- 写入：对原始 XML 做段落级外科手术，其余字节一律不动 ----

    def _spans(self) -> list[tuple[int, int]]:
        spans = []
        for m in re.finditer(r"<w:p(?:\s[^>]*)?(/?)>", self.xml):
            if m.group(1) == "/":
                spans.append((m.start(), m.end()))
            else:
                end = self.xml.find("</w:p>", m.end())
                if end == -1:
                    raise ValueError("段落 XML 不闭合")
                spans.append((m.start(), end + len("</w:p>")))
        return spans

    def _check_alignment(self, spans) -> None:
        if len(spans) != len(self.paras):
            raise ValueError("段落定位数量不一致（文档可能含文本框/嵌套结构），已放弃写入以保护原件")
        wt = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.S)
        for (a, b), p in zip(spans, self.paras):
            texts = [unescape_xml(t) for t in wt.findall(self.xml[a:b])]
            if "".join(texts) != p.text:
                raise ValueError(f"段落 {p.pid} 定位校验失败（含嵌套结构），已放弃写入以保护原件")

    def apply_edits(self, edits: dict[str, str]) -> bytes:
        spans = self._spans()
        self._check_alignment(spans)
        wt = re.compile(r"(<w:t(?:\s[^>]*)?>)(.*?)(</w:t>)", re.S)
        out = []
        cursor = 0
        for (a, b), p in zip(spans, self.paras):
            out.append(self.xml[cursor:a])
            span = self.xml[a:b]
            if p.pid in edits:
                new = edits[p.pid]
                first = True

                def repl(m):
                    nonlocal first
                    tag = m.group(1)
                    if first:
                        first = False
                        if "xml:space" not in tag and (new[:1].isspace() or new[-1:].isspace()):
                            tag = tag[:-1] + ' xml:space="preserve">'
                        return tag + escape_xml(new) + m.group(3)
                    return tag + m.group(3)

                span = wt.sub(repl, span)
            out.append(span)
            cursor = b
        out.append(self.xml[cursor:])
        return "".join(out).encode("utf-8")

    def save(self, new_xml: bytes, dest: Path) -> None:
        if len(new_xml) > MAX_XML_BYTES:
            raise ValueError(f"document.xml 超过预算：{len(new_xml)} > {MAX_XML_BYTES}")
        with zipfile.ZipFile(self.path) as zin, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
            validate_zip_budget(zin)
            for item in zin.infolist():
                data = new_xml if item.filename == "word/document.xml" else zin.read(item.filename)
                zout.writestr(item, data)
            if zin.comment:
                zout.comment = zin.comment


def escape_xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def unescape_xml(s: str) -> str:
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
             .replace("&apos;", "'").replace("&amp;", "&"))


# ---------------------------------------------------------------- 篇内重复

def dedup_analysis(paras: list[Paragraph]) -> dict:
    usable = [p for p in paras if not p.locked and len(p.norm) >= FINGERPRINT]
    index: dict[str, list[tuple[str, int]]] = {}
    for p in usable:
        s = p.norm
        for i in range(len(s) - FINGERPRINT + 1):
            index.setdefault(s[i:i + FINGERPRINT], []).append((p.pid, i))
    covered: dict[str, set] = {p.pid: set() for p in usable}
    peers: dict[str, set] = {p.pid: set() for p in usable}
    sample: dict[str, str] = {}
    for fp, occ in index.items():
        pids = {pid for pid, _ in occ}
        if len(pids) >= 2:
            for pid, i in occ:
                covered[pid].update(range(i, i + FINGERPRINT))
                peers[pid].update(pids - {pid})
                sample.setdefault(pid, fp)
    total = sum(len(p.norm) for p in usable)
    cov_total = sum(len(c) for c in covered.values())
    rows = []
    for p in usable:
        c = len(covered[p.pid])
        if c:
            rows.append({"pid": p.pid, "covered": c, "ratio": round(c / len(p.norm), 4),
                         "peers": sorted(peers[p.pid])[:6], "sample": sample.get(p.pid, "")})
    rows.sort(key=lambda r: (-r["covered"], r["pid"]))
    return {"rate": round(cov_total / total, 4) if total else 0.0,
            "covered_chars": cov_total, "total_chars": total,
            "per_para": {r["pid"]: r for r in rows}, "top": rows[:12]}


# ---------------------------------------------------------------- 9 维诊断

DIMS = [
    ("D1", "模板套话", 3), ("D2", "被动句", 1), ("D3", "句长起伏", 2),
    ("D4", "结构对称", 2), ("D5", "层层嵌套", 2), ("D6", "冒号列举", 1),
    ("D7", "标点节奏", 1), ("D8", "口语化", 2), ("D9", "破折号", 1),
]
TEMPLATES = re.compile(
    r"综上所述|总而言之|由此可见|值得注意的是|不难发现|随着[^，。；]{0,12}的(?:发展|进步|普及|深入|提升)"
    r"|在[^，。；]{0,12}(?:背景|形势|趋势)下|本文(?:旨在|主要|首先)|具有重要意义|发挥[^，。；]{0,6}重要作用"
    r"|不可或缺|日益(?:凸显|重要|突出)|起到[^，。；]{0,6}作用|提供[^，。；]{0,6}(?:参考|借鉴|依据)|奠定[^，。；]{0,6}基础")
PASSIVE = re.compile(r"被|受到|得以|加以|予以|为[^，。；]{0,8}所")
COLLOQUIAL = re.compile(r"其实|说白了|众所周知|大家都知道|简单来说|换句话说|也就是说|不得不说")
COLON_LIST = re.compile(r"：[^。]{0,40}(?:、|；|①|②|③|一是|二是|第一|第二)|(?:：|:)\s*[（(]?\d[)）.、]")
PARALLEL = re.compile(r"首先[^。]{0,200}其次|一方面[^。]{0,200}另一方面|不仅[^。]{0,60}而且|既[^。]{0,40}又")


def dim_hits(text: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    sents = sentences(text)

    def add(d, ev):
        hits.setdefault(d, []).append(ev)

    for m in TEMPLATES.finditer(text):
        add("D1", m.group(0))
    n = len(PASSIVE.findall(text))
    if n:
        add("D2", f"被动标记 ×{n}")
    if len(sents) >= 3:
        lens = [len(s) for s in sents]
        mean = sum(lens) / len(lens)
        var = sum((x - mean) ** 2 for x in lens) / len(lens)
        cv = (var ** 0.5) / mean if mean else 0
        if cv < 0.25:
            add("D3", f"句长变异系数 {cv:.2f}（过于均匀）")
        if mean > 60:
            add("D3", f"平均句长 {mean:.0f} 字")
    if PARALLEL.search(text):
        add("D4", PARALLEL.search(text).group(0)[:24] + "…")
    initials = [s[:2] for s in sents if len(s) >= 6]
    if len(initials) >= 3:
        top = max(set(initials), key=initials.count)
        if initials.count(top) >= 3:
            add("D4", f"{initials.count(top)} 句同以「{top}」开头")
    for s in sents:
        if (len(s) >= 50 and s.count("的") >= 3) or s.count("的") >= 4:
            add("D5", f"{len(s)} 字长句含 {s.count('的')} 个「的」")
            break
    if COLON_LIST.search(text):
        add("D6", COLON_LIST.search(text).group(0)[:24])
    commas = text.count("，") + text.count(",")
    periods = text.count("。") + text.count("！") + text.count("？")
    if periods >= 2 and commas / periods > 3.5:
        add("D7", f"逗号/句号比 {commas / periods:.1f}")
    if len(sents) >= 3 and len({len(s) for s in sents}) == 1:
        add("D7", "各句字数完全相同")
    for m in COLLOQUIAL.finditer(text):
        add("D8", m.group(0))
    dash = text.count("——") + len(re.findall(r"(?<!—)—(?!—)", text))
    if dash:
        add("D9", f"破折号 ×{dash}")
    return hits


def diagnose_paras(paras: list[Paragraph], dedup: dict) -> dict:
    per = {}
    dim_totals = {d: {"hits": 0, "paras": 0} for d, _, _ in DIMS}
    high = mid = low = total_score = 0
    top = []
    for p in paras:
        if p.locked:
            continue
        hits = dim_hits(p.text)
        score = 0
        evidence = []
        ops: list[str] = []
        for d, name, weight in DIMS:
            ev = hits.get(d, [])
            if ev:
                score += weight * min(len(ev), 2)
                dim_totals[d]["hits"] += len(ev)
                dim_totals[d]["paras"] += 1
                evidence.append({"dim": d, "name": name, "samples": ev[:3]})
                for op in DIM_OPS[d]:
                    if op not in ops:
                        ops.append(op)
        ratio = dedup["per_para"].get(p.pid, {}).get("ratio", 0.0)
        if ratio >= REPEAT_O9_RATIO and "O9" not in ops:
            ops.append("O9")
        band = "高" if score >= HIGH_SCORE else ("中" if score >= MID_SCORE else "低")
        if band == "高":
            high += 1
        elif band == "中":
            mid += 1
        else:
            low += 1
        total_score += score
        per[p.pid] = {"score": score, "band": band, "evidence": evidence, "ops": ops, "repeat_ratio": ratio}
        if score >= MID_SCORE or ratio >= REPEAT_TASK_RATIO:
            top.append({"pid": p.pid, "score": score, "band": band, "repeat_ratio": ratio,
                        "ops": ops, "evidence": evidence, "text": p.text[:120]})
    top.sort(key=lambda r: (-r["score"], -r["repeat_ratio"], r["pid"]))
    dims = [{"id": d, "name": n, "weight": w, **dim_totals[d]} for d, n, w in DIMS]
    return {"high": high, "mid": mid, "low": low, "total_score": total_score,
            "dims": dims, "per_para": per, "top": top[:12]}


# ---------------------------------------------------------------- 门禁

def entities(text: str) -> dict[str, Counter]:
    return {"数字": Counter(NUM_RE.findall(text)),
            "引用": Counter(CITE_RE.findall(text)),
            "书名": Counter(BOOK_RE.findall(text)),
            "缩写": Counter(ABBR_RE.findall(text))}


def neg_count(text: str) -> int:
    return len(NEG_RE.findall(text))


def bigram_overlap(a: str, b: str) -> float:
    A = {a[i:i + 2] for i in range(len(a) - 1)}
    B = {b[i:i + 2] for i in range(len(b) - 1)}
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))


def lint_edit(p: Paragraph, new_text: str, op: str) -> list[dict]:
    fails = []
    if p.locked:
        fails.append({"gate": "锁定区", "detail": "该段属于 " + "/".join(p.reasons) + "，一律不改"})
        return fails
    if not isinstance(new_text, str) or not new_text.strip():
        fails.append({"gate": "格式", "detail": "text 必须是非空字符串"})
        return fails
    if op not in OPERATORS:
        fails.append({"gate": "算子", "detail": f"op 必须是 O1–O9，收到 {op!r}"})
    old_e, new_e = entities(p.text), entities(new_text)
    for kind in old_e:
        missing, added = old_e[kind] - new_e[kind], new_e[kind] - old_e[kind]
        if missing or added:
            fails.append({"gate": "实体", "detail": f"{kind}次数不一致：丢失 {dict(missing) or '无'}，新增 {dict(added) or '无'}"})
    if len(SUBJ_RE.findall(new_text)) > len(SUBJ_RE.findall(p.text)):
        fails.append({"gate": "主观表述", "detail": "「我觉得/我认为」类表述增多"})
    ratio = vis_len(new_text) / max(vis_len(p.text), 1)
    if not (LENGTH_LO <= ratio <= LENGTH_HI):
        fails.append({"gate": "长度", "detail": f"字数比 {ratio:.2f} 超出 [{LENGTH_LO}, {LENGTH_HI}]"})
    ov = bigram_overlap(p.norm, norm(new_text))
    if ov < BIGRAM_MIN:
        fails.append({"gate": "语义代理", "detail": f"字二元组重合度 {ov:.2f} < {BIGRAM_MIN}，疑似换内容而非换说法"})
    if bool(NEG_RE.search(p.text)) != bool(NEG_RE.search(new_text)):
        fails.append({"gate": "语义代理", "detail": "显式否定表述增加或消失；请人工核对否定范围与论断强度"})
    return fails


def repeat_acceptable(before: float, after: float) -> bool:
    return after <= before + REPEAT_ACCEPT_TOLERANCE


def check_state(ok: bool, invalid_input: bool = False) -> str:
    if ok:
        return "passed"
    return "rejected" if invalid_input else "not_passed"


def read_edits(path: Path) -> tuple[list[dict], list[str]]:
    rows, errors = [], []
    seen = set()
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"第 {i} 行不是合法 JSON：{e}")
            continue
        if not isinstance(obj, dict) or "pid" not in obj or "text" not in obj:
            errors.append(f"第 {i} 行缺少 pid/text 字段")
            continue
        if obj["pid"] in seen:
            errors.append(f"第 {i} 行 pid 重复：{obj['pid']}")
            continue
        seen.add(obj["pid"])
        rows.append({"pid": obj["pid"], "text": obj["text"], "op": obj.get("op", "O1")})
    return rows, errors


# ---------------------------------------------------------------- 状态

def state_root() -> Path:
    env = os.environ.get("PAPER_DUAL_STATE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".qoder-cn" / "paper-dual"


def doc_key(path: Path) -> str:
    h = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]
    stem = re.sub(r"[^\w\u4e00-\u9fff-]", "_", path.stem)[:40] or "doc"
    return f"{stem}-{h}"


def doc_dir(path: Path) -> Path:
    return state_root() / "docs" / doc_key(path)


def load_state(d: Path) -> dict:
    f = d / "state.json"
    if not f.exists():
        die(f"尚未诊断：{d} 里没有 state.json，请先运行 diagnose", needSetup=True)
    return json.loads(f.read_text(encoding="utf-8"))


@contextmanager
def state_lock(path: Path, timeout: int = STATE_LOCK_TIMEOUT):
    locks = state_root() / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    lock_path = locks / f"{doc_key(path)}.lock"
    f = lock_path.open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                f.write(b"\0")
                f.flush()
            f.seek(0)
            deadline = time.monotonic() + timeout
            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        die("另一进程正在处理该文档，请稍后重试", busy=True)
                    time.sleep(0.1)
        else:
            import fcntl
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        die("另一进程正在处理该文档，请稍后重试", busy=True)
                    time.sleep(0.1)
        yield
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()


def save_state(d: Path, state: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=d)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, d / "state.json")
    finally:
        tmp.unlink(missing_ok=True)


def check_source(state: dict) -> None:
    src = Path(state["doc_path"])
    if not src.exists():
        die("原文文件已不存在；状态快照仍在，可继续查看历史版本，但无法重新诊断或回填", blocked=True)
    if sha256_file(src) != state["doc_hash"]:
        die("原文在诊断后被修改，旧产物不再可信；请重新运行 diagnose（旧产物会自动归档）", blocked=True)


def base_info(state: dict, d: Path) -> tuple[Path, dict]:
    for r in reversed(state["rounds"]):
        if r["status"] == "accepted":
            return d / "rounds" / f"r{r['n']}" / "accepted.docx", r["metrics_after"]
    return d / "original.docx", state["base_metrics"]


def metrics_of(doc: Docx) -> tuple[dict, dict, dict]:
    dd = dedup_analysis(doc.paras)
    risk = diagnose_paras(doc.paras, dd)
    m = {"repeat_rate": dd["rate"], "risk_total": risk["total_score"],
         "high": risk["high"], "mid": risk["mid"], "low": risk["low"],
         "paragraphs": len(doc.paras),
         "rewritable": sum(1 for p in doc.paras if not p.locked)}
    return m, dd, risk


# ---------------------------------------------------------------- 子命令

def cmd_doctor(_args) -> None:
    root = state_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError:
        writable = False
    configured_model = os.environ.get("PAPER_DUAL_AIGC_MODEL", "").strip()
    aigc = {"available": False, "status": "not_configured", "configured": False,
            "note": "未配置真机 AIGC 检测器；当前只运行离线痕迹诊断，不产生模型检测概率。"}
    if configured_model:
        aigc.update(status="configuration_error", configured=True,
                    note="已设置 PAPER_DUAL_AIGC_MODEL，但当前 v0.2.x 尚未接入模型推理；请取消该配置或升级引擎，离线诊断不受影响。")
    emit({"ok": True, "engine_version": ENGINE_VERSION, "python": sys.version.split()[0],
          "python_ok": sys.version_info >= (3, 10), "state_dir": str(root), "writable": writable,
          "aigc": aigc, "max_rounds": MAX_ROUNDS})


def cmd_diagnose(args) -> None:
    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        die(f"文件不存在：{path}")
    if path.suffix.lower() == ".doc":
        die("暂不支持旧版 .doc（二进制格式）；请用 Word/WPS 另存为 .docx 后再诊断")
    if path.suffix.lower() != ".docx":
        die("只支持 .docx 文件")
    d = doc_dir(path)
    d.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{d.name}-", dir=d.parent))
    try:
        snap = stage / "original.docx"
        try:
            shutil.copy2(path, snap)
            doc = Docx(snap)
        except (zipfile.BadZipFile, KeyError, ValueError, ET.ParseError, OSError) as e:
            die(f"无法解析 docx：{e}")
        m, dd, risk = metrics_of(doc)
        locked_reasons: dict[str, int] = {}
        for p in doc.paras:
            for r in p.reasons:
                locked_reasons[r] = locked_reasons.get(r, 0) + 1
        diagnosis = {"doc": {"path": str(path), "name": path.name, "hash": sha256_file(path),
                             "paragraphs": len(doc.paras), "rewritable": m["rewritable"],
                             "locked": len(doc.paras) - m["rewritable"], "locked_reasons": locked_reasons},
                     "repeat": {k: dd[k] for k in ("rate", "covered_chars", "total_chars", "top")},
                     "risk": {k: risk[k] for k in ("high", "mid", "low", "total_score", "dims", "top")}}
        (stage / "diagnosis.json").write_text(json.dumps(diagnosis, ensure_ascii=False, indent=1), encoding="utf-8")
        state = {"engine_version": ENGINE_VERSION, "doc_path": str(path), "doc_hash": diagnosis["doc"]["hash"],
                 "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "original_docx": str(d / "original.docx"),
                 "base_metrics": m, "rounds": []}
        save_state(stage, state)
        archive = None
        if d.exists():
            archive = state_root() / "archive" / f"{d.name}-{time.strftime('%Y%m%d%H%M%S')}-{time.time_ns()}"
            archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(d), str(archive))
        try:
            stage.replace(d)
        except OSError:
            if archive is not None and not d.exists():
                shutil.move(str(archive), str(d))
            raise
    except OSError as e:
        die(f"诊断状态切换失败：{e}")
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    emit({"ok": True, "state_dir": str(d), **diagnosis,
          "next": "rewrite --mode dual|aigc|dedup（生成改写任务）"})


def cmd_rewrite(args) -> None:
    path = Path(args.path).expanduser().resolve()
    d = doc_dir(path)
    state = load_state(d)
    check_source(state)
    mode = args.mode
    if mode not in ROUND_CAPS:
        die("mode 必须是 dual / aigc / dedup")
    if args.style not in ("plain", "academic"):
        die("style 必须是 plain / academic")
    if len(state["rounds"]) >= MAX_ROUNDS:
        die(f"已达五改上限（{MAX_ROUNDS} 轮），请作者复核现有版本后手动定稿", capped=True)
    base_path, base_m = base_info(state, d)
    doc = Docx(base_path)
    dd = dedup_analysis(doc.paras)
    risk = diagnose_paras(doc.paras, dd)
    by = doc.by_pid()
    cand = []
    for p in doc.paras:
        if p.locked:
            continue
        info = risk["per_para"].get(p.pid, {})
        ratio = dd["per_para"].get(p.pid, {}).get("ratio", 0.0)
        score = info.get("score", 0)
        if mode == "dedup" and ratio >= REPEAT_TASK_RATIO:
            cand.append((p.pid, score, ratio))
        elif mode == "aigc" and score >= MID_SCORE:
            cand.append((p.pid, score, ratio))
        elif mode == "dual" and (score >= MID_SCORE or ratio >= REPEAT_TASK_RATIO):
            cand.append((p.pid, score, ratio))
    note = ""
    if mode == "dual" and not any(r >= REPEAT_O9_RATIO for _, _, r in cand):
        note = "没有单段重复占比 ≥45% 的段落，本轮双降任务由 AIGC 痕迹侧驱动（不派 O9）"
    if mode == "dedup" or (mode == "dual" and cand):
        cand.sort(key=lambda t: (-t[2], -t[1], t[0]))
    else:
        cand.sort(key=lambda t: (-t[1], -t[2], t[0]))
    if not cand:
        die("当前基础稿没有可派发的目标段落（重复与痕迹风险均低于任务阈值）", nothingToDo=True)
    pending = state["rounds"] and state["rounds"][-1]["status"] == "awaiting_edits"
    if pending:
        rnd = state["rounds"][-1]
        rnd.update(mode=mode, style=args.style)
        note = (note + "；" if note else "") + "已原地重建当前待回填任务，不新增轮次"
    else:
        rnd = {"n": len(state["rounds"]) + 1, "mode": mode, "style": args.style, "status": "awaiting_edits"}
        state["rounds"].append(rnd)
    cap = ROUND_CAPS[mode][min(rnd["n"], MAX_ROUNDS) - 1]
    selected = cand[:cap]
    rules = PLAIN_RULES if args.style == "plain" else ACADEMIC_RULES
    rd = d / "rounds" / f"r{rnd['n']}"
    rd.mkdir(parents=True, exist_ok=True)
    prompts = []
    for pid, score, ratio in selected:
        p = by[pid]
        info = risk["per_para"].get(pid, {})
        ops = list(info.get("ops", []))
        if ratio >= REPEAT_O9_RATIO and "O9" not in ops:
            ops.append("O9")
        if not ops:
            ops = ["O1"]
        reqs = []
        for ev in info.get("evidence", []):
            reqs.append(f"[{ev['dim']} {ev['name']}] 证据：{'；'.join(ev['samples'])} → 用 {'/'.join(DIM_OPS[ev['dim']])} 消除")
        if ratio >= REPEAT_TASK_RATIO:
            peer = dd["per_para"].get(pid, {})
            reqs.append(f"[消重] 本段 {ratio:.0%} 字符与 {','.join(peer.get('peers', []))} 重复"
                        f"{'（指纹样例：' + peer.get('sample', '') + '）' if peer.get('sample') else ''}"
                        f" → 用 O9 换说法消除字面重合，不得删除事实")
        prompts.append({"pid": pid, "text": p.text, "ops": ops,
                        "risk_score": score, "repeat_ratio": ratio,
                        "requirements": reqs, "style_rules": rules,
                        "constraints": {"length_ratio": [LENGTH_LO, LENGTH_HI],
                                        "entities": "数字/引用/书名/缩写及其出现次数必须与原文完全一致",
                                        "negation": "否定词语义守恒", "no_new_facts": True}})
    pf = rd / "prompts.jsonl"
    pf.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in prompts), encoding="utf-8")
    header = (f"# 改写任务说明（第 {rnd['n']} 轮 · {MODE_NAMES[mode]} · 文风 {args.style}）\n\n"
              f"逐条读取 prompts.jsonl，每行改写一段，输出 edits.jsonl：一行一条 "
              f'{{"pid","text","op"}}，op 取该条 ops 中最主要的一个。\n\n'
              "硬性约束：\n" + "".join(f"- {r}\n" for r in rules) +
              f"- 字数 ∈ 原文 × [{LENGTH_LO}, {LENGTH_HI}]；数字/年份/引用/书名/缩写及次数完全一致；不得增删显式否定，改写后请核对否定范围。\n"
              "- 只改正文；标题、表格、代码、公式、脚注、参考文献一律不动；不新增事实与主观表述。\n"
              "- 总字数控制在原文 ±20% 以内。\n\n"
              "边界：本工具分数为篇内相对指标，不等同任何检测平台判定；改稿必须由作者复核。\n")
    (rd / "prompt_header.md").write_text(header, encoding="utf-8")
    rnd.update(prompts=str(pf), header=str(rd / "prompt_header.md"),
               tasks=[{"pid": t[0], "score": t[1], "ratio": t[2]} for t in selected],
               metrics_before=base_m, base=str(base_path))
    save_state(d, state)
    emit({"ok": True, "round": rnd["n"], "mode": mode, "style": args.style, "base": str(base_path),
          "task_count": len(selected), "cap": cap, "note": note,
          "prompts": str(pf), "header": str(rd / "prompt_header.md"), "state_dir": str(d),
          "tasks": rnd["tasks"],
          "next": "agent 按 prompts.jsonl 写 edits.jsonl → lint → finish"})


def cmd_save_edits(args) -> None:
    path = Path(args.path).expanduser().resolve()
    d = doc_dir(path)
    state = load_state(d)
    if not state["rounds"] or state["rounds"][-1]["status"] != "awaiting_edits":
        die("当前没有待回填的任务轮次，请先运行 rewrite")
    src = Path(args.file)
    rows, errors = read_edits(src)
    if errors:
        die("清单格式校验失败：" + "；".join(errors[:8]), errors=errors)
    if not rows:
        die("清单为空")
    dest = d / "edits.jsonl"
    shutil.copy2(src, dest)
    emit({"ok": True, "saved": str(dest), "count": len(rows), "next": "lint 预检"})


def cmd_lint(args) -> None:
    path = Path(args.path).expanduser().resolve()
    d = doc_dir(path)
    state = load_state(d)
    check_source(state)
    if not state["rounds"] or state["rounds"][-1]["status"] != "awaiting_edits":
        die("当前没有待回填的任务轮次，请先运行 rewrite")
    rnd = state["rounds"][-1]
    edits_path = Path(args.edits) if args.edits else d / "edits.jsonl"
    if not edits_path.exists():
        die(f"清单不存在：{edits_path}；请先写入 edits.jsonl", needEdits=True)
    rows, errors = read_edits(edits_path)
    base_path, _ = base_info(state, d)
    doc = Docx(base_path)
    by = doc.by_pid()
    task_pids = {t["pid"] for t in rnd["tasks"]}
    passed, rejected = [], []
    submitted = {row["pid"] for row in rows}
    for row in rows:
        pid = row["pid"]
        if pid not in by:
            rejected.append({"pid": pid, "gates": [{"gate": "定位", "detail": "pid 不存在于基础稿"}]})
            continue
        if pid not in task_pids:
            rejected.append({"pid": pid, "gates": [{"gate": "任务范围", "detail": "该段不在本轮任务清单内"}]})
            continue
        fails = lint_edit(by[pid], row["text"], row["op"])
        if fails:
            rejected.append({"pid": pid, "gates": fails})
        else:
            passed.append(pid)
    for pid in sorted(task_pids - submitted):
        rejected.append({"pid": pid, "gates": [{"gate": "任务覆盖", "detail": "本轮任务未提交"}]})
    passed_all = not errors and not rejected
    result = {"ok": passed_all, "check_status": check_state(passed_all, bool(errors)),
              "round": rnd["n"], "errors": errors,
              "passed": passed, "rejected": rejected,
              "passed_count": len(passed), "rejected_count": len(rejected)}
    lp = d / "rounds" / f"r{rnd['n']}" / "lint.json"
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    result["lint_path"] = str(lp)
    result["next"] = "finish 回填并复检" if result["ok"] else "按 rejected 中的门禁原因修正清单后重新 lint"
    emit(result)
    if not result["ok"]:
        sys.exit(2)


def cmd_finish(args) -> None:
    path = Path(args.path).expanduser().resolve()
    d = doc_dir(path)
    state = load_state(d)
    check_source(state)
    if not state["rounds"] or state["rounds"][-1]["status"] != "awaiting_edits":
        die("当前没有待回填的任务轮次（本轮可能已回填），请先运行 rewrite 生成下一轮")
    rnd = state["rounds"][-1]
    rd = d / "rounds" / f"r{rnd['n']}"
    edits_path = Path(args.edits) if args.edits else d / "edits.jsonl"
    if not edits_path.exists():
        die(f"清单不存在：{edits_path}", needEdits=True)
    rows, errors = read_edits(edits_path)
    base_path, base_m = base_info(state, d)
    base = Docx(base_path)
    by = base.by_pid()
    task_pids = {t["pid"] for t in rnd["tasks"]}
    passed, rejected, edits = [], [], {}
    submitted = {row["pid"] for row in rows}
    for row in rows:
        pid = row["pid"]
        if pid not in by or pid not in task_pids:
            rejected.append({"pid": pid, "gates": [{"gate": "任务范围", "detail": "pid 不在本轮任务内"}]})
            continue
        fails = lint_edit(by[pid], row["text"], row["op"])
        if fails:
            rejected.append({"pid": pid, "gates": fails})
        else:
            passed.append(pid)
            edits[pid] = row["text"]
    for pid in sorted(task_pids - submitted):
        rejected.append({"pid": pid, "gates": [{"gate": "任务覆盖", "detail": "本轮任务未提交"}]})
    shutil.copy2(edits_path, rd / "edits.jsonl")
    reasons = []
    verdict = "rejected"
    after_m = None
    docx_out = None
    candidate_checked = False
    incomplete = bool(errors or rejected or set(passed) != task_pids)
    if errors:
        reasons.append("清单含非法行：" + "；".join(errors[:5]))
    if incomplete and not args.allow_partial:
        missing = sorted(task_pids - submitted)
        reasons.append(f"本轮 {len(task_pids)} 个任务未全部通过（未提交 {len(missing)} 段，被拒 {len(rejected)} 段）；"
                       "如确认跳过这些段，请明确使用 --allow-partial")
    if not edits:
        reasons.append("没有任何通过门禁的改写，未生成改稿")
    elif errors:
        pass
    elif incomplete and not args.allow_partial:
        pass
    else:
        try:
            new_xml = base.apply_edits(edits)
            cand = rd / "candidate.docx"
            base.save(new_xml, cand)
            out = Docx(cand)
            candidate_checked = True
            if len(out.paras) != len(base.paras):
                reasons.append("篇级复检失败：段落数量变化，整篇回退")
            else:
                ob = out.by_pid()
                for p in base.paras:
                    q = ob[p.pid]
                    if q.sig != p.sig:
                        reasons.append(f"篇级复检失败：{p.pid} 格式签名变化，整篇回退")
                        break
                    if p.locked and q.text != p.text:
                        reasons.append(f"篇级复检失败：锁定段 {p.pid} 文本被改动，整篇回退")
                        break
            if not reasons:
                after_m, _, _ = metrics_of(out)
                mode = rnd["mode"]

                if mode in ("dedup", "dual") and not repeat_acceptable(base_m["repeat_rate"], after_m["repeat_rate"]):
                    reasons.append(f"篇内重复率上升超过 {REPEAT_ACCEPT_TOLERANCE:.1%} 容忍值"
                                   f"（{base_m['repeat_rate']:.2%} → {after_m['repeat_rate']:.2%}），整篇回退")
                if mode in ("aigc", "dual"):
                    if after_m["risk_total"] > base_m["risk_total"]:
                        reasons.append(f"痕迹风险分上升（{base_m['risk_total']} → {after_m['risk_total']}），整篇回退")
                    if after_m["high"] > base_m["high"]:
                        reasons.append(f"高风险段数上升（{base_m['high']} → {after_m['high']}），整篇回退")
                if not reasons:
                    verdict = "accepted"
                    docx_out = rd / "accepted.docx"
                    cand.replace(docx_out)
                else:
                    cand.replace(rd / "rejected.docx")
        except ValueError as e:
            reasons.append(str(e))
    partial = verdict == "accepted" and incomplete
    status = check_state(verdict == "accepted", bool(errors or (incomplete and not args.allow_partial) or not edits) or not candidate_checked)
    rnd.update(status=verdict, check_status=status, finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
               passed=passed, rejected=rejected, reasons=reasons, partial=partial,
               metrics_after=after_m, docx=str(docx_out) if docx_out else None)
    rows_cmp = []
    for pid in passed:
        old, new = by[pid].text, edits[pid]
        rows_cmp.append({"pid": pid, "old": old, "new": new,
                         "old_len": vis_len(old), "new_len": vis_len(new),
                         "old_sents": len(sentences(old)), "new_sents": len(sentences(new))})
    comparison = {"round": rnd["n"], "mode": rnd["mode"], "verdict": verdict,
                  "check_status": status, "partial": partial,
                  "metrics_before": base_m, "metrics_after": after_m,
                  "rows": rows_cmp, "rejected": rejected, "reasons": reasons}
    (rd / "comparison.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=1), encoding="utf-8")
    report = build_report(state, rnd, comparison)
    (rd / "report.md").write_text(report, encoding="utf-8")
    if verdict == "accepted":
        state["base_metrics"] = after_m
    save_state(d, state)
    emit({"ok": verdict == "accepted", "verdict": verdict, "check_status": status,
          "partial": partial, "round": rnd["n"], "mode": rnd["mode"],
          "passed_count": len(passed), "rejected_count": len(rejected), "reasons": reasons,
          "metrics_before": base_m, "metrics_after": after_m,
          "docx": str(docx_out) if docx_out else None,
          "report": str(rd / "report.md"), "comparison": str(rd / "comparison.json"),
          "state_dir": str(d),
          "next": "作者复核改稿；如需继续，rewrite 生成下一轮（最多五改）" if verdict == "accepted"
                  else "按 reasons 修正后重新 rewrite/lint/finish"})
    if verdict != "accepted":
        sys.exit(2)


def build_report(state: dict, rnd: dict, cmp: dict) -> str:
    mb, ma = cmp["metrics_before"], cmp["metrics_after"]
    verdict_text = ("通过（部分完成，已跳过未通过/未提交段）" if cmp.get("partial")
                    else "通过（accepted）" if rnd["status"] == "accepted"
                    else "未通过（候选稿篇级复检未过，已回退）" if cmp.get("check_status") == "not_passed"
                    else "拒绝（输入或任务集不合格，未回填；基础稿不变）")
    lines = [f"# 论文双降报告 · 第 {rnd['n']} 轮 · {MODE_NAMES[rnd['mode']]}",
             "", f"- 文档：{state['doc_path']}", f"- 判定：**{verdict_text}**",
             f"- 通过门禁改写：{len(cmp['rows'])} 段；被拒：{len(cmp['rejected'])} 段", ""]
    if cmp["reasons"]:
        lines += ["## 未通过 / 拒绝原因", ""] + [f"- {r}" for r in cmp["reasons"]] + [""]
    lines += ["## 指标前后对比", "", "| 指标 | 基础稿 | 改稿 |", "| --- | --- | --- |"]
    if ma:
        lines += [f"| 篇内重复率 | {mb['repeat_rate']:.2%} | {ma['repeat_rate']:.2%} |",
                  f"| 痕迹风险总分 | {mb['risk_total']} | {ma['risk_total']} |",
                  f"| 高/中/低分段段数 | {mb['high']}/{mb['mid']}/{mb['low']} | {ma['high']}/{ma['mid']}/{ma['low']} |"]
    else:
        lines += [f"| 篇内重复率 | {mb['repeat_rate']:.2%} | — |",
                  f"| 痕迹风险总分 | {mb['risk_total']} | — |"]
    lines += ["", "## 三栏对照", "", "| pid | 原文 | 改文 | 字数 | 句数 |", "| --- | --- | --- | --- | --- |"]
    for r in cmp["rows"]:
        old = r["old"].replace("|", "\\|").replace("\n", " ")
        new = r["new"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r['pid']} | {old} | {new} | {r['old_len']}→{r['new_len']} | {r['old_sents']}→{r['new_sents']} |")
    if cmp["rejected"]:
        lines += ["", "## 被拒段落与门禁", ""]
        for r in cmp["rejected"]:
            detail = "；".join(g["gate"] + "：" + g["detail"] for g in r["gates"])
            lines.append(f"- {r['pid']}：{detail}")
    lines += ["", "## 边界声明", "",
              "- 早期说明中的 6 维是旧口径；当前诊断展示 9 个文风规则维度，用于定位改写目标，并非 9 个独立检测模型。",
              "- 风险分与重复率均为篇内相对指标，只用于同篇排序，跨文档不可比，不是任何平台的检测概率。",
              "- 本工具不模拟知网/维普/格子达等平台，不承诺通过率；门禁仅用于筛选候选稿，通过不等于原稿更优，请比较后替换。",
              f"- 重复率验收容忍上升 {REPEAT_ACCEPT_TOLERANCE:.1%}（0.2 个百分点）；风险分持平可通过，上升则回退。",
              "- 标题、表格、代码、公式、脚注、参考文献一律未改；改稿必须由作者逐段复核。", ""]
    return "\n".join(lines)


def cmd_status(args) -> None:
    root = state_root()
    if args.path:
        path = Path(args.path).expanduser().resolve()
        d = doc_dir(path)
        state = load_state(d)
        src = Path(state["doc_path"])
        source_ok = src.exists() and sha256_file(src) == state["doc_hash"]
        base_path, base_m = base_info(state, d)
        emit({"ok": True, "state_dir": str(d), "doc_path": state["doc_path"],
              "source_unchanged": source_ok, "base": str(base_path), "base_metrics": base_m,
              "rounds": [{"n": r["n"], "mode": r.get("mode"), "style": r.get("style"),
                          "status": r.get("status"), "check_status": r.get("check_status", "pending"),
                          "partial": r.get("partial", False), "passed": r.get("passed", []),
                          "reasons": r.get("reasons", []),
                          "docx": str(d / "rounds" / f"r{r['n']}" / "accepted.docx") if r.get("docx") else None}
                         for r in state["rounds"]],
              "pending": bool(state["rounds"]) and state["rounds"][-1]["status"] == "awaiting_edits",
              "diagnosis": str(d / "diagnosis.json")})
    else:
        docs = []
        base = root / "docs"
        if base.exists():
            for sd in base.iterdir():
                f = sd / "state.json"
                if f.exists():
                    try:
                        st = json.loads(f.read_text(encoding="utf-8"))
                        docs.append({"doc_path": st["doc_path"], "state_dir": str(sd),
                                     "rounds": len(st["rounds"]), "mtime": f.stat().st_mtime})
                    except (json.JSONDecodeError, KeyError):
                        continue
        docs.sort(key=lambda x: -x["mtime"])
        emit({"ok": True, "state_dir": str(root), "docs": docs[:12]})


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="engine.py", description="论文双降引擎")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    for name in ("diagnose", "rewrite", "lint", "finish", "save-edits", "status"):
        p = sub.add_parser(name)
        p.add_argument("--path", help="论文 .docx 绝对路径（status 可省略以列出最近文档）")
        if name == "rewrite":
            p.add_argument("--mode", default="dual")
            p.add_argument("--style", default="plain")
        if name in ("lint", "finish"):
            p.add_argument("--edits", help="edits.jsonl 路径（默认状态目录内）")
        if name == "finish":
            p.add_argument("--allow-partial", action="store_true", help="明确允许跳过被拒或未提交的任务段")
        if name == "save-edits":
            p.add_argument("--file", required=True, help="待校验保存的 JSONL 文件")
    args = ap.parse_args()
    if args.cmd == "doctor":
        cmd_doctor(args)
        return
    if args.cmd != "status" and not args.path:
        die("该子命令需要 --path")
    if args.cmd == "status" and not args.path:
        cmd_status(args)
        return
    path = Path(args.path).expanduser().resolve()
    with state_lock(path):
        {"diagnose": cmd_diagnose, "rewrite": cmd_rewrite,
         "save-edits": cmd_save_edits, "lint": cmd_lint, "finish": cmd_finish,
         "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    main()