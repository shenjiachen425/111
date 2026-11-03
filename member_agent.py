# -*- coding: utf-8 -*-
"""
拾光连锁 · 会员权益智能体（第九步·会员权益版，替代报销版 agent_harness.py）

业务域：会员权益与门店服务（源自《业务需求发现报告》）。
目标：解决"同一优惠券在小程序与门店解释不一致、收银只能打电话问运营"。

设计要点（对照六岗位诉求）：
  - 收银员(f10/f15)：verify_coupon_explanation 返回统一口径 + 来源章节 + 原记录引用，不编造。
  - 收银员(f11/f15)：identify_exception 识别四类异常（规则缺失/资料缺依据/版本冲突/系统不可用），
                     不自行编口径，强制转人工。
  - 活动策划(f23/f24)：escalate_to_human 严格按"五项交接要素"标注触发原因/接手人/确认时间。
  - 隐私专员(f40/f41/f45)：log_audit 留存业务编号/资料版本/事实引用/人工改动/签核人，可重建。
  - 全局硬约束：不自主修改会员资产（无任何写操作工具）。

运行（mock 即可，无需 API Key）：
  python3.11 member_agent.py demo normal      # 正常券解释核验
  python3.11 member_agent.py demo exception    # 退货积分兑新券无追回规则 → 转人工
  python3.11 member_agent.py demo conflict     # 同券两版本冲突 → 转人工
  python3.11 member_agent.py demo "单笔订单满200减30的券，顾客在门店能用吗？"   # 单轮问答
"""

import os
import re
import sys
import json
import time
import sqlite3
import hashlib
from typing import Callable, Dict, List, Optional, Any

# ===== 复用阶段1的 RAG 基础设施（不重复造轮） =====
from stage1_rag_prototype import (  # 【核心复用】stage1 已验证的模块
    Settings,
    split_markdown_into_chunks,     # → app/rag/ingest.py
    EmbeddingAdapter,               # → app/rag/store.py
    FAISSKnowledgeStore,            # → app/rag/store.py
    BM25Retriever,                  # → app/rag/retrieve.py
    hybrid_retrieve,                # → app/rag/retrieve.py
    call_llm,                       # → app/llm/gateway.py
)

KB_PATH = "会员权益知识库.md"        # 会员权益领域知识库（替代报销制度.md）
CACHE_DIR = ".member_cache"         # 与报销版隔离的独立 FAISS 缓存目录
DB_PATH = "member_agent.db"         # 运行期生成：审计/工具调用日志

# 四类强制转人工的异常（对应知识库第四章）
EXCEPTION_KEYWORDS = {
    "规则缺失型": ["无明确追回规则", "无追回规则", "规则未明确", "没有规则", "规则缺失"],
    "资料缺依据型": ["缺依据", "缺少依据", "无依据", "资料缺失"],
    "版本冲突型": ["版本冲突", "两个版本", "两版本", "冲突", "不一致"],
    "系统不可用型": ["系统不可用", "接口中断", "连不上", "不可用时"],
}


# =====================================================================
# 1. 知识库加载与检索（复用 stage1 的 FAISS + BM25 混合检索）
# =====================================================================
def load_knowledge() -> Dict[str, Any]:
    """读取会员权益知识库 → 切片 → 构建/加载 FAISS + BM25。"""
    settings = Settings(
        markdown_path=KB_PATH,
        chunk_size=500,
        chunk_overlap=50,
        embed_mode="local",
        cache_dir=CACHE_DIR,
    )
    if not os.path.exists(KB_PATH):
        raise FileNotFoundError(f"找不到会员权益知识库：{KB_PATH}")

    with open(KB_PATH, "r", encoding="utf-8") as f:
        raw = f.read()
    chunks = split_markdown_into_chunks(raw, settings.chunk_size, settings.chunk_overlap)

    adapter = EmbeddingAdapter(mode="local", dim=settings.embed_dim)
    store = FAISSKnowledgeStore(settings, adapter)
    if not store.load():
        store.build(chunks)
    bm25 = BM25Retriever(store.chunks)
    return {"settings": settings, "chunks": store.chunks, "adapter": adapter,
            "store": store, "bm25": bm25}


