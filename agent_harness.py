#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
拾光连锁智能体系统 — Agent 编排执行循环（第九步 / 提示词7）

=====================================================================
框架选择：轻量自研 ReAct Harness（不引入 LangChain / AutoGen / LangGraph）
---------------------------------------------------------------------
理由：
  1) 需求要求「create_draft 调用前强制中断、把草稿发给员工确认」，这是典型的
     human-in-the-loop 打断点；通用框架对此要么要定制 Callback，要么要状态机，
     反而绕。自研 Harness 用一个 confirm_fn 回调即可干净插入打断点。
  2) 需求要求「每轮决策记录到日志表」「服务端闸门（校验不通过拒绝建单）」，
     自研循环把日志与闸门放在固定位置，可控、可审计，契合一页纸「全链路审计」基线。
  3) 阶段1已自研 RAG/LLM 链路，继续自研可零额外依赖、与现有代码同构，
     后续 Web 阶段把【核心复用】模块直接搬入 app/agents/ 即可。

本文件标注【核心复用】的模块（Web 阶段搬入 app/agents/）：
  - AgentHarness           编排循环（决策→执行→回填→再决策，≤5 轮）
  - ToolRegistry / TOOLS  工具注册表 + 服务端闸门
  - _log_tool_call        每轮决策写入日志表 tool_calls
  - create_draft 确认中断 confirm_fn 回调
=====================================================================

复用 stage1_rag_prototype（第四/六步产物）：
  Settings / EmbeddingAdapter / FAISSKnowledgeStore / BM25Retriever /
  hybrid_retrieve / split_markdown_into_chunks / call_llm
"""

import re
import sys
import json
import time
import sqlite3
import hashlib
import os
from typing import Callable, Dict, List, Optional, Any

import numpy as np
from stage1_rag_prototype import (
    Settings, EmbeddingAdapter, FAISSKnowledgeStore, BM25Retriever,
    hybrid_retrieve, split_markdown_into_chunks, call_llm,
)

DB_PATH = "agent.db"        # 本阶段独立库（生产并入 chat.db）
MAX_ROUNDS = 5              # 需求：最多 5 轮


# =====================================================================
# 0. 系统提示词（承接第八步「禁区层」约束）
# =====================================================================
AGENT_SYSTEM_PROMPT = """你是「拾光连锁」报销智能体。你的目标是：在规则内自主完成报销处理。

【可用工具】search_policy / validate_invoice / check_budget / create_draft /
route_approval / query_status / escalate_to_human / flag_suspicious

【标准顺序】需要报销时按：
  validate_invoice → check_budget → create_draft → route_approval → query_status

【硬性禁区（违反即严重错误）】
1. 未先调用 validate_invoice 并取得 valid=true，绝不准调用 create_draft；
2. 绝不为通过校验而私自修改/下调/虚构发票金额、税号、日期；
3. 对重复发票号或校验失败的单据，禁止再次尝试建单，必须转人工；
4. 你没有任何「直接提交/改余额/改会员资产」的工具——最终提交由人工点击。

