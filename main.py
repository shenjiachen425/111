#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
拾光连锁智能体系统 · 员工问答入口（后端）
=================================================
FastAPI + SQLite + SSE 流式。本文件是「提示词4：用户端聊天界面」的完整实现（第六步）。

设计要点（详见《接口设计.md》）：
  - POST /chat：接收 {session_id, question}，返回 text/event-stream 流。
  - 会话/消息存 SQLite（sessions / messages 两张表）。
  - 每次请求携带最近 6 轮历史作为上下文。
  - 所有接口先返回错误码 + 中文错误信息（基础校验走 HTTP 状态码，流中异常走 SSE error 事件）。
  - 【核心复用】检索与 LLM 能力直接复用 stage1_rag_prototype 的模块，不重复造轮子：
        split_markdown_into_chunks / EmbeddingAdapter / FAISSKnowledgeStore
        / BM25Retriever / hybrid_retrieve / call_llm
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Dict, Generator, List

import requests
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

# 【核心复用】直接导入阶段1 原型已验证的检索与 LLM 能力
from stage1_rag_prototype import (
    BM25Retriever,
    EmbeddingAdapter,
    FAISSKnowledgeStore,
    Settings,
    call_llm,
    hybrid_retrieve,
    split_markdown_into_chunks,
)

# ===================== 配置 =====================
DB_PATH = "app.db"
MD_PATH = "报销制度.md"          # 复用阶段1 的示例制度文档
CACHE_DIR = ".stage1_cache"      # 复用阶段1 的 FAISS 缓存目录
MAX_Q_LEN = 500                  # 单问题上限（接口设计§5 默认 500 字，超长拒收）
HISTORY_ROUNDS = 6               # 最近 6 轮历史（接口设计§6）
RATE_LIMIT_SEC = 1.0             # 限流：按会话最小间隔 1 秒（接口设计§8 默认策略）
# LLM 供应商（可配置，与环境变量一致；默认 mock 便于本地验证）
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

app = FastAPI(title="拾光智能体 · 员工问答入口")
rag = None                       # 启动时构建的 RAG 服务实例
_session_last: Dict[str, float] = {}   # 简易限流：会话ID -> 上次请求时间戳


# ===================== SQLite 持久化 =====================
def init_db() -> None:
    """建表：sessions（会话）与 messages（消息，含 sources 引用 JSON）。"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT DEFAULT '新对话',
            created_at TEXT NOT NULL,
            updated_at TEXT
        )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            sources TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, id)")
    conn.commit()
    conn.close()


def db() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_session(sid: str) -> None:
    """会话不存在则创建（宽松策略：前端传 session_id，缺失/未知则由后端建）。"""
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO sessions(id, created_at, updated_at) VALUES(?,?,?)",
        (sid, now_iso(), now_iso()),
    )
    conn.commit()
    conn.close()


def save_message(sid: str, role: str, content: str, sources) -> None:
    """写入一条消息；sources 为 JSON 字符串或 None。"""
    conn = db()
    conn.execute(
        "INSERT INTO messages(session_id, role, content, sources, created_at) VALUES(?,?,?,?,?)",
        (sid, role, content, sources, now_iso()),
    )
    conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now_iso(), sid))
    conn.commit()
    conn.close()


def get_history(sid: str, rounds: int) -> List[Dict[str, str]]:
    """取最近 N 轮（=2N 条）历史，按时间正序返回 [{role, content}]。"""
    conn = db()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (sid, rounds * 2),
    ).fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


# ===================== RAG 服务（启动时构建一次）=====================
class RAGService:
    """
    封装检索能力，复用阶段1 的 FAISS + BM25 混合检索。
    启动时加载/构建索引，后续请求直接检索，避免每次重建。
    """

    def __init__(self) -> None:
        settings = Settings(
            embed_mode="local", embed_dim=512,
            cache_dir=CACHE_DIR, markdown_path=MD_PATH,
        )
        self.adapter = EmbeddingAdapter(mode=settings.embed_mode, dim=settings.embed_dim)
        self.store = FAISSKnowledgeStore(settings, self.adapter)
        # 复用阶段1 已落盘的 FAISS 缓存；不存在则读取制度文档重建
        if not self.store.load():
            if not os.path.exists(MD_PATH):
                raise RuntimeError(f"找不到制度文档：{MD_PATH}")
            text = open(MD_PATH, encoding="utf-8").read()
            chunks = split_markdown_into_chunks(text, settings.chunk_size, settings.chunk_overlap)
            self.store.build(chunks)
        self.bm25 = BM25Retriever(self.store.chunks)

    def retrieve(self, question: str) -> List[Dict[str, str]]:
        """混合检索 Top3（向量 + BM25，RRF 融合）。"""
        return hybrid_retrieve(question, self.store, self.bm25, self.adapter, top_k=3)


@app.on_event("startup")
def startup() -> None:
    init_db()
    global rag
    rag = RAGService()
    print("[启动] RAG 服务与 SQLite 已就绪")


# ===================== 工具函数 =====================
def sse(event: str, data) -> str:
    """构造一个 SSE 事件帧：event: <e>\ndata: <json>\n\n"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def build_messages(history: List[Dict[str, str]], contexts: List[Dict[str, str]], question: str):
    """
    组装发给 LLM 的 messages：system + 最近6轮历史 + 本次检索上下文 + 当前问题。
    复用阶段1 build_prompt 的「强制引用来源」思路，并补上多轮历史。
    """
    system = (
        "你是「拾光连锁」报销制度问答助手。只能依据下面提供的制度片段回答，"
        "不得编造。回答时以「[编号]」标注所引用的制度片段与章节。"
        "若片段不足以回答，明确告知用户「制度未覆盖该情形」，不要猜测。"
    )
    msgs = [{"role": "system", "content": system}]
    # 历史轮次（保证多轮连续）
    for h in history:
        msgs.append({"role": h["role"], "content": h["content"]})
    # 本次检索到的制度片段，作为用户消息的上下文
    ctx_text = "\n\n".join(
        f"[{i+1}] 来源章节：{c['section']}\n{c['content']}" for i, c in enumerate(contexts)
    )
    user = f"问题：{question}\n\n可参考的制度片段如下：\n{ctx_text}"
    msgs.append({"role": "user", "content": user})
    return msgs


