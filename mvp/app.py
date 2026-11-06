# -*- coding: utf-8 -*-
"""会员权益智能体 MVP · 后端入口
端口：员工端(/employee) + 管理端(/manager)
组件：Agent 框架(agent_framework) + 知识库(knowledge_base) + 鉴权(auth) + 数据(db)
"""
import os
import json
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from core.knowledge_base import KnowledgeBase
from core.agent_framework import build_member_benefits_agent
from core import auth
import db
from core.util import gen_biz_id, gen_code, now_iso, money

BASE = os.path.dirname(__file__)
KB_PATH = os.path.join(BASE, "data", "knowledge.json")

kb = KnowledgeBase(KB_PATH)
agent = build_member_benefits_agent(kb)
db.init_db()

app = FastAPI(title="拾光连锁·会员权益智能体 MVP")

_NOCACHE = {"Cache-Control": "no-cache, no-store, must-revalidate",
            "Expires": "0", "Pragma": "no-cache"}


# ---------------- 鉴权依赖 ----------------
def require_role(request: Request, data: dict, role: str):
    raw = auth.extract_token(request, data)
    user = auth.get_user_from_token(raw)
    if not user or user["role"] != role:
        return None
    return user


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def compute_coupon(key: str, amount: float):
    """按券类型计算优惠后金额（收银端/核销使用）。"""
    c = kb.get(key)
    if not c:
        return {"usable": None, "new_amount": None, "reason": "券不存在"}
    t = c["type"]
    if t == "满减":
        if amount < c["threshold"]:
            return {"usable": False, "new_amount": amount,
                    "reason": f"订单未满 {c['threshold']} 元，不可用"}
        return {"usable": True, "new_amount": round(amount - c["discount"], 2),
                "reason": f"满 {c['threshold']} 减 {c['discount']}"}
    if t == "折扣":
        return {"usable": True, "new_amount": round(amount * c["rate"], 2),
                "reason": f"享 {int(c['rate']*100)} 折"}
    if t == "抵用":
        if amount < 50:
            return {"usable": False, "new_amount": amount, "reason": "订单未满 50 元，不可用"}
        return {"usable": True, "new_amount": round(amount - c["deduct"], 2),
                "reason": f"抵用 {c['deduct']} 元"}
    if t == "兑换":
        return {"usable": True, "new_amount": None, "exchange": True,
                "reason": "赠品券，不涉及金额抵扣"}
    return {"usable": False, "new_amount": amount, "reason": "未知券类型"}


# ---------------- 页面路由 ----------------
@app.get("/employee")
def employee_page():
    return FileResponse(os.path.join(BASE, "static", "employee.html"), headers=_NOCACHE)


@app.get("/manager")
def manager_page():
    return FileResponse(os.path.join(BASE, "static", "manager.html"), headers=_NOCACHE)


# ---------------- 登录 ----------------
@app.post("/api/login")
async def login(request: Request):
    data = await request.json()
    u = auth.authenticate(data.get("username"), data.get("password"), data.get("role"))
    if not u:
        return JSONResponse(status_code=401, content={"code": 401, "message": "账号或密码或角色错误"})
    return JSONResponse(content={"token": auth.create_token(u["username"], u["role"]),
                                 "role": u["role"], "name": u["name"]})


# ================= 员工端 API =================
@app.post("/api/employee/chat")
async def employee_chat(request: Request):
    data = await request.json()
    user = require_role(request, data, "employee")
    if not user:
        return JSONResponse(status_code=401, content={"code": 401, "message": "未授权"})
    sid = data.get("session_id", "emp-default")
    q = data.get("question", "")
    db.ensure_session(sid, "employee")
    res = agent.handle(sid, q, role="employee")

    # 转人工落库
    if res.escalate:
        db.create_escalation(res.biz_id, res.reason, res.handler, res.need_confirm, q)

    db.save_message(sid, "user", q, [], "", False)
    db.save_message(sid, "bot", res.answer, res.sources, res.biz_id, res.escalate)

    def stream():
        for i in range(0, len(res.answer), 6):
            yield sse("token", {"delta": res.answer[i:i + 6]})
        yield sse("meta", {
            "sources": res.sources, "version": res.version, "biz_id": res.biz_id,
            "escalate": res.escalate, "reason": res.reason, "handler": res.handler,
            "need_confirm": res.need_confirm, "kb_meta": kb.meta_full(),
        })
        yield sse("done", {"finish_reason": "stop"})
    return StreamingResponse(stream(), media_type="text/event-stream", headers=_NOCACHE)


