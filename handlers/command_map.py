"""Command map handler: render the plugin's full command diagram as an image.

Rendering pipeline (3-level fallback, always replies):
1. HTML poster via Star.html_render() (network t2i endpoint).
2. Markdown via Star.text_to_image() (network, falls back to local PIL).
3. Plain text markdown as the final fallback.

Every render path is rate-limited and the image is cached on disk keyed by
a signature of (catalog + config subset + plugin version), so config or
version changes force a re-render on the next trigger.
"""

import asyncio
from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import MessageEventResult

from ..services.command_map import (
    _POSTER_OPTIONS,
    _POSTER_TEMPLATE,
    CommandMapCache,
    build_map_data,
    build_markdown,
    cache_signature,
    poster_data,
)


class CommandMapHandler:
    def __init__(self, plugin, cache: CommandMapCache | None = None):
        self._plugin = plugin
        self.cache = cache if cache is not None else CommandMapCache()
        self._render_lock = asyncio.Lock()

    async def handle(self, event) -> AsyncGenerator[MessageEventResult, None]:
        qq = event.get_sender_id()
        group_id = event.get_group_id() or ""
        cfg = self._plugin.config_cache
        user_cooldown = int(cfg.get("cmd_map_user_cooldown", 30))
        group_cooldown = int(cfg.get("cmd_map_group_cooldown", 10))
        ttl_seconds = int(cfg.get("cmd_map_cache_ttl_hours", 24)) * 3600
        limiter = self._plugin.rate_limiter
        if not limiter.check_user("cmd_map", qq, group_id, user_cooldown):
            yield event.plain_result("指令图生成过于频繁，请稍后再试")
            return
        if not limiter.check_group("cmd_map", group_id, group_cooldown):
            yield event.plain_result("本群指令图生成过于频繁，请稍后再试")
            return

        data = build_map_data(cfg)
        sig = cache_signature(data)
        markdown = build_markdown(data)

        path = self.cache.get(sig, ttl_seconds)
        if path:
            yield event.image_result(path)
            return

        async with self._render_lock:
            path = self.cache.get(sig, ttl_seconds)
            if not path:
                path = await self._render_and_store(data, sig, ttl_seconds)
            if path:
                yield event.image_result(path)
            else:
                yield event.plain_result(markdown)

    async def _render_and_store(
        self, data: dict, sig: str, ttl_seconds: float
    ) -> str | None:
        """Render the command map image with fallbacks and cache the result.

        Args:
            data: Command map data (from build_map_data()).
            sig: Cache signature.
            ttl_seconds: Cache entry lifetime in seconds (0 disables caching).

        Returns:
            Local image path to send, or None when all renderers failed.
        """
        try:
            out = await self._plugin.html_render(
                _POSTER_TEMPLATE,
                poster_data(data),
                return_url=False,
                options=_POSTER_OPTIONS,
            )
            if out and not str(out).startswith("http"):
                path = self.cache.store(sig, str(out), ttl_seconds)
                if path:
                    return path
        except Exception as e:
            logger.warning(f"Command map poster render failed: {e}")

        try:
            out = await self._plugin.text_to_image(build_markdown(data), return_url=False)
            if out and not str(out).startswith("http"):
                path = self.cache.store(sig, str(out), ttl_seconds)
                if path:
                    return path
        except Exception as e:
            logger.warning(f"Command map markdown render failed: {e}")

        logger.error("Command map render failed on all paths; falling back to text.")
        return None

    async def sweep_loop(self) -> None:
        """Background cache sweep; runs until cancelled.

        The sweep interval is derived from the configured cache TTL
        (TTL / 4, at least 1 hour), so TTL changes take effect without
        restarting the loop.
        """
        try:
            while True:
                cfg = self._plugin.config_cache
                ttl_seconds = int(cfg.get("cmd_map_cache_ttl_hours", 24)) * 3600
                interval = max(3600, ttl_seconds // 4)
                self.cache.sweep(ttl_seconds)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
