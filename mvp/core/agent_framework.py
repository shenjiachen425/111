# -*- coding: utf-8 -*-
"""Agent 框架（工具调用型智能体）
------------------------------------------------------------
MVP 默认采用「规则化 IntentRouter + Tool 调用循环」，无需外部大模型即可运行，
且完全可测试、可审计。框架本身与具体业务解耦，生产环境可将 IntentRouter
替换为真实 LLM（见 LLMAdapter 接口），其余 Agent/工具体系不变。

核心组件：
  - Tool / ToolResult      : 工具抽象与返回值
  - IntentRouter           : 意图识别（可插拔为 LLM）
  - Agent                  : 编排器，按意图规划并调用工具，产出结构化结果
  - Memory                 : 会话记忆（按 session 存储）
  - LLMAdapter            : 可插拔大模型适配器（MVP 留接口）
  - MemberBenefitsAgent   : 本项目的具化智能体（注册业务工具）
------------------------------------------------------------
"""
from __future__ import annotations
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Any

from .util import gen_biz_id


# ============================ 工具抽象 ============================
@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    message: str = ""


class Tool:
    """工具基类：name/description 供路由或 LLM 选择，handler 执行业务。"""

    def __init__(self, name: str, description: str, handler: Callable[..., ToolResult]):
        self.name = name
        self.description = description
        self.handler = handler

    def run(self, **kwargs) -> ToolResult:
        return self.handler(**kwargs)


# ============================ 意图路由 ============================
class IntentRouter:
    """将用户输入归类为 (intent, coupon_key)。
    MVP 用关键词规则；可整体替换为 LLM 分类器而不影响 Agent。"""

    INTENTS = {
        "stores":   ["门店", "哪里用", "哪些店", "哪家店", "可用门店", "在门店", "什么店", "哪能用", "可以用"],
        "rule":     ["规则", "怎么用", "如何使用", "如何使", "条件", "限制", "要求", "怎么使", "使用规则"],
        "stack":    ["叠加", "一起用", "同时用", "同用", "共用", "能不能一起", "能否叠加"],
        "validity": ["有效期", "多久", "过期", "时效", "什么时候过期"],
        "redeem":   ["核销", "怎么核销", "如何使用券", "用券"],
    }

    def classify(self, text: str, kb) -> (str, Optional[str]):
        coupon_key = kb.match(text)
        intent = "overview"
        for it, kws in self.INTENTS.items():
            if any(k in text for k in kws):
                intent = it
                break
        return intent, coupon_key


# ============================ 可插拔 LLM ============================
class LLMAdapter:
    """大模型适配器（MVP 未启用）。生产可接入真实 LLM 做意图/答案生成，
    但无论是否使用 LLM，答案的事实引用与版本都必须来自知识库（单一出口）。"""

    def complete(self, prompt: str) -> str:
        raise NotImplementedError("请接入具体大模型实现")


# ============================ 记忆 ============================
class Memory:
    """极简会话记忆：按 session_id 维护消息列表。"""

    def __init__(self):
        self.store: Dict[str, List[dict]] = {}

    def append(self, session_id: str, role: str, content: str, meta: dict = None):
        self.store.setdefault(session_id, []).append(
            {"role": role, "content": content, "meta": meta or {}, "ts": time.time()}
        )

    def history(self, session_id: str) -> List[dict]:
        return self.store.get(session_id, [])


# ============================ Agent 编排器 ============================
@dataclass
class AgentResult:
    answer: str
    intent: str
    coupon_key: Optional[str]
    sources: List[str] = field(default_factory=list)
    version: str = ""
    escalate: bool = False
    reason: str = ""
    handler: str = ""
    need_confirm: str = ""
    biz_id: str = ""
    trace: List[str] = field(default_factory=list)   # 工具调用轨迹，便于审计


