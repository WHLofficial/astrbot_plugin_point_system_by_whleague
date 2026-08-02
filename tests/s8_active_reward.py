"""S8 活跃奖励开启态：命令/签到/抽奖消息跳过、长度过滤、用户/全局冷却、概率、负分跳过、入账与发送、口令双触发。"""

import types

from .common import FakeEvent, TempDB, base_cfg, patch_random


async def _handler_plugin(t, cfg):
    from astrbot_plugin_point_system_by_whleague.handlers.active_reward import (
        ActiveRewardHandler,
    )
    from astrbot_plugin_point_system_by_whleague.services.daily_keyword_service import (
        DailyKeywordService,
    )
    from astrbot_plugin_point_system_by_whleague.services.point_service import (
        PointService,
    )
    from astrbot_plugin_point_system_by_whleague.utils.rate_limiter import RateLimiter

    ps = PointService(t.db, t.dao)
    plugin = types.SimpleNamespace(
        config_cache=cfg,
        rate_limiter=RateLimiter(),
        point_service=ps,
        daily_keyword_service=DailyKeywordService(t.db, t.dao, ps),
    )
    return ActiveRewardHandler(plugin), ps


def _cfg(**over):
    cfg = base_cfg(
        active_reward_enabled=True,
        active_reward_min_length=3,
        active_reward_cooldown=60,
        active_reward_global_cooldown=10,
        active_reward_probability=1.0,
        active_reward_points_min=1,
        active_reward_points_max=5,
    )
    cfg.update(over)
    return cfg


async def test_ar_command_message_skipped():
    async with TempDB() as t:
        handler, ps = await _handler_plugin(t, _cfg())
        await t.dao.set_daily_keyword("G1", "红包", 10, "admin")
        ev = FakeEvent("u1", "G1", msg="抢到红包了", at_wake=True)
        await handler.handle(ev)
        row = await t.dao.get_user("u1", "G1")
        assert row is None  # 口令与活跃奖励都不触发
        assert ev.sent == []
    return "活跃奖励：@bot/唤醒命令消息整体跳过"


async def test_ar_signin_lottery_message_skipped():
    async with TempDB() as t:
        handler, ps = await _handler_plugin(t, _cfg())
        # v0.2.1 严格匹配：仅纯触发词形态被跳过，附加文本消息不再拦截
        ev = FakeEvent("u1", "G1", msg="签到")
        await handler.handle(ev)
        assert await t.dao.get_user("u1", "G1") is None
        ev2 = FakeEvent("u1", "G1", msg="whl抽奖")
        await handler.handle(ev2)
        assert await t.dao.get_user("u1", "G1") is None
    return "活跃奖励：签到/抽奖关键词消息跳过"


async def test_ar_min_length_skip():
    async with TempDB() as t:
        handler, ps = await _handler_plugin(t, _cfg())
        ev = FakeEvent("u1", "G1", msg="ab")
        await handler.handle(ev)
        assert await t.dao.get_user("u1", "G1") is None
        assert ev.sent == []
    return "活跃奖励：短于最短长度的消息跳过"


async def test_ar_user_cooldown():
    async with TempDB() as t:
        cfg = _cfg(active_reward_cooldown=60, active_reward_global_cooldown=0)
        handler, ps = await _handler_plugin(t, cfg)
        with patch_random(randint=3):
            await handler.handle(FakeEvent("u1", "G1", msg="第一条消息内容"))
        row = await t.dao.get_account("u1")
        assert row["points"] == 3
        # 冷却期内第二次：拦截
        with patch_random(randint=5):
            await handler.handle(FakeEvent("u1", "G1", msg="第二条消息内容"))
        row = await t.dao.get_account("u1")
        assert row["points"] == 3
    return "活跃奖励：用户冷却期内拦截"


