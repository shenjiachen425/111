#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
拾光连锁 · 会员权益双端口智能体（后端）
=========================================
FastAPI + SQLite + SSE。两个端口共用同一套会员权益知识库与 Agent 工具：

  - 会员端口 /member  (POST /member/chat  SSE)
        会员以"提问智能体"的方式，询问某张优惠券「可以在哪些门店使用」与「使用规则」。
        智能体基于《会员权益知识库》检索并给出带来源引用的口径。

  - 收银端口 /cashier  (POST /cashier/check  JSON)
        收银员输入「券描述 + 订单金额」，智能体直接判定能否使用 + 优惠后金额。
        满减/折扣/抵用券确定性计算；兑换/赠品券不减金额；无法识别或异常强制转人工。
        严守「不自主修改会员资产」红线，仅计算与审计，不改券状态/积分。

【核心复用】RAG 检索与 Agent 工具直接 import member_agent，不重复造轮子。
"""

import os
import re
import json
import time
import sqlite3
import hashlib
import base64
import hmac
import secrets
from datetime import datetime, timezone, timedelta
from typing import Dict, Generator, List, Optional

from fastapi import FastAPI, Request, Header
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, RedirectResponse

import member_agent as ma

# 登录态有效期（小时）
TOKEN_TTL_HOURS = 8

# 演示用签名密钥（生产应外置为环境变量）。JWT 无状态：签名自带过期，
# 不依赖服务端存储，避免多实例/无状态部署下 token 查不到导致反复跳登录。
SECRET_KEY = os.environ.get("SG_SECRET_KEY", "shiguang_member_portal_demo_secret_2024")

# 进程内注销黑名单（演示级；多实例下不强制共享，前端亦会清空 localStorage）
_REVOKED: set = set()

# ===================== 配置 =====================
DB_PATH = "app_member.db"
MAX_Q_LEN = 500
HISTORY_ROUNDS = 6
RATE_LIMIT_SEC = 1.0
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")

app = FastAPI(title="拾光智能体 · 会员权益双端口")
KB = None
_session_last: Dict[str, float] = {}


# ===================== SQLite 持久化 =====================
def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, title TEXT DEFAULT '新对话',
            created_at TEXT NOT NULL, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL, sources TEXT, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, id);
        -- 账号表：role = admin(收银端) / member(会员端)
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, role TEXT NOT NULL,
            name TEXT, pw_hash TEXT NOT NULL, salt TEXT NOT NULL,
            created_at TEXT NOT NULL);
        -- 登录令牌表
        CREATE TABLE IF NOT EXISTS auth_tokens (
            token TEXT PRIMARY KEY, username TEXT NOT NULL, role TEXT NOT NULL,
            expires_at TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_token_user ON auth_tokens(username);
        """
    )
    conn.commit(); conn.close()


# ===================== 账号与鉴权 =====================
def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               salt.encode("utf-8"), 100_000).hex()


def seed_users() -> None:
    """首次启动写入演示账号（仅当无账号时）。生产应改为外部配置/初始化脚本。"""
    conn = db()
    n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if n > 0:
        conn.close(); return
    demo = [
        ("admin", "admin", "系统管理员", "Admin@123456"),
        ("cashier01", "admin", "门店收银员·小王", "Cashier@123"),
        ("member01", "member", "会员·张女士", "Member@123456"),
        ("member02", "member", "会员·李先生", "Member@123456"),
    ]
    for username, role, name, pw in demo:
        salt = secrets.token_hex(8)
        conn.execute(
            "INSERT INTO users(username, role, name, pw_hash, salt, created_at) VALUES(?,?,?,?,?,?)",
            (username, role, name, _hash_password(pw, salt), salt, now_iso()))
    conn.commit(); conn.close()
    print("[启动] 已写入演示账号：admin / cashier01（收银端）；member01 / member02（会员端）")


