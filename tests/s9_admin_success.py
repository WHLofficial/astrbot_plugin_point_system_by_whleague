"""S9 管理指令成功路径：加减分、商品增删改、口令、配置（含交叉校验/WebUI 落库）、折扣、管理员、日期奖励、全局清空全流程。"""

import os
import time
import types
from pathlib import Path

from .common import (
    FakeBot,
    FakeEvent,
    TempDB,
    base_cfg,
    collect,
    restore_day_boundary,
    snapshot_day_boundary,
)


class _FakeConfig(dict):
    """行为与 AstrBot 托管配置一致的假配置对象。"""

    first_deploy = False

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.save_called = 0

    def save_config(self):
        self.save_called += 1


async def _admin_plugin(t, config=None):
    from astrbot_plugin_point_system_by_whleague.handlers.admin import AdminHandler
    from astrbot_plugin_point_system_by_whleague.services.backup_service import (
        BackupService,
    )
    from astrbot_plugin_point_system_by_whleague.services.point_service import (
        PointService,
    )
    from astrbot_plugin_point_system_by_whleague.services.redeem_service import (
        RedeemService,
    )

    ps = PointService(t.db, t.dao)
    invalidated = []
    plugin = types.SimpleNamespace(
        db=t.db,
        dao=t.dao,
        config_cache=base_cfg(),
        config=config,
        point_service=ps,
        redeem_service=RedeemService(t.db, t.dao, ps),
        backup_service=BackupService(t.db, {"backup_dirs": []}),
        daily_keyword_service=types.SimpleNamespace(
            invalidate=lambda g: invalidated.append(g)
        ),
    )
    return AdminHandler(plugin), invalidated


async def test_admin_adjust_points():
    async with TempDB() as t:
        handler, _ = await _admin_plugin(t)
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/加分 @10001 10")
        msgs = await collect(handler.adjust_points(ev, "加分"))
        assert any("加 10" in m for m in msgs), msgs
        row = await t.dao.get_account("10001")
        assert row["points"] == 10
        txn = await t.db.fetchone(
            "SELECT reason, admin_qq FROM point_transactions WHERE qq='10001'"
        )
        assert txn["reason"] == "admin_add" and txn["admin_qq"] == "admin"
        # 纯数字 Q 号
        ev2 = FakeEvent("admin", "G1", is_admin=True, msg="/加分 20002 5")
        await collect(handler.adjust_points(ev2, "加分"))
        assert (await t.dao.get_account("20002"))["points"] == 5
        # 扣分
        ev3 = FakeEvent("admin", "G1", is_admin=True, msg="/扣分 @10001 3")
        msgs = await collect(handler.adjust_points(ev3, "扣分"))
        assert any("扣 3" in m for m in msgs)
        assert (await t.dao.get_account("10001"))["points"] == 7
        # 扣分可扣成负数（管理员惩罚场景）+ 自动补负分头衔
        ev4 = FakeEvent("admin", "G1", is_admin=True, msg="/扣分 @10001 999")
        msgs = await collect(handler.adjust_points(ev4, "扣分"))
        assert any("扣 999" in m for m in msgs), msgs
        row = await t.dao.get_account("10001")
        assert row["points"] == 7 - 999, row["points"]
        assert (await t.dao.get_user("10001", "G1"))["negative_title_id"] == 1
        # 参数错误
        ev5 = FakeEvent("admin", "G1", is_admin=True, msg="/加分 @10001")
        msgs = await collect(handler.adjust_points(ev5, "加分"))
        assert any("用法" in m for m in msgs)
    return "管理：加分/扣分/@与数字解析/扣成负数+头衔/用法提示"


