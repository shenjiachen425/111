# -*- coding: utf-8 -*-
"""鉴权模块：自研 JWT（HMAC-SHA256 无状态令牌）
- 支持角色：employee（员工端：收银/客服/店员）、manager（管理端：运营/策划/数据/管理层）
- 多源 token 提取：body.token 首选 -> X-Token -> Authorization -> 查询参数
  （规避发布平台网关不透传标准 Authorization 头的限制）
"""
import hmac
import hashlib
import json
import time
import base64
import secrets

SECRET_KEY = "shiguang-mvp-secret-2026"   # MVP 演示密钥，生产应环境变量注入
_ALG = "HS256"
_REVOKED = set()  # 简易黑名单（演示）

# 演示账号：员工端 / 管理端
USERS = {
    "employee01": {"username": "employee01", "password": "Employee@123456", "role": "employee", "name": "门店员工"},
    "manager01":  {"username": "manager01",  "password": "Manager@123456",  "role": "manager",  "name": "运营管理员"},
}


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def create_token(username: str, role: str) -> str:
    name = USERS.get(username, {}).get("name", username)
    header = _b64url(json.dumps({"alg": _ALG, "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps({
        "sub": username, "role": role, "name": name, "iat": int(time.time()),
        "exp": int(time.time()) + 3600 * 12, "jti": secrets.token_hex(8)
    }, separators=(",", ":")).encode())
    sig = hmac.new(SECRET_KEY.encode(), f"{header}.{payload}".encode(),
                   hashlib.sha256).hexdigest()
    return f"{header}.{payload}.{sig}"


def _verify_sig(header: str, payload: str, sig: str) -> bool:
    expect = hmac.new(SECRET_KEY.encode(), f"{header}.{payload}".encode(),
                      hashlib.sha256).hexdigest()
    return hmac.compare_digest(expect, sig)


def get_user_from_token(raw: str):
    """验签 + 过期 + 黑名单；返回 user dict 或 None。"""
    if not raw:
        return None
    try:
        h, p, s = raw.split(".")
    except ValueError:
        return None
    if not _verify_sig(h, p, s):
        return None
    try:
        payload = json.loads(_b64url_decode(p))
    except Exception:
        return None
    if payload.get("jti") in _REVOKED:
        return None
    if payload.get("exp", 0) < int(time.time()):
        return None
    return {"username": payload.get("sub"), "role": payload.get("role"), "name": payload.get("name", payload.get("sub"))}


def extract_token(request, data: dict):
    """多源提取 token。"""
    if isinstance(data, dict) and data.get("token"):
        return data["token"]
    return (request.headers.get("X-Token")
            or request.headers.get("Authorization", "").replace("Bearer ", "", 1)
            or request.query_params.get("token"))


def authenticate(username: str, password: str, role: str):
    u = USERS.get(username)
    if not u or u["password"] != password or u["role"] != role:
        return None
    return u