def authenticate(username: str, password: str) -> Optional[Dict[str, str]]:
    conn = db()
    row = conn.execute(
        "SELECT username, role, name, pw_hash, salt FROM users WHERE username=?",
        (username,)).fetchone()
    conn.close()
    if not row:
        return None
    u, role, name, pw_hash, salt = row
    if _hash_password(password, salt) != pw_hash:
        return None
    return {"username": u, "role": role, "name": name}


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def create_token(username: str, role: str) -> str:
    """签发无状态 JWT（HS256）：payload 内含 username/role/exp，签名自带过期，
    服务端无需查库即可验签，规避多实例部署下 token 存储不一致问题。"""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    now = int(datetime.now(timezone.utc).timestamp())
    exp = now + TOKEN_TTL_HOURS * 3600
    payload = _b64url(json.dumps(
        {"username": username, "role": role, "iat": now, "exp": exp}).encode("utf-8"))
    signing_input = f"{header}.{payload}".encode("utf-8")
    sig = _b64url(hmac.new(SECRET_KEY.encode("utf-8"), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def get_user_from_token(raw: Optional[str]) -> Optional[Dict[str, str]]:
    """校验 JWT：兼容「裸 token」或「Bearer <token>」，验签 + 过期 + 黑名单。"""
    if not raw:
        return None
    token = raw.strip()
    if token.startswith("Bearer "):
        token = token[7:].strip()
    if token in _REVOKED:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, sig = parts
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    expected = _b64url(hmac.new(SECRET_KEY.encode("utf-8"), signing_input, hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64))
        exp = int(payload.get("exp", 0))
    except Exception:
        return None
    if exp < int(datetime.now(timezone.utc).timestamp()):
        return None
    return {"username": payload.get("username"), "role": payload.get("role")}


def extract_token(request: Request, data: Optional[dict] = None) -> Optional[str]:
    """
    多来源提取 token，规避发布平台网关不转发标准 Authorization 头的问题：
      1) 请求体 JSON 的 token 字段（POST 接口，平台对 body 透传）—— 首选
      2) 自定义头 X-Token
      3) 标准 Authorization: Bearer <token>
      4) 查询参数 t / token（兜底）
    """
    if isinstance(data, dict) and data.get("token"):
        return str(data["token"]).strip()
    x = request.headers.get("X-Token")
    if x:
        return x.strip()
    a = request.headers.get("Authorization")
    if a:
        return a.strip()
    t = request.query_params.get("t") or request.query_params.get("token")
    if t:
        return t.strip()
    return None


def db() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_session(sid: str) -> None:
    conn = db()
    conn.execute("INSERT OR IGNORE INTO sessions(id, created_at, updated_at) VALUES(?,?,?)",
                 (sid, now_iso(), now_iso()))
    conn.commit(); conn.close()


def save_message(sid: str, role: str, content: str, sources) -> None:
    conn = db()
    conn.execute("INSERT INTO messages(session_id, role, content, sources, created_at) VALUES(?,?,?,?,?)",
                 (sid, role, content, sources, now_iso()))
    conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now_iso(), sid))
    conn.commit(); conn.close()


def get_history(sid: str, rounds: int) -> List[Dict[str, str]]:
    conn = db()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (sid, rounds * 2)).fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


# ===================== 启动：加载知识库 =====================
@app.on_event("startup")
def startup() -> None:
    init_db()
    seed_users()
    global KB
    KB = ma.load_knowledge()
    print("[启动] 会员权益知识库与 SQLite 已就绪（双端口 · 含登录鉴权）")


# ===================== 通用工具 =====================
def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def to_sources(chunks: List[Dict[str, str]]):
    return [{"section": c["section"], "snippet": c["content"][:200]} for c in chunks]


