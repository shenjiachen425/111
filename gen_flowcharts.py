# -*- coding: utf-8 -*-
"""生成两份流程图 PNG（融入典型事例问题版）。

图1：业务主流程 × 典型问题落点 × 异常升级（含审计链）
图2：双端口智能体闭环 × 各端典型问题（含审计链 / 鉴权 / 量化背景）

典型问题来源：《会员权益域典型事件资料》
  - 已量化指标：投诉量 240→100 件/月；核验时长 3 分钟→40 秒
  - 待验证假设：P1 收银反复打电话问运营；P2 小程序/门店口径不一；P3 售后来回确认
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import matplotlib.font_manager as fm

FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
fm.fontManager.addfont(FONT)
PROP = fm.FontProperties(fname=FONT)
plt.rcParams["font.family"] = PROP.get_name()
plt.rcParams["axes.unicode_minus"] = False

C_MAIN = "#2d6cdf"; C_EXC = "#e8743b"; C_AUD = "#3aa76d"
C_MEM = "#7b61ff"; C_CASH = "#0f9bb3"
C_PROB = "#fff1e8"; C_PROB_B = "#e8743b"      # 问题卡（橙系·待验证）
C_METR = "#eaf2ff"; C_METR_B = "#2d6cdf"      # 量化卡（蓝系·已量化）
C_GREY_B = "#9aa3b5"; C_GREY_F = "#f1f3f7"    # 中立卡（灰系）


def wrap(text, width=14):
    tokens, buf = [], ""
    for ch in text:
        if ord(ch) > 0x2E80:                 # CJK 单字成词
            if buf:
                tokens.append(buf); buf = ""
            tokens.append(ch)
        elif ch == " ":
            if buf:
                tokens.append(buf); buf = ""
        else:
            buf += ch
    if buf:
        tokens.append(buf)
    lines, cur, cnt = [], "", 0
    for t in tokens:
        add = len(t) if ord(t[0]) > 0x2E80 else max(len(t), 1)
        if cnt + add > width and cur:
            lines.append(cur); cur = t; cnt = add
        else:
            cur += t; cnt += add
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def node(ax, x, y, w, h, text, fc, ec, fs=11):
    ax.add_patch(FancyBboxPatch((x - w/2, y - h/2), w, h,
                 boxstyle="round,pad=0.05,rounding_size=0.12",
                 linewidth=1.4, edgecolor=ec, facecolor=fc, zorder=3))
    ax.text(x, y, wrap(text, 16 if w >= 2.5 else 13), ha="center", va="center",
            fontsize=fs, color="white", fontproperties=PROP, zorder=4, weight="bold")
    return (x, y, w, h)


def v_edge(ax, n1, n2, color="#555", style="->", rad=0.0):
    x1, y1, w1, h1 = n1; x2, y2, w2, h2 = n2
    ax.add_patch(FancyArrowPatch((x1, y1 - h1/2), (x2, y2 + h2/2),
                  arrowstyle=style, mutation_scale=14, color=color,
                  connectionstyle=f"arc3,rad={rad}", zorder=2))


def h_edge(ax, n1, n2, color="#555", label=None, style="->", rad=0.0, ls="-"):
    x1, y1, w1, h1 = n1; x2, y2, w2, h2 = n2
    ax.add_patch(FancyArrowPatch((x1 + w1/2, y1), (x2 - w2/2, y2),
                  arrowstyle=style, mutation_scale=14, color=color,
                  connectionstyle=f"arc3,rad={rad}", linestyle=ls, zorder=2))
    if label:
        ax.text((x1 + x2)/2, y1 + 0.30, label, ha="center", va="center",
                fontsize=9, color=color, fontproperties=PROP, zorder=5)


def prob_box(ax, x, y, w, h, head, line1, line2=None, ec=C_PROB_B, fc=C_PROB, tc="#b8551f"):
    """典型问题卡片：标题（彩色粗体）+ 两行说明（深灰）"""
    ax.add_patch(FancyBboxPatch((x - w/2, y - h/2), w, h,
                 boxstyle="round,pad=0.05,rounding_size=0.1",
                 linewidth=1.3, edgecolor=ec, facecolor=fc, zorder=3))
    ax.text(x, y + h/2 - 0.27, head, ha="center", va="center", fontsize=10,
            color=tc, fontproperties=PROP, zorder=4, weight="bold")
    ax.text(x, y - 0.04, line1, ha="center", va="center", fontsize=9, color="#444",
            fontproperties=PROP, zorder=4)
    if line2:
        ax.text(x, y - h/2 + 0.23, line2, ha="center", va="center", fontsize=8.5,
                color="#666", fontproperties=PROP, zorder=4)
    return (x, y, w, h)


def info_box(ax, x, y, w, h, text, ec, fc, tc="#444", fs=8.5):
    """底部信息小栏：多行居中文字"""
    ax.add_patch(FancyBboxPatch((x - w/2, y - h/2), w, h,
                 boxstyle="round,pad=0.04,rounding_size=0.08",
                 linewidth=1.2, edgecolor=ec, facecolor=fc, zorder=3))
    ax.text(x, y, text, ha="center", va="center", fontsize=fs, color=tc,
            fontproperties=PROP, zorder=4, weight="bold" if tc != "#444" else "normal")
    return (x, y, w, h)


# ============ 图1：业务主流程 × 典型问题落点 × 异常升级 ============
fig, ax = plt.subplots(figsize=(13.0, 6.8))
ax.set_xlim(-1.2, 11.0); ax.set_ylim(-3.8, 5.7); ax.axis("off")

ax.text(4.9, 5.35, "图1  业务主流程 × 典型问题落点 × 异常升级（含审计链）",
        ha="center", fontsize=13, color="#1c4fa0", fontproperties=PROP, weight="bold")

# —— 主流程竖列 ——
M = node(ax, 2.2, 4.7, 2.4, 0.7, "会员识别", C_MAIN, "#1c4fa0")
V = node(ax, 2.2, 3.3, 2.4, 0.7, "权益核验", C_MAIN, "#1c4fa0")
C = node(ax, 2.2, 1.9, 2.4, 0.7, "优惠计算", C_MAIN, "#1c4fa0")
P = node(ax, 2.2, 0.5, 2.4, 0.7, "收银确认", C_MAIN, "#1c4fa0")
S = node(ax, 2.2, -0.9, 2.4, 0.7, "售后反馈", C_MAIN, "#1c4fa0")
for a, b in [(M, V), (V, C), (C, P), (P, S)]:
    v_edge(ax, a, b, color="#1c4fa0")

# —— 典型问题落点（橙系·待验证 / 蓝系·已量化）——
Mt = prob_box(ax, 5.9, 4.7, 3.7, 0.95, "量化背景〔已量化·访谈确认〕",
              "投诉量：240 → 100 件/月", "核验时长：3 分钟 → 40 秒",
              ec=C_METR_B, fc=C_METR, tc="#1c4fa0")
P2 = prob_box(ax, 5.9, 3.3, 3.7, 0.95, "P2 口径不一〔待验证〕",
              "同一张券 小程序 vs 门店 解释不同", "易引发会员质疑")
P1 = prob_box(ax, 5.9, 1.9, 3.7, 0.95, "P1 反复打电话〔待验证〕",
              "收银核验依赖人工致电运营", "效率低、现场核验慢")
P3 = prob_box(ax, 5.9, 0.5, 3.7, 0.95, "P3 售后来回〔待验证〕",
              "同一问题在环节间反复流转", "处理链路长")
# 问题归属虚线：P2/P1 来自权益核验，P3 来自售后反馈
h_edge(ax, V, P2, color="#b8551f", ls=(0, (4, 3)), rad=0.0)
h_edge(ax, V, P1, color="#b8551f", ls=(0, (4, 3)), rad=0.12)
h_edge(ax, S, P3, color="#b8551f", ls=(0, (4, 3)), rad=-0.05)

# —— 异常升级（橙实底白字）——
Ev = node(ax, 9.3, 3.3, 3.0, 1.05, "解释不一致 / 缺依据 → 转人工\n（唐映秋·傅景）", C_EXC, "#b8551f", fs=10)
Ep = node(ax, 9.3, 0.5, 3.0, 1.05, "退货积分已兑新券·无追回规则\n→ 暂停 + 转人工", C_EXC, "#b8551f", fs=10)
h_edge(ax, V, Ev, color="#b8551f", ls=(0, (4, 3)), rad=0.0)
h_edge(ax, P, Ep, color="#b8551f", ls=(0, (4, 3)), rad=0.0)

# 角色标签（左）
roles = [(4.7, "会员", C_MAIN), (3.3, "门店收银\n（林亦舟）", "#666"),
         (1.9, "门店收银\n（林亦舟）", "#666"), (0.5, "门店收银\n（林亦舟）", "#666"),
         (-0.9, "客服/数据隐私\n（叶书·傅景）", "#666")]
for y, t, c in roles:
    ax.text(-0.5, y, t, ha="right", va="center", fontsize=9, color=c,
            fontproperties=PROP, weight="bold" if c == C_MAIN else "normal")

# 审计链底栏
ax.add_patch(FancyBboxPatch((0.1, -2.85), 10.8, 1.05,
             boxstyle="round,pad=0.05,rounding_size=0.1",
             linewidth=1.3, edgecolor=C_AUD, facecolor="#eafaf0", zorder=3))
ax.text(5.5, -2.30, "审计链贯穿：业务编号 + 资料版本 + 事实引用 + 人工改动 + 签核人（可重建单次处理）",
        ha="center", va="center", fontsize=10.5, color="#247a4c",
        fontproperties=PROP, weight="bold", zorder=4)
ax.text(5.5, -3.45, "红线：不自主修改会员资产；接口仅可读；对外承诺/资产变更须审批人确认",
        ha="center", fontsize=9, color="#888", fontproperties=PROP)

plt.tight_layout()
fig.savefig("/workspace/flow1_business.png", dpi=160, bbox_inches="tight")
plt.close(fig)
print("flow1 done")


# ============ 图2：双端口智能体闭环 × 各端典型问题 ============
fig, ax = plt.subplots(figsize=(12.6, 7.2))
ax.set_xlim(-1.2, 8.6); ax.set_ylim(-3.3, 6.0); ax.axis("off")

ax.text(3.7, 5.62, "图2  双端口智能体闭环 × 各端典型问题（含审计链 / 鉴权 / 量化背景）",
        ha="center", fontsize=13, color="#1c4fa0", fontproperties=PROP, weight="bold")

# 端口标题
ax.text(1.5, 4.78, "会员端（提问 / 选券）", ha="center", fontsize=11, color=C_MEM, fontproperties=PROP, weight="bold")
ax.text(5.8, 4.78, "收银端（核销 / 计价）", ha="center", fontsize=11, color=C_CASH, fontproperties=PROP, weight="bold")

# 会员端
A1 = node(ax, 1.5, 4.0, 2.6, 0.7, "会员登录", C_MEM, "#5a44c4")
A2 = node(ax, 1.5, 2.8, 2.7, 0.9, "提问查门店/规则，或选券使用", C_MEM, "#5a44c4", fs=10)
A3 = node(ax, 1.5, 1.4, 2.6, 0.9, "生成 6 位核销码 + 业务编号", C_MEM, "#5a44c4", fs=10)
# 收银端
B1 = node(ax, 5.8, 4.0, 2.6, 0.7, "收银登录", C_CASH, "#0a7d92")
B2 = node(ax, 5.8, 2.8, 2.6, 0.9, "输入核销码 + 订单金额", C_CASH, "#0a7d92", fs=10)
B3 = node(ax, 5.8, 1.7, 2.6, 0.9, "核销判定（读 COUPONS 计算）", C_CASH, "#0a7d92", fs=10)
B4 = node(ax, 5.8, 0.4, 2.6, 0.9, "返回应付金额 + 业务编号", C_CASH, "#0a7d92", fs=10)

for seq in [(A1, A2), (A2, A3), (B1, B2), (B2, B3), (B3, B4)]:
    v_edge(ax, seq[0], seq[1], color="#555")

h_edge(ax, A3, B2, color="#d6336c", label="核销码 + 订单金额", rad=0.0)
h_edge(ax, B4, A3, color="#d6336c", label="核销完成回执", rad=0.0, ls=(0, (4, 3)))

# 各端典型问题（落在端口下方，下移到避免与节点重叠）
MP = prob_box(ax, 1.5, -1.10, 3.0, 1.0, "P2 口径不一〔待验证〕",
              "小程序 vs 门店 解释不同", "会员提问查规则时易遇此问题")
CP = prob_box(ax, 5.8, -1.10, 3.0, 1.0, "P1 反复打电话〔待验证〕",
              "收银核验依赖人工致电运营", "现场核验慢")
h_edge(ax, A2, MP, color="#b8551f", ls=(0, (4, 3)), rad=0.0)
h_edge(ax, B2, CP, color="#b8551f", ls=(0, (4, 3)), rad=0.0)

# 审计链顶栏
ax.add_patch(FancyBboxPatch((0.2, 5.05), 7.2, 0.42,
             boxstyle="round,pad=0.04,rounding_size=0.08",
             linewidth=1.2, edgecolor=C_AUD, facecolor="#eafaf0", zorder=3))
ax.text(3.8, 5.26, "审计链：按业务编号重建「问答链 + 核销链」（资料版本 v2026.11 + 事实引用）",
        ha="center", va="center", fontsize=9.5, color="#247a4c", fontproperties=PROP, weight="bold", zorder=4)

# 底部四栏：P3 售后 / 量化 / 鉴权 / 异常升级
by = -2.5
info_box(ax, 1.15, by, 1.9, 1.1, "P3 售后来回〔待验证〕\n同一问题环节间\n反复流转",
         C_PROB_B, C_PROB, tc="#444")
info_box(ax, 3.30, by, 2.0, 1.1, "量化背景〔已量化〕\n投诉240→100/月\n核验3分→40秒",
         C_METR_B, C_METR, tc="#1c4fa0")
info_box(ax, 5.45, by, 1.9, 1.1, "鉴权\nadmin→收银端\nmember→会员端\n跨角色403",
         C_GREY_B, C_GREY_F, tc="#555")
info_box(ax, 7.55, by, 1.9, 1.1, "异常升级\n解释不一致→转人工\n退货无追回→暂停",
         C_PROB_B, C_PROB, tc="#b8551f")

plt.tight_layout()
fig.savefig("/workspace/flow2_dualport.png", dpi=160, bbox_inches="tight")
plt.close(fig)
print("flow2 done")
