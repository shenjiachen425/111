# -*- coding: utf-8 -*-
"""生成《会员权益智能体·详细项目设计方案》docx
包含：系统架构/功能模块/技术选型/实施计划 + 低中高三档报价 + 投入产出(ROI)测算。
ROI 与报价的数字均以「测算假设」形式透明列出，待以客户实际运营数据校准。
"""
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

OUT = "/workspace/会员权益智能体-详细项目设计方案.docx"

doc = Document()

def set_cjk(style):
    style.font.name = "Microsoft YaHei"
    style._element.rPr.rFonts.set(qn('w:eastAsia'), "Microsoft YaHei")
    style._element.rPr.rFonts.set(qn('w:ascii'), "Calibri")
    style._element.rPr.rFonts.set(qn('w:hAnsi'), "Calibri")

normal = doc.styles['Normal']
normal.font.size = Pt(10.5)
set_cjk(normal)

def H(text, level=1):
    p = doc.add_heading(level=level)
    r = p.add_run(text); r.font.name="Microsoft YaHei"
    r._element.rPr.rFonts.set(qn('w:eastAsia'),"Microsoft YaHei")
    if level==1: r.font.size=Pt(16); r.font.color.rgb=RGBColor(0x1F,0x33,0x64)
    elif level==2: r.font.size=Pt(13); r.font.color.rgb=RGBColor(0x2D,0x3E,0x63)
    else: r.font.size=Pt(11.5); r.font.color.rgb=RGBColor(0x33,0x33,0x33)
    return p

def P(text="", bold=False, size=10.5, color=None, align=None, italic=False):
    p = doc.add_paragraph()
    r = p.add_run(text); r.bold=bold; r.italic=italic; r.font.size=Pt(size)
    if color: r.font.color.rgb=color
    r.font.name="Microsoft YaHei"; r._element.rPr.rFonts.set(qn('w:eastAsia'),"Microsoft YaHei")
    if align: p.alignment=align
    return p

def bullet(text, level=0):
    p = doc.add_paragraph(style='List Bullet')
    r = p.add_run(text); r.font.size=Pt(10.5)
    r.font.name="Microsoft YaHei"; r._element.rPr.rFonts.set(qn('w:eastAsia'),"Microsoft YaHei")
    if level: p.paragraph_format.left_indent = Cm(1.0+0.6*level)
    return p

def numbered(text):
    p = doc.add_paragraph(style='List Number')
    r=p.add_run(text); r.font.size=Pt(10.5)
    r.font.name="Microsoft YaHei"; r._element.rPr.rFonts.set(qn('w:eastAsia'),"Microsoft YaHei")
    return p

def make_table(headers, rows, widths=None, header_bg="2D3E63"):
    t = doc.add_table(rows=1, cols=len(headers)); t.style='Table Grid'; t.alignment=WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0].cells
    for i,h in enumerate(headers):
        hdr[i].text=""; run=hdr[i].paragraphs[0].add_run(h)
        run.bold=True; run.font.size=Pt(9.5); run.font.color.rgb=RGBColor(0xFF,0xFF,0xFF)
        run.font.name="Microsoft YaHei"; run._element.rPr.rFonts.set(qn('w:eastAsia'),"Microsoft YaHei")
        tcPr=hdr[i]._tc.get_or_add_tcPr()
        tcPr.append(tcPr.makeelement(qn('w:shd'),{qn('w:val'):'clear',qn('w:fill'):header_bg}))
    for row in rows:
        cells=t.add_row().cells
        for i,val in enumerate(row):
            cells[i].text=""; run=cells[i].paragraphs[0].add_run(str(val))
            run.font.size=Pt(9.5); run.font.name="Microsoft YaHei"; run._element.rPr.rFonts.set(qn('w:eastAsia'),"Microsoft YaHei")
    if widths:
        for r in t.rows:
            for i,w in enumerate(widths): r.cells[i].width=Cm(w)
    return t