# =====================================================================
# 会员端口：基于检索的「门店可用性 + 使用规则」回答构造
# =====================================================================
def build_member_answer(question: str, chunks: List[Dict[str, str]]) -> str:
    """
    从检索到的知识库片段中，提取与「适用门店 / 使用规则」相关的句子，
    组织成会员可读的回答，并标注来源章节。mock 下为抽取式（非生成式）。
    """
    if not chunks:
        return ("目前知识库中未检索到该券的相关规则。为避免给出错误口径，"
                "建议联系门店运营或活动策划员核实后再使用。")

    # 命中与问题相关的券类型关键词，便于聚焦与重排
    q = question
    focus = ""
    if "满减" in q or re.search(r"满\s*\d+\s*减", q):
        focus, kw = "满减券", "满减"
    elif "折扣" in q or "折" in q:
        focus, kw = "折扣券", "折扣"
    elif "兑换" in q or "赠品" in q:
        focus, kw = "兑换/赠品券", "兑换"
    elif "抵用" in q:
        focus, kw = "抵用券", "抵用"
    else:
        kw = None

    # 重排：优先把命中焦点券类型的片段排到前面，提升相关性
    if kw:
        chunks = sorted(chunks, key=lambda c: (0 if kw in c["section"] or kw in c["content"] else 1, c.get("score", 0)))

    # 标题噪声过滤：跳过章节标题行（如「第二章…」「2.1 满减券」）
    def is_title(s: str) -> bool:
        return bool(re.match(r"^第.+[章篇节]", s)) or bool(re.match(r"^\d+(\.\d+)+\s*[\u4e00-\u9fa5]+$", s))

    lines: List[str] = []
    seen = set()
    for c in chunks:
        clean = re.sub(r"^[#>\-\s]+", "", c["content"], flags=re.M)   # 去 Markdown 标题/引用符
        clean = re.sub(r"[-]{3,}", "", clean)
        for sent in re.split(r"[。；;！!？?\n]", clean):
            sent = sent.strip().lstrip("-*•")
            if not sent or sent in seen or is_title(sent):
                continue
            if any(k in sent for k in
                   ["门店", "通用", "适用", "核销", "范围", "规则", "口径", "减",
                    "折", "叠加", "等级", "有效期", "品类", "限用", "会员", "一致"]):
                lines.append(f"· {sent}（来源：《{c['section']}》）")
                seen.add(sent)
            if len(lines) >= 6:
                break
        if len(lines) >= 6:
            break

    if not lines:
        norm = lambda s: re.sub(r"\s+", " ", re.sub(r"^[#>\-\s]+", "", s)).strip()[:80]
        lines = [f"· {norm(c['content'])}（来源：《{c['section']}》）" for c in chunks[:3]]

    header = ("根据《会员权益知识库》，为您整理该优惠券的可用门店与使用规则如下：\n"
              if not focus else
              f"根据《会员权益知识库》，为您整理【{focus}】的可用门店与使用规则如下：\n")
    footer = ("\n\n温馨提示：以上口径以当前生效版本为准，最终以业务负责人维护的正式版本为准。"
              if focus else
              "\n\n温馨提示：以上口径以当前生效版本为准；若遇版本冲突或接口异常，请由门店运营/隐私专员核对。")
    return header + "\n".join(lines) + footer


