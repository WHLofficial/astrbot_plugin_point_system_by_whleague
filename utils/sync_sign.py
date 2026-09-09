"""竞猜系统 ↔ 积分插件 同步通道签名工具。

签名契约（WHL-Daily-Activities-System/docs/astrbot-sync-api.md）：
    X-Sign      = hex(HMAC-SHA256(SECRET, "METHOD|path含query|秒级ts|rawBody"))
    X-Timestamp = Unix 秒字符串，允许 ±300 秒时钟偏差
    小写 hex，常数时间比较；GET 请求 rawBody 为空串。
"""

import hashlib
import hmac
import time

SIGN_HEADER = "X-Sign"
TS_HEADER = "X-Timestamp"
SIGN_WINDOW_SECONDS = 300


def build_canonical(method: str, path_with_query: str, ts: int, body: bytes | None) -> bytes:
    """构造待签名字节串。body 以原始字节直接拼接，避免任何编解码差异。"""
    if body is None:
        body = b""
    head = f"{method.upper()}|{path_with_query}|{ts}|".encode("utf-8")
    return head + body


def sign(
    secret: str,
    method: str,
    path_with_query: str,
    body: bytes | None,
    ts: int | None = None,
) -> str:
    """按契约计算签名（小写 hex）。"""
    if ts is None:
        ts = int(time.time())
    canonical = build_canonical(method, path_with_query, ts, body)
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def sign_headers(
    secret: str,
    method: str,
    path_with_query: str,
    body: bytes | None,
    ts: int | None = None,
) -> dict[str, str]:
    """构造出站请求的签名头（插件 → 竞猜系统）。"""
    if ts is None:
        ts = int(time.time())
    return {
        TS_HEADER: str(ts),
        SIGN_HEADER: sign(secret, method, path_with_query, body, ts),
    }


def verify(
    secret: str,
    method: str,
    path_with_query: str,
    body: bytes | None,
    ts_header: str | None,
    sign_header: str | None,
    now: int | None = None,
    window: int = SIGN_WINDOW_SECONDS,
) -> bool:
    """校验入站请求签名。任何缺失/格式错误/超窗/不匹配一律 False。

    Args:
        secret: 共享密钥。
        method: HTTP 方法。
        path_with_query: 原始请求行中的 path（含 query，未解码）。
        body: 原始请求体字节（GET 传 b""）。
        ts_header: X-Timestamp 头。
        sign_header: X-Sign 头。
        now: 当前 Unix 秒（测试可注入）。
        window: 允许的时钟偏差秒数。
    """
    if not secret or not ts_header or not sign_header:
        return False
    try:
        ts = int(str(ts_header).strip())
    except (TypeError, ValueError):
        return False
    if now is None:
        now = int(time.time())
    if abs(now - ts) > window:
        return False
    expected = sign(secret, method, path_with_query, body, ts)
    provided = str(sign_header).strip().lower()
    return hmac.compare_digest(expected, provided)