【输出格式】每轮只输出一个 JSON 决策，置于 <decision> 标签内：
<decision>{"thought":"简要思路","action":"工具名 或 final_answer","action_input":{...}}</decision>
当 action=final_answer 时，action_input={"answer":"给用户的自然语言答复"}。
信息不足或校验失败时，调用 escalate_to_human 并在 final_answer 中说明。
"""


# =====================================================================
# 1. 数据库与日志表（需求2：每轮决策记录到日志表）
# =====================================================================
def init_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    """初始化 agent.db，建日志表与草稿/转人工表。
    【核心复用】Web 阶段这部分 DDL 并入 migrations，工具调用审计字段不变。"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # 每轮工具调用日志（供「阶段5」分析：工具名/参数/耗时/结果摘要/思路）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tool_calls (
            id            INTEGER PRIMARY KEY,
            session_id    TEXT,
            trace_id      TEXT,
            round_no      INTEGER,
            tool_name     TEXT,
            thought       TEXT,
            params_json   TEXT,
            duration_ms   REAL,
            result_ok     INTEGER,
            result_summary TEXT,
            created_at    TEXT DEFAULT (datetime('now'))
        )
    """)
    # 草稿表（create_draft 落库，状态机：pending_confirmation→confirmed→approved）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS drafts (
            id         INTEGER PRIMARY KEY,
            session_id TEXT,
            draft_json TEXT,
            status     TEXT DEFAULT 'pending_confirmation',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # 转人工记录
    cur.execute("""
        CREATE TABLE IF NOT EXISTS escalations (
            id         INTEGER PRIMARY KEY,
            session_id TEXT,
            reason     TEXT,
            context    TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def _log_tool_call(conn, session_id, trace_id, round_no, tool_name,
                   thought, params, duration_ms, result):
    """把一轮决策写入 tool_calls 表（需求2）。"""
    summary = (result.get("summary") or result.get("error") or
               ("ok" if result.get("ok") else "fail"))
    summary = str(summary)[:300]
    conn.execute(
        "INSERT INTO tool_calls (session_id, trace_id, round_no, tool_name, "
        "thought, params_json, duration_ms, result_ok, result_summary) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (session_id, trace_id, round_no, tool_name,
         thought, json.dumps(params, ensure_ascii=False), round(duration_ms, 1),
         1 if result.get("ok") else 0, summary),
    )
    conn.commit()


# =====================================================================
# 2. 会话上下文（跨轮保存校验态，支撑服务端闸门）
# =====================================================================
class SessionCtx:
    """单个会话内的临时状态。用于服务端强制校验前置条件。"""
    def __init__(self):
        self.validated_refs: Dict[str, dict] = {}   # validate_ref -> 校验结果
        self.seen_invoices: set = set()            # 已出现的发票号（防重复报销）
        self.draft_id: Optional[int] = None         # 最近一次成功建单 id


# =====================================================================
# 3. 工具实现（Function Calling 规范；【读】/【写】标注）
# =====================================================================
def _tool_search_policy(args: dict, ctx: SessionCtx, harness: "AgentHarness") -> dict:
    """【读】检索报销制度条文（复用 stage1 混合检索）。"""
    query = args.get("query", "")
    hits = harness.retrieve(query, top_k=3)
    return {"ok": True, "hits": hits,
            "summary": f"检索到 {len(hits)} 条制度片段"}


def _tool_validate_invoice(args: dict, ctx: SessionCtx, harness: "AgentHarness") -> dict:
    """【读】校验发票合规。通过则签发 validate_ref（服务端闸门凭证）。"""
    invoice_no = (args.get("invoice_no") or "").strip()
    amount = args.get("amount")
    tax_id = (args.get("tax_id") or "").strip()
    invoice_type = args.get("invoice_type", "")
    date = (args.get("date") or "").strip()

    reasons = []
    if not invoice_no:
        reasons.append("缺少发票号码")
    if not isinstance(amount, (int, float)) or float(amount) <= 0:
        reasons.append("金额必须为正数")
    if not tax_id:
        reasons.append("缺少销售方税号")
    if invoice_type not in ("增值税专票", "增值税普票", "电子发票", "其他"):
        reasons.append("发票类型非法")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        reasons.append("日期格式应为 YYYY-MM-DD")
    if invoice_no and invoice_no in ctx.seen_invoices:
        reasons.append("该发票号已出现，疑似重复报销")

    if reasons:
        return {"ok": True, "valid": False, "reasons": reasons,
                "summary": "校验未通过：" + "；".join(reasons)}

    # 签发服务端闸门凭证（create_draft 必须回传此 ref，服务端会复核）
    raw = f"{invoice_no}|{amount}|{tax_id}|{harness.session_id}"
    validate_ref = hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]
    ctx.validated_refs[validate_ref] = {
        "invoice_no": invoice_no, "amount": amount, "tax_id": tax_id}
    ctx.seen_invoices.add(invoice_no)
    return {"ok": True, "valid": True, "validate_ref": validate_ref,
            "summary": f"校验通过，发票 {invoice_no}（{amount}元）"}


