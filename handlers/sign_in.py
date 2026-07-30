from astrbot.api import logger
from utils.keyword_matcher import is_signin_message


class SignInHandler:
    def __init__(self, plugin):
        self._plugin = plugin

    async def handle(self, event):
        try:
            qq = event.get_sender_id()
            group_id = event.get_group_id()
            if not group_id:
                yield event.plain_result("\u7b7e\u5230\u4ec5\u652f\u6301\u7fa4\u804a")
                return
            platform = event.get_platform_name()
            msg = event.get_message_str()

            result = await self._plugin.sign_in_service.sign_in(qq, group_id, platform, msg)
            if result["already_signed"]:
                yield event.plain_result(result["msg"])
                return

            yield event.plain_result(result["msg"])
        except Exception as e:
            logger.error(f"Sign-in error for {event.get_sender_id()}: {e}")
            yield event.plain_result("\u7b7e\u5230\u5931\u8d25\uff0c\u5df2\u8bb0\u5f55\u9519\u8bef")
