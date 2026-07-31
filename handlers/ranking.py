from collections.abc import AsyncGenerator
from astrbot.api import logger
from astrbot.api.event import MessageEventResult


class RankingHandler:
    def __init__(self, plugin):
        self._plugin = plugin

    async def handle(self, event) -> AsyncGenerator[MessageEventResult, None]:
        try:
            group_id = event.get_group_id()
            if not group_id:
                yield event.plain_result("\u6392\u884c\u4ec5\u652f\u6301\u7fa4\u804a")
                return

            result = await self._plugin.ranking_service.get_ranking(group_id)
            users = result["users"]
            if not users:
                yield event.plain_result("\u6682\u65e0\u6392\u884c\u6570\u636e")
                return

            prefix = "\U0001f30d \u5168\u5c40\u6392\u884c" if result["is_global"] else "\U0001f3c6 \u672c\u7fa4\u6392\u884c"
            lines = [f"{prefix} (Top {len(users)})"]
            for i, u in enumerate(users, 1):
                if result["is_global"]:
                    lines.append(f"{i}. {u['qq']}  {u['points']} \u79ef\u5206 (\u7fa4{u['group_id']})")
                else:
                    lines.append(f"{i}. {u['qq']}  {u['points']} \u79ef\u5206")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"Ranking error: {e}")
            yield event.plain_result("\u67e5\u8be2\u5931\u8d25\uff0c\u5df2\u8bb0\u5f55\u9519\u8bef")

    async def stats(self, event) -> AsyncGenerator[MessageEventResult, None]:
        try:
            group_id = event.get_group_id()
            if not group_id:
                yield event.plain_result("\u7edf\u8ba1\u4ec5\u652f\u6301\u7fa4\u804a")
                return
            s = await self._plugin.sign_in_service.get_stats(group_id)
            lines = [
                "\U0001f4ca \u4eca\u65e5\u7b7e\u5230\u7edf\u8ba1",
                f"\U0001f465 \u603b\u6ce8\u518c\u7528\u6237: {s['total']}",
                f"\u2705 \u4eca\u65e5\u5df2\u7b7e\u5230: {s['today_count']}",
                f"\U0001f4c8 \u7b7e\u5230\u7387: {s['rate']}",
            ]
            if s["first_signer_qq"]:
                lines.append(f"\U0001f947 \u4eca\u65e5\u9996\u7b7e: {s['first_signer_qq']}")
            if s["streak_king_qq"]:
                lines.append(f"\U0001f3c6 \u5f53\u524d\u8fde\u7b7e\u738b: {s['streak_king_qq']} ({s['streak_days']}\u5929)")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"Stats error: {e}")
            yield event.plain_result("\u67e5\u8be2\u5931\u8d25\uff0c\u5df2\u8bb0\u5f55\u9519\u8bef")
