# -*- coding: utf-8 -*-
"""生成两份流程图 PNG（修正版）：1) 业务主流程+异常升级 2) 双端口闭环+审计链"""
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


# ============ 图1：业务主流程 + 异常升级 ============
fig, ax = plt.subplots(figsize=(9.6, 5.8))
ax.set_xlim(-1.0, 6.2); ax.set_ylim(-3.2, 4.9); ax.axis("off")

M = node(ax, 1.2, 4.0, 2.3, 0.7, "会员识别", C_MAIN, "#1c4fa0")
V = node(ax, 1.2, 2.9, 2.3, 0.7, "权益核验", C_MAIN, "#1c4fa0")
C = node(ax, 1.2, 1.8, 2.3, 0.7, "优惠计算", C_MAIN, "#1c4fa0")
P = node(ax, 1.2, 0.7, 2.3, 0.7, "收银确认", C_MAIN, "#1c4fa0")
S = node(ax, 1.2, -0.4, 2.3, 0.7, "售后反馈", C_MAIN, "#1c4fa0")
for a, b in [(M, V), (V, C), (C, P), (P, S)]:
    v_edge(ax, a, b, color="#1c4fa0")

Ev = node(ax, 4.7, 2.9, 2.7, 1.0, "解释不一致 / 缺依据 → 转人工（唐映秋·傅景）", C_EXC, "#b8551f", fs=10)
Ep = node(ax, 4.7, 0.7, 2.7, 1.0, "退货积分已兑新券·无追回规则 → 暂停+转人工", C_EXC, "#b8551f", fs=10)
h_edge(ax, V, Ev, color="#b8551f", ls=(0, (4, 3)))
h_edge(ax, P, Ep, color="#b8551f", ls=(0, (4, 3)))

# 左置角色标签，避免压框
roles = [(4.0, "会员", C_MAIN), (2.9, "门店收银\n（林亦舟）", "#666"),
         (1.8, "门店收银\n（林亦舟）", "#666"), (0.7, "门店收银\n（林亦舟）", "#666"),
         (-0.4, "客服/数据隐私\n（叶书·傅景）", "#666")]
for y, t, c in roles:
    ax.text(-0.7, y, t, ha="right", va="center", fontsize=9,
            color=c, fontproperties=PROP, weight="bold" if c == C_MAIN else "normal")

# 审计链底栏（放大）
ax.add_patch(FancyBboxPatch((0.1, -2.05), 5.9, 1.0,
             boxstyle="round,pad=0.05,rounding_size=0.1",
             linewidth=1.3, edgecolor=C_AUD, facecolor="#eafaf0", zorder=3))
ax.text(3.05, -1.55, "审计链贯穿：业务编号 + 资料版本 + 事实引用 + 人工改动 + 签核人（可重建单次处理）",
        ha="center", va="center", fontsize=10.5, color="#247a4c",
        fontproperties=PROP, weight="bold", zorder=4)
ax.text(3.05, -2.75, "红线：不自主修改会员资产；接口仅可读；对外承诺/资产变更须审批人确认",
        ha="center", fontsize=9, color="#888", fontproperties=PROP)

plt.tight_layout()
fig.savefig("/workspace/flow1_business.png", dpi=160, bbox_inches="tight")
plt.close(fig)
print("flow1 done")


# ============ 图2：双端口智能体闭环 + 审计链 ============
fig, ax = plt.subplots(figsize=(9.8, 6.0))
ax.set_xlim(-0.6, 6.4); ax.set_ylim(-1.3, 5.7); ax.axis("off")

ax.text(1.5, 4.55, "会员端（提问 / 选券）", ha="center", fontsize=11, color=C_MEM, fontproperties=PROP, weight="bold")
ax.text(5.0, 4.55, "收银端（核销 / 计价）", ha="center", fontsize=11, color=C_CASH, fontproperties=PROP, weight="bold")

A1 = node(ax, 1.5, 3.9, 2.6, 0.7, "会员登录", C_MEM, "#5a44c4")
A2 = node(ax, 1.5, 2.7, 2.7, 0.9, "提问查门店/规则，或选券使用", C_MEM, "#5a44c4", fs=10)
A3 = node(ax, 1.5, 1.2, 2.6, 0.9, "生成 6 位核销码 + 业务编号(biz_id)", C_MEM, "#5a44c4", fs=10)

B1 = node(ax, 5.0, 3.9, 2.6, 0.7, "收银登录", C_CASH, "#0a7d92")
B2 = node(ax, 5.0, 2.7, 2.6, 0.9, "输入核销码 + 订单金额", C_CASH, "#0a7d92", fs=10)
B3 = node(ax, 5.0, 1.5, 2.6, 0.9, "核销判定（读 COUPONS 计算）", C_CASH, "#0a7d92", fs=10)
B4 = node(ax, 5.0, 0.1, 2.6, 0.9, "返回应付金额 + 业务编号(biz_id)", C_CASH, "#0a7d92", fs=10)

for seq in [(A1, A2), (A2, A3), (B1, B2), (B2, B3), (B3, B4)]:
    v_edge(ax, seq[0], seq[1], color="#555")

h_edge(ax, A3, B2, color="#d6336c", label="核销码 + 订单金额", rad=0.0)
h_edge(ax, B4, A3, color="#d6336c", label="核销完成回执", rad=0.0, ls=(0, (4, 3)))

# 审计链顶栏
ax.add_patch(FancyBboxPatch((0.2, 4.95), 6.1, 0.42,
             boxstyle="round,pad=0.04,rounding_size=0.08",
             linewidth=1.2, edgecolor=C_AUD, facecolor="#eafaf0", zorder=3))
ax.text(3.25, 5.16, "审计链：按业务编号重建「问答链 + 核销链」（资料版本 v2026.11 + 事实引用）",
        ha="center", va="center", fontsize=10, color="#247a4c", fontproperties=PROP, weight="bold", zorder=4)

ax.text(3.25, -0.85, "鉴权：admin→收银端，member→会员端；跨角色 403、无 token 401、重复核销 409",
        ha="center", fontsize=9, color="#888", fontproperties=PROP)

plt.tight_layout()
fig.savefig("/workspace/flow2_dualport.png", dpi=160, bbox_inches="tight")
plt.close(fig)
print("flow2 done")
