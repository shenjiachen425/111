#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""生成《拾光连锁·会员权益智能体 MVP — 上线交付运行手册》docx。
内容严格基于 /workspace/mvp 真实代码（app.py / db.py / core/* / data/knowledge.json）。
架构章节嵌入已改造的流程图（融入典型事例问题版）。
"""
import os
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

OUT = "/workspace/拾光连锁会员权益智能体MVP-上线交付运行手册.docx"

CN_FONT = "微软雅黑"
BODY = "宋体"
NAVY = RGBColor(0x2D, 0x3E, 0x63)
DARK = RGBColor(0x1F, 0x2A, 0x44)
GREY = RGBColor(0x6B, 0x77, 0x94)
ACCENT = RGBColor(0xE8, 0x74, 0x3B)
GREEN = RGBColor(0x24, 0x7A, 0x4C)


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
    sh.set(qn("w:val"), "clear"); sh.set(qn("w:color"), "auto"); sh.set(qn("w:fill"), hexcolor)
    tcpr.append(sh)


def add_para(doc, text, size=10.5, bold=False, color=DARK, align=None,
             before=4, after=4, font=BODY, line=1.45, italic=False):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = line
    r = p.add_run(text)
    r.font.size = Pt(size); r.font.bold = bold; r.font.italic = italic
    r.font.color.rgb = color; set_cn(r, font)
    return p


def add_heading(doc, num, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(14)
    p.paragraph_format.space_after = Pt(5)
    p.paragraph_format.line_spacing = 1.2
    r = p.add_run(f"{num}  {text}")
    r.font.size = Pt(13.5); r.font.bold = True; r.font.color.rgb = NAVY; set_cn(r, CN_FONT)
    pPr = p._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single"); bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "2"); bottom.set(qn("w:color"), "2D3E63")
    pbdr.append(bottom); pPr.append(pbdr)
    return p


def add_bullet(doc, text, sub=None, color=DARK):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.35
    r = p.add_run(text)
    r.font.size = Pt(10.5); r.font.color.rgb = color; set_cn(r, BODY)
    if sub:
        r2 = p.add_run(sub); r2.font.size = Pt(9.5); r2.font.color.rgb = GREY; set_cn(r2, BODY)


def add_table(doc, headers, rows, widths, header_color="2D3E63", zebra="F4F6FB"):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for j, h in enumerate(headers):
        c = t.rows[0].cells[j]; c.text = ""
        rp = c.paragraphs[0].add_run(h)
        rp.font.size = Pt(9.5); rp.font.bold = True; rp.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        set_cn(rp, CN_FONT); shade(c, header_color)
    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        for j, v in enumerate(row):
            cells[j].text = ""
            rp = cells[j].paragraphs[0].add_run(str(v))
            rp.font.size = Pt(9.3); rp.font.color.rgb = DARK; set_cn(rp, BODY)
            if j == 0:
                rp.font.bold = True
            if ri % 2 == 1:
                shade(cells[j], zebra)
    for j, w in enumerate(widths):
        for r in t.rows:
            r.cells[j].width = Cm(w)
    return t


def add_img(doc, path, width_cm, caption=None):
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6); p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(path, width=Cm(width_cm))
    if caption:
        cp = doc.add_paragraph(); cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = cp.add_run(caption); r.font.size = Pt(9); r.font.color.rgb = GREY; set_cn(r, BODY)
        cp.paragraph_format.space_after = Pt(6)


def build():
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(2.2); section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(2.4); section.right_margin = Cm(2.4)
    style = doc.styles["Normal"]
    style.font.size = Pt(10.5); style.font.name = BODY
    style.element.rPr.rFonts.set(qn("w:eastAsia"), BODY)

    # ===== 封面 =====
    for _ in range(3):
        doc.add_paragraph()
    t = doc.add_paragraph(); t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rt = t.add_run("拾光连锁 · 会员权益智能体 MVP")
    rt.font.size = Pt(24); rt.font.bold = True; rt.font.color.rgb = NAVY; set_cn(rt, CN_FONT)
    t2 = doc.add_paragraph(); t2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rt2 = t2.add_run("上线交付运行手册")
    rt2.font.size = Pt(20); rt2.font.bold = True; rt2.font.color.rgb = ACCENT; set_cn(rt2, CN_FONT)
    for _ in range(2):
        doc.add_paragraph()
    meta = doc.add_paragraph(); meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for line, sz in [("文档版本：V1.0（对应系统版本 v2026.11）", 11),
                     ("编制：智能体项目组（基于六人访谈痛点）", 10.5),
                     ("编制日期：2026-11-06", 10.5),
                     ("适用系统：会员权益智能体 MVP（员工端 + 管理端）", 10.5)]:
        rr = meta.add_run(line + "\n"); rr.font.size = Pt(sz); rr.font.color.rgb = GREY; set_cn(rr, BODY)
    for _ in range(3):
        doc.add_paragraph()
    note = doc.add_paragraph(); note.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rn = note.add_run("本文档用于指导系统的安装部署、配置、运行、运维与验收；所有量化指标为\n"
                     "访谈设定的「目标/验收基线」，非系统已自动达成的运行数据。")
    rn.font.size = Pt(9.5); rn.font.color.rgb = GREY; rn.font.italic = True; set_cn(rn, BODY)
    doc.add_page_break()

    # ===== 0. 修订记录 / 目录说明 =====
    add_heading(doc, "0", "文档说明与修订记录")
    add_para(doc, "本手册面向实施工程师、运维人员与验收负责人，提供从部署到日常运维的完整操作指引。"
                 "系统为 MVP（最小可行产品）版本，采用规则化 Agent 路由，未接入外部大模型；"
                 "知识库为结构化 JSON（非向量检索），生产可按既定接口升级。")
    add_table(doc, ["版本", "日期", "修订内容", "编制"],
              [["V1.0", "2026-11-06", "首版：部署/配置/运维/验收全指引，嵌入改造后流程图", "项目组"]],
              [1.6, 2.4, 9.0, 2.0])

    # ===== 1. 概述 =====
    add_heading(doc, "1", "系统概述")
    add_heading(doc, "1.1", "背景与目标")
    add_para(doc, "针对六人访谈识别的会员权益运营痛点（解释口径不一、权益核验慢、缺乏审计、"
                 "异常无转人工、规则缺口、版本化缺失、数据孤岛），以 MVP 方式在「员工端 + 管理端」"
                 "两个端口落地一套可运行、可演示、可追溯的会员权益智能体系统。")
    add_para(doc, "核心目标：用统一知识库单一出口消除口径分裂；用核销码 + 订单金额秒级计价替代"
                 "现场人工核验；用结构化审计链（业务编号 + 事实引用 + 版本号）支撑事后追溯；"
                 "用强制转人工兜底规则缺口。", color=DARK)

    add_heading(doc, "1.2", "读者与适用范围")
    add_table(doc, ["角色", "关注章节"],
              [["实施 / 运维工程师", "第 3–5 章（环境、部署、配置）、第 9–10 章（运维、故障）"],
               ["运营 / 业务管理员", "第 6 章（操作指南）、第 8 章（知识库运维）"],
               ["验收负责人", "第 12 章（验收清单）、第 1.4 / 2.4 节（痛点与红线）"],
               ["安全 / 合规", "第 11 章（安全与合规）"]],
              [4.5, 11.5])

    add_heading(doc, "1.3", "三大核心组件")
    add_bullet(doc, "工程架构", "—— 前端双端口 + FastAPI 无状态后端 + SQLite 数据层，按路径与角色区分双端。")
    add_bullet(doc, "Agent 框架", "—— 工具调用型智能体（Tool / IntentRouter / Agent / Memory / LLMAdapter 接口）。")
    add_bullet(doc, "知识库", "—— 结构化、版本化、可发版的单口径出口（data/knowledge.json）。")

    add_heading(doc, "1.4", "解决的核心痛点（对照访谈）")
    add_table(doc,
              ["编号", "痛点", "系统解法", "目标指标"],
              [["PT-1", "解释不一致", "知识库单一出口 + 版本化；查询附引用与版本", "—"],
               ["PT-2", "核验慢", "员工端输入核销码+金额秒级出价", "3 分钟 → 40 秒（目标）"],
               ["PT-3", "投诉高", "解释一致 + 快速核销 + 转人工兜底", "240 → 100 件/月（目标）"],
               ["PT-4", "规则缺口（兑换券退货）", "Agent 强制转人工，管理端处理台跟踪", "—"],
               ["PT-5/7", "售后 / 数据孤岛", "管理端审计看板 + 转人工处理台统一视图", "—"],
               ["PT-6", "版本化缺失", "知识库版本管理 + 发版快照", "—"]],
              [1.4, 4.2, 7.0, 3.4])
    add_para(doc, "注：PT-2、PT-3 的指标为访谈设定目标，需经试点采集基线后作为验收对照，"
                  "非系统上线即自动达成。", size=9, color=GREY)

    # ===== 2. 系统架构 =====
    add_heading(doc, "2", "系统架构")
    add_para(doc, "系统采用分层、模块化、可演进设计，单一代码库同时承载员工端与管理端，"
                  "通过路径与 JWT 角色区分，从架构层面规避口径分裂。")
    add_table(doc,
              ["层级", "组件", "职责"],
              [["前端", "employee.html / manager.html", "员工端（查询/核销/代生成码/转人工）、管理端（知识库/审计/处理台/概览）"],
               ["接入层", "FastAPI（app.py）", "双端路由 + API；HTTPS(SSE) + JWT；页面 no-cache"],
               ["核心层", "core/agent_framework", "Agent 编排、意图路由、工具、记忆、LLM 适配器（接口）"],
               ["核心层", "core/knowledge_base", "结构化知识库：版本化 / 检索 / 发版快照"],
               ["核心层", "core/auth", "JWT 角色鉴权（employee / manager），多源 token"],
               ["核心层", "core/util", "业务编号(biz_id) / 核销码 / 时间 / 金额格式化"],
               ["数据层", "db.py（SQLite）", "sessions / messages / redemptions / escalations / kb_versions"]],
              [1.8, 5.2, 9.0])
    add_para(doc, "完整业务流程与双端口闭环，详见下图（已融入典型事例问题映射）：", before=6)
    add_img(doc, "/workspace/flow2_dualport.png", 15.5,
            "图2 双端口智能体闭环 × 各端典型问题（含审计链 / 鉴权 / 量化背景）")
    add_img(doc, "/workspace/flow1_business.png", 15.5,
            "图1 业务主流程 × 典型问题落点 × 异常升级（含审计链）")

    add_heading(doc, "2.4", "关键设计要点与红线")
    add_bullet(doc, "单一口径出口", "—— 所有解释/计算/选券/核销源自 data/knowledge.json 的 COUPONS 数据。")
    add_bullet(doc, "无状态 JWT + 多源 token", "—— body.token 首选 → X-Token → Authorization → 查询参数，规避网关不透传 Authorization 头。")
    add_bullet(doc, "页面 no-cache", "—— 根治浏览器缓存误显示。")
    add_bullet(doc, "组件可独立替换", "—— 知识库可升级为向量检索，Agent 可接入真实 LLM，体系不变。")
    add_para(doc, "红线（强制）：不自主修改会员资产；接口仅可读；对外承诺 / 资产变更须审批人确认。",
             color=ACCENT, bold=True, before=4)

    # ===== 3. 环境要求 =====
    add_heading(doc, "3", "环境要求")
    add_table(doc,
              ["类别", "要求"],
              [["操作系统", "Linux（Ubuntu 20.04+ 验证）；macOS / Windows 亦可运行"],
               ["Python", "3.11（已验证）；需支持标准库 sqlite3"],
               ["第三方依赖", "fastapi、uvicorn（pip 安装，无其它强制依赖）"],
               ["数据库", "SQLite（随 Python 自带，无需独立数据库服务）"],
               ["端口", "默认 8001；可通过环境变量 PORT 覆盖"],
               ["浏览器", "Chrome / Edge / Safari 现代版本（管理端/员工端为纯前端 HTML）"]],
              [3.2, 13.8])
    add_para(doc, "说明：仓库当前未提供 requirements.txt。请按第 4.2 节安装依赖；"
                  "如需要可自建 requirements.txt（fastapi、uvicorn）。", size=9.5, color=GREY)

    # ===== 4. 安装与部署 =====
    add_heading(doc, "4", "安装与部署")
    add_heading(doc, "4.1", "获取代码与目录结构")
    add_para(doc, "项目根目录为 mvp/，结构如下：")
    code = ("mvp/\n"
            "├── app.py                # FastAPI 入口（双端路由 + API）\n"
            "├── db.py                 # SQLite 数据层（审计/核销/转人工/版本）\n"
            "├── README.md             # 工程架构说明\n"
            "├── core/\n"
            "│   ├── agent_framework.py # Agent 框架（Tool/IntentRouter/Agent/Memory）\n"
            "│   ├── knowledge_base.py  # 知识库（版本化/检索/发版）\n"
            "│   ├── auth.py            # JWT 角色鉴权\n"
            "│   └── util.py            # 工具函数\n"
            "├── data/\n"
            "│   └── knowledge.json     # 结构化知识库（4 类券 + 版本/来源）\n"
            "└── static/\n"
            "    ├── employee.html      # 员工端\n"
            "    └── manager.html       # 管理端")
    cp = doc.add_paragraph()
    rp = cp.add_run(code)
    rp.font.name = "Consolas"; rp.font.size = Pt(8.8); rp.font.color.rgb = DARK
    rpr = rp._element.get_or_add_rPr(); rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts"); rpr.append(rf)
    rf.set(qn("w:eastAsia"), "宋体")
    cp.paragraph_format.line_spacing = 1.1

    add_heading(doc, "4.2", "安装依赖")
    add_para(doc, "在 mvp/ 目录下执行：")
    add_para(doc, "pip install fastapi uvicorn", font="Consolas", size=10, color=NAVY, before=2, after=2)

    add_heading(doc, "4.3", "初始化数据库")
    add_para(doc, "数据库随服务首次启动自动初始化（db.init_db() 建表），无需手工建库。"
                  "数据文件为 mvp.db（位于 mvp/ 目录），首次启动自动创建。")

    add_heading(doc, "4.4", "启动服务")
    add_para(doc, "方式一（直接运行，默认 8001）：")
    add_para(doc, "cd mvp && python app.py", font="Consolas", size=10, color=NAVY, before=2, after=2)
    add_para(doc, "方式二（uvicorn，可指定端口）：")
    add_para(doc, "cd mvp && PORT=8001 uvicorn app:app --host 0.0.0.0 --port 8001",
             font="Consolas", size=10, color=NAVY, before=2, after=2)
    add_para(doc, "启动成功后控制台监听 8001；员工端与登录入口见 4.6。")

    add_heading(doc, "4.5", "进程守护（生产建议）")
    add_para(doc, "MVP 演示可用 nohup 临时守护；生产环境建议使用 systemd / supervisor 托管，"
                  "示例如下（systemd 单元 /etc/systemd/system/mvp.service 片段）：")
    svc = ("[Unit]\nDescription=Shiguang MVP Member Benefits Agent\n"
           "[Service]\nWorkingDirectory=/opt/mvp\n"
           "ExecStart=/usr/bin/python3 app.py\nEnvironment=PORT=8001\n"
           "Restart=always\nUser=app\n"
           "[Install]\nWantedBy=multi-user.target")
    sp = doc.add_paragraph(); sr = sp.add_run(svc)
    sr.font.name = "Consolas"; sr.font.size = Pt(8.8); sr.font.color.rgb = DARK
    sp.paragraph_format.line_spacing = 1.1

    add_heading(doc, "4.6", "访问入口与演示账号")
    add_table(doc,
              ["端", "地址", "账号", "口令", "角色"],
              [["员工端", "http://<服务器IP>:8001/employee", "employee01", "Employee@123456", "employee"],
               ["管理端", "http://<服务器IP>:8001/manager", "manager01", "Manager@123456", "manager"]],
              [1.6, 6.2, 2.6, 3.4, 2.2])
    add_para(doc, "安全提示：演示账号为弱口令，上线前务必在 core/auth.py 的 USERS 中更换强口令，"
                  "或改为外部账号源。", size=9.5, color=ACCENT)

    # ===== 5. 配置说明 =====
    add_heading(doc, "5", "配置说明")
    add_heading(doc, "5.1", "端口配置")
    add_para(doc, "通过环境变量 PORT 指定监听端口，默认 8001（见 app.py：int(os.environ.get('PORT', 8001))）。")
    add_heading(doc, "5.2", "鉴权配置")
    add_bullet(doc, "密钥", "—— core/auth.py 中 SECRET_KEY = 'shiguang-mvp-secret-2026' 为 MVP 演示密钥，"
                           "生产应通过环境变量注入并在部署侧保密。")
    add_bullet(doc, "账号", "—— USERS 字典内置 employee01 / manager01，含 password / role / name；"
                           "上线前务必更换强口令。")
    add_bullet(doc, "令牌", "—— JWT 有效期 12 小时（exp = iat + 12h），含 jti 简易黑名单；"
                           "多源提取 token 以兼容发布网关。")
    add_heading(doc, "5.3", "知识库配置（data/knowledge.json）")
    add_para(doc, "结构：meta（版本/责任方/过期阈值）+ coupons（每券含 type/规则/门店/叠加/有效期/版本/来源/别名）。"
                  "当前内置 4 类券（版本 v2026.11）：")
    add_table(doc,
              ["券 ID", "名称", "类型", "关键规则"],
              [["man200_30", "满200减30券", "满减", "满 200 减 30；不与其它满减叠加"],
               ["disc85", "8.5折会员折扣券", "折扣", "0.85 折；电商渠道不参与"],
               ["voucher5", "5元抵用券", "抵用", "满 50 可用 5 元；不与满减同用"],
               ["exchangeA", "兑换券A（赠品券）", "兑换", "兑赠品，不涉及金额抵扣"]],
              [2.6, 3.8, 1.6, 9.0])
    add_heading(doc, "5.4", "数据库路径")
    add_para(doc, "db.py 中 DB_PATH = mvp/mvp.db。如迁移路径，请同步修改该常量并重新 init_db()。")

    # ===== 6. 操作指南 =====
    add_heading(doc, "6", "功能与操作指南")
    add_heading(doc, "6.1", "员工端（权益查询助手 + 核销 + 代生成码 + 转人工）")
    add_bullet(doc, "权益查询", "—— 输入会员关于券规则的问题，系统返回答案并附 事实引用(sources) 与 规则版本(version)。")
    add_bullet(doc, "代生成核销码", "—— 选券后生成 6 位核销码 + 业务编号(biz_id)，供会员至收银核销。")
    add_bullet(doc, "核销计价", "—— 输入核销码 + 订单金额，系统判定可用性并计算应付金额（满减/折扣/抵用规则）。")
    add_bullet(doc, "转人工队列", "—— 解释不一致/缺依据、或兑换券退货等规则缺口，自动转人工并可在队列查看。")
    add_para(doc, "典型操作：会员出示核销码 → 员工端「核销计价」输入码与订单金额 → 返回应付金额与业务编号 → "
                  "完成收银。异常（不可用/规则缺口）自动转人工并落库 escalations。", size=10)
    add_heading(doc, "6.2", "管理端（概览 + 知识库 + 审计 + 转人工处理台）")
    add_bullet(doc, "概览", "—— 知识库版本、券数量、核销总量/已用、转人工总量/未处理统计。")
    add_bullet(doc, "知识库运维", "—— 查看券；upsert 入库（待发版）；publish 发版并写版本快照。")
    add_bullet(doc, "审计看板", "—— 最近核销记录与对话消息（含 biz_id / sources / version，可重建单次处理）。")
    add_bullet(doc, "转人工处理台", "—— 列出转人工工单，指派处理人并标记 resolved。")

    # ===== 7. 接口清单 =====
    add_heading(doc, "7", "API 接口清单")
    add_table(doc,
              ["方法", "路径", "角色", "说明", "主要返回/错误"],
              [["POST", "/api/login", "—", "登录获取 JWT（按 role 限定）", "token / 401"],
               ["GET", "/employee", "—", "员工端页面（no-cache）", "HTML"],
               ["POST", "/api/employee/chat", "employee", "权益问答（SSE 流式）", "sources/version/biz_id/escalate"],
               ["POST", "/api/employee/generate_code", "employee", "代生成 6 位核销码", "code/biz_id"],
               ["POST", "/api/employee/redeem", "employee", "核销 + 计价", "new_amount/usable/escalate；400 重复核销/码不存在"],
               ["GET", "/api/employee/escalations", "employee", "转人工队列", "open 工单列表"],
               ["GET", "/manager", "—", "管理端页面（no-cache）", "HTML"],
               ["GET", "/api/manager/kb", "manager", "知识库查看", "meta/coupons"],
               ["POST", "/api/manager/kb/upsert", "manager", "券入库（待发版）", "ok/400"],
               ["POST", "/api/manager/kb/publish", "manager", "发版并写快照", "ok/新版本"],
               ["GET", "/api/manager/audit", "manager", "审计看板", "redemptions/messages"],
               ["GET", "/api/manager/escalations", "manager", "转人工列表", "全部工单"],
               ["POST", "/api/manager/escalations/resolve", "manager", "处理转人工", "ok"],
               ["GET", "/api/manager/overview", "manager", "概览统计", "kb_version/计数"]],
              [1.4, 4.6, 1.8, 4.6, 4.8])
    add_para(doc, "鉴权规则：除 /api/login 与页面路由外，所有 API 需携带有效 token 且角色匹配；"
                  "越权或无 token 返回 401，参数/券错误返回 400（含「重复核销」「码不存在」）。",
             size=9.5, color=GREY)

    # ===== 8. 知识库运维 =====
    add_heading(doc, "8", "知识库运维与发版")
    add_para(doc, "知识库是口径唯一出口，变更必须经过「入库 → 发版」两阶段，确保可追溯。")
    add_bullet(doc, "修改券规则", "—— 在 data/knowledge.json 中编辑对应券字段。")
    add_bullet(doc, "upsert（待发版）", "—— 调用 /api/manager/kb/upsert 入库，此时尚未生效对外口径。")
    add_bullet(doc, "publish（发版）", "—— 调用 /api/manager/kb/publish 生成新版本，写 kb_versions 快照；"
                                      "此后答案口径随版本号更新。")
    add_bullet(doc, "版本回退", "—— MVP 阶段通过恢复 knowledge.json 历史版本 + 重新 publish 实现；"
                               "kb_versions 表保留发版快照供追溯。")
    add_para(doc, "注意：发版需运营/策划审批（对应红线「对外承诺/资产变更须审批人确认」）。",
             size=9.5, color=ACCENT)

    # ===== 9. 运维监控 =====
    add_heading(doc, "9", "运维与监控")
    add_bullet(doc, "运行监控", "—— 通过管理端「概览 / 审计看板 / 转人工处理台」实时掌握核销、对话与工单。")
    add_bullet(doc, "日志", "—— MVP 将运行信息输出到标准输出(stdout)；生产建议重定向至日志文件或接入日志系统，"
                           "并按需记录访问与异常。")
    add_bullet(doc, "数据备份", "—— 定期备份 mvp/mvp.db 与 mvp/data/knowledge.json（二者即全部持久化状态）。")
    add_bullet(doc, "数据恢复", "—— 停止服务后，用备份文件覆盖对应路径并重启即可；SQLite 为单文件，恢复简单。")
    add_para(doc, "建议备份脚本（示例）：cp mvp/mvp.db mvp/mvp.db.bak-$(date +%F) ；"
                  "cp mvp/data/knowledge.json mvp/data/knowledge.json.bak-$(date +%F)",
             font="Consolas", size=9.5, color=NAVY, before=2)

    # ===== 10. 故障排查 =====
    add_heading(doc, "10", "故障排查")
    add_table(doc,
              ["现象", "可能原因", "处理"],
              [["启动报端口占用", "8001 已被占用", "改用 PORT=其他端口；或释放占用进程"],
               ["ImportError / 模块缺失", "未安装 fastapi/uvicorn", "执行 pip install fastapi uvicorn"],
               ["登录返回 401", "账号/口令/角色不匹配", "核对 USERS；注意 employee/manager 角色区分"],
               ["接口 401（已登录仍失败）", "token 过期(12h)/来源未被识别", "重新登录；确认 token 来源(推荐 body.token)"],
               ["核销提示「重复核销」", "该码已 used", "核对码是否二次扫描；属正常防重"],
               ["核销提示「码不存在」", "码未生成或输入错误", "在员工端核对核销码；确认代生成成功"],
               ["页面显示旧内容", "浏览器缓存", "系统已置 no-cache；仍异常则硬刷新(Ctrl+F5)"],
               ["数据库写入失败", "mvp.db 权限/被锁", "检查目录写权限；避免多写进程并发锁"]],
              [3.6, 5.8, 7.6])

    # ===== 11. 安全合规 =====
    add_heading(doc, "11", "安全与合规")
    add_bullet(doc, "密钥管理", "—— SECRET_KEY 生产应环境变量注入，禁止硬编码于仓库；定期轮换。")
    add_bullet(doc, "账号口令", "—— 上线前更换演示弱口令；按需接入企业账号体系。")
    add_bullet(doc, "最小权限", "—— 接口仅可读会员/券信息，不自主修改会员资产（红线）。")
    add_bullet(doc, "缓存策略", "—— 全部页面 no-cache，避免旧口径误显示。")
    add_bullet(doc, "对外承诺审批", "—— 资产/口径变更须经审批人确认（发版流程体现）。")
    add_bullet(doc, "传输安全", "—— 生产建议前置 HTTPS（反向代理/网关）后再暴露端口。")

    # ===== 12. 验收清单 =====
    add_heading(doc, "12", "验收清单")
    add_table(doc,
              ["项", "验收内容", "标准"],
              [["AC-01", "单一口径出口", "所有解释/计算源自 knowledge.json，附 sources+version"],
               ["AC-02", "秒级核销计价", "输入码+金额即出应付（对照 PT-2 目标 40 秒）"],
               ["AC-03", "转人工兜底", "解释不一致/兑换券退货等自动转人工并落库"],
               ["AC-04", "审计链完整", "biz_id + sources + version + 人工改动可重建单次处理"],
               ["AC-05", "知识库版本化", "upsert→publish 流程可用，版本快照可追溯"],
               ["AC-06", "双端角色鉴权", "越权 401、无 token 401、重复核销 409/400"],
               ["AC-07", "页面 no-cache", "刷新不显示旧口径"],
               ["AC-08", "红线合规", "不自主改资产、接口只读、变更需审批"],
               ["AC-09", "投诉下降目标", "试点对照 240→100 件/月（需基线采集后验收）"],
               ["AC-10", "核验提速目标", "对照 3 分钟→40 秒（需基线采集后验收）"]],
              [1.6, 7.4, 7.0])
    add_para(doc, "AC-09、AC-10 为量化目标，需先完成试点基线采集再作为正式验收依据（见典型事件资料数据缺口说明）。",
             size=9.5, color=GREY)

    # ===== 13. 升级与回滚 =====
    add_heading(doc, "13", "升级与回滚")
    add_bullet(doc, "知识库升级", "—— 编辑 knowledge.json → upsert → publish（发版快照留存）。")
    add_bullet(doc, "应用代码升级", "—— 通过 Git 拉取新版本；MVP 为单进程，替换文件后重启进程即生效。")
    add_bullet(doc, "回滚", "—— 代码回退到上一 Git 提交并重启；知识库回退到历史版本重新 publish；"
                           "数据库如需回退用 9.4 备份恢复。")
    add_bullet(doc, "演进路线", "—— 可把 IntentRouter 替换为 LLM 分类器、知识库升级为向量检索(RAG)，"
                               "接口与数据层体系不变。")

    # ===== 14. 交付物清单 =====
    add_heading(doc, "14", "交付物清单")
    add_table(doc,
              ["类别", "交付物", "说明"],
              [["代码", "mvp/（app.py, db.py, core/, data/, static/）", "可运行系统全部源码"],
               ["文档", "README.md", "工程架构说明"],
               ["文档", "本手册（上线交付运行手册）", "部署/配置/运维/验收指引"],
               ["文档", "典型事件资料 / 需求分析方案 / 详细设计方案 / 项目汇总", "项目配套文档"],
               ["图", "flow1_business.png / flow2_dualport.png", "业务流程与双端口闭环（含典型问题）"],
               ["数据", "data/knowledge.json（v2026.11）", "内置 4 类券基线知识库"],
               ["运行态", "mvp.db", "首次启动自动初始化的 SQLite 库"]],
              [1.8, 6.6, 7.6])

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc.save(OUT)
    print("SAVED:", OUT)


if __name__ == "__main__":
    build()
