# -*- coding: utf-8 -*-
"""数据访问层：SQLite 持久化（会话/消息/核销/转人工/知识库版本）。
所有可追溯信息（biz_id、sources、version）均落库，支撑审计链与一致率验收。"""
import sqlite3
import os
from core.util import now_iso

DB_PATH = os.path.join(os.path.dirname(__file__), "mvp.db")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS sessions(
        id TEXT PRIMARY KEY, role TEXT, created_at TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
        content TEXT, sources TEXT, biz_id TEXT, escalate INTEGER, created_at TEXT);
    CREATE TABLE IF NOT EXISTS redemptions(
        code TEXT PRIMARY KEY, coupon_key TEXT, coupon_name TEXT, coupon_type TEXT,
        status TEXT, order_amount REAL, new_amount REAL, member_name TEXT,
        created_at TEXT, redeemed_at TEXT, biz_id TEXT, version TEXT, sources TEXT);
    CREATE TABLE IF NOT EXISTS escalations(
        id INTEGER PRIMARY KEY AUTOINCREMENT, biz_id TEXT, reason TEXT, handler TEXT,
        need_confirm TEXT, input_text TEXT, status TEXT DEFAULT 'open',
        assignee TEXT, resolved_at TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS kb_versions(
        id INTEGER PRIMARY KEY AUTOINCREMENT, version TEXT, note TEXT, owner TEXT,
        snapshot TEXT, created_at TEXT);
    """)
    conn.commit()
    conn.close()


def save_message(session_id, role, content, sources, biz_id, escalate=False):
    conn = db()
    conn.execute(
        "INSERT INTO messages(session_id,role,content,sources,biz_id,escalate,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (session_id, role, content, json_dumps(sources), biz_id, int(escalate), now_iso()))
    conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now_iso(), session_id))
    conn.commit(); conn.close()


def ensure_session(sid, role):
    conn = db()
    if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
        conn.execute("INSERT INTO sessions(id,role,created_at,updated_at) VALUES(?,?,?,?)",
                     (sid, role, now_iso(), now_iso()))
    conn.commit(); conn.close()


def create_redemption(code, coupon_key, coupon_name, coupon_type, member_name, biz_id, version, sources):
    conn = db()
    conn.execute(
        "INSERT INTO redemptions(code,coupon_key,coupon_name,coupon_type,status,member_name,"
        "created_at,biz_id,version,sources) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (code, coupon_key, coupon_name, coupon_type, "pending", member_name,
         now_iso(), biz_id, version, json_dumps(sources)))
    conn.commit(); conn.close()


def redeem_redemption(code, order_amount, new_amount):
    conn = db()
    r = conn.execute("SELECT * FROM redemptions WHERE code=?", (code,)).fetchone()
    if not r:
        conn.close(); return None, "码不存在"
    if r["status"] == "used":
        conn.close(); return None, "重复核销"
    conn.execute(
        "UPDATE redemptions SET status='used', order_amount=?, new_amount=?, redeemed_at=? WHERE code=?",
        (order_amount, new_amount, now_iso(), code))
    conn.commit(); conn.close()
    return dict(r), None


def create_escalation(biz_id, reason, handler, need_confirm, input_text):
    conn = db()
    conn.execute(
        "INSERT INTO escalations(biz_id,reason,handler,need_confirm,input_text,status,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (biz_id, reason, handler, need_confirm, input_text, "open", now_iso()))
    conn.commit(); conn.close()


def list_escalations(status=None):
    conn = db()
    if status:
        rows = conn.execute("SELECT * FROM escalations WHERE status=? ORDER BY id DESC",
                            (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM escalations ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def resolve_escalation(eid, assignee):
    conn = db()
    conn.execute("UPDATE escalations SET status='resolved', assignee=?, resolved_at=? WHERE id=?",
                 (assignee, now_iso(), eid))
    conn.commit(); conn.close()


def log_kb_version(version, note, owner, snapshot):
    conn = db()
    conn.execute("INSERT INTO kb_versions(version,note,owner,snapshot,created_at) VALUES(?,?,?,?,?)",
                 (version, note, owner, snapshot, now_iso()))
    conn.commit(); conn.close()


def recent_redemptions(limit=20):
    conn = db()
    rows = conn.execute("SELECT * FROM redemptions ORDER BY created_at DESC LIMIT ?",
                        (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def recent_messages(limit=20):
    conn = db()
    rows = conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?",
                        (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def json_dumps(x):
    import json
    return json.dumps(x, ensure_ascii=False)