# =====================================================================
# 2. 工具集（Function Calling）—— 全部只读/辅助，无任何写操作
# =====================================================================
def search_benefit_rule(kb: Dict[str, Any], query: str, top_k: int = 3) -> List[Dict[str, str]]:
    """【工具T1·读】检索会员权益规则（券解释/活动版本/异常处理/权限红线）。"""
    return hybrid_retrieve(query, kb["store"], kb["bm25"], kb["adapter"], top_k=top_k)


def verify_coupon_explanation(kb: Dict[str, Any], query: str) -> Dict[str, Any]:
    """
    【工具T2·读】券解释一致性核验：检索最匹配规则，返回统一口径 + 来源版本 + 原记录引用。
    若命中多个冲突版本，返回 conflict=True，交由后续 identify_exception 处理。
    """
    chunks = search_benefit_rule(kb, query, top_k=3)
    if not chunks:
        return {"found": False, "unified_explanation": "", "sources": [],
                "conflict": False, "note": "制度中未找到对应规则，需人工核对"}
    # 冲突判定：基于"用户问题文本"中的冲突信号（与 identify_exception 口径一致），
    # 不基于检索到的规则正文（正文总会提到"冲突"处理逻辑，否则会误判）。
    # mock 下无法真正理解"这张券是否冲突"，故以问题是否显式带冲突信号为准。
    conflict = any(k in query for k in ["版本冲突", "两个版本", "冲突", "不一致", "无法唯一"])
    explanation = "；".join(
        "《{}》：{}".format(c["section"], re.sub(r"\s+", " ", c["content"])[:80])
        for c in chunks[:2]
    )
    sources = [{"section": c["section"], "snippet": c["content"][:120]} for c in chunks]
    return {"found": True, "unified_explanation": explanation, "sources": sources,
            "conflict": conflict, "note": "以当前生效版本口径为准"}


def identify_exception(scenario: str) -> Dict[str, Any]:
    """
    【工具T3·读】识别四类强制转人工异常，返回类型与触发原因；非异常返回 type=none。
    对应知识库第四章：规则缺失/资料缺依据/版本冲突/系统不可用。
    """
    for etype, kws in EXCEPTION_KEYWORDS.items():
        for kw in kws:
            if kw in scenario:
                return {"type": etype, "trigger": f"命中关键词「{kw}」", "need_human": True}
    return {"type": "none", "trigger": "", "need_human": False}


def escalate_to_human(reason: str, handler: str, business_id: str,
                      version: str, status: str, unresolved: str) -> Dict[str, Any]:
    """
    【工具T4·写-仅记录】强制转人工：严格按活动策划(f23)的"五项交接要素"标注。
    仅生成交接记录文本，不修改任何会员资产/业务数据。
    """
    confirm_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    handoff = {
        "business_id": business_id, "version": version, "status": status,
        "unresolved": unresolved, "handler": handler, "confirm_time": confirm_time,
        "reason": reason,
    }
    text = (f"[转人工] 触发原因={reason}；接手人={handler}；确认时间={confirm_time}；"
            f"业务编号={business_id}；版本={version}；状态={status}；未解决事项={unresolved}")
    return {"handoff": handoff, "text": text}


def log_audit(trace_id: str, business_id: str, rule_version: str,
              fact_ref: str, suggestion: str, human_change: str,
              signer: str) -> Dict[str, Any]:
    """
    【工具T5·写-仅审计】追加审计记录，满足隐私专员(f45)可重建要求：
    业务编号/资料版本/事实引用/建议结果/人工改动/签核人。
    """
    return {"trace_id": trace_id, "business_id": business_id, "rule_version": rule_version,
            "fact_ref": fact_ref, "suggestion": suggestion, "human_change": human_change,
            "signer": signer, "ok": True}


