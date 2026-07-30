from astrbot.api import logger
from utils.keyword_matcher import is_signin_message, is_lottery_message


class ActiveRewardHandler:
    def __init__(self, plugin):
        self._plugin = plugin

    async def handle(self, event):
        try:
            group_id = event.get_group_id()
            if not group_id:
                return

            msg = event.get_message_str()
            qq = event.get_sender_id()
            cfg = self._plugin.config_cache

            if not cfg["active_reward_enabled"]:
                return

            if len(msg) < cfg["active_reward_min_length"]:
                return

            sign_kw = cfg.get("keyword_sign", [])
            if is_signin_message(msg, sign_kw):
                return

            passphrase = cfg.get("lottery_passphrase", "")
            lottery_kw = cfg.get("keyword_lottery", [])
            if passphrase and is_lottery_message(msg, passphrase, lottery_kw):
                return

            is_neg = await self._plugin.point_service.is_negative(qq, group_id)
            if is_neg:
                return

            limiter = self._plugin.rate_limiter
            user_cd = cfg["active_reward_cooldown"]
            if not limiter.check_user("active_reward", qq, group_id, user_cd):
                return

            global_cd = cfg["active_reward_global_cooldown"]
            if not limiter.check_group("active_reward", group_id, global_cd):
                return

            import random
            if random.random() > cfg["active_reward_probability"]:
                return

            points = cfg["active_reward_points"]
            result = await self._plugin.point_service.add(
                qq, group_id, points, "active_reward"
            )

            from astrbot.api.message_chain import MessageChain
            await event.send(
                MessageChain().message(
                    f"\U0001f389 {event.get_sender_name()} \u53d1\u8a00\u83b7\u5f97 +{points} \u79ef\u5206\uff01\u5f53\u524d\u4f59\u989d: {result['balance']}"
                )
            )

            dk_result = await self._plugin.daily_keyword_service.check_and_claim(
                qq, group_id, msg
            )
            if dk_result.get("claimed"):
                await event.send(
                    MessageChain().message(
                        f"\U0001f3af \u4eca\u65e5\u53e3\u4ee4\u5956\u52b1: +{dk_result['points']} \u79ef\u5206"
                    )
                )

        except Exception as e:
            logger.error(f"Active reward error: {e}")