async def test_ar_group_cooldown():
    async with TempDB() as t:
        cfg = _cfg(active_reward_cooldown=0, active_reward_global_cooldown=10)
        handler, ps = await _handler_plugin(t, cfg)
        with patch_random(randint=2):
            await handler.handle(FakeEvent("u1", "G1", msg="第一条消息内容"))
        # 全局冷却期内：其他用户也被拦截
        with patch_random(randint=2):
            await handler.handle(FakeEvent("u2", "G1", msg="第二条消息内容"))
        row = await t.dao.get_user("u2", "G1")
        assert row is None
    return "活跃奖励：全局冷却拦截跨用户"


async def test_ar_probability_miss_hit():
    async with TempDB() as t:
        cfg = _cfg(
            active_reward_cooldown=0,
            active_reward_global_cooldown=0,
            active_reward_probability=0.5,
        )
        handler, ps = await _handler_plugin(t, cfg)
        with patch_random(random=0.9, randint=4):
            await handler.handle(FakeEvent("u1", "G1", msg="概率未命中消息"))
        assert await t.dao.get_user("u1", "G1") is None
        with patch_random(random=0.1, randint=4):
            await handler.handle(FakeEvent("u2", "G1", msg="概率命中消息内容"))
        row = await t.dao.get_account("u2")
        assert row["points"] == 4
    return "活跃奖励：概率未命中不发、命中发放"


async def test_ar_negative_user_skipped():
    async with TempDB() as t:
        handler, ps = await _handler_plugin(t, _cfg(active_reward_global_cooldown=0))
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('neg',-5)"
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('neg','G1')"
        )
        ev = FakeEvent("neg", "G1", msg="负分用户消息内容")
        with patch_random(randint=4):
            await handler.handle(ev)
        row = await t.dao.get_account("neg")
        assert row["points"] == -5  # 未加分
        assert ev.sent == []
    return "活跃奖励：负分用户跳过"


async def test_ar_reward_added_and_sent():
    async with TempDB() as t:
        handler, ps = await _handler_plugin(t, _cfg())
        ev = FakeEvent("u1", "G1", msg="触发活跃奖励的消息内容")
        with patch_random(randint=4):
            await handler.handle(ev)
        row = await t.dao.get_account("u1")
        assert row["points"] == 4 and row["total_earned"] == 4
        txn = await t.db.fetchone(
            "SELECT reason, amount FROM point_transactions WHERE qq='u1'"
        )
        assert txn["reason"] == "active_reward" and txn["amount"] == 4
        assert len(ev.sent) == 1 and "活跃奖励" in str(ev.sent[0])
    return "活跃奖励：加分入账+流水+发送文案"


async def test_ar_daily_keyword_combined():
    async with TempDB() as t:
        handler, ps = await _handler_plugin(t, _cfg())
        await t.dao.set_daily_keyword("G1", "红包", 10, "admin")
        ev = FakeEvent("u1", "G1", msg="今天抢到红包了")
        with patch_random(randint=2):
            await handler.handle(ev)
        row = await t.dao.get_account("u1")
        assert row["points"] == 12  # 口令 10 + 活跃 2
        assert len(ev.sent) == 2
        texts = [str(c) for c in ev.sent]
        assert any("今日口令奖励" in x for x in texts)
        assert any("活跃奖励" in x for x in texts)
    return "活跃奖励：同消息同时触发口令与活跃奖励"


TESTS = [
    ("ar_command_skipped", test_ar_command_message_skipped),
    ("ar_signin_lottery_skipped", test_ar_signin_lottery_message_skipped),
    ("ar_min_length_skip", test_ar_min_length_skip),
    ("ar_user_cooldown", test_ar_user_cooldown),
    ("ar_group_cooldown", test_ar_group_cooldown),
    ("ar_probability", test_ar_probability_miss_hit),
    ("ar_negative_skipped", test_ar_negative_user_skipped),
    ("ar_reward_added", test_ar_reward_added_and_sent),
    ("ar_keyword_combined", test_ar_daily_keyword_combined),
]