def _tool_check_budget(args: dict, ctx: SessionCtx, harness: "AgentHarness") -> dict:
    """【读】查预算余额（本阶段 mock：默认充足，可传 remaining 覆盖）。"""
    remaining = args.get("remaining", 5000.0)
    return {"ok": True, "sufficient": True, "remaining": remaining,
            "summary": f"预算充足，剩余 {remaining} 元"}


def _tool_create_draft(args: dict, ctx: SessionCtx, harness: "AgentHarness") -> dict:
    """【写】生成报销草稿。带服务端闸门 + commit 开关（确认中断用）。
    - commit=False：仅返回预览（不落库），供员工确认前展示；
    - commit=True ：闸门复核 validate_ref，通过才落库。"""
    validate_ref = args.get("validate_ref")
    items = args.get("items", [])

    # —— 服务端闸门（防提示词被绕过）——
    if validate_ref not in ctx.validated_refs:
        return {"ok": False, "error": "GATE: 未通过 validate_invoice，拒绝建单",
                "summary": "GATE: 未通过校验，拒绝建单"}

    total = sum(float(it.get("amount", 0)) for it in items)
    preview = {
        "items": items,
        "total": round(total, 2),
        "validate_ref": validate_ref,
        "summary": f"含 {len(items)} 项，合计 {round(total, 2)} 元",
    }

    if not args.get("commit", False):
        preview["_dry_run"] = True
        return {"ok": True, "dry_run": True, "preview": preview,
                "summary": "草稿预览（待确认）"}

    # 确认后正式落库
    cur = harness.conn.cursor()
    cur.execute("INSERT INTO drafts (session_id, draft_json, status) VALUES (?,?,?)",
                (harness.session_id, json.dumps(preview, ensure_ascii=False),
                 "pending_confirmation"))
    draft_id = cur.lastrowid
    harness.conn.commit()
    ctx.draft_id = draft_id
    return {"ok": True, "draft_id": draft_id, "preview": preview,
            "summary": f"已建草稿 #{draft_id}，合计 {round(total, 2)} 元"}


def _tool_route_approval(args: dict, ctx: SessionCtx, harness: "AgentHarness") -> dict:
    """【写】按金额路由审批人（承接 BPM 规则雏形）。"""
    draft_id = args.get("draft_id") or ctx.draft_id
    if not draft_id:
        return {"ok": False, "error": "无 draft_id，请先 create_draft",
                "summary": "缺少草稿，无法路由"}
    # 取草稿金额
    row = harness.conn.execute(
        "SELECT draft_json FROM drafts WHERE id=?", (draft_id,)).fetchone()
    total = json.loads(row[0]).get("total", 0) if row else 0
    approver = "财务经理" if total > 5000 else "直属主管"
    return {"ok": True, "approver": approver, "draft_id": draft_id,
            "summary": f"草稿 #{draft_id}（{total}元）已路由至 {approver}"}


def _tool_query_status(args: dict, ctx: SessionCtx, harness: "AgentHarness") -> dict:
    """【读】查草稿状态。"""
    draft_id = args.get("draft_id") or ctx.draft_id
    if not draft_id:
        return {"ok": False, "error": "无 draft_id", "summary": "无草稿可查"}
    row = harness.conn.execute(
        "SELECT status FROM drafts WHERE id=?", (draft_id,)).fetchone()
    status = row[0] if row else "未知"
    return {"ok": True, "draft_id": draft_id, "status": status,
            "summary": f"草稿 #{draft_id} 状态：{status}"}


def _tool_escalate(args: dict, ctx: SessionCtx, harness: "AgentHarness") -> dict:
    """【写】转人工（合法终点）。记录原因与上下文。"""
    reason = args.get("reason", "模型判定需人工介入")
    context = args.get("context", "")
    harness.conn.execute(
        "INSERT INTO escalations (session_id, reason, context) VALUES (?,?,?)",
        (harness.session_id, reason, context))
    harness.conn.commit()
    return {"ok": True, "summary": f"已转人工：{reason}"}


