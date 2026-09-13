"""竞猜系统同步通道：HTTP 服务端 + 战报轮询 + 绑定指令。

- HTTP 服务（aiohttp）：POST /sync/credit、GET /sync/summary，
  所有端点先验签（失败一律 401 {"error":"bad sign"}），仅监听本机回环地址。
- 战报轮询：每 sync_poll_interval 秒拉取待发战报 → 转发到配置群 →
  全部目标群发送成功才 ack；失败不 ack，下轮重拉。
- 绑定指令：群内「绑定 <码>」→ 核销一次性码。目标可切：配了 bind_claim_url 走
  统一认证中心（bind_secret 独立验签，P0-8），留空沿用竞猜系统老路。
- 解绑指令：群内「解绑」→ 调认证中心 /api/identity/unbind（仅认证中心绑定模式）。

安全红线：SECRET 不打日志；验签用原始请求行与原始 body 字节。
"""

import asyncio
import json
from collections.abc import AsyncGenerator

import aiohttp
from aiohttp import web

from astrbot.api import logger
from astrbot.api.event import MessageChain, MessageEventResult
from astrbot.api.platform import MessageType

from ..services.sync_service import SyncConflictError, SyncValidationError
from ..utils.sync_sign import SIGN_HEADER, TS_HEADER, sign_headers, verify

# 绑定码长度上限（竞猜系统为 8 位形如 AB12-CD34，留裕量防刷超长串）
_BIND_CODE_MAX_LEN = 64


