from collections.abc import AsyncGenerator
from astrbot.api import logger
from astrbot.api.event import MessageEventResult
from ..utils.security import parse_birthday


class BirthdayHandler:
    def __init__(self, plugin):
        self._plugin = plugin

    async def set_birthday(self, event) -> AsyncGenerator[MessageEventResult, None]:
        try:
            qq = event.get_sender_id()
            group_id = event.get_group_id()
            msg = event.get_message_str()
            parts = msg.split(maxsplit=1)
            if len(parts) < 2:
                yield event.plain_result("\u7528\u6cd5: /\u8bbe\u7f6e\u751f\u65e5 <MM-DD \u6216 MM\u6708DD\u65e5>")
                return
            birthday = parse_birthday(parts[1])
            await self._plugin.dao.ensure_user(qq, group_id)
            await self._plugin.db.execute(
                "UPDATE users SET birthday=?, updated_at=datetime('now','localtime') WHERE qq=? AND group_id=?",
                (birthday, qq, group_id),
            )
            yield event.plain_result(f"\u5df2\u8bbe\u7f6e\u751f\u65e5\u4e3a {birthday}")
        except ValueError as e:
            yield event.plain_result(str(e))
        except Exception as e:
            logger.error(f"Set birthday error: {e}")
            yield event.plain_result("\u8bbe\u7f6e\u5931\u8d25\uff0c\u5df2\u8bb0\u5f55\u9519\u8bef")

    async def query_birthday(self, event) -> AsyncGenerator[MessageEventResult, None]:
        try:
            group_id = event.get_group_id()
            msg = event.get_message_str()
            target_qq = event.get_sender_id()

            parts = msg.split()
            if len(parts) >= 2:
                from ..utils.security import parse_qq, parse_qq_arg
                target_qq = parse_qq_arg(parts[1])
                if target_qq is None:
                    target_qq = parse_qq(parts[1])

            user = await self._plugin.dao.get_user(target_qq, group_id)
            if not user or not user["birthday"]:
                yield event.plain_result(f"{target_qq} \u8fd8\u6ca1\u6709\u8bbe\u7f6e\u751f\u65e5")
                return
            yield event.plain_result(f"{target_qq} \u7684\u751f\u65e5\u662f {user['birthday']}")
        except Exception as e:
            logger.error(f"Query birthday error: {e}")
            yield event.plain_result("\u67e5\u8be2\u5931\u8d25\uff0c\u5df2\u8bb0\u5f55\u9519\u8bef")