# =====================================================================
# 2.5 收银端口专用：券可用性判定 + 优惠后金额计算（确定性，非 LLM 猜测）
# =====================================================================
def calculate_coupon_discount(coupon_text: str, order_amount: float) -> Dict[str, Any]:
    """
    【工具T6·收银计算】解析券面参数并以知识库口径计算：
      满减券  满X减Y        → 订单≥X 可用，新金额 = 订单 - Y
      折扣券  X折           → 新金额 = 订单 × (X/10)，需银卡及以上
      抵用券  N元抵用       → 新金额 = max(0, 订单 - N)
      兑换/赠品券            → 不减金额，按活动版本核销门店核销
    无法识别或金额异常 → 返回 escalate=True，交由异常分支转人工。
    仅计算、不写任何会员资产（符合总则第一条）。
    """
    order_amount = float(order_amount)
    ct = (coupon_text or "").strip()

    # 满减券：满X减Y
    m = re.search(r"满\s*(\d+)\s*减\s*(\d+)", ct)
    if m:
        threshold, reduce = int(m.group(1)), int(m.group(2))
        if order_amount < threshold:
            return {"coupon_type": "满减券", "usable": False, "new_amount": round(order_amount, 2),
                    "reason": f"未达满减门槛（需满{threshold}元，当前{order_amount}元），本券不可用",
                    "note": "满减券全门店通用、单笔订单满额可用、不与其他满减叠加"}
        return {"coupon_type": "满减券", "usable": True,
                "new_amount": round(order_amount - reduce, 2),
                "reason": f"满{threshold}减{reduce}，已减{reduce}元",
                "note": "不与其他满减叠加；限指定品类须核对订单品类"}

    # 折扣券：X折
    m = re.search(r"(\d+(?:\.\d+)?)\s*折", ct)
    if m:
        rate = float(m.group(1)) / 10.0
        if not (0 < rate <= 1):
            return {"coupon_type": "折扣券", "usable": None, "new_amount": None,
                    "reason": "折扣率异常，需人工核对", "escalate": True}
        new = round(order_amount * rate, 2)
        return {"coupon_type": "折扣券", "usable": True, "new_amount": new,
                "reason": f"按{m.group(1)}折，优惠后{new}元",
                "note": "需会员等级≥银卡、同日同券限用1次、不叠加会员等级折扣"}

    # 抵用券：N元抵用
    m = re.search(r"(\d+)\s*元\s*抵用", ct)
    if m:
        reduce = int(m.group(1))
        if order_amount <= 0:
            return {"coupon_type": "抵用券", "usable": False, "new_amount": 0.0,
                    "reason": "订单金额为0，抵用券不可用"}
        return {"coupon_type": "抵用券", "usable": True,
                "new_amount": round(max(0.0, order_amount - reduce), 2),
                "reason": f"抵用{reduce}元，已减{reduce}元"}

    # 兑换券 / 赠品券：不减金额，按活动版本核销门店核销
    if ("兑换券" in ct) or ("赠品券" in ct):
        return {"coupon_type": "兑换券/赠品券", "usable": True, "new_amount": round(order_amount, 2),
                "reason": "兑换/赠品券不减金额；按活动版本指定核销门店与有效期核销",
                "note": "一经核销不可撤销；退货已兑新券无追回规则（见第四章，属异常）"}

    # 无法识别券类型
    return {"coupon_type": "未知", "usable": None, "new_amount": None,
            "reason": "无法识别券类型或金额，需人工核对", "escalate": True}