async def test_admin_item_crud():
    async with TempDB() as t:
        handler, _ = await _admin_plugin(t)
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/添加兑换 商品A 100")
        await collect(handler.add_item(ev))
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/添加兑换 商品B 50 2")
        await collect(handler.add_item(ev))
        items = await t.dao.get_active_items()
        assert len(items) == 2
        assert items[0]["stock"] == -1 and items[1]["stock"] == 2
        # 非法参数
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/添加兑换 商品C")
        msgs = await collect(handler.add_item(ev))
        assert any("用法" in m for m in msgs)
        # 删除（软删）
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/删除兑换 1")
        await collect(handler.delete_item(ev))
        items = await t.dao.get_active_items()
        assert [i["name"] for i in items] == ["商品B"]
        # 修改各字段
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/修改兑换 2 cost 30")
        await collect(handler.modify_item(ev))
        assert (await t.dao.get_item(2))["cost"] == 30
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/修改兑换 2 stock 0")
        await collect(handler.modify_item(ev))
        assert (await t.dao.get_item(2))["stock"] == 0
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/修改兑换 2 name 新名字")
        await collect(handler.modify_item(ev))
        assert (await t.dao.get_item(2))["name"] == "新名字"
        # 折扣价 >= 原价被拒
        ev = FakeEvent(
            "admin", "G1", is_admin=True, msg="/修改兑换 2 discount_price 30"
        )
        msgs = await collect(handler.modify_item(ev))
        assert any("折扣价应低于原价" in m for m in msgs)
        ev = FakeEvent(
            "admin", "G1", is_admin=True, msg="/修改兑换 2 discount_price 20"
        )
        await collect(handler.modify_item(ev))
        assert (await t.dao.get_item(2))["discount_price"] == 20
        # 截止时间格式校验
        ev = FakeEvent(
            "admin",
            "G1",
            is_admin=True,
            msg="/修改兑换 2 discount_end_time 2026-12-31 23:59",
        )
        msgs = await collect(handler.modify_item(ev))
        assert not any("参数错误" in m for m in msgs), msgs
        ev = FakeEvent(
            "admin", "G1", is_admin=True, msg="/修改兑换 2 discount_end_time bad"
        )
        msgs = await collect(handler.modify_item(ev))
        assert any("参数错误" in m for m in msgs)
        # 非法字段
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/修改兑换 2 points 5")
        msgs = await collect(handler.modify_item(ev))
        assert any("参数错误" in m for m in msgs)
    return "管理：商品添加/删除/修改全字段与校验"


async def test_admin_daily_kw_reserved():
    """口令保留字校验：触发词 / 口令±触发词 组合形态全部拒绝，正常口令通过。"""
    async with TempDB() as t:
        handler, _ = await _admin_plugin(t)
        blocked = (
            "签到", "打卡", "抽奖", "lottery", "排行", "排名", "积分榜",
            "whl抽奖", "抽奖whl",
        )
        for kw in blocked:
            ev = FakeEvent("admin", "G1", is_admin=True, msg=f"/设置今日口令 {kw} 10")
            msgs = await collect(handler.set_daily_kw(ev))
            assert any("口令" in m and "触发词" in m for m in msgs), (kw, msgs)
            assert await t.count("daily_keyword") == 0, kw
        # 正常口令通过（不构成任何冲突形态，附加文本不属于组合形态）
        for kw in ("红包", "口令", "whlx", "抽奖券", "whl抽奖吧"):
            ev = FakeEvent("admin", "G1", is_admin=True, msg=f"/设置今日口令 {kw} 10")
            msgs = await collect(handler.set_daily_kw(ev))
            assert any("已设置今日口令" in m for m in msgs), (kw, msgs)
            await t.dao.clear_daily_keyword("G1")
    return "口令保留字：触发词/口令±触发词三形态拒绝、正常口令通过"


async def test_admin_daily_keyword():
    async with TempDB() as t:
        handler, invalidated = await _admin_plugin(t)
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/设置今日口令 红包 10")
        msgs = await collect(handler.set_daily_kw(ev))
        assert any("红包" in m and "10" in m for m in msgs)
        kw = await t.dao.get_daily_keyword("G1", handler._plugin.dao._today_str())
        assert kw["keyword"] == "红包" and kw["points"] == 10
        assert invalidated == ["G1"]
        # 二次设置（upsert）
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/设置今日口令 新口令 20")
        await collect(handler.set_daily_kw(ev))
        kw = await t.dao.get_daily_keyword("G1", handler._plugin.dao._today_str())
        assert kw["keyword"] == "新口令" and kw["points"] == 20
        # 非法参数（无关键词）
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/设置今日口令")
        msgs = await collect(handler.set_daily_kw(ev))
        assert any("用法" in m for m in msgs)
        # 清除
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/清除今日口令")
        await collect(handler.clear_daily_kw(ev))
        assert (
            await t.dao.get_daily_keyword("G1", handler._plugin.dao._today_str())
            is None
        )
        assert len(invalidated) == 3  # 设置×2 + 清除×1
    return "管理：口令设置/upsert/空关键词/清除+缓存失效"