@app.post("/api/employee/generate_code")
async def employee_generate_code(request: Request):
    data = await request.json()
    user = require_role(request, data, "employee")
    if not user:
        return JSONResponse(status_code=401, content={"code": 401, "message": "未授权"})
    key = data.get("coupon_key")
    c = kb.get(key)
    if not c:
        return JSONResponse(status_code=400, content={"code": 400, "message": "未知优惠券"})
    biz = gen_biz_id("BIZ")
    code = gen_code(6)
    db_conn = db.db()
    while db_conn.execute("SELECT 1 FROM redemptions WHERE code=?", (code,)).fetchone():
        code = gen_code(6)
    db_conn.close()
    db.create_redemption(code, key, c["name"], c["type"], user["username"],
                         biz, c.get("version", kb.version()), c.get("sources", []))
    return JSONResponse(content={"code": code, "biz_id": biz, "coupon_name": c["name"],
                                 "coupon_type": c["type"], "stores": c["stores"],
                                 "rule_version": c.get("version")})


@app.post("/api/employee/redeem")
async def employee_redeem(request: Request):
    data = await request.json()
    user = require_role(request, data, "employee")
    if not user:
        return JSONResponse(status_code=401, content={"code": 401, "message": "未授权"})
    code = str(data.get("code", "")).strip()
    amount = float(data.get("order_amount", 0))
    r = db.redeem_redemption(code, amount, 0.0)
    record, err = r
    if err:
        return JSONResponse(status_code=400, content={"code": 400, "message": err})
    comp = compute_coupon(record["coupon_key"], amount)
    # 更新应付金额
    db.redeem_redemption(code, amount, comp.get("new_amount") if comp.get("new_amount") is not None else 0.0)
    biz = gen_biz_id("BIZ")
    return JSONResponse(content={
        "ok": True, "biz_id": biz, "code": code, "coupon_name": record["coupon_name"],
        "coupon_type": record["coupon_type"], "order_amount": money(amount),
        "new_amount": money(comp["new_amount"]) if comp.get("new_amount") is not None else None,
        "usable": comp["usable"], "reason": comp["reason"],
        "exchange": comp.get("exchange", False),
        "rule_version": record.get("version"), "sources": db.json_dumps(record.get("sources")),
        "escalate": (not comp["usable"]),
    })


@app.get("/api/employee/escalations")
def employee_escalations(request: Request):
    user = require_role(request, {}, "employee")
    if not user:
        return JSONResponse(status_code=401, content={"code": 401, "message": "未授权"})
    return JSONResponse(content=db.list_escalations("open"))


# ================= 管理端 API =================
@app.get("/api/manager/kb")
def manager_kb(request: Request):
    user = require_role(request, {}, "manager")
    if not user:
        return JSONResponse(status_code=401, content={"code": 401, "message": "未授权"})
    return JSONResponse(content={"meta": kb.meta_full(), "coupons": kb.list()})


@app.post("/api/manager/kb/upsert")
async def manager_kb_upsert(request: Request):
    data = await request.json()
    user = require_role(request, data, "manager")
    if not user:
        return JSONResponse(status_code=401, content={"code": 401, "message": "未授权"})
    try:
        kb.upsert(data)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"code": 400, "message": str(e)})
    return JSONResponse(content={"ok": True, "meta": kb.meta_full(), "coupons": kb.list()})


@app.post("/api/manager/kb/publish")
async def manager_kb_publish(request: Request):
    data = await request.json()
    user = require_role(request, data, "manager")
    if not user:
        return JSONResponse(status_code=401, content={"code": 401, "message": "未授权"})
    meta = kb.publish_version(data.get("version", "v2026.12"),
                              owner=user["name"], note=data.get("note", ""))
    db.log_kb_version(meta["version"], data.get("note", ""), user["name"], kb.snapshot())
    return JSONResponse(content={"ok": True, "meta": kb.meta_full(), "coupons": kb.list()})


@app.get("/api/manager/audit")
def manager_audit(request: Request):
    user = require_role(request, {}, "manager")
    if not user:
        return JSONResponse(status_code=401, content={"code": 401, "message": "未授权"})
    return JSONResponse(content={"redemptions": db.recent_redemptions(20),
                                "messages": db.recent_messages(20)})


@app.get("/api/manager/escalations")
def manager_escalations(request: Request):
    user = require_role(request, {}, "manager")
    if not user:
        return JSONResponse(status_code=401, content={"code": 401, "message": "未授权"})
    return JSONResponse(content=db.list_escalations())


@app.post("/api/manager/escalations/resolve")
async def manager_escalations_resolve(request: Request):
    data = await request.json()
    user = require_role(request, data, "manager")
    if not user:
        return JSONResponse(status_code=401, content={"code": 401, "message": "未授权"})
    db.resolve_escalation(data.get("id"), user["name"])
    return JSONResponse(content={"ok": True})


@app.get("/api/manager/overview")
def manager_overview(request: Request):
    user = require_role(request, {}, "manager")
    if not user:
        return JSONResponse(status_code=401, content={"code": 401, "message": "未授权"})
    red = db.recent_redemptions(1000)
    esc = db.list_escalations()
    used = sum(1 for r in red if r["status"] == "used")
    open_esc = sum(1 for e in esc if e["status"] == "open")
    return JSONResponse(content={
        "kb_version": kb.version(),
        "coupon_count": len(kb.list()),
        "redemption_total": len(red),
        "redemption_used": used,
        "escalation_total": len(esc),
        "escalation_open": open_esc,
    })


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