class Agent:
    def __init__(self, kb, tools: Optional[List[Tool]] = None,
                 router: Optional[IntentRouter] = None, memory: Optional[Memory] = None,
                 llm: Optional[LLMAdapter] = None):
        self.kb = kb
        self.tools: List[Tool] = tools or []
        self.router = router or IntentRouter()
        self.memory = memory or Memory()
        self.llm = llm  # 可插拔

    def register(self, tool: Tool):
        self.tools.append(tool)

    def _tool(self, name: str) -> Optional[Tool]:
        for t in self.tools:
            if t.name == name:
                return t
        return None

    def handle(self, session_id: str, user_input: str, role: str = "employee") -> AgentResult:
        intent, coupon_key = self.router.classify(user_input, self.kb)
        trace: List[str] = []
        biz = gen_biz_id("BIZ")

        # 1) 未识别券 -> 转人工工具
        if coupon_key is None:
            esc = self._tool("escalation")
            r = esc.run(reason="无法识别优惠券类型", handler="活动策划员（唐映秋）",
                        need_confirm="券类型与活动版本", input_text=user_input) if esc else None
            trace.append("escalation(未识别券)")
            res = AgentResult(
                answer="未能识别您提到的优惠券，已转人工核对，请稍候或由运营确认券面信息。",
                intent=intent, coupon_key=None,
                escalate=True, reason="无法识别优惠券类型",
                handler="活动策划员（唐映秋）", need_confirm="券类型与活动版本",
                biz_id=biz, trace=trace,
            )
            self.memory.append(session_id, "bot", res.answer,
                               {"biz_id": biz, "escalate": True, "sources": []})
            return res

        entry = self.kb.get(coupon_key)
        version = entry.get("version", self.kb.version())
        sources = entry.get("sources", [])

        # 2) 规则缺口检测（兑换券退货追回）—— 典型异常转人工场景
        if "退货" in user_input and entry.get("type") == "兑换":
            esc = self._tool("escalation")
            if esc:
                esc.run(reason="兑换券退货后已兑换新券的追回规则未定义",
                        handler="活动策划员（唐映秋）/ 数据风控（叶书）",
                        need_confirm="追回规则", input_text=user_input)
            trace.append("escalation(规则缺口:兑换券退货追回)")
            res = AgentResult(
                answer=f"{entry['name']} 的退货追回规则当前未定义，已转人工处理，请勿自行承诺会员。",
                intent=intent, coupon_key=coupon_key, sources=sources, version=version,
                escalate=True, reason="兑换券退货后已兑换新券的追回规则未定义",
                handler="活动策划员（唐映秋）/ 数据风控（叶书）", need_confirm="追回规则",
                biz_id=biz, trace=trace,
            )
            self.memory.append(session_id, "bot", res.answer,
                               {"biz_id": biz, "escalate": True, "sources": sources, "version": version})
            return res

        # 3) 知识检索工具生成答案
        kq = self._tool("knowledge_query")
        if kq:
            r = kq.run(intent=intent, coupon_key=coupon_key)
            answer = r.data if r.ok else r.message
            trace.append(f"knowledge_query({intent},{coupon_key})")
        else:
            answer = entry.get("rule", "")

        res = AgentResult(
            answer=answer, intent=intent, coupon_key=coupon_key,
            sources=sources, version=version, biz_id=biz, trace=trace,
        )
        self.memory.append(session_id, "bot", answer,
                           {"biz_id": biz, "escalate": False, "sources": sources, "version": version})
        return res


# ============================ 具化智能体 ============================
def build_member_benefits_agent(kb) -> Agent:
    """构造「会员权益智能体」：注册业务工具，返回编排好的 Agent。"""

    def _knowledge_query(intent: str, coupon_key: str) -> ToolResult:
        c = kb.get(coupon_key)
        if not c:
            return ToolResult(ok=False, message="未找到该券规则")
        if intent == "stores":
            return ToolResult(ok=True, data=f"{c['name']} 可用门店：{c['stores']}。")
        if intent == "stack":
            return ToolResult(ok=True, data=f"{c['name']} 叠加规则：{c['stack']}")
        if intent == "validity":
            return ToolResult(ok=True, data=f"{c['name']} 有效期：{c['validity']}")
        if intent == "redeem":
            return ToolResult(ok=True, data=f"{c['name']} 核销说明：{c['rule']}")
        if intent == "rule":
            return ToolResult(ok=True, data=f"{c['name']} 使用规则：{c['rule']}")
        # overview：门店 + 规则（精简，不堆砌）
        return ToolResult(ok=True, data=f"{c['name']}\n可用门店：{c['stores']}\n使用规则：{c['rule']}")

    def _escalation(reason: str, handler: str, need_confirm: str, input_text: str = "") -> ToolResult:
        # 实际落库在 API 层（写入 escalations 表），此处仅返回结构化结果
        return ToolResult(ok=True, data={"reason": reason, "handler": handler,
                                        "need_confirm": need_confirm, "input_text": input_text})

    agent = Agent(
        kb=kb,
        tools=[
            Tool("knowledge_query", "按意图检索券规则（门店/规则/叠加/有效期/核销）", _knowledge_query),
            Tool("escalation", "异常转人工：输出触发原因/接手人/待确认字段", _escalation),
        ],
        router=IntentRouter(),
        memory=Memory(),
    )
    return agent