def add_img(path, width=15.5, caption=None):
    try:
        doc.add_picture(path, width=Cm(width))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        if caption:
            cp = doc.add_paragraph(); cp.alignment=WD_ALIGN_PARAGRAPH.CENTER
            r=cp.add_run(caption); r.italic=True; r.font.size=Pt(9); r.font.color.rgb=RGBColor(0x88,0x88,0x88)
            r.font.name="Microsoft YaHei"; r._element.rPr.rFonts.set(qn('w:eastAsia'),"Microsoft YaHei")
    except Exception as e:
        P("[图片缺失："+str(e)+"]", italic=True, color=RGBColor(0xAA,0x33,0x33))

# ===================== 封面 =====================
t=doc.add_paragraph(); t.alignment=WD_ALIGN_PARAGRAPH.CENTER
r=t.add_run("拾光连锁 · 会员权益智能体"); r.bold=True; r.font.size=Pt(24); r.font.color.rgb=RGBColor(0x1F,0x33,0x64)
r.font.name="Microsoft YaHei"; r._element.rPr.rFonts.set(qn('w:eastAsia'),"Microsoft YaHei")
s=doc.add_paragraph(); s.alignment=WD_ALIGN_PARAGRAPH.CENTER
r=s.add_run("详细项目设计方案（含低/中/高报价与投入产出测算）"); r.bold=True; r.font.size=Pt(15); r.font.color.rgb=RGBColor(0x2D,0x3E,0x63)
r.font.name="Microsoft YaHei"; r._element.rPr.rFonts.set(qn('w:eastAsia'),"Microsoft YaHei")
m=doc.add_paragraph(); m.alignment=WD_ALIGN_PARAGRAPH.CENTER
r=m.add_run("版本 v1.1 ｜ 依据：需求分析方案 + 六人访谈 + 已落地双端口系统\n资料版本 v2026.11 ｜ 报价与 ROI 数字为测算模型，待以客户实际运营数据校准")
r.font.size=Pt(10); r.font.color.rgb=RGBColor(0x66,0x66,0x66)
r.font.name="Microsoft YaHei"; r._element.rPr.rFonts.set(qn('w:eastAsia'),"Microsoft YaHei")
doc.add_paragraph()

# ===================== 0 =====================
H("0 文档说明与编制依据",1)
P("本文档在《系统需求分析方案》基础上，给出可落地的详细设计、三档报价与投入产出测算（ROI）。设计内容以已验证落地的双端口系统为基线，报价与 ROI 以透明「测算假设」列示，便于与客户实际运营数据校准，不将假设数字伪装为既定事实。")
bullet("依据一：系统需求分析方案（痛点 PT-1~PT-7、AI 改造 AI-1~AI-8、优先级、边界、验收 AC-01~AC-12）。")
bullet("依据二：六人访谈（r0–r5）+ 补充访谈（turn-8/10），含红线：只读不写、单一出口、可审计、异常转人工。")
bullet("依据三：已落地系统（FastAPI 双端口 + JWT 鉴权 + SQLite + 结构化知识库 COUPONS + 审计链 /audit + 转人工 + 版本化）。")

# ===================== 1 概述 =====================
H("1 项目概述",1)
H("1.1 建设目标",2)
make_table(["目标","现状","目标值","达成手段"],
[["权益投诉量","约 240 件/月","≤ 100 件/月","单一出口精准问答 + 转人工"],
["单次核验时长","平均 3 分钟","≤ 40 秒","收银端秒级核销计算"],
["解释一致率","多渠道不一致","≥ 99%","结构化知识库 + 版本化"],
["处理可追溯","无留痕","100% 可重建","业务编号 + 事实引用 + 审计链"]],
widths=[3.0,3.6,3.4,4.6])
H("1.2 设计原则（红线）",2)
bullet("RED-1 只读不写：不修改积分/券库存等会员资产。")
bullet("RED-2 单一出口：解释口径只来自统一知识库。")
bullet("RED-3 可审计：无业务编号/事实引用不得对外呈现。")
bullet("RED-4 异常转人工：规则缺口/资损风险/版本冲突强制转人工。")

