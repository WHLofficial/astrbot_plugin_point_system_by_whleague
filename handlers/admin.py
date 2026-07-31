import json
from collections.abc import AsyncGenerator
from astrbot.api import logger
from astrbot.api.event import MessageEventResult
from ..utils.security import parse_int, parse_qq, parse_qq_arg, sanitize_text


class AdminHandler:
    def __init__(self, plugin):
        self._plugin = plugin

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
        return group is not None and str(group.group_owner) == str(event.get_sender_id())

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

    async def adjust_points(self, event, action: str) -> AsyncGenerator[MessageEventResult, None]:
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
                    target, group_id, amount, reason, admin_override=True,
                    admin_qq=admin_qq, bot=getattr(event, "bot", None),
                )
                yield event.plain_result(f"已给 {target} 加 {amount} 积分，当前余额: {r['balance']}")
            else:
                try:
                    r = await self._plugin.point_service.subtract(
                        target, group_id, amount, reason,
                        admin_qq=admin_qq, bot=getattr(event, "bot", None),
                    )
                    yield event.plain_result(f"已给 {target} 扣 {amount} 积分，当前余额: {r['balance']}")
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
            item_id = await self._plugin.dao.add_item(name, cost, stock)
            yield event.plain_result(f"已添加兑换物品: {name} (消耗{cost}积分, 库存{stock})")
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
            points = parse_int(parts[2], min_val=1)
            group_id = event.get_group_id()
            admin_qq = event.get_sender_id()
            await self._plugin.dao.set_daily_keyword(group_id, keyword, points, admin_qq)
            self._plugin.daily_keyword_service.invalidate(group_id)
            yield event.plain_result(f"已设置今日口令: \"{keyword}\" 奖励 {points} 积分")
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"参数错误: {e}")
        except Exception as e:
            logger.error(f"Set daily keyword error: {e}")
            yield event.plain_result("操作失败")

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
            if key in ("keyword_sign", "keyword_lottery", "backup_dirs"):
                stored = json.dumps(parsed, ensure_ascii=False)
            else:
                stored = str(parsed)
            self._plugin.config_cache = new_cache
            if self._plugin.config is not None:
                self._plugin.config[key] = parsed
                self._plugin.config.save_config()
            else:
                await self._plugin.dao.set_config(key, stored)
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
            result = await self._plugin.redeem_service.set_discount(item_id, price, end_time)
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
                yield event.plain_result("用法: /添加日期奖励 <MM-DD|MM-DD~MM-DD> <关键词> <积分> [概率]")
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
                f"已添加日期奖励 #{reward_id}: {range_text} 关键词\"{keyword}\" +{points}积分 概率{probability}"
            )
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"参数错误: {e}")
        except Exception as e:
            logger.error(f"Add date reward error: {e}")
            yield event.plain_result("操作失败")

    async def delete_date_reward(self, event) -> AsyncGenerator[MessageEventResult, None]:
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

    async def view_date_rewards(self, event) -> AsyncGenerator[MessageEventResult, None]:
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
                range_text = r["start_date"] + (f"~{r['end_date']}" if r["end_date"] else "")
                lines.append(
                    f"{status} #{r['id']} {range_text} 关键词\"{r['keyword']}\" +{r['points']}积分 概率{r['probability']}"
                )
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"View date rewards error: {e}")
            yield event.plain_result("查询失败")