class SyncHandler:
    def __init__(self, plugin):
        self._plugin = plugin
        self._runner: web.AppRunner | None = None
        self._session: aiohttp.ClientSession | None = None

    # ─── 配置 ─────────────────────────────────────────────

    def _cfg(self, key: str, default=None):
        return self._plugin.config_cache.get(key, default)

    def _enabled(self) -> bool:
        """总开关：sync_enabled 且已配置非空 SYNC_SECRET。"""
        if not self._cfg("sync_enabled", False):
            return False
        if not str(self._cfg("sync_secret") or "").strip():
            logger.warning("[sync] sync_enabled 已开启但 sync_secret 为空，同步不生效")
            return False
        return True

    def _base_url(self) -> str:
        return str(self._cfg("sync_base_url") or "").strip().rstrip("/")

    def _bind_target(self) -> tuple[str, str, bool]:
        """绑定/解绑指令的出站目标（统一认证 P0-8）：
        配了 bind_claim_url 走认证中心（bind_secret 独立验签，不复用 SYNC_SECRET）；
        留空沿用竞猜系统老路。返回 (base, secret, is_auth)。"""
        bind_url = str(self._cfg("bind_claim_url") or "").strip().rstrip("/")
        if bind_url:
            return bind_url, str(self._cfg("bind_secret") or "").strip(), True
        return self._base_url(), str(self._cfg("sync_secret") or "").strip(), False

    # ─── HTTP 服务端 ──────────────────────────────────────

    async def start(self) -> bool:
        """启动 HTTP 服务。未启用或端口占用时返回 False（不抛异常）。"""
        if not self._enabled():
            logger.info("[sync] sync disabled, http server not started")
            return False
        host = str(self._cfg("sync_listen_host", "127.0.0.1"))
        try:
            port = int(self._cfg("sync_listen_port", 9991))
        except (TypeError, ValueError):
            logger.error("[sync] invalid sync_listen_port, using 9991")
            port = 9991
        app = web.Application()
        app.router.add_post("/sync/credit", self._http_credit)
        app.router.add_get("/sync/summary", self._http_summary)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, port)
        try:
            await site.start()
        except OSError as e:
            logger.error(f"[sync] failed to listen {host}:{port}: {e}")
            await self._runner.cleanup()
            self._runner = None
            return False
        logger.info(f"[sync] http server listening on {host}:{port}")
        return True

    async def stop(self):
        if self._session is not None and not self._session.closed:
            try:
                await self._session.close()
            except Exception as e:
                logger.warning(f"[sync] close http session error: {e}")
        self._session = None
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception as e:
                logger.warning(f"[sync] cleanup http server error: {e}")
            self._runner = None

    @staticmethod
    def _request_path(request) -> str:
        """原始请求行 path（含 query，未解码），与契约签名一致。

        aiohttp 的 raw_path 即原始 request target，已含 query；仅在缺失时补拼。
        """
        raw = request.raw_path
        if "?" in raw:
            return raw
        qs = request.query_string
        return f"{raw}?{qs}" if qs else raw

    def _verify_request(self, request, body: bytes) -> bool:
        secret = str(self._cfg("sync_secret") or "").strip()
        return verify(
            secret,
            request.method,
            self._request_path(request),
            body,
            request.headers.get(TS_HEADER),
            request.headers.get(SIGN_HEADER),
        )

    async def _http_credit(self, request) -> web.Response:
        raw = await request.read()
        if not self._verify_request(request, raw):
            return web.json_response({"error": "bad sign"}, status=401)
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "bad body"}, status=400)
        try:
            result = await self._plugin.sync_service.handle_credit(body)
        except SyncValidationError:
            return web.json_response({"error": "bad body"}, status=400)
        except SyncConflictError as e:
            return web.json_response({"error": str(e)}, status=409)
        except Exception as e:
            logger.error(f"[sync] credit internal error: {e}")
            return web.json_response({"error": "internal error"}, status=500)
        return web.json_response(result)

    async def _http_summary(self, request) -> web.Response:
        raw = await request.read()
        if not self._verify_request(request, raw):
            return web.json_response({"error": "bad sign"}, status=401)
        try:
            result = await self._plugin.sync_service.handle_summary(
                request.query.get("date")
            )
        except SyncValidationError:
            return web.json_response({"error": "bad date"}, status=400)
        except Exception as e:
            logger.error(f"[sync] summary internal error: {e}")
            return web.json_response({"error": "internal error"}, status=500)
        return web.json_response(result)

    # ─── 出站请求（插件 → 竞猜系统） ─────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def _signed_request(
        self, method: str, path_with_query: str, body_obj: dict | None = None,
        *, base: str | None = None, secret: str | None = None,
    ) -> tuple[int | None, dict | None]:
        """带签名的出站请求。返回 (status, 解析后的 JSON)；网络异常返回 (None, None)。

        契约：rawBody 必须与签名一致，故先序列化再签名、原样发送。
        base/secret 缺省走竞猜同步通道；绑定/解绑指令显式传入 _bind_target() 的目标。
        """
        secret = str(self._cfg("sync_secret") or "").strip() if secret is None else secret
        base = self._base_url() if base is None else base
        if not secret or not base:
            logger.warning(
                "[sync] outbound skipped: sync_secret or sync_base_url not configured"
            )
            return None, None
        if body_obj is None:
            payload = b""
        else:
            payload = json.dumps(
                body_obj, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        headers = sign_headers(secret, method, path_with_query, payload)
        if payload:
            headers["Content-Type"] = "application/json; charset=utf-8"
        session = await self._get_session()
        try:
            async with session.request(
                method, base + path_with_query, data=payload or None, headers=headers
            ) as resp:
                text = await resp.text()
                try:
                    data = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    data = None
                if resp.status != 200:
                    err = data.get("error") if isinstance(data, dict) else ""
                    logger.warning(
                        f"[sync] {method} {path_with_query} -> {resp.status} {err}"
                    )
                return resp.status, data if isinstance(data, dict) else None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[sync] {method} {path_with_query} network error: {e}")
            return None, None

    # ─── 战报轮询 ─────────────────────────────────────────

    async def poll_loop(self):
        while True:
            try:
                try:
                    interval = max(15, int(self._cfg("sync_poll_interval", 60)))
                except (TypeError, ValueError):
                    interval = 60
                await asyncio.sleep(interval)
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[sync] poll loop error: {e}")

    async def _poll_once(self):
        if not self._enabled():
            return
        status, data = await self._signed_request("GET", "/api/reports/pending")
        if status != 200 or not isinstance(data, dict):
            return
        reports = data.get("reports")
        if not isinstance(reports, list) or not reports:
            return
        groups = [
            str(g).strip()
            for g in (self._cfg("sync_report_groups") or [])
            if str(g).strip()
        ]
        if not groups:
            logger.warning(
                "[sync] 有待发战报但 sync_report_groups 未配置，本轮不转发也不确认"
            )
            return
        acked: list[str] = []
        for r in reports:
            if not isinstance(r, dict):
                logger.warning(f"[sync] 忽略异常战报记录: {r!r}")
                continue
            rid = str(r.get("id") or "")
            content = r.get("content")
            if not rid or not isinstance(content, str) or not content:
                logger.warning(f"[sync] 忽略异常战报记录: {r!r}")
                continue
            if await self._send_to_groups(content, groups):
                acked.append(rid)
                logger.info(f"[sync] report {rid} forwarded to {len(groups)} group(s)")
            else:
                logger.warning(f"[sync] report {rid} 发送失败，未确认，下轮重试")
                break
        if acked:
            st, resp = await self._signed_request(
                "POST", "/api/reports/ack", {"ids": acked}
            )
            if st == 200 and isinstance(resp, dict) and resp.get("ok"):
                logger.info(f"[sync] acked {len(acked)} report(s)")
            else:
                logger.warning(
                    f"[sync] ack failed (status={st})，这批战报可能被重复转发"
                )

    def _platform_candidates(self) -> list[str]:
        """战报发送的候选平台 id：配置的优先，其余平台实例兜底（首个成功即用）。"""
        candidates: list[str] = []
        configured = str(self._cfg("sync_platform_id") or "").strip()
        if configured:
            candidates.append(configured)
        platform_insts = getattr(
            getattr(self._plugin.context, "platform_manager", None),
            "platform_insts",
            None,
        )
        if platform_insts:
            for inst in platform_insts:
                pid = getattr(inst.meta(), "id", None)
                if pid and str(pid) not in candidates:
                    candidates.append(str(pid))
        return candidates or ["aiocqhttp"]

    async def _send_to_groups(self, content: str, groups: list[str]) -> bool:
        """向全部目标群发送战报；任一群失败即返回 False（调用方不 ack）。"""
        candidates = self._platform_candidates()
        for gid in groups:
            sent = False
            for pid in candidates:
                origin = f"{pid}:{MessageType.GROUP_MESSAGE.value}:{gid}"
                try:
                    sent = bool(
                        await self._plugin.context.send_message(
                            origin, MessageChain().message(content)
                        )
                    )
                except Exception as e:
                    logger.warning(f"[sync] report send via {pid} to {gid} error: {e}")
                    sent = False
                if sent:
                    break
            if not sent:
                logger.warning(
                    f"[sync] report send to group {gid} failed (all platforms)"
                )
                return False
        return True

    # ─── 绑定指令（统一认证 P0-8：目标可切认证中心，解绑指令同通道） ───────

    async def handle_bind(
        self, event, code: str
    ) -> AsyncGenerator[MessageEventResult, None]:
        qq = event.get_sender_id()
        try:
            cooldown = max(0, int(self._cfg("sync_bind_cooldown", 10)))
        except (TypeError, ValueError):
            cooldown = 10
        if not self._plugin.rate_limiter.check_user(
            "sync_bind", qq, event.get_group_id() or "", cooldown
        ):
            remaining = self._plugin.rate_limiter.get_remaining(
                "sync_bind", qq, event.get_group_id() or "", cooldown
            )
            yield event.plain_result(f"操作太频繁，请 {remaining:.0f} 秒后再试")
            return
        tbase, tsecret, is_auth = self._bind_target()
        if not is_auth and not self._enabled():
            yield event.plain_result("同步功能未启用，请联系管理员")
            return
        if not tbase or not tsecret:
            yield event.plain_result(
                "绑定功能未配置完整（认证中心需配 bind_claim_url 与 bind_secret），请联系管理员"
                if is_auth
                else "同步功能未启用，请联系管理员"
            )
            return
        if not code or len(code) > _BIND_CODE_MAX_LEN:
            yield event.plain_result(
                f"绑定码无效，请在{'认证中心' if is_auth else '竞猜'}网页重新生成"
            )
            return
        status, data = await self._signed_request(
            "POST", "/api/bind/claim", {"code": code, "qq_id": qq},
            base=tbase, secret=tsecret,
        )
        if status is None:
            yield event.plain_result("绑定失败：网络异常，请稍后再试")
            return
        if status == 200 and isinstance(data, dict) and data.get("ok"):
            name = str(data.get("displayName") or "").strip()
            display = f"{name} ({qq})" if name else qq
            yield event.plain_result(f"✅ 绑定成功：QQ {qq} ↔ {display}")
            return
        if status == 401:
            logger.error("[sync] bind claim rejected by server (bad sign)")
            yield event.plain_result("绑定失败：服务端验签未通过，请联系管理员检查绑定密钥配置")
            return
        err = (data or {}).get("error", "") if isinstance(data, dict) else ""
        gen_hint = "认证中心" if is_auth else "竞猜"
        messages = {
            "invalid_code": f"❌ 绑定码无效或已过期，请在{gen_hint}网页重新生成",
            "qq_bound": "❌ 该 QQ 已绑定过账号，无需重复绑定",
            "user_bound": "❌ 该账号已绑定过其他 QQ",
            "bind_moved": "❌ 绑定已迁移到统一认证中心，请在认证中心网页重新生成绑定码",
        }
        if err in messages:
            yield event.plain_result(messages[err])
            return
        fallback = (
            (data or {}).get("message") if isinstance(data, dict) else None
        )
        yield event.plain_result(f"❌ 绑定失败：{fallback or '请稍后再试'}")

    async def handle_unbind(self, event) -> AsyncGenerator[MessageEventResult, None]:
        """解绑指令：解除本 QQ 与账号的绑定。仅认证中心绑定模式下可用；
        积分真源在本插件侧、主键 QQ 号，解绑只解除关联，积分余额不动。"""
        qq = event.get_sender_id()
        try:
            cooldown = max(0, int(self._cfg("sync_bind_cooldown", 10)))
        except (TypeError, ValueError):
            cooldown = 10
        if not self._plugin.rate_limiter.check_user(
            "sync_unbind", qq, event.get_group_id() or "", cooldown
        ):
            remaining = self._plugin.rate_limiter.get_remaining(
                "sync_unbind", qq, event.get_group_id() or "", cooldown
            )
            yield event.plain_result(f"操作太频繁，请 {remaining:.0f} 秒后再试")
            return
        tbase, tsecret, is_auth = self._bind_target()
        if not is_auth or not tbase or not tsecret:
            yield event.plain_result("解绑功能未启用：需先配置认证中心绑定（bind_claim_url 与 bind_secret）")
            return
        status, data = await self._signed_request(
            "POST", "/api/identity/unbind", {"qq_id": qq},
            base=tbase, secret=tsecret,
        )
        if status is None:
            yield event.plain_result("解绑失败：网络异常，请稍后再试")
            return
        if status == 200 and isinstance(data, dict) and data.get("ok"):
            name = str(data.get("displayName") or "").strip()
            display = f"{name} ({qq})" if name else qq
            yield event.plain_result(f"✅ 已解绑：QQ {qq} ↔ {display}，积分余额不受影响")
            return
        if status == 401:
            logger.error("[sync] unbind rejected by server (bad sign)")
            yield event.plain_result("解绑失败：服务端验签未通过，请联系管理员检查绑定密钥配置")
            return
        err = (data or {}).get("error", "") if isinstance(data, dict) else ""
        if err == "not_bound":
            yield event.plain_result("该 QQ 未绑定过账号")
            return
        fallback = (
            (data or {}).get("message") if isinstance(data, dict) else None
        )
        yield event.plain_result(f"❌ 解绑失败：{fallback or '请稍后再试'}")