# ===================== 2 总体架构 =====================
H("2 系统总体架构",1)
P("系统采用「前端双端口 + 无状态后端 + 结构化知识库 + 审计存储」的分层架构，已落地验证。会员端与收银端共用同一后端服务与鉴权体系，确保口径单一出口与可追溯一致性。")
add_img("/workspace/flow2_dualport.png", 15.5, "图 1 双端口闭环与审计链架构（会员端 ↔ 收银端 ↔ 统一知识库/审计）")
H("2.1 技术栈",2)
make_table(["层","技术选型","说明"],
[["接入层","HTML + Fetch(SSE)","会员/收银/审计三套静态页，no-cache 防误显"],
["服务层","FastAPI + Uvicorn","双端口路由 + 流式问答 + 鉴权中间件"],
["鉴权","自研 JWT(HMAC-SHA256) 无状态","多源 token 提取，绕过网关不转发 Authorization 限制"],
["知识层","结构化字典 COUPONS + 版本字段","单一口径出口，支持 version/effective_date"],
["存储","SQLite（messages/redemptions/sessions）","轻量、可审计、易部署"],
["图表","matplotlib + Noto CJK","流程图/报表本地生成"]],
widths=[2.2,4.6,6.2])
H("2.2 部署拓扑",2)
bullet("单服务多端口模式：同一 FastAPI 应用通过路径区分 /member、/cashier、/audit，由发布平台网关统一代理。")
bullet("无外部依赖：SQLite 文件库 + 本地知识库，适合先试点后扩展，不绑定特定云。")
bullet("可演进：知识库可平滑升级为向量检索（RAG）以支持更长尾的权益问法，属 High 档可选增强。")

# ===================== 3 功能模块 =====================
H("3 功能模块设计",1)
H("3.1 会员端（提问式查询 + 选券）",2)
bullet("精准问答：按「券类型 + 问题意图」作答——问门店只答门店、问规则只答规则，附事实引用与资料版本。")
bullet("我的优惠券：列出可用券，点击「使用」生成 6 位纯数字核销码（状态 pending）。")
bullet("免责标注：结果区标记「本结果为辅助信息，最终以门店/运营口径为准」。")
H("3.2 收银端（核验 + 出价）",2)
bullet("手动输入：收银员输入会员端生成的核销码 + 订单金额，点「确认核销」即出应付金额。")
bullet("判定逻辑：不满足门槛/码不存在/重复核销分别红色提示，绝不虚构金额。")
bullet("结果卡：展示券名、核销码、依据版本、业务编号；兑换券标「待人工确认」徽标。")
H("3.3 鉴权与角色隔离",2)
bullet("admin → 收银端，member → 会员端；跨角色 403、无 token 401。")
bullet("JWT 无状态 + 多源 token（body.token 首选），规避发布网关不透传 Authorization 的坑。")
H("3.4 知识库与版本化",2)
bullet("COUPONS 单源字典承载满减/折扣/抵用/兑换四类券规则，前端问答/计算/选券/核销均以它为唯一数据源。")
bullet("每条券带 version + effective_date；答案与核销结果标注当前生效版本 v2026.11。")
H("3.5 审计链",2)
bullet("每次问答/使用/核销生成 BIZ 业务编号，关联问答链与核销链；/audit 按编号重建单次处理。")
H("3.6 异常转人工",2)
bullet("触发：券型无法识别、兑换券退货追回规则缺口、版本冲突、计算异常。")
bullet("输出：escalate + 触发原因 + 建议接手人 + 待确认字段，不覆盖原记录、不给虚构结论。")
add_img("/workspace/flow1_business.png", 15.5, "图 2 业务主流程与异常升级路径（会员识别→权益核验→优惠计算→收银确认→售后反馈）")

