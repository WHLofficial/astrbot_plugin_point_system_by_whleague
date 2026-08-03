"""打劫入口处理器（v0.4.0）：@ 目标解析 + 反馈格式化。

消息形态：无前缀「打劫 @目标」（取第一个有效 At 段，排除 AtAll 与 bot 自身；
其余 Plain 文本压缩空白后严格等于某打劫关键词）。
"""

from collections.abc import AsyncGenerator
from math import ceil

from astrbot.api import logger
from astrbot.api.event import MessageEventResult

from ..utils.group_info import fetch_member_info
from ..utils.keyword_matcher import parse_rob_message
from ..utils.security import clean_display_name


class RobHandler:
    def __init__(self, plugin):
        self._plugin = plugin

    async def handle(self, event) -> AsyncGenerator[MessageEventResult, None]:
        try:
            group_id = event.get_group_id()
            if not group_id:
                yield event.plain_result("\u6253\u52ab\u4ec5\u652f\u6301\u7fa4\u804a")
                return

            qq = event.get_sender_id()
            cfg = self._plugin.config_cache
            parsed = parse_rob_message(
                event.get_messages(), cfg.get("keyword_rob", []), event.get_self_id()
            )
            targets = parsed["targets"]

            if not targets:
                if parsed["has_invalid_at"]:
                    yield event.plain_result(
                        "\u4e0d\u80fd\u6253\u52ab\u673a\u5668\u4eba/\u5168\u4f53\u6210\u5458"
                    )
                elif parsed["text_match"]:
                    yield event.plain_result("\u7528\u6cd5: \u6253\u52ab @\u76ee\u6807")
                return
            if len(targets) > 1:
                yield event.plain_result(
                    "\u4e00\u6b21\u53ea\u80fd\u6253\u52ab\u4e00\u4e2a\u76ee\u6807"
                )
                return
            if targets[0] == qq:
                yield event.plain_result("\u4e0d\u80fd\u6253\u52ab\u81ea\u5df1")
                return
            if not parsed["text_match"]:
                return

            result = await self._plugin.rob_service.rob(
                qq, targets[0], group_id, bot=getattr(event, "bot", None)
            )
            if not result["performed"]:
                yield event.plain_result(result["msg"])
                return
            yield event.plain_result(
                await self._format_result(event, targets[0], group_id, result)
            )
        except Exception as e:
            logger.error(f"Rob error for {event.get_sender_id()}: {e}")
            yield event.plain_result(
                "\u6253\u52ab\u5931\u8d25\uff0c\u5df2\u8bb0\u5f55\u9519\u8bef"
            )

    async def _format_result(
        self, event, target_qq: str, group_id: str, result: dict
    ) -> str:
        info = await fetch_member_info(getattr(event, "bot", None), target_qq, group_id)
        if info:
            name = (info.get("card") or "").strip() or (
                info.get("nickname") or ""
            ).strip()
        else:
            name = ""
        display = clean_display_name(name or target_qq)

        lines = []
        if result["success"]:
            lines.append(f"\u2705 \u6253\u52ab{display}\u6210\u529f\uff01")
            lines.append(f"  \u00b7 \u62a2\u5f97: +{result['stolen']} \u79ef\u5206")
            lines.append(
                f"  \u00b7 \u76ee\u6807\u5269\u4f59: {result['target_balance']} \u79ef\u5206"
            )
        else:
            lines.append(
                f"\U0001f4a2 \u6253\u52ab\u5931\u8d25\uff01\u88ab{display}\u6293\u4f4f\u4e86\uff01"
            )
            lines.append(f"  \u00b7 \u6210\u672c: -{result['cost']} \u79ef\u5206")
        lines.append(f"  \u00b7 \u5f53\u524d\u79ef\u5206: {result['balance']}")

        cooldown = self._plugin.config_cache.get("rob_cooldown", 0)
        if cooldown > 0:
            lines.append(
                f"  \u00b7 \u51b7\u5374: {ceil(cooldown / 60)} \u5206\u949f\u540e\u53ef\u518d\u6b21\u6253\u52ab"
            )
        return "\n".join(lines)