# =====================================================================
# 接口1：会员端口问答（SSE）—— 需 member 角色登录
# =====================================================================
@app.post("/member/chat")
async def member_chat(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"code": 40004, "message": "请求格式错误，应为JSON"})
    user = get_user_from_token(extract_token(request, data))
    if not user:
        return JSONResponse(status_code=401, content={"code": 40101, "message": "未登录或登录已过期，请先登录会员端"})
    if user["role"] != "member":
        return JSONResponse(status_code=403, content={"code": 40301, "message": "权限不足：该接口仅会员可访问"})
    session_id = (data.get("session_id") or "").strip()
    question = (data.get("question") or "").strip()
    if not session_id:
        return JSONResponse(status_code=400, content={"code": 40002, "message": "会话ID不能为空"})
    if not question:
        return JSONResponse(status_code=400, content={"code": 40001, "message": "问题内容不能为空"})
    if len(question) > MAX_Q_LEN:
        return JSONResponse(status_code=400,
                            content={"code": 40003, "message": f"问题长度不能超过{MAX_Q_LEN}字"})

    now = time.time()
    last = _session_last.get(session_id)
    if last and now - last < RATE_LIMIT_SEC:
        return JSONResponse(status_code=429, content={"code": 42901, "message": "请求过于频繁，请稍后再试"})
    _session_last[session_id] = now
    ensure_session(session_id)

    def event_stream() -> Generator[str, None, None]:
        try:
            history = get_history(session_id, HISTORY_ROUNDS)
            save_message(session_id, "user", question, None)
        except Exception:
            yield sse("error", {"code": 50003, "message": "消息保存失败，请稍后重试"}); return

        try:
            yield sse("retrieving", {"stage": "retrieving"})
            chunks = ma.search_benefit_rule(KB, question, top_k=3)
        except Exception:
            yield sse("error", {"code": 50001, "message": "权益检索服务异常，请稍后重试"}); return

        try:
            answer = build_member_answer(question, chunks)
            buf: List[str] = []
            # mock 打字机：按 6 字一段输出（真实 LLM 可替换为流式 delta）
            for i in range(0, len(answer), 6):
                piece = answer[i:i + 6]
                buf.append(piece)
                yield sse("token", {"delta": piece})
            full = "".join(buf)
        except Exception:
            yield sse("error", {"code": 50002, "message": "模型服务暂时不可用，请稍后重试"}); return

        try:
            sources = to_sources(chunks)
            yield sse("sources", sources)
            save_message(session_id, "assistant", full, json.dumps(sources, ensure_ascii=False))
            yield sse("done", {"finish_reason": "stop"})
        except Exception:
            yield sse("error", {"code": 50003, "message": "消息保存失败，请稍后重试"})

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# =====================================================================
# 接口2：收银端口核验（JSON）—— 需 admin 角色登录
# =====================================================================
@app.post("/cashier/check")
async def cashier_check(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"code": 40004, "message": "请求格式错误，应为JSON"})
    user = get_user_from_token(extract_token(request, data))
    if not user:
        return JSONResponse(status_code=401, content={"code": 40101, "message": "未登录或登录已过期，请先登录收银端"})
    if user["role"] != "admin":
        return JSONResponse(status_code=403, content={"code": 40301, "message": "权限不足：该接口仅收银/管理员可访问"})
    coupon_text = (data.get("coupon_text") or "").strip()
    try:
        order_amount = float(data.get("order_amount"))
    except (TypeError, ValueError):
        return JSONResponse(status_code=400,
                            content={"code": 40005, "message": "订单金额必须为数字"})
    if not coupon_text:
        return JSONResponse(status_code=400, content={"code": 40001, "message": "券描述不能为空"})

    trace_id = "CASH-" + hashlib.md5(f"{coupon_text}{order_amount}{time.time()}".encode()).hexdigest()[:8]
    try:
        res = ma.cashier_check(KB, coupon_text, order_amount, trace_id=trace_id)
    except Exception:
        return JSONResponse(status_code=500, content={"code": 50001, "message": "收银核验服务异常，请稍后重试"})

    payload = {
        "trace_id": trace_id,
        "coupon_text": coupon_text,
        "order_amount": round(order_amount, 2),
        "coupon_type": res.get("coupon_type"),
        "usable": res.get("usable"),
        "new_amount": res.get("new_amount"),
        "reason": res.get("reason"),
        "note": res.get("note"),
        "escalate": bool(res.get("escalate")),
        "handler": res.get("handler"),
        "sources": res.get("sources", []),
    }
    return JSONResponse(content=payload)


# =====================================================================
# 登录 / 鉴权接口
# =====================================================================
@app.post("/login")
async def login(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"code": 40004, "message": "请求格式错误，应为JSON"})
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    role = (data.get("role") or "").strip()   # admin / member，用于校验账号类型
    if not username or not password or not role:
        return JSONResponse(status_code=400, content={"code": 40006, "message": "用户名、密码与角色不能为空"})

    user = authenticate(username, password)
    if not user:
        return JSONResponse(status_code=401, content={"code": 40102, "message": "用户名或密码错误"})
    if user["role"] != role:
        return JSONResponse(status_code=403,
                            content={"code": 40302,
                                     "message": f"该账号不是「{'收银端' if role == 'admin' else '会员端'}」账号，请使用对应端口登录"})
    token = create_token(user["username"], user["role"])
    return JSONResponse(content={"token": token, "role": user["role"], "name": user.get("name"),
                                 "redirect": "/cashier" if user["role"] == "admin" else "/member"})


@app.get("/me")
@app.get("/me")
async def me(request: Request):
    user = get_user_from_token(extract_token(request))
    if not user:
        return JSONResponse(status_code=401, content={"code": 40101, "message": "未登录或已过期"})
    return JSONResponse(content={"username": user["username"], "role": user["role"]})


@app.post("/logout")
async def logout(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = None
    tok = extract_token(request, data)
    if tok:
        _REVOKED.add(tok)
    return JSONResponse(content={"ok": True})


# =====================================================================
# 前端入口
# =====================================================================
@app.get("/")
def index():
    return RedirectResponse("/login")


@app.get("/login")
def login_page():
    return FileResponse("login.html")


@app.get("/member")
def member_page():
    return FileResponse("member.html")


@app.get("/cashier")
def cashier_page():
    return FileResponse("cashier.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
