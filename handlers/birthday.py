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
                yield event.plain_result(
                    "\u7528\u6cd5: /\u8bbe\u7f6e\u751f\u65e5 <MM-DD \u6216 MM\u6708DD\u65e5>"
                )
                return
            birthday = parse_birthday(parts[1])
            await self._plugin.dao.ensure_user(qq, group_id)
            await self._plugin.dao.set_account_birthday(qq, birthday)
            yield event.plain_result(f"已设置生日为 {birthday}")
        except ValueError as e:
            yield event.plain_result(str(e))
        except Exception as e:
            logger.error(f"Set birthday error: {e}")
            yield event.plain_result(
                "\u8bbe\u7f6e\u5931\u8d25\uff0c\u5df2\u8bb0\u5f55\u9519\u8bef"
            )

    async def query_birthday(self, event) -> AsyncGenerator[MessageEventResult, None]:
        try:
            msg = event.get_message_str()
            target_qq = event.get_sender_id()

            parts = msg.split()
            if len(parts) >= 2:
                from ..utils.security import parse_qq, parse_qq_arg

                target_qq = parse_qq_arg(parts[1])
                if target_qq is None:
                    target_qq = parse_qq(parts[1])

            account = await self._plugin.dao.get_account(target_qq)
            if not account or not account["birthday"]:
                yield event.plain_result(f"{target_qq} 还没有设置生日")
                return
            yield event.plain_result(f"{target_qq} 的生日是 {account['birthday']}")
        except Exception as e:
            logger.error(f"Query birthday error: {e}")
            yield event.plain_result(
                "\u67e5\u8be2\u5931\u8d25\uff0c\u5df2\u8bb0\u5f55\u9519\u8bef"
            )
