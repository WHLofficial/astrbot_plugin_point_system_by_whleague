import random
from astrbot.api import logger
from astrbot.api.event import MessageChain
from ..utils.keyword_matcher import is_signin_message, is_lottery_message


class ActiveRewardHandler:
    def __init__(self, plugin):
        self._plugin = plugin

    async def handle(self, event) -> None:
        try:
            group_id = event.get_group_id()
            if not group_id:
                return

            # 命令消息（以唤醒前缀开头或 @bot）不参与活跃奖励与每日口令
            if event.is_at_or_wake_command:
                return

            msg = event.get_message_str()
            qq = event.get_sender_id()
            cfg = self._plugin.config_cache

            sign_kw = cfg.get("keyword_sign", [])
            if is_signin_message(msg, sign_kw):
                return

            passphrase = cfg.get("lottery_passphrase", "")
            lottery_kw = cfg.get("keyword_lottery", [])
            if passphrase and is_lottery_message(msg, passphrase, lottery_kw):
                return

            # 每日口令：独立于活跃奖励，同一消息可同时触发两者
            dk_result = await self._plugin.daily_keyword_service.check_and_claim(
                qq, group_id, msg, bot=getattr(event, "bot", None)
            )
            if dk_result.get("claimed"):
                await event.send(
                    MessageChain().message(
                        f"\U0001f3af \u4eca\u65e5\u53e3\u4ee4\u5956\u52b1: +{dk_result['points']} \u79ef\u5206"
                    )
                )

            if not cfg["active_reward_enabled"]:
                return

            if len(msg) < cfg["active_reward_min_length"]:
                return

            # 廉价检查（内存）优先于 DB 读取
            limiter = self._plugin.rate_limiter
            user_cd = cfg["active_reward_cooldown"]
            if not limiter.check_user("active_reward", qq, group_id, user_cd):
                return

            global_cd = cfg["active_reward_global_cooldown"]
            if not limiter.check_group("active_reward", group_id, global_cd):
                return

            if random.random() > cfg["active_reward_probability"]:
                return

            is_neg = await self._plugin.point_service.is_negative(qq, group_id)
            if is_neg:
                return

            points = random.randint(
                min(cfg["active_reward_points_min"], cfg["active_reward_points_max"]),
                max(cfg["active_reward_points_min"], cfg["active_reward_points_max"]),
            )
            await self._plugin.point_service.add(
                qq, group_id, points, "active_reward", bot=getattr(event, "bot", None)
            )

            await event.send(
                MessageChain().message(
                    f"\U0001f389 \u606d\u559c\uff01{event.get_sender_name()}\u89e6\u53d1\u6d3b\u8dc3\u5956\u52b1 +{points} \u79ef\u5206"
                )
            )

        except Exception as e:
            logger.error(f"Active reward error: {e}")