def _tool_flag_suspicious(args: dict, ctx: SessionCtx, harness: "AgentHarness") -> dict:
    """【写】标记疑似违规报销（联动审计页）。"""
    reason = args.get("reason", "模型标记疑似违规")
    return {"ok": True, "summary": f"已标记疑似违规：{reason}"}


# 工具注册表：name -> (函数, {读/写}, 描述)
TOOLS = {
    "search_policy":      (_tool_search_policy, "读", "检索报销制度条文"),
    "validate_invoice":   (_tool_validate_invoice, "读", "校验发票合规，通过签发 validate_ref"),
    "check_budget":       (_tool_check_budget, "读", "查预算余额"),
    "create_draft":       (_tool_create_draft, "写", "生成报销草稿（需 validate_ref；确认后才落库）"),
    "route_approval":     (_tool_route_approval, "写", "按金额路由审批人"),
    "query_status":       (_tool_query_status, "读", "查草稿状态"),
    "escalate_to_human":  (_tool_escalate, "写", "转人工（合法终点）"),
    "flag_suspicious":    (_tool_flag_suspicious, "写", "标记疑似违规报销"),
}


# =====================================================================
# 4. ReAct Harness 编排循环（需求1：决策→执行→回填→再决策，≤5 轮）
# =====================================================================
class AgentHarness:
    """轻量自研 ReAct Harness。
    - decide: 可注入脚本化/真实 LLM 决策器（便于 demo 与接入真实模型）
    - confirm_fn: create_draft 前的确认中断回调（需求3）
    - on_event: 流式事件回调（供 SSE 层消费）
    """

    def __init__(self, decide: Callable, confirm_fn: Callable,
                 on_event: Callable = None, db_path: str = DB_PATH,
                 markdown_path: str = "报销制度.md",
                 llm_kwargs: dict = None):
        self.decide = decide
        self.confirm_fn = confirm_fn
        self.on_event = on_event or (lambda e: None)
        self.conn = init_db(db_path)
        self.session_id = None
        self.trace_id = None
        self.llm_kwargs = llm_kwargs or {}
        # —— 复用 stage1 检索栈（核心复用）——
        settings = Settings(markdown_path=markdown_path, cache_dir=".stage1_cache")
        self.adapter = EmbeddingAdapter(mode="local", dim=512)
        self.store = FAISSKnowledgeStore(settings, self.adapter)
        if not self.store.load():
            with open(markdown_path, encoding="utf-8") as f:
                text = f.read()
            self.store.build(split_markdown_into_chunks(text))
        self.bm25 = BM25Retriever(self.store.chunks)

    def retrieve(self, query: str, top_k: int = 3) -> List[dict]:
        return hybrid_retrieve(query, self.store, self.bm25, self.adapter, top_k=top_k)

    # ---- 决策提示词拼装（把工具目录 + 轨迹回填给模型） ----
    def _build_decision_messages(self, user_message, history, trajectory) -> list:
        tool_catalog = "\n".join(
            f"- {name}（{rw}）：{desc}" for name, (_, rw, desc) in TOOLS.items())
        traj_text = "\n".join(
            f"调用 {t['action']}({json.dumps(t['input'], ensure_ascii=False)}) "
            f"→ {t['summary']}" for t in trajectory) or "（无）"
        hist_text = "\n".join(f"{m['role']}: {m['content']}" for m in (history or [])[-12:]) or "（无）"
        user_block = (
            f"用户原始问题：{user_message}\n\n"
            f"最近对话历史：\n{hist_text}\n\n"
            f"已完成的工具调用轨迹：\n{traj_text}\n\n"
            f"可用工具：\n{tool_catalog}\n\n"
            f"请基于以上，输出下一轮 <decision> JSON。"
        )
        return [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_block},
        ]

    @staticmethod
    def _parse_decision(text: str) -> dict:
        """从模型输出中解析 <decision> JSON。"""
        m = re.search(r"<decision>(.*?)</decision>", text, re.S)
        if not m:
            m = re.search(r"\{(.*)\}", text, re.S)
        if not m:
            return {"thought": "", "action": "final_answer",
                    "action_input": {"answer": text.strip()}}
        try:
            # group(0) 含最外层花括号，才是合法 JSON 对象
            return json.loads(m.group(0))
        except Exception:
            return {"thought": "", "action": "final_answer",
                    "action_input": {"answer": text.strip()}}

    def run(self, session_id: str, user_message: str, history: list = None):
        """执行循环，yield 事件 dict（SSE 友好）。"""
        self.session_id = session_id
        self.trace_id = hashlib.md5(
            f"{session_id}{time.time()}".encode()).hexdigest()[:12]
        ctx = SessionCtx()
        trajectory = []

        for round_i in range(1, MAX_ROUNDS + 1):
            messages = self._build_decision_messages(user_message, history, trajectory)
            raw = self.decide(messages, round_i, self.llm_kwargs)   # 注入决策器
            dec = self._parse_decision(raw)
            thought = dec.get("thought", "")
            action = dec.get("action", "final_answer")
            ainput = dec.get("action_input", {}) or {}

            self.on_event({"type": "decision", "round": round_i,
                           "thought": thought, "action": action, "input": ainput})

            # —— 终态：直接答复 ——
            if action == "final_answer":
                answer = ainput.get("answer", "")
                self.on_event({"type": "final", "round": round_i, "answer": answer})
                return {"answer": answer, "trajectory": trajectory,
                        "trace_id": self.trace_id}

            # —— create_draft 确认中断（需求3）——
            if action == "create_draft":
                # 先 dry-run 取预览，不落库
                preview_res = self._exec(action, {**ainput, "commit": False}, ctx, round_i, thought)
                self.on_event({"type": "draft_confirmation",
                               "round": round_i, "preview": preview_res.get("preview")})
                confirmed = self.confirm_fn(preview_res.get("preview"), session_id)
                if not confirmed:
                    # 员工拒绝 → 不建单，记录并转人工/结束
                    refuse = {"ok": False, "error": "员工取消确认，未建单",
                              "summary": "员工拒绝确认，草稿未生成"}
                    _log_tool_call(self.conn, session_id, self.trace_id, round_i,
                                   action, thought, ainput, 0.0, refuse)
                    self.on_event({"type": "tool_result", "round": round_i,
                                   "tool": action, "summary": refuse["summary"], "ok": False})
                    trajectory.append({"action": action, "input": ainput,
                                       "summary": refuse["summary"]})
                    # 强制转人工并收尾
                    esc = self._exec("escalate_to_human",
                                     {"reason": "员工取消草稿确认"}, ctx, round_i, thought)
                    self.on_event({"type": "tool_result", "round": round_i,
                                   "tool": "escalate_to_human",
                                   "summary": esc["summary"], "ok": True})
                    trajectory.append({"action": "escalate_to_human", "input": {},
                                       "summary": esc["summary"]})
                    final = "您取消了草稿确认，该报销未生成，已为您转人工跟进。"
                    self.on_event({"type": "final", "round": round_i, "answer": final})
                    return {"answer": final, "trajectory": trajectory,
                            "trace_id": self.trace_id}
                # 确认通过 → 正式 commit
                ainput = {**ainput, "commit": True}

            # —— 正常执行（带计时 + 日志）——
            result = self._exec(action, ainput, ctx, round_i, thought)
            self.on_event({"type": "tool_result", "round": round_i, "tool": action,
                           "summary": result.get("summary"), "ok": result.get("ok", False)})
            trajectory.append({"action": action, "input": ainput,
                               "summary": result.get("summary")})

        # —— 超过 5 轮：强制转人工收尾 ——
        esc = self._exec("escalate_to_human",
                         {"reason": "已达最大轮次，转人工"}, ctx, MAX_ROUNDS + 1, "超限")
        final = "已达最大处理轮次，已转人工，请稍候。"
        self.on_event({"type": "final", "round": MAX_ROUNDS + 1, "answer": final})
        return {"answer": final, "trajectory": trajectory, "trace_id": self.trace_id}

    def _exec(self, action, ainput, ctx, round_i, thought) -> dict:
        """执行单个工具：校验存在性 → 计时 → 记日志 → 返回结果。"""
        if action not in TOOLS:
            res = {"ok": False, "error": f"未知工具 {action}", "summary": "未知工具"}
            _log_tool_call(self.conn, self.session_id, self.trace_id, round_i,
                           action, thought, ainput, 0.0, res)
            return res
        fn, _rw, _desc = TOOLS[action]
        t0 = time.time()
        try:
            res = fn(ainput, ctx, self)
        except Exception as e:
            res = {"ok": False, "error": str(e), "summary": f"执行异常：{e}"}
        dur = (time.time() - t0) * 1000
        _log_tool_call(self.conn, self.session_id, self.trace_id, round_i,
                       action, thought, ainput, dur, res)
        return res