# ===================== 4 技术实现要点 =====================
H("4 技术选型与实现要点",1)
bullet("前端本地解析 JWT 校验过期，去除对 /me 接口的依赖，规避网关鉴权头丢失导致的登录闪退。")
bullet("SSE 流式输出 + 事件化下发（token/sources/meta/biz_id/kb_meta），前端按需渲染引用与版本。")
bullet("数据新鲜度函数 data_freshness()：资料更新时间 + 阈值，超期在前端提示「资料待刷新」。")
bullet("no-cache 响应头：所有页面 FileResponse 强制不缓存，根治浏览器缓存误显示旧 UI。")
bullet("数据库迁移：messages/redemptions 表增 biz_id/rule_version/sources 字段，向后兼容。")

# ===================== 5 实施计划 =====================
H("5 实施计划与里程碑",1)
make_table(["阶段","周期(示例)","交付物","对应优先级"],
[["一 基线交付","第 1–6 周","双端口+登录+精准问答+核销闭环+审计+版本化（已落地）","P0"],
["二 试点强化","第 7–10 周","转人工扩面 + 数据时效 + 一致率抽样基线","P1"],
["三 增强试点","第 11–16 周","售后预填(若启用) + 培训 + 试点复盘","P2"],
["四 规模运营","第 17 周起","运营后台联动 / 知识库持续运营（High 档）","P3"]],
widths=[3.0,3.2,6.4,2.4])

# ===================== 6 报价 =====================
H("6 报价方案（低 / 中 / 高）",1)
P("报价以「一次性实施费 + 首年运维/SLA」结构给出，币种为人民币（含税）。各档交付范围与需求分析方案的优先级（P0/P1/P2/P3）严格对应。", bold=True)
H("6.1 三档总览",2)
make_table(["档位","交付范围（对应优先级）","一次性实施费","首年运维/SLA","适用客户"],
[["低·基础版","P0 全量（双端口+问答+核销+审计+登录+部署）","¥85,000","¥48,000","单区域/小连锁试点"],
["中·标准版","低档 + P1（转人工扩面+数据时效）+ 试点陪跑1月 + 培训 + 知识库初始化","¥158,000","¥84,000","多店连锁"],
["高·旗舰版","中档 + P2 售后预填 + 主动推送 + 运营后台 + 专属SLA + 年度知识库运营","¥268,000","¥156,000","全渠道规模化"]],
widths=[2.2,5.4,2.4,2.2,2.8])
H("6.2 低·基础版明细",2)
bullet("范围：环境部署、双端口前端、JWT 鉴权、COUPONS 知识库初始化（4 类券）、审计链、版本化、上线陪跑 2 周。")
bullet("不含：转人工场景扩面、数据时效监控、售后预填、运营后台、长期专属 SLA。")
H("6.3 中·标准版明细",2)
bullet("在基础版上增加：异常转人工场景扩面（版本冲突/计算异常）、数据新鲜度监控与过期提示、1 个月试点陪跑、门店/运营培训、知识库首轮初始化审核。")
bullet("价值：把 P0 基线推向「一致率 99% 可验收」状态，覆盖试点期主要风险。")
H("6.4 高·旗舰版明细",2)
bullet("在标准版上增加：AI-7 售后反馈智能预填与去重、AI-8 运营异动主动推送、运营后台（知识库版本管理/审计看板）、7×12 专属 SLA、年度知识库运营服务。")
bullet("价值：从「单点工具」升级为「权益运营中枢」，强化合规与可扩展性，对应战略级投入。")

