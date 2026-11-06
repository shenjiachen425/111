#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""基于两份访谈记录，整理《会员权益域典型事件资料》docx（简洁排版）。
内容严格来自访谈素材；未核实/未量化的项标注为「待验证假设」，不臆造数字。
"""
import os
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

OUT = "/workspace/会员权益域典型事件资料.docx"

CN_FONT = "微软雅黑"   # 中文显示字体
BODY = "宋体"          # 正文中文
NAVY = RGBColor(0x2D, 0x3E, 0x63)
DARK = RGBColor(0x1F, 0x2A, 0x44)
GREY = RGBColor(0x6B, 0x77, 0x94)
ACCENT = RGBColor(0xFF, 0x6B, 0x35)


def set_cn(run, font=CN_FONT):
    run.font.name = font
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), font)
    rfonts.set(qn("w:ascii"), font)
    rfonts.set(qn("w:hAnsi"), font)


def shade(cell, hexcolor):
    tcpr = cell._tc.get_or_add_tcPr()
    sh = OxmlElement("w:shd")
    sh.set(qn("w:val"), "clear")
    sh.set(qn("w:color"), "auto")
    sh.set(qn("w:fill"), hexcolor)
    tcpr.append(sh)


def add_para(doc, text, size=10.5, bold=False, color=DARK, align=None,
             before=4, after=4, font=BODY, line=1.4):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = line
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.color.rgb = color
    set_cn(r, font)
    return p


def add_heading(doc, num, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.2
    r = p.add_run(f"{num}  {text}")
    r.font.size = Pt(13)
    r.font.bold = True
    r.font.color.rgb = NAVY
    set_cn(r, CN_FONT)
    # 底部边框
    pPr = p._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "2")
    bottom.set(qn("w:color"), "2D3E63")
    pbdr.append(bottom)
    pPr.append(pbdr)
    return p


def add_bullet(doc, text, sub=None):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.35
    r = p.add_run(text)
    r.font.size = Pt(10.5)
    r.font.color.rgb = DARK
    set_cn(r, BODY)
    if sub:
        r2 = p.add_run(sub)
        r2.font.size = Pt(9.5)
        r2.font.color.rgb = GREY
        set_cn(r2, BODY)


def build():
    doc = Document()
    # 页面与默认字体
    section = doc.sections[0]
    section.top_margin = Cm(2.2); section.bottom_margin = Cm(2.2)
    section.left_margin = Cm(2.4); section.right_margin = Cm(2.4)
    style = doc.styles["Normal"]
    style.font.size = Pt(10.5)
    style.font.name = BODY
    style.element.rPr.rFonts.set(qn("w:eastAsia"), BODY)

    # 标题
    t = doc.add_paragraph(); t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    t.paragraph_format.space_after = Pt(2)
    rt = t.add_run("会员权益域典型事件资料")
    rt.font.size = Pt(20); rt.font.bold = True; rt.font.color.rgb = NAVY; set_cn(rt, CN_FONT)

    mt = doc.add_paragraph(); mt.alignment = WD_ALIGN_PARAGRAPH.CENTER
    mt.paragraph_format.space_after = Pt(8)
    rm = mt.add_run("资料来源：会员运营经理顾明岚访谈记录（turn-8、turn-10） · 整理日期：2026-11-06")
    rm.font.size = Pt(9.5); rm.font.color.rgb = GREY; set_cn(rm, BODY)

    # 数据口径说明框
    note = doc.add_paragraph()
    note.paragraph_format.space_after = Pt(8)
    note.paragraph_format.line_spacing = 1.4
    rn = note.add_run("【数据口径说明】")
    rn.font.size = Pt(10); rn.font.bold = True; rn.font.color.rgb = ACCENT; set_cn(rn, CN_FONT)
    rn2 = note.add_run(
        " 本资料所有数字均来自访谈中明确给出的量化依据（见末尾「引用事实索引」）。"
        "凡访谈对象未核实、未单独量化的项，一律标注为「待验证假设」，不做臆造数字。"
        "典型事件场景为基于主流程拼接的「可讲场景描述」，非真实单据样本。")
    rn2.font.size = Pt(10); rn2.font.color.rgb = DARK; set_cn(rn2, BODY)

    # 一、业务背景与主流程
    add_heading(doc, "一、", "业务背景与主流程")
    add_para(doc,
             "会员权益解释是门店高频、跨角色（会员—收银—运营）的协同场景。当前以人工核验与口头解释为主，"
             "缺乏统一口径与自动化支撑。业务主流程已被明确拆为五个节点：")
    for i, node in enumerate(["会员识别", "权益核验", "优惠计算", "收银确认", "售后反馈"], 1):
        add_bullet(doc, f"节点{i}：{node}", "（各节点等待与处理耗时尚未分别量出 [f01]）")

    # 二、已量化的核心指标
    add_heading(doc, "二、", "已量化的核心指标")
    add_para(doc, "访谈中仅两项指标具备量化依据，且均给出「现状 / 目标」对照：", after=6)
    table = doc.add_table(rows=1, cols=5)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = ["指标", "现状", "目标", "改善幅度（计算值）", "来源"]
    widths = [Cm(3.2), Cm(2.6), Cm(2.4), Cm(3.4), Cm(2.0)]
    for j, h in enumerate(hdr):
        c = table.rows[0].cells[j]
        c.text = ""
        rp = c.paragraphs[0].add_run(h)
        rp.font.size = Pt(9.5); rp.font.bold = True; rp.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        set_cn(rp, CN_FONT)
        shade(c, "2D3E63")
    rows = [
        ["会员权益投诉量", "每月 240 件", "每月 100 件", "约 −58.3%", "f00"],
        ["权益核验时长", "平均 3 分钟（180 秒）", "40 秒", "约 −77.8%", "f00"],
    ]
    for row in rows:
        cells = table.add_row().cells
        for j, v in enumerate(row):
            cells[j].text = ""
            rp = cells[j].paragraphs[0].add_run(v)
            rp.font.size = Pt(9.5); rp.font.color.rgb = DARK; set_cn(rp, BODY)
            if j == 0:
                rp.font.bold = True
            if j % 2 == 1:
                shade(cells[j], "F4F6FB")
    for j, w in enumerate(widths):
        for r in table.rows:
            r.cells[j].width = w
    add_para(doc, "注：「改善幅度」为按现状与目标计算的派生值，用于直观呈现差距，非访谈原始口径。",
             size=9, color=GREY, before=4, after=6)

    # 三、典型事件场景
    add_heading(doc, "三、", "典型事件场景（场景描述，非真实单据样本）")
    add_para(doc,
             "可讲述的场景链路：会员在门店对同一张优惠券的权益解释产生疑问 → 收银现场核验较慢，"
             "还需打电话向运营确认 → 最终问题流转至售后反馈环节。该链路能说明痛点集中在「解释口径不一 + "
             "核验慢 + 跨角色来回」上。", after=4)
    add_para(doc,
             "边界声明：具体到「哪家门店、哪类券、单笔耗时多少」访谈对象尚未核实，本场景仅用于说明痛点位置，"
             "不可作为真实案例细节或试点验收样本。", size=9.5, color=GREY, before=2, after=6)

    # 四、已识别但未量化的问题（待验证假设）
    add_heading(doc, "四、", "已识别但未量化的问题（待验证假设）")
    add_para(doc, "以下问题在业务现场确实存在，但访谈对象尚未分别量出频次与耗时，按数据诚信原则列为待验证假设：", after=4)
    add_bullet(doc, "收银反复打电话问运营", "——核验依赖人工确认，效率低")
    add_bullet(doc, "同一张券在「小程序」与「门店」解释不一致", "——口径不统一，易引发会员质疑")
    add_bullet(doc, "售后反馈来回确认", "——同一问题在环节间反复流转，处理链路长")

    # 五、数据缺口与试点范围影响
    add_heading(doc, "五、", "数据缺口与试点范围影响")
    add_para(doc,
             "主流程五个节点的等待与处理时间尚未分别量化，管理层无法定位瓶颈究竟卡在哪一段。"
             "这直接卡住试点范围的划定：不清楚哪段最耗时，试点就只能凭感觉划范围 [f01]。")

    # 六、下一步采集建议
    add_heading(doc, "六、", "下一步采集建议（来自访谈对象）")
    add_bullet(doc, "先按节点拆耗时", "——作为「待验证假设」先行建立，再逐步用数据校准")
    add_bullet(doc, "数据来源分工", "——林亦舟提供现场操作记录、秦芮确认系统能否取到节点时间、傅景先过数据边界/隐私")
    add_bullet(doc, "区分两种采集口径", "——「立项排序」与「试点验收基线」口径不同，需先明确用途再采集")

    # 七、引用事实索引
    add_heading(doc, "七、", "引用事实索引")
    add_para(doc, "[retail-membership-f00] 会员权益投诉每月 240 件（目标 100 件）；权益核验平均 3 分钟（目标 40 秒）。", after=2)
    add_para(doc, "[retail-membership-f01] 主流程为「会员识别→权益核验→优惠计算→收银确认→售后反馈」，各节点耗时尚未分别量出。", after=2)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc.save(OUT)
    print("SAVED:", OUT)


if __name__ == "__main__":
    build()