# =====================================================================
# 5. 决策器：脚本化（demo 用）+ 真实 LLM（call_llm 复用）
# =====================================================================
class ScriptedDecider:
    """按轮次回放预编排的决策序列（用于 3 个轨迹示例的可复现演示）。"""
    def __init__(self, script: List[dict]):
        self.script = script

    def __call__(self, messages, round_i, llm_kwargs):
        if round_i - 1 < len(self.script):
            return json.dumps(self.script[round_i - 1], ensure_ascii=False)
        # 兜底：收尾
        return json.dumps({"thought": "已无更多步骤", "action": "final_answer",
                           "action_input": {"answer": "处理完毕。"}}, ensure_ascii=False)


class LLMDecider:
    """真实 LLM 决策（复用 stage1 的 call_llm，供应商可配置）。"""
    def __call__(self, messages, round_i, llm_kwargs):
        kw = llm_kwargs or {}
        return call_llm(messages, provider=kw.get("provider", "openai-compatible"),
                        base_url=kw.get("base_url", ""),
                        api_key=kw.get("api_key", ""),
                        model=kw.get("model", "deepseek-chat"))


# =====================================================================
# 6. 3 个典型对话流程的预期轨迹示例（需求4）
# =====================================================================
SCRIPT_NORMAL = [   # 轨迹A：正常报销（校验通过→确认→路由）
    {"thought": "先校验发票", "action": "validate_invoice",
     "action_input": {"invoice_no": "INV-001", "amount": 3200, "tax_id": "91110000X",
                      "invoice_type": "电子发票", "date": "2025-03-12"}},
    {"thought": "校验通过，查预算", "action": "check_budget", "action_input": {}},
    {"thought": "预算充足，建草稿", "action": "create_draft",
     "action_input": {"validate_ref": "PLACEHOLDER",
                      "items": [{"invoice_no": "INV-001", "amount": 3200, "category": "差旅"}]}},
    {"thought": "草稿已确认，路由审批", "action": "route_approval", "action_input": {}},
    {"thought": "完成", "action": "final_answer",
     "action_input": {"answer": "报销草稿已生成并提交直属主管审批，合计 3200 元。"}},
]