# ===================== 7 ROI =====================
H("7 投入产出测算（ROI）",1)
P("以下为透明测算模型。所有输入均为「测算假设」，需以客户实际门店数、核销笔数、人力成本校准；模型仅量化可计量的「人力节省」类效益，合规/品牌/可扩展等战略价值另作定性说明。", italic=True, color=RGBColor(0x88,0x88,0x88))
H("7.1 测算假设（基准情景，待校准）",2)
make_table(["参数","符号","基准值","说明"],
[["连锁门店数","N","50 家","示例规模"],
["月券核销笔数","T","30,000 笔","约 600 笔/店/月"],
["月权益投诉","C0→C1","240→100 件","访谈确认，目标 100"],
["单次核验耗时","t0→t1","180→40 秒","访谈确认，目标 40"],
["综合人力成本","H","¥28/小时","收银+客服加权"],
["单投诉处理","-","15 分钟 + 隐性 ¥50/件","处理工时 + 品牌/流失损失"]],
widths=[3.2,1.6,3.0,5.2])
H("7.2 效益模型与计算",2)
P("年化效益 = 核验提效收益 B1 + 投诉减少收益 B2", bold=True)
bullet("B1 = T × 12 × (t0−t1)/3600 × H = 30,000 × 12 × 140/3600 × 28 ≈ ¥391,993 / 年")
bullet("B2 = (C0−C1) × 12 × (15/60 × H + 50) = 140 × 12 × (7 + 50) = 140 × 12 × 57 ≈ ¥95,760 / 年")
bullet("年度可量化总效益 B ≈ ¥487,753 / 年（基准情景）")
H("7.3 分档 ROI（首年 = B − 一次性 − 首年运维）",2)
make_table(["档位","一次性","首年运维","首年净收益","首年 ROI","回收期(约)"],
[["低·基础版","¥85,000","¥48,000","+¥354,753","274%","3.3 个月"],
["中·标准版","¥158,000","¥84,000","+¥245,753","101%","6.0 个月"],
["高·旗舰版","¥268,000","¥156,000","+¥63,753","15%","10.6 个月"]],
widths=[2.6,2.2,2.2,2.8,1.8,2.4])
P("说明：高端版首年 ROI 偏低，因其溢价投入在合规、资损防控与可扩展性上——这些价值不在纯人力节省模型内。随门店/核销规模扩大，B1 线性放大，各档回收期同步缩短。", italic=True, size=9, color=RGBColor(0x88,0x88,0x88))
H("7.4 敏感性（核销规模变动）",2)
bullet("若 T=10,000 笔/月（较小规模）：B≈¥226,424/年 → 低档 ROI≈70%、回收约 7 个月；中/高档首年略亏，回收约 13/25 个月。")
bullet("若 T=60,000 笔/月（较大规模）：B≈¥867,000/年 → 三档首年 ROI 均 >150%，回收均 <6 个月。")
bullet("结论：项目经济性高度依赖券核销规模；建议先以低/中档在小范围试点，用真实数据回填模型后再定高档投入。")

# ===================== 8 风险保障 =====================
H("8 风险与保障",1)
make_table(["风险","影响","保障/应对"],
[["一致率基线缺失","99% 验收前提难达","试点前建标准答案库并抽样（见验收 AC）"],
["未量化痛点(PT-5/PT-7)","ROI 不准","以真实数据回填 7.1 假设后再扩大投入"],
["规则缺口(PT-4)","资损/纠纷","RED-4 强制转人工，等运营定义规则"],
["数据过期误用(PT-6)","错误承诺","数据时效监控 + 超期提示（P1 已含）"],
["对外承诺误用(f41)","品牌风险","全端免责标注 + 人工终审红线"]],
widths=[3.4,3.4,6.2])

# ===================== 附录 =====================
H("附录 A 与需求分析方案的对应关系",1)
bullet("痛点 PT-1~PT-7 → 第 3 章功能模块一一对应实现。")
bullet("AI 改造 AI-1~AI-8 → 第 6 章报价档位按 P0/P1/P2/P3 分级承载。")
bullet("验收 AC-01~AC-12 → 第 5 章里程碑与第 8 章保障共同确保可验收。")
P("\n（本方案报价与 ROI 为测算模型，最终以双方确认的商务合同与客户实际运营数据为准。）", italic=True, size=9, color=RGBColor(0x88,0x88,0x88))

doc.save(OUT)
print("SAVED:", OUT)
