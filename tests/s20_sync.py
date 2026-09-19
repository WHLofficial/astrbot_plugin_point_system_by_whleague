"""S20 竞猜同步通道（v0.6.0 核心）：签名、幂等入账、冲正、东八区汇总、绑定、战报 ack。

验收对照（PLUGIN_PROMPT.md 验收 1~5）：
1. 合法签名 credit 入账 + 流水 reason 正确；
2. 篡改/超窗/缺头一律 401 {"error":"bad sign"}；
3. 重复 payout_id 幂等（duplicate:true，余额不变）；
4. 冲正净额正确，余额不足 409 且账本无残留；
5. summary 东八区日期过滤 + 缺省当天 + 非法 400；
另覆盖：绑定指令响应映射与出站验签、战报「先发成功后 ack / 失败不 ack 下轮重拉」、未启用全链路跳过。

说明：HTTP 层用真 aiohttp（服务端与客户端均在 127.0.0.1 随机端口），竞猜系统侧用
本文件内的 mock 服务器（与 scripts/mock-plugin.js 行为对齐：验签 + 可脚本化响应）。
"""

import asyncio
import json
import socket
import time
import types
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import aiohttp
from aiohttp import web

from .common import FakeContext, FakeEvent, TempDB, base_cfg, collect

SECRET = "testsecret"
TS_HEADER = "X-Timestamp"
SIGN_HEADER = "X-Sign"


# ─── 桩与工具 ──────────────────────────────────────────────


class _OkContext(FakeContext):
    """send_message 返回 True（FakeContext 返回 None，会被判发送失败）。"""

    async def send_message(self, origin, chain):
        self.sent.append((origin, chain))
        return True


class _FailContext(_OkContext):
    """所有群消息发送失败。"""

    async def send_message(self, origin, chain):
        self.sent.append((origin, chain))
        return False