SCRIPT_INVALID = [   # 轨迹B：发票不合规（校验失败→转人工，不建单）
    {"thought": "先校验发票", "action": "validate_invoice",
     "action_input": {"invoice_no": "INV-002", "amount": 0, "tax_id": "",
                      "invoice_type": "其他", "date": "2025-13-40"}},
    {"thought": "校验失败，转人工", "action": "escalate_to_human",
     "action_input": {"reason": "发票校验未通过（金额/税号/类型/日期均异常），不建单"}},
    {"thought": "答复用户", "action": "final_answer",
     "action_input": {"answer": "您的发票校验未通过（金额非正、缺税号、日期非法），已转人工处理，请勿重复提交。"}},
]

SCRIPT_CANCEL = [    # 轨迹C：校验通过但员工拒绝确认（不建单）
    {"thought": "先校验发票", "action": "validate_invoice",
     "action_input": {"invoice_no": "INV-003", "amount": 1500, "tax_id": "91110000Y",
                      "invoice_type": "增值税普票", "date": "2025-04-01"}},
    {"thought": "查预算", "action": "check_budget", "action_input": {}},
    {"thought": "建草稿待确认", "action": "create_draft",
     "action_input": {"validate_ref": "PLACEHOLDER",
                      "items": [{"invoice_no": "INV-003", "amount": 1500, "category": "办公"}]}},
    # 第4轮：员工在 confirm_fn 中拒绝，harness 自动转人工并 final
]