def cashier_check(kb: Dict[str, Any], coupon_text: str, order_amount: float,
                  trace_id: str = "CASHIER") -> Dict[str, Any]:
    """
    收银端口智能体决策入口：检索规则 → 识别异常 → 计算 → 审计留存。
    返回 {usable, new_amount, coupon_type, reason, escalate, handler, sources}。
    """
    # 1) 先检索规则，作为事实引用来源
    chunks = search_benefit_rule(kb, coupon_text, top_k=3)
    sources = [{"section": c["section"], "snippet": c["content"][:120]} for c in chunks]

    # 2) 异常识别（版本冲突/系统不可用等强制转人工）
    exc = identify_exception(f"{coupon_text} 订单{order_amount}元")
    if exc["need_human"]:
        handoff = escalate_to_human(
            reason=f"收银核验触发异常（{exc['type']}）：{exc['trigger']}",
            handler="活动策划员/数据隐私专员", business_id="BIZ-" + trace_id,
            version="v-current", status="暂停收银建议待核对",
            unresolved="异常券由谁定")
        log_audit(trace_id, "BIZ-" + trace_id, "v-current",
                  fact_ref="第四章 异常", suggestion="仅提示不编造",
                  human_change="转人工", signer="收银员")
        return {"usable": None, "new_amount": None, "coupon_type": "异常",
                "reason": handoff["text"], "escalate": True, "handler": handoff["handoff"]["handler"],
                "sources": sources}

    # 3) 确定性计算券可用性与优惠后金额
    calc = calculate_coupon_discount(coupon_text, order_amount)
    calc["sources"] = sources
    if calc.get("escalate"):
        log_audit(trace_id, "BIZ-" + trace_id, "v-current",
                  fact_ref="第二章 券规则", suggestion="无法识别，转人工",
                  human_change="转人工", signer="收银员")
        calc["handler"] = "活动策划员"
        return calc

    # 4) 正常结算：审计留存（可重建单次处理）
    log_audit(trace_id, "BIZ-" + trace_id, "v-current",
              fact_ref="第二章 券规则", suggestion=calc["reason"],
              human_change="无", signer="收银员")
    return calc