async def test_admin_set_config():
    async with TempDB() as t:
        handler, _ = await _admin_plugin(t)
        # bool / int / 列表键
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/设置 signin_fixed_mode true")
        await collect(handler.set_config(ev))
        assert handler._plugin.config_cache["signin_fixed_mode"] is True
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/设置 signin_fixed_points 15")
        await collect(handler.set_config(ev))
        assert handler._plugin.config_cache["signin_fixed_points"] == 15
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/设置 keyword_sign 签到,sign")
        await collect(handler.set_config(ev))
        assert handler._plugin.config_cache["keyword_sign"] == ["签到", "sign"]
        # 落库（config 为 None 时写 plugin_config）
        row = await t.db.fetchone(
            "SELECT value FROM plugin_config WHERE key='signin_fixed_points'"
        )
        assert row and row["value"] == "15"
        row = await t.db.fetchone(
            "SELECT value FROM plugin_config WHERE key='keyword_sign'"
        )
        assert row and row["value"].startswith("[")
        # 交叉校验：min 不能大于 max
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/设置 signin_random_min 30")
        msgs = await collect(handler.set_config(ev))
        assert any("不能大于" in m for m in msgs)
        assert handler._plugin.config_cache["signin_random_min"] == 1  # 未生效
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/设置 signin_random_max 40")
        await collect(handler.set_config(ev))
        assert handler._plugin.config_cache["signin_random_max"] == 40
        # 非法值
        ev = FakeEvent(
            "admin", "G1", is_admin=True, msg="/设置 signin_fixed_mode maybe"
        )
        msgs = await collect(handler.set_config(ev))
        assert any("参数错误" in m for m in msgs)
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/设置 nope 1")
        msgs = await collect(handler.set_config(ev))
        assert any("参数错误" in m for m in msgs)
        # 概率边界
        ev = FakeEvent(
            "admin", "G1", is_admin=True, msg="/设置 active_reward_probability 1.5"
        )
        msgs = await collect(handler.set_config(ev))
        assert any("参数错误" in m for m in msgs)
    return "管理：/设置 各类型/交叉校验/落库/非法值"


async def test_admin_set_config_webui_path():
    async with TempDB() as t:
        cfg = _FakeConfig(base_cfg())
        handler, _ = await _admin_plugin(t, config=cfg)
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/设置 signin_fixed_points 15")
        await collect(handler.set_config(ev))
        assert cfg["signin_fixed_points"] == 15
        assert cfg.save_called == 1
        # config 非 None 时不再写 plugin_config
        row = await t.db.fetchone(
            "SELECT value FROM plugin_config WHERE key='signin_fixed_points'"
        )
        assert row is None
    return "管理：/设置 WebUI 托管配置路径 save_config"


async def test_admin_view_config():
    async with TempDB() as t:
        handler, _ = await _admin_plugin(t)
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/查看配置")
        msgs = await collect(handler.view_config(ev))
        assert len(msgs) == 1
        text = msgs[0]
        assert "signin_fixed_points = 10" in text
        assert "signin_fixed_mode = false" in text
    return "管理：查看配置输出全键"


async def test_admin_set_config_hot_reload():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.utils import helpers as _helpers

        boundary = snapshot_day_boundary()
        try:
            handler, _ = await _admin_plugin(t)
            rescheduled = []

            async def _reschedule():
                rescheduled.append(1)

            handler._plugin.reschedule_cron_jobs = _reschedule
            # signin_refresh_time：立即更新业务日分界（热生效）
            ev = FakeEvent(
                "admin", "G1", is_admin=True, msg="/设置 signin_refresh_time 06:30"
            )
            await collect(handler.set_config(ev))
            assert handler._plugin.config_cache["signin_refresh_time"] == "06:30"
            assert _helpers.get_day_boundary() == (6, 30)
            # backup_time：触发 cron 重建
            ev = FakeEvent("admin", "G1", is_admin=True, msg="/设置 backup_time 05:15")
            await collect(handler.set_config(ev))
            assert rescheduled == [1]
            assert handler._plugin.config_cache["backup_time"] == "05:15"
            # birthday_announce_time / backup_enabled 同样触发重建
            ev = FakeEvent(
                "admin", "G1", is_admin=True, msg="/设置 birthday_announce_time 09:00"
            )
            await collect(handler.set_config(ev))
            assert rescheduled == [1, 1]
        finally:
            restore_day_boundary(boundary)
    return "管理：/设置 时间类配置热生效（日界更新 + cron 重建）"