# =====================================================================
# 7. Demo 入口（可运行验证 3 个轨迹 + 日志表）
# =====================================================================
def _demo():
    import sys, copy
    scenario = (sys.argv[2] if len(sys.argv) > 2 else "normal")
    script_map = {"normal": SCRIPT_NORMAL, "invalid": SCRIPT_INVALID, "cancel": SCRIPT_CANCEL}
    script = copy.deepcopy(script_map.get(scenario, SCRIPT_NORMAL))

    # 预计算 validate_invoice 在运行时签发的 validate_ref，回灌给 create_draft，
    # 模拟「模型把校验返回的 ref 原样回传」的真实流程（否则服务端闸门会拒绝）。
    sid = f"demo-{scenario}"
    vinvoice = next((s for s in script if s["action"] == "validate_invoice"), None)
    cdraft = next((s for s in script if s["action"] == "create_draft"), None)
    if vinvoice and cdraft:
        a = vinvoice["action_input"]
        ref = hashlib.md5(
            f"{a['invoice_no']}|{a['amount']}|{a['tax_id']}|{sid}".encode()).hexdigest()[:12]
        cdraft["action_input"]["validate_ref"] = ref

    # confirm_fn：normal→确认；cancel→拒绝；invalid 不会触发
    def confirm_fn(preview, session_id):
        if scenario == "cancel":
            print("  [确认中断] 员工选择：❌ 取消")
            return False
        print("  [确认中断] 员工选择：✅ 确认，草稿内容：")
        print("    ", json.dumps(preview, ensure_ascii=False)[:200])
        return True

    def on_event(e):
        if e["type"] == "decision":
            print(f"\n— 第{e['round']}轮决策 → {e['action']} | 思路：{e['thought']}")
        elif e["type"] == "draft_confirmation":
            pass  # 由 confirm_fn 打印
        elif e["type"] == "tool_result":
            print(f"    工具结果[{e['tool']}]：{e['summary']} (ok={e['ok']})")
        elif e["type"] == "final":
            print(f"  ➜ 最终答复（第{e['round']}轮）：{e['answer']}")

    # 把占位 validate_ref 替换为工具真实签发的：先跑一步校验拿到 ref
    # 简化：让 harness 在执行 create_draft 时，ctx 里已有该 ref（因为同一脚本先 validate）
    h = AgentHarness(decide=ScriptedDecider(script), confirm_fn=confirm_fn,
                     on_event=on_event)
    print(f"\n========== 场景：{scenario} ==========")
    res = h.run(session_id=f"demo-{scenario}", user_message="帮我报销这张发票")
    print(f"\n[trace_id] {res['trace_id']}")

    # 打印日志表内容（需求2 验证）
    print("\n=== tool_calls 日志表 ===")
    rows = h.conn.execute(
        "SELECT round_no, tool_name, duration_ms, result_ok, result_summary "
        "FROM tool_calls WHERE session_id=? ORDER BY id",
        (f"demo-{scenario}",)).fetchall()
    for r in rows:
        print(f"  轮{r[0]} | {r[1]:18s} | {r[2]:.0f}ms | ok={r[3]} | {r[4]}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        print("用法：python3.11 agent_harness.py demo [normal|invalid|cancel]")