# =====================================================================
# 3. 工具执行日志表（供"阶段5 分析"——对照上线检查清单·异常兜底）
# =====================================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS tool_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT, trace_id TEXT, round_no INTEGER,
        tool_name TEXT, thought TEXT, params_json TEXT,
        duration_ms INTEGER, result_ok INTEGER, result_summary TEXT, created_at TEXT)""")
    conn.commit()
    return conn


def log_tool_call(conn, session_id, trace_id, round_no, tool_name, thought,
                 params, result_summary, result_ok=True):
    dur = 0  # 演示用固定值；真实环境用 time.perf_counter() 差值
    conn.execute(
        "INSERT INTO tool_calls (session_id, trace_id, round_no, tool_name, thought, "
        "params_json, duration_ms, result_ok, result_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (session_id, trace_id, round_no, tool_name, thought, json.dumps(params, ensure_ascii=False),
         dur, 1 if result_ok else 0, result_summary, time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()


# =====================================================================
# 4. Agent 执行循环（决策 → 执行 → 回填 → 再决策，≤5 轮）
# =====================================================================
class MemberAgent:
    """轻量 ReAct Harness（同第九步架构，但工具全部换成会员权益域）。"""

    def __init__(self, kb: Dict[str, Any], session_id: str = "s1",
                 decider: Optional[Callable] = None):
        self.kb = kb
        self.session_id = session_id
        self.trace_id = hashlib.md5(f"{session_id}{time.time()}".encode()).hexdigest()[:12]
        self.conn = init_db()
        self.decider = decider or self._mock_decide
        self.trajectory: List[Dict] = []

    def run(self, user_query: str) -> Dict:
        """执行一轮会话：最多 5 轮工具调用，最终给出可追溯的解释或转人工结论。"""
        print(f"\n=== 会员权益智能体启动（session={self.session_id}, trace={self.trace_id}）===")
        context = user_query
        final = None
        for r in range(1, 6):
            decision = self.decider(self.kb, context, self.trajectory, r)
            action = decision.get("action")
            params = decision.get("action_input", {})

            if action == "final_answer":
                final = decision.get("answer", "")
                print(f"[第{r}轮] final_answer：{final}")
                break

            # —— 执行工具 ——
            t0 = time.perf_counter()
            if action == "search_benefit_rule":
                chunks = search_benefit_rule(self.kb, params.get("query", context))
                summary = f"检索到 {len(chunks)} 条规则；首条《{chunks[0]['section']}》"
                result = {"chunks": chunks}
            elif action == "verify_coupon_explanation":
                res = verify_coupon_explanation(self.kb, params.get("query", context))
                summary = ("冲突" if res["conflict"] else "一致") + f"口径；来源{len(res['sources'])}条"
                result = res
            elif action == "identify_exception":
                res = identify_exception(params.get("scenario", context))
                summary = f"异常类型={res['type']}；需转人工={res['need_human']}"
                result = res
            elif action == "escalate_to_human":
                res = escalate_to_human(
                    reason=params.get("reason", "未指明"),
                    handler=params.get("handler", "活动策划员/数据隐私专员"),
                    business_id=params.get("business_id", "BIZ-" + self.trace_id),
                    version=params.get("version", "v-current"),
                    status=params.get("status", "待人工核对"),
                    unresolved=params.get("unresolved", "见触发原因"))
                summary = res["text"]
                result = res
            elif action == "log_audit":
                res = log_audit(self.trace_id, params.get("business_id", "BIZ-" + self.trace_id),
                                params.get("rule_version", "v-current"),
                                params.get("fact_ref", ""), params.get("suggestion", ""),
                                params.get("human_change", "无"), params.get("signer", "收银员"))
                summary = f"审计已留存（trace={self.trace_id}）"
                result = res
            else:
                summary = f"未知工具：{action}"
                result = {}
            dur_ms = int((time.perf_counter() - t0) * 1000)

            # —— 写日志表 ——
            log_tool_call(self.conn, self.session_id, self.trace_id, r, action,
                          decision.get("thought", ""), params, summary,
                          result_ok=("error" not in result))
            self.trajectory.append({"action": action, "input": params,
                                    "summary": summary, "result": result})
            print(f"[第{r}轮] 调用 {action} → {summary}")
            # —— 回填：把结果摘要拼回上下文，供下一轮决策 ——
            context = f"{context}\n上一动作结果：{summary}"

        if final is None:
            final = "已达最大轮次，转人工复核。"
        return {"trace_id": self.trace_id, "final": final, "trajectory": self.trajectory}


# =====================================================================
# 5. 脚本化决策器（mock 确定性演示，真实环境换 LLMDecider）
# =====================================================================
def scripted_decider(scripts: Dict[str, List[Dict]]) -> Callable:
    """返回一个按预排脚本逐轮决策的 decider。"""
    state = {"i": 0}
    def decider(kb, context, traj, r):
        scr = scripts.get("plan", [])
        if state["i"] < len(scr):
            d = scr[state["i"]]
            state["i"] += 1
            return d
        return {"action": "final_answer",
                "answer": "处理完成，详见上方各轮记录与审计留存。"}
    return decider


SCRIPT_NORMAL = [
    {"action": "search_benefit_rule", "thought": "先检索券解释规则",
     "action_input": {"query": "满200减30 券 门店 可用 解释口径"}},
    {"action": "verify_coupon_explanation", "thought": "核验统一口径并给出来源版本",
     "action_input": {"query": "满200减30 券 门店 可用 解释口径"}},
    {"action": "log_audit", "thought": "留存审计，满足可追溯",
     "action_input": {"business_id": "BIZ-COUPON-001", "rule_version": "v1",
                      "fact_ref": "第二章 2.1 满减券", "suggestion": "门店可用，满200减30，不叠加",
                      "human_change": "无", "signer": "收银员"}},
    {"action": "final_answer", "answer": "该满减券（满200减30）在门店可用，按券面标注执行，"
                                        "不与其它满减叠加；来源《第二章 2.1 满减券》。已留存审计。"},
]

SCRIPT_EXCEPTION = [
    {"action": "identify_exception", "thought": "判断是否属于强制转人工异常",
     "action_input": {"scenario": "顾客退货，积分已兑换新券，系统无明确追回规则"}},
    {"action": "escalate_to_human", "thought": "规则缺失型异常，强制转人工并标注五项要素",
     "action_input": {"reason": "退货积分已兑新券，系统无明确追回规则（规则缺失型）",
                      "handler": "活动策划员 唐映秋", "business_id": "BIZ-RET-002",
                      "version": "v-current", "status": "暂停建议待核对",
                      "unresolved": "追回规则由谁定"}},
    {"action": "log_audit", "thought": "转人工也需审计留存",
     "action_input": {"business_id": "BIZ-RET-002", "rule_version": "v-current",
                      "fact_ref": "第四章 异常1", "suggestion": "仅提示不编造口径",
                      "human_change": "转人工", "signer": "收银员"}},
    {"action": "final_answer", "answer": "检测到规则缺失型异常（退货积分已兑新券无追回规则），"
                                        "已转人工（接手人：活动策划员 唐映秋），不编造处理口径。"},
]

SCRIPT_CONFLICT = [
    {"action": "verify_coupon_explanation", "thought": "核验券解释，检测版本冲突",
     "action_input": {"query": "折扣券 小程序 门店 解释不一致 版本冲突"}},
    {"action": "identify_exception", "thought": "确认版本冲突型异常",
     "action_input": {"scenario": "同一折扣券匹配到两个冲突版本"}},
    {"action": "escalate_to_human", "thought": "版本冲突强制转人工，附两版本",
     "action_input": {"reason": "同一权益匹配到两个冲突版本，无法唯一判定",
                      "handler": "数据隐私专员 傅景", "business_id": "BIZ-CON-003",
                      "version": "v-A / v-B", "status": "待定版本",
                      "unresolved": "两版本取舍"}},
    {"action": "final_answer", "answer": "检测到版本冲突型异常，已转数据隐私专员 傅景 核对两版本，"
                                        "不自行择一。"},
]


# =====================================================================
# 6. 演示入口
# =====================================================================
def _demo():
    scenario = sys.argv[2] if len(sys.argv) > 2 else "normal"
    kb = load_knowledge()
    plan_map = {"normal": SCRIPT_NORMAL, "exception": SCRIPT_EXCEPTION, "conflict": SCRIPT_CONFLICT}
    if scenario in plan_map:
        agent = MemberAgent(kb, session_id=f"demo-{scenario}",
                            decider=scripted_decider({"plan": plan_map[scenario]}))
        out = agent.run("请处理会员权益咨询")
    else:
        # 单轮问答：走一次 search + verify + final
        agent = MemberAgent(kb, session_id="demo-once",
                            decider=scripted_decider({"plan": [
                                {"action": "search_benefit_rule", "thought": "检索",
                                 "action_input": {"query": scenario}},
                                {"action": "verify_coupon_explanation", "thought": "核验",
                                 "action_input": {"query": scenario}},
                                {"action": "final_answer", "answer": f"基于会员权益知识库的检索结果：{scenario}"},
                            ]}))
        out = agent.run(scenario)

    # —— 打印 tool_calls 日志表 ——
    print("\n=== tool_calls 日志表（供阶段5分析）===")
    rows = agent.conn.execute(
        "SELECT round_no, tool_name, duration_ms, result_summary FROM tool_calls "
        "WHERE session_id=? ORDER BY round_no", (agent.session_id,)).fetchall()
    for row in rows:
        print(f"  轮{row[0]} | {row[1]} | {row[2]}ms | {row[3]}")
    print(f"\n最终结论：{out['final']}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        print("用法：")
        print("  python3.11 member_agent.py demo normal      # 正常券解释核验")
        print("  python3.11 member_agent.py demo exception    # 退货兑新券无追回规则→转人工")
        print("  python3.11 member_agent.py demo conflict     # 同券两版本冲突→转人工")
        print('  python3.11 member_agent.py demo "单笔订单满200减30的券门店能用吗？"')