async def test_pending_clear_prune():
    async with TempDB() as t:
        handler, _ = await _admin_plugin(t)
        ev = FakeEvent("admin", "G1", is_admin=True)
        await collect(handler.clear_data(ev, "group"))
        assert "admin" in handler._pending_clears
        # 过期条目在下次发起清空时被清理（仅保留新条目）
        handler._pending_clears["admin"]["expires_at"] = time.time() - 1
        await collect(handler.clear_data(ev, "group"))
        assert set(handler._pending_clears) == {"admin"}
        assert handler._pending_clears["admin"]["expires_at"] > time.time()
        # confirm_clear 只清理他人过期条目；本人条目无论是否过期都会被 pop 消耗（单次有效）
        await collect(handler.clear_data(FakeEvent("admin2", "G1", is_admin=True), "group"))
        handler._pending_clears["admin"]["expires_at"] = 0
        handler._pending_clears["admin2"]["expires_at"] = 0
        token2 = handler._pending_clears["admin2"]["token"]
        e = FakeEvent("admin2", "G1", is_admin=True, msg=f"/确认清空 {token2}")
        msgs = await collect(handler.confirm_clear(e))
        assert any("过期" in m for m in msgs), msgs
        assert "admin" not in handler._pending_clears
        assert "admin2" not in handler._pending_clears
    return "管理：清空令牌过期清理（发起时 prune / 确认时保留本人）"


async def test_admin_discount_handlers():
    async with TempDB() as t:
        handler, _ = await _admin_plugin(t)
        await t.dao.add_item("商品", 100, 5)
        ev = FakeEvent(
            "admin", "G1", is_admin=True, msg="/设置折扣 1 50 2026-12-31 23:59"
        )
        msgs = await collect(handler.set_discount(ev))
        assert any("已设置" in m for m in msgs)
        item = await t.dao.get_item(1)
        assert item["discount_price"] == 50
        # 时间格式错误
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/设置折扣 1 50 bad")
        msgs = await collect(handler.set_discount(ev))
        assert any("截止时间" in m for m in msgs)
        # 折扣 >= 原价
        ev = FakeEvent(
            "admin", "G1", is_admin=True, msg="/设置折扣 1 100 2026-12-31 23:59"
        )
        msgs = await collect(handler.set_discount(ev))
        assert any("低于原价" in m for m in msgs)
        # 清除折扣
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/清除折扣 1")
        msgs = await collect(handler.clear_discount(ev))
        assert any("已清除" in m for m in msgs)
        item = await t.dao.get_item(1)
        assert item["discount_price"] is None
    return "管理：折扣设置/格式校验/清除"


async def test_admin_add_remove_admin():
    async with TempDB() as t:
        handler, _ = await _admin_plugin(t)
        # 群主可提权
        ev = FakeEvent(
            "owner", "G1", is_admin=False, group_owner="owner", msg="/添加管理 12345"
        )
        msgs = await collect(handler.add_admin(ev))
        assert not any("没有权限" in m for m in msgs), msgs
        assert await t.dao.is_admin("12345", "G1")
        # 普通成员被拒
        ev = FakeEvent("member", "G1", is_admin=False, msg="/添加管理 99999")
        msgs = await collect(handler.add_admin(ev))
        assert any("没有权限" in m for m in msgs)
        # 无群 ID
        ev = FakeEvent(
            "owner", None, is_admin=False, group_owner="owner", msg="/添加管理 12345"
        )
        msgs = await collect(handler.add_admin(ev))
        assert any("群聊" in m for m in msgs)
        # 删除管理
        ev = FakeEvent(
            "owner", "G1", is_admin=False, group_owner="owner", msg="/删除管理 12345"
        )
        msgs = await collect(handler.remove_admin(ev))
        assert any("已删除" in m for m in msgs)
        assert not await t.dao.is_admin("12345", "G1")
    return "管理：添加/删除管理员、群主判定、成员拒绝"


