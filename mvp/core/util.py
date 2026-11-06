# -*- coding: utf-8 -*-
"""通用工具：时间、业务编号、金额格式化。"""
import time
import secrets
import hashlib
import datetime


def now_iso() -> str:
    """当前时间（本地，可读格式），用于审计时间戳。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def gen_biz_id(prefix: str = "BIZ") -> str:
    """生成可追溯的业务编号：BIZ-XXXXXXXX（8 位大写十六进制）。"""
    rand = secrets.token_hex(4).upper()
    return f"{prefix}-{rand}"


def gen_code(digits: int = 6) -> str:
    """生成 6 位纯数字核销码（会员端/员工端代生成使用）。"""
    return "".join(secrets.choice("0123456789") for _ in range(digits))


def money(x: float) -> str:
    """金额保留两位小数。"""
    return f"{float(x):.2f}"