class _SelContext(_OkContext):
    """按内容选择失败：content 含 fail_mark 时发送失败。"""

    def __init__(self, fail_mark):
        super().__init__()
        self.fail_mark = fail_mark

    async def send_message(self, origin, chain):
        self.sent.append((origin, chain))
        return self.fail_mark not in str(chain)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _dumps(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


async def _req(method: str, url: str, *, headers=None, data=None):
    """发一次真实 HTTP 请求，返回 (status, json 或 None)。"""
    async with aiohttp.ClientSession() as s:
        async with s.request(method, url, headers=headers, data=data) as resp:
            text = await resp.text()
            try:
                body = json.loads(text) if text else None
            except json.JSONDecodeError:
                body = None
            return resp.status, body


def _sign_headers(
    method: str,
    path: str,
    payload: bytes,
    *,
    secret: str = SECRET,
    ts: int | None = None,
) -> dict:
    from astrbot_plugin_point_system_by_whleague.utils.sync_sign import sign_headers

    if ts is None:
        ts = int(time.time())
    return sign_headers(secret, method, path, payload, ts=ts)


async def _post_credit(base: str, obj, *, secret: str = SECRET, sign_path=None, ts=None):
    """签名 POST /sync/credit；obj 为 bytes 时按原始字节签名发送。"""
    payload = obj if isinstance(obj, bytes) else _dumps(obj)
    headers = _sign_headers("POST", sign_path or "/sync/credit", payload,
                            secret=secret, ts=ts)
    return await _req("POST", f"{base}/sync/credit", headers=headers, data=payload)


async def _get_summary(base: str, query: str = "", *, sign_path: str | None = None,
                       headers: dict | None = None, **kw):
    path = "/sync/summary" + query
    if headers is None:
        headers = _sign_headers("GET", sign_path or path, b"", **kw)
    return await _req("GET", f"{base}{path}", headers=headers)


# ─── 组件堆叠 ──────────────────────────────────────────────


@asynccontextmanager
async def _plugin_ctx(t, context=None, **overrides):
    """构造 SyncHandler 及其依赖（真服务层 + 真 DAO + 临时库）。"""
    from astrbot_plugin_point_system_by_whleague.db.sync_dao import SyncDAO
    from astrbot_plugin_point_system_by_whleague.handlers.sync import SyncHandler
    from astrbot_plugin_point_system_by_whleague.services.point_service import (
        PointService,
    )
    from astrbot_plugin_point_system_by_whleague.services.sync_service import (
        SyncService,
    )
    from astrbot_plugin_point_system_by_whleague.utils.rate_limiter import RateLimiter

    cfg = {
        "sync_enabled": True,
        "sync_secret": SECRET,
        "sync_report_groups": ["888"],
        "sync_platform_id": "bot1",
    }
    cfg.update(overrides)
    plugin = types.SimpleNamespace(
        config_cache=base_cfg(**cfg),
        rate_limiter=RateLimiter(),
        context=context if context is not None else _OkContext(),
    )
    plugin.sync_service = SyncService(t.db, SyncDAO(t.db), PointService(t.db, t.dao))
    handler = SyncHandler(plugin)
    try:
        yield plugin, handler
    finally:
        await handler.stop()


@asynccontextmanager
async def _server_ctx(handler):
    """在随机端口启动插件的 HTTP 服务，返回 base url。"""
    port = _free_port()
    handler._plugin.config_cache["sync_listen_port"] = port
    assert await handler.start() is True
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await handler.stop()


def _mock_app(state) -> web.Application:
    """竞猜系统 mock：对入站请求做与插件相同的验签，响应可脚本化。"""
    from astrbot_plugin_point_system_by_whleague.utils.sync_sign import verify

    app = web.Application()

    async def claim(request):
        raw = await request.read()
        ok = verify(
            state["secret"], request.method, request.path_qs, raw,
            request.headers.get(TS_HEADER), request.headers.get(SIGN_HEADER),
        )
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = None
        state["bind_calls"].append({"verify": ok, "body": body})
        if not ok:
            return web.json_response({"error": "bad sign"}, status=401)
        status, payload = state["bind_queue"].pop(0)
        return web.json_response(payload, status=status)

    async def pending(request):
        raw = await request.read()
        ok = verify(
            state["secret"], request.method, request.path_qs, raw,
            request.headers.get(TS_HEADER), request.headers.get(SIGN_HEADER),
        )
        state["pending_calls"] += 1
        if not ok:
            return web.json_response({"error": "bad sign"}, status=401)
        reports = [r for r in state["reports"] if r["id"] not in state["acked"]]
        return web.json_response({"reports": reports})

    async def ack(request):
        raw = await request.read()
        ok = verify(
            state["secret"], request.method, request.path_qs, raw,
            request.headers.get(TS_HEADER), request.headers.get(SIGN_HEADER),
        )
        state["ack_calls"].append(
            {"verify": ok, "ids": json.loads(raw.decode("utf-8")).get("ids", []) if ok else None}
        )
        if not ok:
            return web.json_response({"error": "bad sign"}, status=401)
        ids = state["ack_calls"][-1]["ids"]
        state["acked"].update(ids)
        return web.json_response({"ok": True, "acked": len(ids)})

    async def unbind(request):
        raw = await request.read()
        ok = verify(
            state["secret"], request.method, request.path_qs, raw,
            request.headers.get(TS_HEADER), request.headers.get(SIGN_HEADER),
        )
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = None
        state["unbind_calls"].append({"verify": ok, "body": body})
        if not ok:
            return web.json_response({"error": "bad sign"}, status=401)
        status, payload = state["unbind_queue"].pop(0)
        return web.json_response(payload, status=status)

    async def unbind_confirm(request):
        raw = await request.read()
        ok = verify(
            state["secret"], request.method, request.path_qs, raw,
            request.headers.get(TS_HEADER), request.headers.get(SIGN_HEADER),
        )
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = None
        state["confirm_calls"].append({"verify": ok, "body": body})
        if not ok:
            return web.json_response({"error": "bad sign"}, status=401)
        status, payload = state["confirm_queue"].pop(0)
        return web.json_response(payload, status=status)

    app.router.add_post("/api/bind/claim", claim)
    app.router.add_post("/api/identity/unbind", unbind)
    app.router.add_post("/api/identity/unbind/confirm", unbind_confirm)
    app.router.add_get("/api/reports/pending", pending)
    app.router.add_post("/api/reports/ack", ack)
    return app


@asynccontextmanager
async def _mock_ctx(**extra):
    state = {
        "secret": SECRET,
        "bind_queue": [],
        "bind_calls": [],
        "unbind_queue": [],
        "unbind_calls": [],
        "confirm_queue": [],
        "confirm_calls": [],
        "pending_calls": 0,
        "reports": [],
        "ack_calls": [],
        "acked": set(),
    }
    state.update(extra)
    runner = web.AppRunner(_mock_app(state), access_log=None)
    await runner.setup()
    port = _free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    try:
        yield state, f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


# ─── 签名单元 ──────────────────────────────────────────────


async def test_sign_unit():
    """契约签名：canonical 拼接、正常通过、篡改/超窗/缺头/错密钥拒绝。"""
    from astrbot_plugin_point_system_by_whleague.utils.sync_sign import (
        build_canonical,
        sign,
        sign_headers,
        verify,
    )

    assert build_canonical("POST", "/sync/credit", 123, b"x") == b"POST|/sync/credit|123|x"
    assert build_canonical("GET", "/sync/summary?date=2026-09-09", 1, b"") == (
        b"GET|/sync/summary?date=2026-09-09|1|"
    )
    ts = int(time.time())
    h = sign_headers(SECRET, "POST", "/sync/credit", b"{}", ts=ts)
    assert h[TS_HEADER] == str(ts) and len(h[SIGN_HEADER]) == 64
    assert h[SIGN_HEADER] == h[SIGN_HEADER].lower()
    assert verify(SECRET, "POST", "/sync/credit", b"{}", h[TS_HEADER], h[SIGN_HEADER])
    # GET 空 body 一样可签可验
    hg = sign_headers(SECRET, "GET", "/sync/summary", b"", ts=ts)
    assert verify(SECRET, "GET", "/sync/summary", b"", hg[TS_HEADER], hg[SIGN_HEADER])
    # 篡改 sign / 换 body / 换 path / 错密钥 / 超窗 / 缺头 → 全 False
    assert not verify(SECRET, "POST", "/sync/credit", b"{}", h[TS_HEADER], "0" * 64)
    assert not verify(SECRET, "POST", "/sync/credit", b"other", h[TS_HEADER], h[SIGN_HEADER])
    assert not verify(SECRET, "POST", "/sync/other", b"{}", h[TS_HEADER], h[SIGN_HEADER])
    assert not verify("wrong", "POST", "/sync/credit", b"{}", h[TS_HEADER], h[SIGN_HEADER])
    old = sign(SECRET, "POST", "/sync/credit", b"{}", ts=ts - 301)
    assert not verify(SECRET, "POST", "/sync/credit", b"{}", str(ts - 301), old)
    future = sign(SECRET, "POST", "/sync/credit", b"{}", ts=ts + 301)
    assert not verify(SECRET, "POST", "/sync/credit", b"{}", str(ts + 301), future)
    assert not verify(SECRET, "POST", "/sync/credit", b"", None, h[SIGN_HEADER])
    assert not verify(SECRET, "POST", "/sync/credit", b"", h[TS_HEADER], None)


# ─── credit：入账 / 幂等 / 冲正 / 非法 body ─────────────────


async def test_credit_happy_path():
    """合法签名入账：200 {ok,duplicate:false,balance}，账本与流水落库，reason 正确。"""
    async with TempDB() as t, _plugin_ctx(t) as (plugin, handler), _server_ctx(handler) as base:
        st, body = await _post_credit(
            base,
            {"payout_id": "po-1", "qq_id": "10001", "amount": 530,
             "type": "reward", "event_id": 1},
        )
        assert st == 200, (st, body)
        assert body == {"ok": True, "duplicate": False, "balance": 530}
        assert await t.count("sync_ledger") == 1
        row = await t.db.fetchone("SELECT * FROM sync_ledger WHERE payout_id='po-1'")
        assert row["qq_id"] == "10001" and row["amount"] == 530
        assert row["type"] == "reward" and row["event_id"] == 1
        # 积分真源：余额、累计获得、流水 reason/ref_id
        assert await plugin.sync_service._point_service.get_balance("10001") == 530
        acct = await t.db.fetchone("SELECT * FROM accounts WHERE qq='10001'")
        assert acct["total_earned"] == 530
        tx = await t.db.fetchone(
            "SELECT * FROM point_transactions WHERE qq='10001'"
        )
        assert tx["amount"] == 530 and tx["balance_after"] == 530
        assert tx["reason"] == "guess_reward" and tx["ref_id"] == 1
        assert tx["group_id"] == "guess"


async def test_credit_duplicate_idempotent():
    """重复 payout_id：duplicate:true，余额与账本均不变；新 id 正常入账。"""
    payload = {"payout_id": "po-dup", "qq_id": "10002", "amount": 100, "type": "reward"}
    async with TempDB() as t, _plugin_ctx(t) as (_, handler), _server_ctx(handler) as base:
        st1, b1 = await _post_credit(base, payload)
        st2, b2 = await _post_credit(base, payload)
        assert st1 == 200 and st2 == 200
        assert b1 == {"ok": True, "duplicate": False, "balance": 100}
        assert b2 == {"ok": True, "duplicate": True, "balance": 100}
        assert await t.count("sync_ledger") == 1
        st3, b3 = await _post_credit(base, {**payload, "payout_id": "po-dup-2"})
        assert st3 == 200 and b3["duplicate"] is False and b3["balance"] == 200
        assert await t.count("sync_ledger") == 2


async def test_credit_reversal_and_conflict():
    """冲正净额正确；余额不足 409 且账本回滚无残留。"""
    async with TempDB() as t, _plugin_ctx(t) as (plugin, handler), _server_ctx(handler) as base:
        st, b = await _post_credit(
            base, {"payout_id": "po-r1", "qq_id": "10003", "amount": 530, "type": "reward"}
        )
        assert st == 200 and b["balance"] == 530
        st, b = await _post_credit(
            base, {"payout_id": "po-r2", "qq_id": "10003", "amount": -200,
                   "type": "reversal", "event_id": 1}
        )
        assert st == 200 and b == {"ok": True, "duplicate": False, "balance": 330}
        tx2 = await t.db.fetchone(
            "SELECT * FROM point_transactions WHERE qq='10003' AND amount=-200"
        )
        assert tx2["reason"] == "guess_reversal"
        acct = await t.db.fetchone("SELECT * FROM accounts WHERE qq='10003'")
        assert acct["total_earned"] == 530  # 冲正不回减累计获得，只动余额
        # 余额不足的冲正 → 409，账本无残留，余额不变
        st, b = await _post_credit(
            base, {"payout_id": "po-r3", "qq_id": "10003", "amount": -600,
                   "type": "reversal"}
        )
        assert st == 409, (st, b)
        assert await t.count("sync_ledger") == 2
        assert await plugin.sync_service._point_service.get_balance("10003") == 330


async def test_credit_bad_body():
    """签名合法但 body 非法：一律 400 {"error":"bad body"}，不产生任何账本行。"""
    cases = [
        b"{not json",                                # 非 JSON
        b"[]",                                       # 非 dict
        _dumps({"qq_id": "1", "amount": 1}),         # 缺 payout_id
        _dumps({"payout_id": "po-x", "amount": 1}),  # 缺 qq_id
        _dumps({"payout_id": "po-x", "qq_id": "1"}),  # 缺 amount
        _dumps({"payout_id": "po-x", "qq_id": "1", "amount": True}),   # bool
        _dumps({"payout_id": "po-x", "qq_id": "1", "amount": 0}),      # 零
        _dumps({"payout_id": "po-x", "qq_id": "1", "amount": "5"}),    # 字符串
        _dumps({"payout_id": "po-x", "qq_id": "1", "amount": 1_000_001}),  # 超上限
        _dumps({"payout_id": "po-x", "qq_id": "1", "amount": 1, "type": "hack"}),
        _dumps({"payout_id": "po-x", "qq_id": "1", "amount": 1, "event_id": True}),
        _dumps({"payout_id": "", "qq_id": "1", "amount": 1}),  # 空 payout_id
    ]
    async with TempDB() as t, _plugin_ctx(t) as (_, handler), _server_ctx(handler) as base:
        for i, raw in enumerate(cases):
            st, body = await _post_credit(base, raw)
            assert st == 400 and body == {"error": "bad body"}, (i, st, body)
        assert await t.count("sync_ledger") == 0
        assert await t.count("point_transactions") == 0


# ─── 验签红线：非法请求一律 401 ─────────────────────────────


async def test_auth_rejections():
    """篡改/超窗/缺头/错路径/错密钥/body 换包 → 401 {"error":"bad sign"}。"""
    payload = _dumps({"payout_id": "po-a", "qq_id": "1", "amount": 1})
    async with TempDB() as t, _plugin_ctx(t) as (_, handler), _server_ctx(handler) as base:
        # 缺头
        st, b = await _req("POST", f"{base}/sync/credit", data=payload)
        assert st == 401 and b == {"error": "bad sign"}
        # 篡改 sign
        h = _sign_headers("POST", "/sync/credit", payload)
        h[SIGN_HEADER] = "0" * 64
        assert (await _req("POST", f"{base}/sync/credit", headers=h, data=payload))[0] == 401
        # 超窗（-400s / +400s）
        for ts in (int(time.time()) - 400, int(time.time()) + 400):
            h = _sign_headers("POST", "/sync/credit", payload, ts=ts)
            st, _ = await _req("POST", f"{base}/sync/credit", headers=h, data=payload)
            assert st == 401, ts
        # 签名路径与请求行不符（含 query 场景）
        h = _sign_headers("POST", "/sync/other", payload)
        assert (await _req("POST", f"{base}/sync/credit", headers=h, data=payload))[0] == 401
        # 错密钥
        h = _sign_headers("POST", "/sync/credit", payload, secret="nope")
        assert (await _req("POST", f"{base}/sync/credit", headers=h, data=payload))[0] == 401
        # 签名后换 body
        h = _sign_headers("POST", "/sync/credit", payload)
        st, _ = await _req(
            "POST", f"{base}/sync/credit", headers=h, data=_dumps({"payout_id": "po-b"})
        )
        assert st == 401
        # summary：缺头 / 签名漏 query
        st, b = await _req("GET", f"{base}/sync/summary")
        assert st == 401 and b == {"error": "bad sign"}
        h = _sign_headers("GET", "/sync/summary", b"")
        st, _ = await _req("GET", f"{base}/sync/summary?date=2026-09-09", headers=h)
        assert st == 401
        # summary：签名含 query 才放行
        st, b = await _get_summary(base, "?date=2026-09-09")
        assert st == 200 and b["date"] == "2026-09-09"
        assert await t.count("sync_ledger") == 0  # 被拒请求不入账


# ─── summary：东八区过滤 / 缺省当天 / 非法日期 ───────────────


async def test_summary_shanghai_filter():
    """按东八区日期过滤、净额含负数、缺省当天、非法 400。"""
    today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
    today_str = today.strftime("%Y-%m-%d")
    d2_str = (today - timedelta(days=2)).strftime("%Y-%m-%d")
    async with TempDB() as t, _plugin_ctx(t) as (_, handler), _server_ctx(handler) as base:
        for pid, qq, amt, typ in [
            ("po-s1", "20001", 530, "reward"),
            ("po-s2", "20001", -200, "reversal"),
            ("po-s3", "20002", 150, "reward"),
        ]:
            st, _ = await _post_credit(base, {"payout_id": pid, "qq_id": qq,
                                              "amount": amt, "type": typ})
            assert st == 200
        # po-s3 挪到两天前
        await t.db.execute(
            "UPDATE sync_ledger SET credited_at = datetime(credited_at, '-2 day') "
            "WHERE payout_id = 'po-s3'"
        )
        st, b = await _get_summary(base, f"?date={today_str}")
        assert st == 200 and b["date"] == today_str
        assert {i["qq_id"]: i["total"] for i in b["items"]} == {"20001": 330}
        st, b = await _get_summary(base, f"?date={d2_str}")
        assert st == 200 and {i["qq_id"]: i["total"] for i in b["items"]} == {"20002": 150}
        # 缺省 date → 东八区当天
        st, b = await _get_summary(base)
        assert st == 200 and b["date"] == today_str
        assert {i["qq_id"]: i["total"] for i in b["items"]} == {"20001": 330}
        # 非法日期
        for q in ("?date=2026-13-01", "?date=not-a-date"):
            st, b = await _get_summary(base, q)
            assert st == 400 and b == {"error": "bad date"}, q


# ─── 绑定指令 ──────────────────────────────────────────────


async def test_bind_flow():
    """绑定：出站请求验签可通过；响应码 → 文案映射；网络异常与限流。"""
    cases = [
        (200, {"ok": True, "displayName": "小明"}, "绑定成功", "小明"),
        (400, {"error": "invalid_code", "message": "expired"}, "绑定码无效或已过期", None),
        (400, {"error": "qq_bound", "message": "dup"}, "已绑定过账号", None),
        (400, {"error": "user_bound", "message": "dup"}, "已绑定过其他 QQ", None),
        (401, {"error": "bad sign"}, "验签未通过", None),
        (400, {"error": "weird", "message": "维护中"}, "维护中", None),
        (400, {"error": "weird"}, "请稍后再试", None),
    ]
    async with TempDB() as t, _plugin_ctx(t) as (plugin, handler), _mock_ctx() as (state, mock_base):
        plugin.config_cache["sync_base_url"] = mock_base
        for i, (status, payload, *expects) in enumerate(cases):
            state["bind_queue"] = [(status, payload)]
            ev = FakeEvent(qq=str(9000 + i), group_id="123")
            texts = await collect(handler.handle_bind(ev, "AB12-CD34"))
            assert len(texts) == 1
            for expect in filter(None, expects):
                assert expect in texts[0], (i, texts[0])
        # 出站请求服务端验签通过、body 形状正确
        assert state["bind_calls"][0]["verify"] is True
        assert state["bind_calls"][0]["body"] == {"code": "AB12-CD34", "qq_id": "9000"}
        # 网络异常（无监听端口）
        plugin.config_cache["sync_base_url"] = f"http://127.0.0.1:{_free_port()}"
        ev = FakeEvent(qq="9100", group_id="123")
        texts = await collect(handler.handle_bind(ev, "AB12-CD34"))
        assert "网络异常" in texts[0]
        # 超长码直接拒绝，不出站
        n_calls = len(state["bind_calls"])
        ev = FakeEvent(qq="9101", group_id="123")
        texts = await collect(handler.handle_bind(ev, "A" * 65))
        assert "绑定码无效" in texts[0] and len(state["bind_calls"]) == n_calls


async def test_bind_disabled_and_rate_limit():
    """未启用提示；限流命中提示（先于出站）。"""
    async with TempDB() as t, _plugin_ctx(t, sync_enabled=False) as (plugin, handler):
        ev = FakeEvent(qq="9200", group_id="123")
        texts = await collect(handler.handle_bind(ev, "AB12-CD34"))
        assert "同步功能未启用" in texts[0]
        # 开关打开后：第一次放行（网络失败不影响限流），第二次立即被限流
        plugin.config_cache["sync_enabled"] = True
        plugin.config_cache["sync_base_url"] = f"http://127.0.0.1:{_free_port()}"
        ev1 = FakeEvent(qq="9201", group_id="123")
        texts = await collect(handler.handle_bind(ev1, "AB12-CD34"))
        assert "网络异常" in texts[0]
        ev2 = FakeEvent(qq="9201", group_id="123")
        texts = await collect(handler.handle_bind(ev2, "AB12-CD34"))
        assert "操作太频繁" in texts[0]


async def test_bind_auth_target_and_unbind():
    """统一认证 P0-8：配 bind_claim_url + bind_secret 后绑定/解绑走认证中心，
    出站用独立 bind_secret 验签（与 SYNC_SECRET 无关）；解绑响应映射；
    未配置完整时的提示。"""
    bind_secret = "bindsecret-two"
    async with TempDB() as t, _plugin_ctx(t) as (plugin, handler), _mock_ctx(secret=bind_secret) as (state, mock_base):
        plugin.config_cache["bind_claim_url"] = mock_base
        plugin.config_cache["bind_secret"] = bind_secret
        # 绑定成功：出站验签（bind_secret）通过、body 形状不变
        state["bind_queue"] = [(200, {"ok": True, "displayName": "小明"})]
        ev = FakeEvent(qq="9300", group_id="123")
        texts = await collect(handler.handle_bind(ev, "AB12-CD34"))
        assert "绑定成功" in texts[0] and "小明" in texts[0], texts[0]
        assert state["bind_calls"][0]["verify"] is True
        assert state["bind_calls"][0]["body"] == {"code": "AB12-CD34", "qq_id": "9300"}
        # bind_moved（目标仍是竞猜老路时的误配提示）
        state["bind_queue"] = [(400, {"error": "bind_moved"})]
        ev = FakeEvent(qq="9302", group_id="123")
        texts = await collect(handler.handle_bind(ev, "AB12-CD34"))
        assert "已迁移到统一认证中心" in texts[0], texts[0]
        # 解绑：成功 / 未绑定 / 未知错误回退 message；出站验签用 bind_secret
        cases = [
            (200, {"ok": True, "displayName": "小明"}, "已解绑", "积分余额不受影响"),
            (400, {"error": "not_bound"}, "未绑定过账号", None),
            (400, {"error": "weird", "message": "维护中"}, "维护中", None),
        ]
        for i, (status, payload, *expects) in enumerate(cases):
            state["unbind_queue"] = [(status, payload)]
            ev = FakeEvent(qq=str(9310 + i), group_id="123")
            texts = await collect(handler.handle_unbind(ev))
            assert len(texts) == 1
            for expect in filter(None, expects):
                assert expect in texts[0], (i, texts[0])
        assert state["unbind_calls"][0]["verify"] is True
        assert state["unbind_calls"][0]["body"] == {"qq_id": "9310"}
        # 认证中心地址配了但 bind_secret 空：绑定/解绑都提示配置不完整
        plugin.config_cache["bind_secret"] = ""
        ev = FakeEvent(qq="9320", group_id="123")
        texts = await collect(handler.handle_bind(ev, "AB12-CD34"))
        assert "未配置完整" in texts[0], texts[0]
        ev = FakeEvent(qq="9321", group_id="123")
        texts = await collect(handler.handle_unbind(ev))
        assert "解绑功能未启用" in texts[0], texts[0]

    # 完全未配 bind_claim_url：解绑指令提示未启用（独立于 sync_enabled）
    async with TempDB() as t, _plugin_ctx(t) as (plugin, handler):
        ev = FakeEvent(qq="9400", group_id="123")
        texts = await collect(handler.handle_unbind(ev))
        assert "解绑功能未启用" in texts[0], texts[0]


async def test_unbind_confirm_with_code():
    """增量 11：网页发起解绑 → 群里「解绑 <码>」走确认核销端点（P1-4）。
    带码走 /api/identity/unbind/confirm；无码兼容走原直解端点。"""
    bind_secret = "bindsecret-confirm"
    async with TempDB() as t, _plugin_ctx(t) as (plugin, handler), _mock_ctx(secret=bind_secret) as (state, mock_base):
        plugin.config_cache["bind_claim_url"] = mock_base
        plugin.config_cache["bind_secret"] = bind_secret
        # 带码成功：出站打 confirm 端点、body 带 code、验签用 bind_secret
        state["confirm_queue"] = [(200, {"ok": True, "displayName": "小明"})]
        ev = FakeEvent(qq="9500", group_id="123")
        texts = await collect(handler.handle_unbind(ev, "123456"))
        assert "已解绑" in texts[0] and "积分余额不受影响" in texts[0], texts[0]
        assert state["confirm_calls"][0]["verify"] is True
        assert state["confirm_calls"][0]["body"] == {"qq_id": "9500", "code": "123456"}
        assert state["unbind_calls"] == [], "带码不应再打直解端点"
        # 带码失败映射：无效码 / 码与 QQ 不一致 / 未绑定 / 未知错误回退 message
        cases = [
            (400, {"error": "invalid_code"}, "解绑码无效或已过期", "网页重新发起"),
            (400, {"error": "code_mismatch"}, "账号不一致", None),
            (400, {"error": "not_bound"}, "未绑定过账号", None),
            (400, {"error": "weird", "message": "维护中"}, "维护中", None),
        ]
        for i, (status, payload, *expects) in enumerate(cases):
            state["confirm_queue"] = [(status, payload)]
            ev = FakeEvent(qq=str(9510 + i), group_id="123")
            texts = await collect(handler.handle_unbind(ev, "654321"))
            assert len(texts) == 1
            for expect in filter(None, expects):
                assert expect in texts[0], (i, texts[0])
        # 无码仍走原直解端点（老路兼容）
        state["unbind_queue"] = [(200, {"ok": True, "displayName": "小明"})]
        ev = FakeEvent(qq="9520", group_id="123")
        texts = await collect(handler.handle_unbind(ev))
        assert "已解绑" in texts[0], texts[0]
        assert state["unbind_calls"][-1]["body"] == {"qq_id": "9520"}


# ─── 战报轮询 ──────────────────────────────────────────────


async def test_reports_forward_then_ack():
    """拉到战报 → 全部群发送成功 → 按 id 保序 ack；ack 后下轮不再重发。"""
    async with TempDB() as t, _plugin_ctx(t) as (plugin, handler), _mock_ctx() as (state, mock_base):
        plugin.config_cache["sync_base_url"] = mock_base
        state["reports"] = [
            {"id": "rp-1", "event_id": 1, "content": "🏆 战报一", "created_at": "x"},
            {"id": "rp-2", "event_id": 1, "content": "🏆 战报二", "created_at": "y"},
        ]
        await handler._poll_once()
        ctx = plugin.context
        assert [str(c) for _, c in ctx.sent] == ["🏆 战报一", "🏆 战报二"]
        assert all(o == "bot1:GroupMessage:888" for o, _ in ctx.sent)
        assert state["ack_calls"] == [{"verify": True, "ids": ["rp-1", "rp-2"]}]
        # ack 后 pending 清空 → 再轮询无动作
        await handler._poll_once()
        assert len(ctx.sent) == 2 and len(state["ack_calls"]) == 1


async def test_reports_failure_no_ack_then_retry():
    """发送失败不 ack；下轮重拉后成功才 ack（部分成功只 ack 成功前缀）。"""
    async with TempDB() as t, _plugin_ctx(t) as (plugin, handler), _mock_ctx() as (state, mock_base):
        plugin.config_cache["sync_base_url"] = mock_base
        state["reports"] = [
            {"id": "rp-1", "event_id": 1, "content": "🏆 A", "created_at": "x"},
            {"id": "rp-2", "event_id": 1, "content": "FAIL B", "created_at": "y"},
        ]
        # 全部发送失败 → 无 ack，战报保留
        plugin.context = _FailContext()
        await handler._poll_once()
        assert plugin.context.sent and state["ack_calls"] == []
        assert state["pending_calls"] == 1
        # 部分成功（第一条成功、第二条失败）→ 只 ack 成功前缀，失败的下轮重拉
        plugin.context = _SelContext("FAIL")
        await handler._poll_once()
        assert state["ack_calls"] == [{"verify": True, "ids": ["rp-1"]}]
        # 恢复全部成功 → 只剩 rp-2 被重发并 ack
        plugin.context = _OkContext()
        await handler._poll_once()
        assert [str(c) for _, c in plugin.context.sent] == ["FAIL B"]
        assert [a["ids"] for a in state["ack_calls"]] == [["rp-1"], ["rp-2"]]


async def test_reports_no_groups_and_disabled():
    """未配目标群：转发与 ack 都不做；未启用：连拉取都不发生。"""
    async with TempDB() as t, _plugin_ctx(t, sync_report_groups=[]) as (plugin, handler), \
            _mock_ctx() as (state, mock_base):
        plugin.config_cache["sync_base_url"] = mock_base
        state["reports"] = [{"id": "rp-1", "content": "🏆", "created_at": "x"}]
        await handler._poll_once()
        assert plugin.context.sent == [] and state["ack_calls"] == []
        assert state["pending_calls"] == 1  # 拉了但不转发不确认
        # 未启用：_poll_once 直接返回
        plugin.config_cache["sync_enabled"] = False
        await handler._poll_once()
        assert state["pending_calls"] == 1


async def test_start_disabled_and_poll_loop_cancel():
    """未启用时 start() 返回 False；poll_loop 可正常取消退出。"""
    async with TempDB() as t, _plugin_ctx(t, sync_enabled=False) as (_, handler):
        assert await handler.start() is False
        assert handler._runner is None
    async with TempDB() as t, _plugin_ctx(t, sync_secret="") as (_, handler):
        # enabled 但 secret 为空 → 视为未启用
        assert await handler.start() is False
    async with TempDB() as t, _plugin_ctx(t) as (_, handler):
        task = asyncio.create_task(handler.poll_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.cancelled()


TESTS = [
    ("签名工具（canonical/篡改/超窗/缺头）", test_sign_unit),
    ("credit 合法入账与流水", test_credit_happy_path),
    ("credit 重复幂等", test_credit_duplicate_idempotent),
    ("credit 冲正与余额不足 409", test_credit_reversal_and_conflict),
    ("credit 非法 body 400", test_credit_bad_body),
    ("验签红线 401（credit+summary）", test_auth_rejections),
    ("summary 东八区过滤与净额", test_summary_shanghai_filter),
    ("绑定指令全路径", test_bind_flow),
    ("绑定未启用与限流", test_bind_disabled_and_rate_limit),
    ("战报转发后 ack", test_reports_forward_then_ack),
    ("战报失败不 ack 下轮重拉", test_reports_failure_no_ack_then_retry),
    ("战报未配群与未启用", test_reports_no_groups_and_disabled),
    ("start 未启用与 poll_loop 取消", test_start_disabled_and_poll_loop_cancel),
]