async def test_admin_date_rewards():
    async with TempDB() as t:
        handler, _ = await _admin_plugin(t)
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/添加日期奖励 01-01 元旦 50")
        msgs = await collect(handler.add_date_reward(ev))
        assert any("元旦" in m for m in msgs)
        ev = FakeEvent(
            "admin", "G1", is_admin=True, msg="/添加日期奖励 12-30~01-02 跨年 30 0.5"
        )
        msgs = await collect(handler.add_date_reward(ev))
        assert any("跨年" in m for m in msgs)
        rows = await t.dao.get_all_date_rewards()
        assert len(rows) == 2
        assert rows[1]["start_date"] == "12-30" and rows[1]["end_date"] == "01-02"
        assert rows[1]["probability"] == 0.5
        # 非法概率 / 非法日期
        for bad in ("1.5", "0", "nan", "inf"):
            ev = FakeEvent(
                "admin", "G1", is_admin=True, msg=f"/添加日期奖励 01-01 元旦 50 {bad}"
            )
            msgs = await collect(handler.add_date_reward(ev))
            assert any("参数错误" in m for m in msgs), (bad, msgs)
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/添加日期奖励 13-01 元旦 50")
        msgs = await collect(handler.add_date_reward(ev))
        assert any("参数错误" in m for m in msgs)
        # 查看
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/查看日期奖励")
        msgs = await collect(handler.view_date_rewards(ev))
        assert any("01-01" in m for m in msgs)
        # 删除
        ev = FakeEvent("admin", "G1", is_admin=True, msg="/删除日期奖励 1")
        await collect(handler.delete_date_reward(ev))
        rows = await t.dao.get_all_date_rewards()
        assert rows[0]["is_active"] == 0
    return "管理：日期奖励增删查/区间/概率边界"


async def test_admin_global_clear_flow():
    async with TempDB() as t:
        handler, _ = await _admin_plugin(t)
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('1001',-5)"
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('1001','1')"
        )
        await t.db.execute(
            "UPDATE users SET negative_title_id=1, negative_title_prev_card='原名片' WHERE qq='1001'"
        )
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('1002',10)"
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('1002','1')"
        )
        await t.dao.add_item("商品", 10, 1)
        await t.dao.add_date_reward("01-01", None, "元旦", 5, 1.0)
        await t.dao.add_admin("gadmin", "owner", "1")

        bot = FakeBot(member_card="现名片")
        ev = FakeEvent("root", "1", is_admin=True, msg="/清空全部数据", bot=bot)
        await collect(handler.clear_data(ev, "global"))
        token = handler._pending_clears["root"]["token"]
        ev2 = FakeEvent("root", "1", is_admin=True, msg=f"/确认清空 {token}", bot=bot)
        msgs = await collect(handler.confirm_clear(ev2))
        assert any("已清空全部数据" in m for m in msgs), msgs
        # 全部业务表清空
        assert await t.count("users") == 0
        assert await t.count("accounts") == 0
        assert await t.count("redeem_items") == 0
        assert await t.count("date_rewards") == 0
        assert await t.count("admins") == 0
        assert await t.count("point_transactions") == 0
        # 彩蛋事件重种为默认 2 条
        assert await t.count("easter_events") == 2
        # 清空前备份文件存在
        backup_dir = Path(t.db.db_path).parent / "backup_before_clear"
        assert backup_dir.is_dir()
        assert any(f.endswith(".db") for f in os.listdir(backup_dir))
        # 负分头衔原名片恢复调用
        restore_calls = [c for a, c in bot.calls if a == "set_group_card"]
        assert any(c["card"] == "原名片" for c in restore_calls), bot.calls
    return "管理：全局清空全流程（表清空/彩蛋重种/备份/名片恢复）"


TESTS = [
    ("admin_adjust_points", test_admin_adjust_points),
    ("admin_item_crud", test_admin_item_crud),
    ("admin_daily_keyword", test_admin_daily_keyword),
    ("admin_daily_kw_reserved", test_admin_daily_kw_reserved),
    ("admin_set_config", test_admin_set_config),
    ("admin_set_config_webui", test_admin_set_config_webui_path),
    ("admin_set_config_hot_reload", test_admin_set_config_hot_reload),
    ("pending_clear_prune", test_pending_clear_prune),
    ("admin_view_config", test_admin_view_config),
    ("admin_discount_handlers", test_admin_discount_handlers),
    ("admin_add_remove_admin", test_admin_add_remove_admin),
    ("admin_date_rewards", test_admin_date_rewards),
    ("admin_global_clear", test_admin_global_clear_flow),
]