def to_sources(contexts: List[Dict[str, str]]):
    """把检索到的 chunk 映射为前端展示的引用来源（文件名 + 章节 + 片段）。"""
    return [
        {"file": MD_PATH, "section": c["section"], "snippet": c["content"][:200]}
        for c in contexts
    ]


def stream_llm(messages: List[Dict[str, str]]) -> Generator[str, None, None]:
    """
    流式调用 LLM。扩展自阶段1 call_llm（LLM 网关雏形），此处补上「流式」能力。
      - mock：返回抽取式摘要，按 6 字一段模拟打字机。
      - openai-compatible：stream=True，逐行解析 SSE 的 delta.content。
    """
    if LLM_PROVIDER == "mock":
        full = call_llm(messages, provider="mock", base_url=LLM_BASE_URL,
                        api_key=LLM_API_KEY, model=LLM_MODEL)
        for i in range(0, len(full), 6):
            yield full[i:i + 6]
        return
    # 真实供应商：OpenAI 兼容端点，开启 stream
    resp = requests.post(
        f"{LLM_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {LLM_API_KEY}"},
        json={"model": LLM_MODEL, "messages": messages, "temperature": 0.2, "stream": True},
        stream=True, timeout=30,
    )
    resp.raise_for_status()
    for line in resp.iter_lines():
        if not line:
            continue
        line = line.decode("utf-8")
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
            delta = obj["choices"][0]["delta"].get("content", "")
            if delta:
                yield delta
        except Exception:
            continue


# ===================== 接口：POST /chat（SSE）=====================
@app.post("/chat")
async def chat_endpoint(request: Request):
    """
    员工问答主接口。
    流程：基础校验 → 限流 → 取历史 → 持久化问题 → 检索 → 流式生成 → 返回来源 → 持久化回答。
    基础校验失败：HTTP 状态码 + {code, message}（中文）。
    流中异常：SSE error 事件 {code, message}。
    """
    # ---- 1) 基础校验（先返回错误码与中文信息）----
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"code": 40004, "message": "请求格式错误，应为JSON"})
    session_id = (data.get("session_id") or "").strip()
    question = (data.get("question") or "").strip()
    if not session_id:
        return JSONResponse(status_code=400, content={"code": 40002, "message": "会话ID不能为空"})
    if not question:
        return JSONResponse(status_code=400, content={"code": 40001, "message": "问题内容不能为空"})
    if len(question) > MAX_Q_LEN:
        return JSONResponse(status_code=400,
                            content={"code": 40003, "message": f"问题长度不能超过{MAX_Q_LEN}字"})

    # ---- 2) 限流（按会话，默认 1 秒）----
    now = time.time()
    last = _session_last.get(session_id)
    if last and now - last < RATE_LIMIT_SEC:
        return JSONResponse(status_code=429, content={"code": 42901, "message": "请求过于频繁，请稍后再试"})
    _session_last[session_id] = now

    ensure_session(session_id)

    # ---- 3) SSE 生成器：同步逻辑，由 StreamingResponse 在线程池迭代 ----
    def event_stream() -> Generator[str, None, None]:
        # 3.1 持久化用户问题 + 取历史（不含当前问题）
        try:
            history = get_history(session_id, HISTORY_ROUNDS)
            save_message(session_id, "user", question, None)
        except Exception:
            yield sse("error", {"code": 50003, "message": "消息保存失败，请稍后重试"})
            return

        # 3.2 检索
        try:
            yield sse("retrieving", {"stage": "retrieving"})   # 前端显示「正在检索制度…」
            contexts = rag.retrieve(question)
        except Exception:
            yield sse("error", {"code": 50001, "message": "制度检索服务异常，请稍后重试"})
            return

        # 3.3 流式生成
        try:
            messages = build_messages(history, contexts, question)
            buf: List[str] = []
            for delta in stream_llm(messages):
                buf.append(delta)
                yield sse("token", {"delta": delta})   # 前端打字机
            answer = "".join(buf)
        except Exception:
            yield sse("error", {"code": 50002, "message": "模型服务暂时不可用，请稍后重试"})
            return

        # 3.4 返回引用来源 + 持久化回答 + 结束
        try:
            sources = to_sources(contexts)
            yield sse("sources", sources)   # 前端在回答底部展示
            save_message(session_id, "assistant", answer, json.dumps(sources, ensure_ascii=False))
            yield sse("done", {"finish_reason": "stop"})
        except Exception:
            yield sse("error", {"code": 50003, "message": "消息保存失败，请稍后重试"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ===================== 前端入口 =====================
@app.get("/")
def index():
    """返回单页聊天界面。"""
    return FileResponse("index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
