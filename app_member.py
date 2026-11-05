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

from fastapi import FastAPI, Request, Header, Query
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
        -- 待核销/已核销券记录（会员端"使用"生成，收银端据此核销）
        CREATE TABLE IF NOT EXISTS redemptions (
            code TEXT PRIMARY KEY,
            coupon_key TEXT NOT NULL,
            coupon_name TEXT,
            coupon_type TEXT,
            status TEXT NOT NULL,
            member_name TEXT,
            created_at TEXT NOT NULL,
            redeemed_at TEXT,
            order_amount REAL,
            new_amount REAL);
        CREATE INDEX IF NOT EXISTS idx_rdm_status ON redemptions(status);
        """
    )
    conn.commit(); conn.close()


def migrate_db() -> None:
    """审计链增强：为已存在的表补充业务编号/版本/事实引用字段（幂等）。"""
    adds = [
        ("messages", "biz_id", "TEXT"),
        ("redemptions", "biz_id", "TEXT"),
        ("redemptions", "rule_version", "TEXT"),
        ("redemptions", "sources", "TEXT"),
    ]
    conn = db()
    for table, col, ctype in adds:
        try:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ctype}")
        except Exception as e:
            print(f"[迁移] {table}.{col} 跳过：{e}")
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


def save_message(sid: str, role: str, content: str, sources, biz_id: Optional[str] = None) -> None:
    conn = db()
    conn.execute("INSERT INTO messages(session_id, role, content, sources, biz_id, created_at) VALUES(?,?,?,?,?,?)",
                 (sid, role, content, sources, biz_id, now_iso()))
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
    migrate_db()
    seed_users()
    global KB
    KB = ma.load_knowledge()
    print("[启动] 会员权益知识库与 SQLite 已就绪（双端口 · 含登录鉴权 · 审计链）")


# ===================== 通用工具 =====================
def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def to_sources(chunks: List[Dict[str, str]]):
    return [{"section": c["section"], "snippet": c["content"][:200]} for c in chunks]


# =====================================================================
# 会员端口：基于检索的「门店可用性 + 使用规则」回答构造
# =====================================================================
# =====================================================================
# 结构化券知识（基于《会员权益知识库》2.1~2.3 提炼）
# 问答 / 收银计算 / 会员选券 / 核销 统一以本字典为唯一数据源，确保口径一致
# =====================================================================
COUPONS = {
    "man200_30": {
        "key": "man200_30", "name": "满200减30全门店通用券", "type": "满减券",
        "threshold": 200.0, "face": 30.0,
        "stores": "全门店通用（所有拾光连锁门店均可使用）",
        "rule": "单笔订单满200元可用，立减30元；不与其他满减券叠加。",
        "stack": "不与其它满减券叠加；与折扣券/赠品券能否同用，以当前生效活动版本为准。",
        "validity": "以券面标注的生效日期与有效期为准。",
        "version": "v2026.11", "effective_date": "2026-11-01 起生效（以券面为准）",
        "alias": ["满200减30", "满200减30券", "满200减30的全门店通用券", "满减"],
    },
    "discount85": {
        "key": "discount85", "name": "8.5折会员折扣券", "type": "折扣券",
        "rate": 0.85,
        "stores": "全门店通用（限会员等级≥银卡）",
        "rule": "会员银卡及以上可用，同一会员同日同券限用1次，按订单金额8.5折计算。",
        "stack": "与满减券能否同用，以当前生效活动版本为准。",
        "validity": "以券面生效日期为准。",
        "version": "v2026.11", "effective_date": "2026-11-01 起生效（限银卡及以上）",
        "alias": ["8.5折", "折扣券", "会员折扣券", "折扣"],
    },
    "voucher5": {
        "key": "voucher5", "name": "5元抵用券", "type": "抵用券",
        "threshold": 0.0, "face": 5.0,
        "stores": "全门店通用",
        "rule": "下单直接抵用5元，无使用门槛、不找零。",
        "stack": "可与满减券、折扣券同用，以活动版本为准。",
        "validity": "以券面有效期为准。",
        "version": "v2026.11", "effective_date": "2026-11-01 起生效",
        "alias": ["5元抵用", "抵用券", "5元抵用券", "抵用"],
    },
    "exchangeA": {
        "key": "exchangeA", "name": "兑换券A（赠品券）", "type": "赠品券/兑换券",
        "exchange": True,
        "stores": "按活动版本指定的核销门店与有效期（见券面核销门店列表）",
        "rule": "在指定核销门店、有效期内兑换对应赠品；一经核销即不可撤销。",
        "stack": "不与现金抵扣类券同用。",
        "validity": "以券面有效期为准。",
        "version": "v2026.11", "effective_date": "2026-11-01 起生效（按券面核销门店）",
        "alias": ["兑换券", "兑换券A", "赠品券", "兑换", "赠品"],
    },
}


# 知识库/口径元数据（满足需求 f34/f35：数据过期待刷新提示 + 版本化可追溯）
KB_META = {
    "version": "v2026.11",
    "owner": "会员运营（顾明岚）",
    "data_updated_at": "2026-11-01",
    "stale_after_days": 30,
}


def data_freshness() -> Dict[str, object]:
    """返回口径数据新鲜度；超过 stale_after_days 提示以门店/运营口径为准。"""
    try:
        upd = datetime.strptime(KB_META["data_updated_at"], "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        days = (today - upd).days
    except Exception:
        days = 0
    stale = days > KB_META["stale_after_days"]
    return {
        "version": KB_META["version"],
        "owner": KB_META["owner"],
        "data_updated_at": KB_META["data_updated_at"],
        "days_since_update": max(days, 0),
        "stale": stale,
        "note": "资料待刷新，以门店/运营口径为准" if stale else "",
    }


def match_coupon(text: str):
    """从问题描述中识别具体券，返回 coupon_key；识别不到返回 None。"""
    t = (text or "").lower()
    m = re.search(r"满\s*(\d+)\s*减\s*(\d+)", t)
    if m:
        th, fa = float(m.group(1)), float(m.group(2))
        for c in COUPONS.values():
            if c["type"] == "满减券" and c.get("threshold") == th and c.get("face") == fa:
                return c["key"]
    for key, c in COUPONS.items():
        for a in c.get("alias", []):
            if a.lower() in t:
                return key
    if "折扣" in t or "折" in t:
        return "discount85"
    if "抵用" in t:
        return "voucher5"
    if "兑换" in t or "赠品" in t:
        return "exchangeA"
    if "满减" in t or "满" in t:
        return "man200_30"
    return None


def compute_coupon(key: str, order_amount: float) -> Optional[Dict]:
    """确定性计算核销后金额；返回 usable / new_amount / reason 等。"""
    c = COUPONS.get(key)
    if not c:
        return None
    t = c["type"]
    if t == "满减券":
        th, fa = c["threshold"], c["face"]
        if order_amount < th:
            return {"coupon_type": t, "usable": False, "new_amount": round(order_amount, 2),
                    "reason": f"订单¥{order_amount:.0f}未达满¥{th:.0f}门槛，不可使用", "note": c["rule"]}
        return {"coupon_type": t, "usable": True, "new_amount": round(order_amount - fa, 2),
                "reason": f"满¥{th:.0f}减¥{fa:.0f}，已减¥{fa:.0f}", "note": c["rule"]}
    if t == "折扣券":
        rate = c["rate"]
        return {"coupon_type": t, "usable": True, "new_amount": round(order_amount * rate, 2),
                "reason": f"按{rate * 10:.1f}折计算", "note": c["rule"]}
    if t == "抵用券":
        fa = c["face"]
        return {"coupon_type": t, "usable": True, "new_amount": round(max(order_amount - fa, 0), 2),
                "reason": f"抵用¥{fa:.0f}", "note": c["rule"]}
    if "兑换" in t or "赠品" in t:
        return {"coupon_type": t, "usable": True, "new_amount": round(order_amount, 2),
                "reason": "赠品券核销，不抵扣金额，兑换对应赠品", "note": c["rule"], "exchange": True}
    return None


def gen_biz_id(prefix: str = "BIZ") -> str:
    """生成业务编号，用于审计链重建（会员识别→权益核验→优惠计算→收银确认→售后反馈）。"""
    return f"{prefix}-{secrets.token_hex(4).upper()}"


def coupon_sources(key: Optional[str] = None) -> List[Dict[str, str]]:
    """返回事实引用列表（资料版本 + 规则摘要），满足审计『事实引用』要求。"""
    if key:
        c = COUPONS.get(key)
        if c:
            return [{"section": c["name"], "snippet": c["rule"],
                     "version": c.get("version", ""), "key": key}]
    return [{"section": c["name"], "snippet": c["rule"],
             "version": c.get("version", ""), "key": k} for k, c in COUPONS.items()]


def build_answer(question: str) -> Dict:
    """
    按「券类型 + 问题意图」返回针对性回答；不同问题给不同答案。
    返回结构化结果：text / sources(事实引用) / version(资料版本) / escalate(是否转人工)
    + reason(触发原因) / handler(建议接手人) / need_confirm(待确认字段)。
    """
    q = question
    key = match_coupon(q)
    intent_stores = any(k in q for k in ["门店", "哪里用", "哪些店", "哪家店", "适用门店",
                                         "可以使用", "能用吗", "可用门店", "在门店", "什么店", "哪能用"])
    intent_rule = any(k in q for k in ["规则", "怎么用", "如何使用", "如何使", "条件", "限制", "要求", "怎么使"])
    intent_stack = any(k in q for k in ["叠加", "一起用", "同时用", "同用", "共用", "能不能一起", "能否叠加"])
    intent_valid = any(k in q for k in ["有效期", "多久", "过期", "时效", "什么时候过期"])
    intent_redeem = "核销" in q
    intent_refund = any(k in q for k in ["退货", "退回", "追回", "退掉", "退款", "退券"])

    if not key:
        if intent_stores:
            return {"text": "各优惠券可用门店如下：\n" + "\n".join(f"· {c['name']}：{c['stores']}" for c in COUPONS.values()),
                    "sources": coupon_sources(), "version": "v2026.11", "escalate": False}
        if intent_stack:
            return {"text": "各优惠券叠加规则如下：\n" + "\n".join(f"· {c['name']}：{c['stack']}" for c in COUPONS.values()),
                    "sources": coupon_sources(), "version": "v2026.11", "escalate": False}
        if intent_valid:
            return {"text": "各优惠券有效期均以券面标注的生效日期与有效期为准；如有疑问请联系门店运营核实。",
                    "sources": coupon_sources(), "version": "v2026.11", "escalate": False}
        # 未识别到具体券：强制转人工，不硬猜口径
        return {"text": ("未能识别到具体优惠券或规则依据不足，已为您转人工核对。您可以这样问我：\n"
                         "· 「满200减30的券可以在哪些门店使用？」\n"
                         "· 「8.5折券的使用规则是什么？」\n"
                         "· 「兑换券A怎么核销？」\n"
                         "也可以直接在下方「我的优惠券」中选择使用。"),
                "sources": coupon_sources(), "version": "v2026.11",
                "escalate": True, "reason": "未识别到具体优惠券或规则依据不足，无法给出确定口径",
                "handler": "活动策划员（唐映秋）/ 会员运营（顾明岚）", "need_confirm": "券类型与适用门店"}

    c = COUPONS[key]
    src = coupon_sources(key)
    ver = c["version"]
    if intent_stores:
        return {"text": f"{c['name']} 可用门店：{c['stores']}。", "sources": src, "version": ver, "escalate": False}
    if intent_stack:
        return {"text": f"{c['name']} 叠加规则：{c['stack']}", "sources": src, "version": ver, "escalate": False}
    if intent_valid:
        return {"text": f"{c['name']} 有效期：{c['validity']}", "sources": src, "version": ver, "escalate": False}
    if intent_redeem:
        return {"text": f"{c['name']} 核销说明：{c['rule']}", "sources": src, "version": ver, "escalate": False}
    if intent_refund and c.get("exchange"):
        # 兑换/赠品券退货追回规则未明确 -> 转人工（红线兜底）
        return {"text": (f"{c['name']} 核销说明：{c['rule']}\n"
                         f"提示：若发生退货，当前无明确的积分/券追回规则，需人工核对后再处理，原单据不覆盖。"),
                "sources": src, "version": ver, "escalate": True,
                "reason": "兑换/赠品券退货追回规则未明确", "handler": "活动策划员（唐映秋）",
                "need_confirm": "退货追回方式"}
    if intent_rule:
        return {"text": f"{c['name']} 使用规则：{c['rule']}", "sources": src, "version": ver, "escalate": False}
    # 默认：门店 + 规则（精简，不堆砌）
    return {"text": f"{c['name']}\n可用门店：{c['stores']}\n使用规则：{c['rule']}",
            "sources": src, "version": ver, "escalate": False}


def answer_question(question: str) -> str:
    """兼容旧调用的文本接口。"""
    return build_answer(question)["text"]


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
            save_message(session_id, "user", question, None)
        except Exception:
            yield sse("error", {"code": 50003, "message": "消息保存失败，请稍后重试"}); return

        try:
            # 意图驱动的精确问答：按券类型+问题意图返回针对性答案（含事实引用/版本/转人工）
            ans = build_answer(question)
            text = ans["text"]
            biz = gen_biz_id()
            buf: List[str] = []
            # mock 打字机：按 6 字一段输出（真实 LLM 可替换为流式 delta）
            for i in range(0, len(text), 6):
                piece = text[i:i + 6]
                buf.append(piece)
                yield sse("token", {"delta": piece})
            full = "".join(buf)
            # 事实引用 / 资料版本 / 业务编号 / 转人工信息 一并发给前端，满足审计链要求
            meta = {"items": ans.get("sources", []), "version": ans.get("version", ""),
                    "biz_id": biz, "escalate": ans.get("escalate", False),
                    "reason": ans.get("reason", ""), "handler": ans.get("handler", ""),
                    "need_confirm": ans.get("need_confirm", ""), "kb_meta": data_freshness()}
            yield sse("sources", meta)
        except Exception:
            yield sse("error", {"code": 50002, "message": "模型服务暂时不可用，请稍后重试"}); return

        try:
            save_message(session_id, "bot", full, json.dumps(meta, ensure_ascii=False), biz_id=biz)
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
    key = match_coupon(coupon_text)
    if not key:
        return JSONResponse(content={
            "trace_id": trace_id, "biz_id": gen_biz_id("BIZ"),
            "coupon_text": coupon_text,
            "order_amount": round(order_amount, 2),
            "coupon_type": None, "usable": None, "new_amount": None,
            "reason": "无法识别该券类型，请转人工或核对券面信息",
            "escalate": True, "handler": "活动策划员（唐映秋）/ 数据隐私专员（傅景）",
            "need_confirm": "券类型与活动版本", "sources": coupon_sources(),
            "version": "v2026.11", "stores": None, "kb_meta": data_freshness()
        })
    comp = compute_coupon(key, order_amount)
    comp.update({
        "trace_id": trace_id, "biz_id": gen_biz_id("BIZ"), "coupon_text": coupon_text,
        "order_amount": round(order_amount, 2),
        "stores": COUPONS[key]["stores"],
        "rule_version": COUPONS[key]["version"],
        "escalate": False, "handler": None,
        "sources": coupon_sources(key), "version": COUPONS[key]["version"],
        "kb_meta": data_freshness()
    })
    return JSONResponse(content=comp)


# =====================================================================
# 会员端：选择使用优惠券 -> 生成待核销券（收银端据此核销）
# =====================================================================
@app.post("/member/use_coupon")
async def member_use_coupon(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"code": 40004, "message": "请求格式错误，应为JSON"})
    user = get_user_from_token(extract_token(request, data))
    if not user:
        return JSONResponse(status_code=401, content={"code": 40101, "message": "未登录或登录已过期，请先登录会员端"})
    if user["role"] != "member":
        return JSONResponse(status_code=403, content={"code": 40301, "message": "权限不足：该接口仅会员可访问"})
    key = (data.get("coupon_key") or "").strip()
    c = COUPONS.get(key)
    if not c:
        return JSONResponse(status_code=400, content={"code": 40007, "message": "未知优惠券"})
    digits = "0123456789"
    code = "".join(secrets.choice(digits) for _ in range(6))
    biz = gen_biz_id("BIZ")
    conn = db()
    while conn.execute("SELECT 1 FROM redemptions WHERE code=?", (code,)).fetchone():
        code = "".join(secrets.choice(digits) for _ in range(6))
    conn.execute(
        "INSERT INTO redemptions(code, coupon_key, coupon_name, coupon_type, status, member_name, created_at, "
        "biz_id, rule_version, sources) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (code, key, c["name"], c["type"], "pending", user["username"], now_iso(),
         biz, c["version"], json.dumps(coupon_sources(key), ensure_ascii=False)))
    conn.commit(); conn.close()
    return JSONResponse(content={"code": code, "biz_id": biz, "coupon_name": c["name"],
                                 "coupon_type": c["type"], "stores": c["stores"],
                                 "rule_version": c["version"], "kb_meta": data_freshness()})


# =====================================================================
# 收银端：核销优惠券 -> 标记已用并计算核销后金额
# =====================================================================
@app.post("/cashier/redeem")
async def cashier_redeem(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"code": 40004, "message": "请求格式错误，应为JSON"})
    user = get_user_from_token(extract_token(request, data))
    if not user:
        return JSONResponse(status_code=401, content={"code": 40101, "message": "未登录或登录已过期，请先登录收银端"})
    if user["role"] != "admin":
        return JSONResponse(status_code=403, content={"code": 40301, "message": "权限不足：该接口仅收银/管理员可访问"})
    code = (data.get("code") or "").strip()
    try:
        order_amount = float(data.get("order_amount"))
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"code": 40005, "message": "订单金额必须为数字"})
    if not code:
        return JSONResponse(status_code=400, content={"code": 40008, "message": "核销码不能为空"})
    conn = db()
    row = conn.execute(
        "SELECT coupon_key, status, coupon_name, coupon_type FROM redemptions WHERE code=?",
        (code,)).fetchone()
    if not row:
        conn.close()
        return JSONResponse(status_code=404, content={"code": 40401, "message": "核销码无效或不存在"})
    ck, status, name, ctype = row
    if status == "used":
        conn.close()
        return JSONResponse(status_code=409,
                            content={"code": 40901, "message": "该券已核销，不可重复使用",
                                     "coupon_name": name, "coupon_type": ctype, "already_used": True})
    comp = compute_coupon(ck, order_amount)
    if not comp:
        conn.close()
        return JSONResponse(status_code=400, content={"code": 40001, "message": "券类型计算失败"})
    if not comp["usable"]:
        conn.close()
        return JSONResponse(content={"code": 40002, "biz_id": gen_biz_id("BIZ"),
                                     "message": comp["reason"],
                                     "coupon_name": name, "coupon_type": ctype, "usable": False})
    new_amount = comp["new_amount"]
    biz = gen_biz_id("BIZ")
    conn.execute(
        "UPDATE redemptions SET status='used', order_amount=?, new_amount=?, redeemed_at=?, "
        "biz_id=?, rule_version=?, sources=? WHERE code=?",
        (order_amount, new_amount, now_iso(), biz, COUPONS[ck]["version"],
         json.dumps(coupon_sources(ck), ensure_ascii=False), code))
    conn.commit(); conn.close()
    resp = {
        "ok": True, "biz_id": biz, "code": code, "coupon_name": name, "coupon_type": ctype,
        "order_amount": round(order_amount, 2), "new_amount": new_amount,
        "reason": comp["reason"], "exchange": comp.get("exchange", False),
        "rule_version": COUPONS[ck]["version"], "sources": coupon_sources(ck),
        "kb_meta": data_freshness()
    }
    # 兑换/赠品券：退货追回规则未明确 -> 标注待确认（不覆盖原单据，不阻断合法核销）
    if comp.get("exchange"):
        resp.update({
            "escalate": True,
            "note": "赠品券核销后不可撤销；若发生退货，当前无明确积分/券追回规则，需活动策划员（唐映秋）人工核对",
            "reason": "兑换/赠品券退货追回规则未明确",
            "handler": "活动策划员（唐映秋）", "need_confirm": "退货追回方式"
        })
    return JSONResponse(content=resp)


# 收银端：查看待核销券列表
@app.get("/cashier/pending")
async def cashier_pending(request: Request):
    user = get_user_from_token(extract_token(request))
    if not user:
        return JSONResponse(status_code=401, content={"code": 40101, "message": "未登录或登录已过期"})
    if user["role"] != "admin":
        return JSONResponse(status_code=403, content={"code": 40301, "message": "权限不足"})
    conn = db()
    rows = conn.execute(
        "SELECT code, coupon_name, coupon_type, created_at FROM redemptions "
        "WHERE status='pending' ORDER BY created_at DESC").fetchall()
    conn.close()
    return JSONResponse([{"code": r[0], "coupon_name": r[1], "coupon_type": r[2], "created_at": r[3]}
                         for r in rows])


# =====================================================================
# 审计链：按业务编号重建单次处理链（需求 P0 可追溯审计）
# =====================================================================
@app.get("/audit/query")
async def audit_query(request: Request, biz_id: str = Query("")):
    user = get_user_from_token(extract_token(request))
    if not user:
        return JSONResponse(status_code=401, content={"code": 40101, "message": "未登录或登录已过期"})
    biz = (biz_id or "").strip()
    if not biz:
        return JSONResponse(status_code=400, content={"code": 40001, "message": "业务编号不能为空"})
    conn = db()
    qrows = conn.execute(
        "SELECT session_id, role, content, created_at FROM messages WHERE biz_id=? ORDER BY id",
        (biz,)).fetchall()
    qa_chain = [{"session_id": r[0], "role": r[1], "content": r[2], "created_at": r[3]} for r in qrows]
    rrows = conn.execute(
        "SELECT code, coupon_name, coupon_type, status, order_amount, new_amount, created_at, "
        "redeemed_at, rule_version, sources FROM redemptions WHERE biz_id=?", (biz,)).fetchall()
    redemption_chain = [{
        "code": r[0], "coupon_name": r[1], "coupon_type": r[2], "status": r[3],
        "order_amount": r[4], "new_amount": r[5], "created_at": r[6], "redeemed_at": r[7],
        "rule_version": r[8], "sources": (json.loads(r[9]) if r[9] else [])
    } for r in rrows]
    conn.close()
    return JSONResponse({
        "biz_id": biz, "found": bool(qa_chain or redemption_chain),
        "kb_meta": data_freshness(), "qa_chain": qa_chain, "redemption_chain": redemption_chain,
    })


@app.get("/audit/list")
async def audit_list(request: Request, limit: int = 20):
    user = get_user_from_token(extract_token(request))
    if not user:
        return JSONResponse(status_code=401, content={"code": 40101, "message": "未登录或登录已过期"})
    limit = min(max(limit, 1), 100)
    conn = db()
    rrows = conn.execute(
        "SELECT biz_id, code, coupon_name, status, created_at FROM redemptions "
        "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    redemptions = [{"biz_id": r[0], "code": r[1], "coupon_name": r[2], "status": r[3], "created_at": r[4]}
                   for r in rrows]
    mrows = conn.execute(
        "SELECT DISTINCT biz_id, session_id, created_at FROM messages "
        "WHERE biz_id IS NOT NULL ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    qa = [{"biz_id": r[0], "session_id": r[1], "created_at": r[2]} for r in mrows]
    conn.close()
    return JSONResponse({"redemptions": redemptions, "qa": qa})


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


_NOCACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}


@app.get("/login")
def login_page():
    return FileResponse("login.html", headers=_NOCACHE)


@app.get("/member")
def member_page():
    return FileResponse("member.html", headers=_NOCACHE)


@app.get("/cashier")
def cashier_page():
    return FileResponse("cashier.html", headers=_NOCACHE)


@app.get("/audit")
def audit_page():
    return FileResponse("audit.html", headers=_NOCACHE)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
