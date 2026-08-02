import json
import random
import time
from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import MessageEventResult

from ..utils.security import parse_int, parse_qq, parse_qq_arg, sanitize_text

_CLEAR_TOKEN_TTL = 300.0

# 群清空范围对应的表（顺序即删除顺序，先删子表再删父表以通过外键约束）。
# 群清空=清零本群成员的共享积分 + 删除群级记录，users（成员关系）与 accounts（身份）保留。
_GROUP_CLEAR_TABLES = (
    "daily_keyword_claim",
    "daily_keyword",
    "redeem_records",
    "sign_in_log",
    "lottery_record",
    "point_transactions",
    "birthday_announce_log",
)
_GLOBAL_CLEAR_TABLES = (
    "daily_keyword_claim",
    "daily_keyword",
    "redeem_records",
    "redeem_items",
    "sign_in_log",
    "lottery_record",
    "point_transactions",
    "birthday_announce_log",
    "date_rewards",
    "admins",
    "easter_events",
    "users",
    "accounts",
)


class AdminHandler:
    def __init__(self, plugin):
        self._plugin = plugin
        self._pending_clears: dict[str, dict] = {}
        """qq -> {"token", "scope", "group_id", "expires_at"}"""

    def _prune_pending_clears(self, keep: str | None = None) -> None:
        """清理已过期的清空令牌，防止长期未确认的条目驻留内存。

        Args:
            keep: 保留的 QQ（当前操作者），其过期判定留给 confirm_clear 给出明确提示。
        """
        if not self._pending_clears:
            return
        now = time.time()
        expired = [
            qq
            for qq, p in self._pending_clears.items()
            if qq != keep and p["expires_at"] <= now
        ]
        for qq in expired:
            del self._pending_clears[qq]

    async def _is_admin(self, event) -> bool:
        if event.is_admin():
            return True
        qq = event.get_sender_id()
        group_id = event.get_group_id()
        return await self._plugin.dao.is_admin(qq, group_id)

    async def _is_group_owner(self, event) -> bool:
        """aiocqhttp 平台：调用 get_group 拉取群信息判断发送者是否为群主。

        其他平台不支持时返回 False。
        """
        get_group = getattr(event, "get_group", None)
        if not get_group:
            return False
        try:
            group = await get_group()
        except Exception as e:
            logger.warning(f"Failed to fetch group info for owner check: {e}")
            return False
        return group is not None and str(group.group_owner) == str(
            event.get_sender_id()
        )

    async def _require_admin(self, event) -> bool:
        """普通协程（非生成器）：返回是否有管理员权限。"""
        return await self._is_admin(event)

    async def _require_super_admin(self, event) -> bool:
        """提权类操作：仅限 AstrBot 全局管理员或群主。"""
        if event.is_admin():
            return True
        return await self._is_group_owner(event)

    async def _deny(self, event) -> AsyncGenerator[MessageEventResult, None]:
        yield event.plain_result("你没有权限执行此操作")

    async def adjust_points(
        self, event, action: str
    ) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        try:
            msg = event.get_message_str()
            parts = msg.split()
            if len(parts) < 3:
                yield event.plain_result(f"用法: /{action} @用户/Q号 <分值>")
                return
            target = parse_qq_arg(parts[1])
            if target is None:
                target = parse_qq(parts[1])
            amount = parse_int(parts[2], min_val=1, max_val=1000000)
            group_id = event.get_group_id()
            admin_qq = event.get_sender_id()

            reason = "admin_add" if action == "加分" else "admin_sub"
            if action == "加分":
                r = await self._plugin.point_service.add(
                    target,
                    group_id,
                    amount,
                    reason,
                    admin_override=True,
                    admin_qq=admin_qq,
                    bot=getattr(event, "bot", None),
                )
                yield event.plain_result(
                    f"已给 {target} 加 {amount} 积分，当前余额: {r['balance']}"
                )
            else:
                try:
                    r = await self._plugin.point_service.subtract(
                        target,
                        group_id,
                        amount,
                        reason,
                        admin_qq=admin_qq,
                        bot=getattr(event, "bot", None),
                    )
                    yield event.plain_result(
                        f"已给 {target} 扣 {amount} 积分，当前余额: {r['balance']}"
                    )
                except ValueError as e:
                    yield event.plain_result(str(e))
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"参数错误: {e}")
        except Exception as e:
            logger.error(f"Admin adjust points error: {e}")
            yield event.plain_result("操作失败，已记录错误")

    async def add_item(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        try:
            msg = event.get_message_str()
            parts = msg.split(maxsplit=3)
            if len(parts) < 3:
                yield event.plain_result("用法: /添加兑换 <名称> <消耗> [库存]")
                return
            name = sanitize_text(parts[1])
            cost = parse_int(parts[2], min_val=1)
            stock = -1
            if len(parts) >= 4:
                stock = parse_int(parts[3], min_val=-1)
            await self._plugin.dao.add_item(name, cost, stock)
            yield event.plain_result(
                f"已添加兑换物品: {name} (消耗{cost}积分, 库存{stock})"
            )
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"参数错误: {e}")
        except Exception as e:
            logger.error(f"Add item error: {e}")
            yield event.plain_result("操作失败")

    async def delete_item(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        try:
            msg = event.get_message_str()
            parts = msg.split()
            if len(parts) < 2:
                yield event.plain_result("用法: /删除兑换 <物品ID>")
                return
            item_id = parse_int(parts[1], min_val=1)
            await self._plugin.dao.soft_delete_item(item_id)
            yield event.plain_result(f"已删除兑换物品 (ID: {item_id})")
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"参数错误: {e}")
        except Exception as e:
            logger.error(f"Delete item error: {e}")
            yield event.plain_result("操作失败")

    async def modify_item(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        try:
            msg = event.get_message_str()
            parts = msg.split(maxsplit=3)
            if len(parts) < 4:
                yield event.plain_result("用法: /修改兑换 <ID> <字段> <值>")
                return
            item_id = parse_int(parts[1], min_val=1)
            field = parts[2]
            raw_value = parts[3]
            if field == "cost":
                value = parse_int(raw_value, min_val=1)
            elif field == "stock":
                value = parse_int(raw_value, min_val=-1)
            elif field == "discount_price":
                value = parse_int(raw_value, min_val=1)
                item = await self._plugin.dao.get_item(item_id)
                if item and value >= item["cost"]:
                    yield event.plain_result("折扣价应低于原价")
                    return
            elif field == "discount_end_time":
                from datetime import datetime

                try:
                    datetime.strptime(raw_value, "%Y-%m-%d %H:%M")
                except ValueError:
                    raise ValueError("折扣截止时间需为 YYYY-MM-DD HH:MM 格式")
                value = raw_value
            else:
                value = sanitize_text(raw_value)
            await self._plugin.dao.update_item_field(item_id, field, value)
            yield event.plain_result(f"已修改物品 {item_id} 的 {field}")
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"参数错误: {e}")
        except Exception as e:
            logger.error(f"Modify item error: {e}")
            yield event.plain_result("操作失败")

    async def set_daily_kw(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        try:
            msg = event.get_message_str()
            parts = msg.split(maxsplit=2)
            if len(parts) < 3:
                yield event.plain_result("用法: /设置今日口令 <关键词> <积分>")
                return
            keyword = sanitize_text(parts[1])
            if not keyword:
                yield event.plain_result("关键词不能为空")
                return
            # 保留字校验（v0.2.1）：口令关键词不得与签到/抽奖/排行触发词冲突，
            # 含「口令±触发词」组合形态（否则该形态消息会被触发词拦截，口令永远领不到）
            blocked = self._reserved_keyword_reason(keyword)
            if blocked:
                yield event.plain_result(blocked)
                return
            points = parse_int(parts[2], min_val=1)
            group_id = event.get_group_id()
            admin_qq = event.get_sender_id()
            await self._plugin.dao.set_daily_keyword(
                group_id, keyword, points, admin_qq
            )
            self._plugin.daily_keyword_service.invalidate(group_id)
            yield event.plain_result(f'已设置今日口令: "{keyword}" 奖励 {points} 积分')
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"参数错误: {e}")
        except Exception as e:
            logger.error(f"Set daily keyword error: {e}")
            yield event.plain_result("操作失败")

    def _reserved_keyword_reason(self, keyword: str) -> str | None:
        """判断口令关键词是否与触发词冲突（保留字），冲突返回提示文案。

        触发词 = keyword_sign ∪ keyword_lottery ∪ 排行关键词。
        冲突形态（压缩空白、大小写不敏感比较）：
          1. 关键词本身就是触发词
          2. 口令 + 触发词（如 "whl抽奖"）
          3. 触发词 + 口令（如 "抽奖whl"）
        """
        cfg = self._plugin.config_cache
        reserved = set(cfg.get("keyword_sign", []))
        reserved |= set(cfg.get("keyword_lottery", []))
        reserved |= {"排行", "排名", "积分榜"}
        passphrase = str(cfg.get("lottery_passphrase", "") or "")

        norm = lambda s: "".join(s.split()).lower()  # noqa: E731
        norm_k = norm(keyword)
        if any(norm_k == norm(t) for t in reserved):
            return "口令不能与签到/抽奖/排行触发词相同"
        if passphrase:
            p_n = norm(passphrase)
            for t in reserved:
                t_n = norm(t)
                if norm_k == p_n + t_n or norm_k == t_n + p_n:
                    return "口令不能与触发词构成「口令+触发词」组合（会被触发判定拦截）"
        return None

    async def clear_daily_kw(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        try:
            group_id = event.get_group_id()
            await self._plugin.dao.clear_daily_keyword(group_id)
            self._plugin.daily_keyword_service.invalidate(group_id)
            yield event.plain_result("已清除今日口令")
        except Exception as e:
            logger.error(f"Clear daily keyword error: {e}")
            yield event.plain_result("操作失败")

    async def set_config(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        try:
            msg = event.get_message_str()
            parts = msg.split(maxsplit=2)
            if len(parts) < 3:
                yield event.plain_result("用法: /设置 <配置项> <值>")
                return
            key = parts[1]
            value = parts[2]
            from ..config.defaults import validate_and_cast

            parsed = validate_and_cast(key, value)
            new_cache = dict(self._plugin.config_cache)
            new_cache[key] = parsed
            if key == "signin_random_min" and parsed > new_cache["signin_random_max"]:
                yield event.plain_result("signin_random_min 不能大于 signin_random_max")
                return
            if key == "signin_random_max" and parsed < new_cache["signin_random_min"]:
                yield event.plain_result("signin_random_max 不能小于 signin_random_min")
                return
            if key in ("active_reward_points_min", "active_reward_points_max"):
                other = (
                    "active_reward_points_max"
                    if key == "active_reward_points_min"
                    else "active_reward_points_min"
                )
                if key == "active_reward_points_min" and parsed > new_cache[other]:
                    yield event.plain_result(
                        "active_reward_points_min 不能大于 active_reward_points_max"
                    )
                    return
                if key == "active_reward_points_max" and parsed < new_cache[other]:
                    yield event.plain_result(
                        "active_reward_points_max 不能小于 active_reward_points_min"
                    )
                    return
            if key in ("keyword_sign", "keyword_lottery", "backup_dirs"):
                stored = json.dumps(parsed, ensure_ascii=False)
            else:
                stored = str(parsed)
            self._plugin.config_cache[key] = parsed
            if self._plugin.config is not None:
                self._plugin.config[key] = parsed
                self._plugin.config.save_config()
            else:
                await self._plugin.dao.set_config(key, stored)
            if key == "signin_refresh_time":
                from ..utils.helpers import set_day_boundary

                set_day_boundary(parsed)
            if key in ("backup_time", "birthday_announce_time", "backup_enabled"):
                await self._plugin.reschedule_cron_jobs()
            yield event.plain_result(f"已更新配置 {key} = {parsed}")
        except ValueError as e:
            yield event.plain_result(f"参数错误: {e}")
        except Exception as e:
            logger.error(f"Set config error: {e}")
            yield event.plain_result("操作失败")

    async def view_config(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        try:
            from ..config.defaults import DEFAULT_CONFIG

            lines = ["⚙ 当前配置"]
            for key, default in DEFAULT_CONFIG.items():
                val = self._plugin.config_cache.get(key, default)
                if isinstance(val, list):
                    display = json.dumps(val, ensure_ascii=False)
                elif isinstance(val, bool):
                    display = str(val).lower()
                else:
                    display = str(val)
                lines.append(f"{key} = {display}")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"View config error: {e}")
            yield event.plain_result("查询失败")

    async def set_discount(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        try:
            msg = event.get_message_str()
            parts = msg.split(maxsplit=3)
            if len(parts) < 4:
                yield event.plain_result("用法: /设置折扣 <物品ID> <折扣价> <截止时间>")
                return
            item_id = parse_int(parts[1], min_val=1)
            price = parse_int(parts[2], min_val=1)
            end_time = parts[3]
            result = await self._plugin.redeem_service.set_discount(
                item_id, price, end_time
            )
            yield event.plain_result(result["msg"])
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"参数错误: {e}")
        except Exception as e:
            logger.error(f"Set discount error: {e}")
            yield event.plain_result("操作失败")

    async def clear_discount(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        try:
            msg = event.get_message_str()
            parts = msg.split()
            if len(parts) < 2:
                yield event.plain_result("用法: /清除折扣 <物品ID>")
                return
            item_id = parse_int(parts[1], min_val=1)
            result = await self._plugin.redeem_service.clear_discount(item_id)
            yield event.plain_result(result["msg"])
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"参数错误: {e}")
        except Exception as e:
            logger.error(f"Clear discount error: {e}")
            yield event.plain_result("操作失败")

    async def add_admin(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_super_admin(event):
            async for r in self._deny(event):
                yield r
            return
        try:
            parts = event.get_message_str().split()
            if len(parts) < 2:
                yield event.plain_result("用法: /添加管理 <QQ号>")
                return
            target = parse_qq_arg(parts[1])
            if target is None:
                target = parse_qq(parts[1])
            group_id = event.get_group_id()
            if not group_id:
                yield event.plain_result("仅支持在群聊中管理")
                return
            await self._plugin.dao.add_admin(target, event.get_sender_id(), group_id)
            yield event.plain_result(f"已将 {target} 添加为本群积分系统管理员")
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"参数错误: {e}")
        except Exception as e:
            logger.error(f"Add admin error: {e}")
            yield event.plain_result("操作失败")

    async def remove_admin(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_super_admin(event):
            async for r in self._deny(event):
                yield r
            return
        try:
            parts = event.get_message_str().split()
            if len(parts) < 2:
                yield event.plain_result("用法: /删除管理 <QQ号>")
                return
            target = parse_qq_arg(parts[1])
            if target is None:
                target = parse_qq(parts[1])
            group_id = event.get_group_id()
            await self._plugin.dao.remove_admin(target, group_id)
            yield event.plain_result(f"已删除 {target} 的管理员权限")
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"参数错误: {e}")
        except Exception as e:
            logger.error(f"Remove admin error: {e}")
            yield event.plain_result("操作失败")

    async def add_date_reward(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        try:
            parts = event.get_message_str().split(maxsplit=4)
            if len(parts) < 4:
                yield event.plain_result(
                    "用法: /添加日期奖励 <MM-DD|MM-DD~MM-DD> <关键词> <积分> [概率]"
                )
                return
            from ..utils.security import parse_birthday, sanitize_text

            date_range = parts[1].strip()
            if "~" in date_range:
                start_s, end_s = date_range.split("~", 1)
                start_date = parse_birthday(start_s)
                end_date = parse_birthday(end_s)
            else:
                start_date = parse_birthday(date_range)
                end_date = None
            keyword = sanitize_text(parts[2])
            if not keyword:
                yield event.plain_result("关键词不能为空")
                return
            points = parse_int(parts[3], min_val=1)
            probability = 1.0
            if len(parts) >= 5:
                try:
                    probability = float(parts[4])
                except ValueError:
                    raise ValueError("概率必须为数字")
                if not (0 < probability <= 1):
                    raise ValueError("概率必须在 (0, 1] 之间")
            reward_id = await self._plugin.dao.add_date_reward(
                start_date, end_date, keyword, points, probability
            )
            range_text = start_date + (f"~{end_date}" if end_date else "")
            yield event.plain_result(
                f'已添加日期奖励 #{reward_id}: {range_text} 关键词"{keyword}" +{points}积分 概率{probability}'
            )
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"参数错误: {e}")
        except Exception as e:
            logger.error(f"Add date reward error: {e}")
            yield event.plain_result("操作失败")

    async def delete_date_reward(
        self, event
    ) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        try:
            parts = event.get_message_str().split()
            if len(parts) < 2:
                yield event.plain_result("用法: /删除日期奖励 <ID>")
                return
            reward_id = parse_int(parts[1], min_val=1)
            await self._plugin.dao.soft_delete_date_reward(reward_id)
            yield event.plain_result(f"已删除日期奖励 #{reward_id}")
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"参数错误: {e}")
        except Exception as e:
            logger.error(f"Delete date reward error: {e}")
            yield event.plain_result("操作失败")

    async def view_date_rewards(
        self, event
    ) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        try:
            rows = await self._plugin.dao.get_all_date_rewards()
            if not rows:
                yield event.plain_result("暂无日期奖励")
                return
            lines = ["🎯 日期奖励列表"]
            for r in rows:
                status = "✅" if r["is_active"] else "❌"
                range_text = r["start_date"] + (
                    f"~{r['end_date']}" if r["end_date"] else ""
                )
                lines.append(
                    f'{status} #{r["id"]} {range_text} 关键词"{r["keyword"]}" +{r["points"]}积分 概率{r["probability"]}'
                )
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"View date rewards error: {e}")
            yield event.plain_result("查询失败")

    # ─── 数据清空（验证码二次确认） ─────────────────────────

    async def clear_data(
        self, event, scope: str
    ) -> AsyncGenerator[MessageEventResult, None]:
        """发起清空操作：生成验证码令牌，等待 /确认清空 完成。"""
        if scope == "group":
            if not await self._require_admin(event):
                async for r in self._deny(event):
                    yield r
                return
            group_id = event.get_group_id()
            if not group_id:
                yield event.plain_result("仅支持在群聊中清空本群数据")
                return
            scope_desc = "本群成员的积分余额与流水记录（成员关系保留）"
        else:
            if not event.is_admin():
                yield event.plain_result("仅 AstrBot 全局管理员可执行全局清空")
                return
            group_id = None
            scope_desc = "全部数据（含商店物品、日期奖励、管理名单等所有数据）"

        token = str(random.randint(100000, 999999))
        self._prune_pending_clears()
        self._pending_clears[event.get_sender_id()] = {
            "token": token,
            "scope": scope,
            "group_id": group_id,
            "expires_at": time.time() + _CLEAR_TOKEN_TTL,
        }
        yield event.plain_result(
            f"⚠️ 确认清空{scope_desc}？此操作不可恢复！\n"
            f"清空前将自动备份数据库。\n"
            f"请回复 /确认清空 {token} 完成确认（5 分钟内有效）。"
        )

    async def confirm_clear(self, event) -> AsyncGenerator[MessageEventResult, None]:
        """校验验证码并执行清空：先备份，再恢复负分头衔名片，最后事务内删除。"""
        try:
            parts = event.get_message_str().split()
            if len(parts) < 2:
                yield event.plain_result("用法: /确认清空 <验证码>")
                return

            qq = event.get_sender_id()
            self._prune_pending_clears(keep=qq)
            pending = self._pending_clears.pop(qq, None)
            if not pending:
                yield event.plain_result(
                    "没有待确认的清空操作，请先发起 /清空数据 或 /清空全部数据"
                )
                return
            if time.time() > pending["expires_at"]:
                yield event.plain_result("验证码已过期，请重新发起清空操作")
                return
            if pending["token"] != parts[1].strip():
                yield event.plain_result("验证码错误，已取消本次清空")
                return

            scope = pending["scope"]
            group_id = pending["group_id"]
            bot = getattr(event, "bot", None)

            # 1. 清空前自动备份（不依赖 backup_dirs 配置）
            from pathlib import Path

            backup_dir = Path(self._plugin.db.db_path).parent / "backup_before_clear"
            try:
                backup_path = await self._plugin.backup_service.backup_unique(
                    backup_dir, "before_clear"
                )
            except Exception as e:
                logger.error(f"Backup before clear failed: {e}")
                backup_path = None

            # 2. 恢复负分头衔原名片（原名片存于待删行内，须先恢复）
            restored = await self._restore_negative_cards(bot, scope, group_id)

            # 3. 事务内删除
            counts = await self._do_clear(scope, group_id)

            lines = [f"✅ 已清空{'本群' if scope == 'group' else '全部'}数据："]
            if scope == "group":
                lines.append(
                    f"  · 清零本群成员共享积分 {counts.get('accounts_reset', 0)} 人"
                )
                lines.append(
                    f"  · 流水 {counts.get('point_transactions', 0)} 条，签到 {counts.get('sign_in_log', 0)}，抽奖 {counts.get('lottery_record', 0)}，兑换 {counts.get('redeem_records', 0)}"
                )
            else:
                lines.append(
                    f"  · 用户 {counts.get('users', 0)}，账户 {counts.get('accounts', 0)}，流水 {counts.get('point_transactions', 0)} 条"
                )
                lines.append(
                    f"  · 签到 {counts.get('sign_in_log', 0)}，抽奖 {counts.get('lottery_record', 0)}，兑换 {counts.get('redeem_records', 0)}"
                )
                lines.append(
                    f"  · 物品 {counts.get('redeem_items', 0)}，日期奖励 {counts.get('date_rewards', 0)}，管理员 {counts.get('admins', 0)}，口令 {counts.get('daily_keyword', 0)}"
                )
            lines.append(f"  · 恢复群名片 {restored} 人（尽力而为）")
            lines.append(
                f"清空前备份: {backup_path}"
                if backup_path
                else "⚠️ 清空前备份失败，请手动确认数据安全"
            )
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"Confirm clear error: {e}")
            yield event.plain_result("操作失败，已记录错误")

    async def _do_clear(self, scope: str, group_id: str | None) -> dict:
        counts: dict[str, int] = {}

        async def _tx(conn):
            if scope == "group":
                # 1. 清零本群成员的共享积分与签到状态（积分全局共享，跨群余额同步归零）
                async with conn.execute(
                    "UPDATE accounts SET points=0, total_earned=0, last_sign_date=NULL, "
                    "consecutive_days=0, total_sign_days=0, updated_at=datetime('now','localtime') "
                    "WHERE qq IN (SELECT qq FROM users WHERE group_id=?)",
                    (group_id,),
                ) as cur:
                    counts["accounts_reset"] = cur.rowcount
                # 2. 群级记录
                for table in _GROUP_CLEAR_TABLES:
                    async with conn.execute(
                        f"DELETE FROM {table} WHERE group_id=?", (group_id,)
                    ) as cur:
                        counts[table] = cur.rowcount
                # 3. 成员关系保留，负分头衔显式收尾（余额已归零）
                async with conn.execute(
                    "UPDATE users SET negative_title_id=NULL, negative_title_prev_card=NULL WHERE group_id=?",
                    (group_id,),
                ) as cur:
                    counts["title_cleared"] = cur.rowcount
            else:
                for table in _GLOBAL_CLEAR_TABLES:
                    async with conn.execute(f"DELETE FROM {table}") as cur:
                        counts[table] = cur.rowcount

        await self._plugin.db.execute_transaction(_tx)

        if scope == "global":
            # 重播种彩蛋事件也走持锁事务，避免与其他写操作并发竞争
            from ..db.schema import _seed_default_easter_events

            async def _reseed(conn):
                await _seed_default_easter_events(conn)

            await self._plugin.db.execute_transaction(_reseed)
        return counts

    async def _restore_negative_cards(
        self, bot, scope: str, group_id: str | None
    ) -> int:
        if scope == "group":
            rows = await self._plugin.db.fetchall(
                "SELECT qq, group_id, negative_title_prev_card FROM users "
                "WHERE negative_title_id IS NOT NULL AND group_id=?",
                (group_id,),
            )
        else:
            rows = await self._plugin.db.fetchall(
                "SELECT qq, group_id, negative_title_prev_card FROM users "
                "WHERE negative_title_id IS NOT NULL"
            )
        restored = 0
        for r in rows:
            try:
                await self._plugin.point_service._set_group_card(
                    bot, r["qq"], r["group_id"], r["negative_title_prev_card"] or ""
                )
                restored += 1
            except Exception as e:
                logger.warning(f"Restore group card failed for {r['qq']}: {e}")
        return restored
