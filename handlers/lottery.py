from astrbot.api import logger
from utils.keyword_matcher import is_lottery_message, is_signin_message


class LotteryHandler:
    def __init__(self, plugin):
        self._plugin = plugin

    async def handle(self, event):
        try:
            qq = event.get_sender_id()
            group_id = event.get_group_id()
            if not group_id:
                yield event.plain_result("\u62bd\u5956\u4ec5\u652f\u6301\u7fa4\u804a")
                return

            msg = event.get_message_str()
            cfg = self._plugin.config_cache

            if is_signin_message(msg, cfg["keyword_sign"]):
                return

            passphrase = cfg["lottery_passphrase"]
            lottery_kw = cfg["keyword_lottery"]
            if not is_lottery_message(msg, passphrase, lottery_kw):
                return

            result = await self._plugin.lottery_service.draw(qq, group_id)
            yield event.plain_result(result["msg"])
        except Exception as e:
            logger.error(f"Lottery error for {event.get_sender_id()}: {e}")
            yield event.plain_result("\u62bd\u5956\u5931\u8d25\uff0c\u5df2\u8bb0\u5f55\u9519\u8bef")
