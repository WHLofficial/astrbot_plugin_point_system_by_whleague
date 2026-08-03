"""S6 彩蛋/日期奖励：概率选择、保底 pity、权重、签到集成（正负彩蛋、负余额→负分头衔、total_earned 排除）。"""

from .common import TempDB, base_cfg, patch_random


async def test_easter_no_events():
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")
        from astrbot_plugin_point_system_by_whleague.services.easter_service import (
            EasterService,
        )

        svc = EasterService(t.dao)
        r = await svc.trigger(
            "u1",
            "G1",
            0,
            0,
            lucky_probability=0.005,
            unlucky_probability=0.005,
            lucky_pity_count=200,
            unlucky_pity_count=200,
        )
        assert r["event"] is None
        assert r["lucky_pity"] == 1 and r["unlucky_pity"] == 1
    return "彩蛋：无事件时 event=None 且保底计数递增"


async def test_easter_probability_branches():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.easter_service import (
            EasterService,
        )

        svc = EasterService(t.dao)
        # 概率命中 lucky（lucky_probability=0.02，unlucky_probability=0.03）
        with patch_random(random=0.01):
            r = await svc.trigger(
                "u1",
                "G1",
                0,
                0,
                lucky_probability=0.02,
                unlucky_probability=0.03,
                lucky_pity_count=200,
                unlucky_pity_count=200,
            )
            assert r["event"]["event_type"] == "lucky"
            assert 50 <= r["event"]["points"] <= 200
            assert r["lucky_pity"] == 0 and r["unlucky_pity"] == 1
        # 概率命中 unlucky
        with patch_random(random=0.025):
            r = await svc.trigger(
                "u1",
                "G1",
                0,
                0,
                lucky_probability=0.02,
                unlucky_probability=0.03,
                lucky_pity_count=200,
                unlucky_pity_count=200,
            )
            assert r["event"]["event_type"] == "unlucky"
            assert -200 <= r["event"]["points"] <= -50
            assert r["lucky_pity"] == 1 and r["unlucky_pity"] == 0
        # 概率未命中：仅递增
        with patch_random(random=0.99):
            r = await svc.trigger(
                "u1",
                "G1",
                2,
                2,
                lucky_probability=0.02,
                unlucky_probability=0.03,
                lucky_pity_count=200,
                unlucky_pity_count=200,
            )
            assert r["event"] is None
            assert r["lucky_pity"] == 3 and r["unlucky_pity"] == 3
    return "彩蛋：概率命中 lucky/unlucky、未命中仅递增"


async def test_easter_pity_force():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.easter_service import (
            EasterService,
        )

        svc = EasterService(t.dao)
        # 保底触发 lucky：new_lucky_pity(2) >= lucky_pity_count(2)
        with patch_random(random=0.99):  # 概率必不中，验证保底强制
            r = await svc.trigger(
                "u1",
                "G1",
                1,
                0,
                lucky_probability=0.02,
                unlucky_probability=0.03,
                lucky_pity_count=2,
                unlucky_pity_count=15,
            )
            assert r["event"]["event_type"] == "lucky"
            assert r["lucky_pity"] == 0
        # 保底触发 unlucky
        with patch_random(random=0.99):
            r = await svc.trigger(
                "u1",
                "G1",
                0,
                2,
                lucky_probability=0.02,
                unlucky_probability=0.03,
                lucky_pity_count=10,
                unlucky_pity_count=3,
            )
            assert r["event"]["event_type"] == "unlucky"
            assert r["unlucky_pity"] == 0
        # 双保底同时满足：lucky 优先
        with patch_random(random=0.99):
            r = await svc.trigger(
                "u1",
                "G1",
                1,
                2,
                lucky_probability=0.02,
                unlucky_probability=0.03,
                lucky_pity_count=2,
                unlucky_pity_count=3,
            )
            assert r["event"]["event_type"] == "lucky"
    return "彩蛋：lucky/unlucky 保底强制触发与重置、双保底 lucky 优先"


async def test_easter_weighted_choice():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.easter_service import (
            EasterService,
        )

        await t.db.execute("UPDATE easter_events SET is_active=0")
        await t.db.execute(
            "INSERT INTO easter_events (event_type, name, description, probability, points_min, points_max, pity_count, is_active) "
            "VALUES ('lucky','小欧','',0.1,10,10,0,1),('lucky','大欧','',0.9,99,99,0,1)"
        )
        row = await t.db.fetchone("SELECT * FROM easter_events WHERE name='大欧'")
        svc = EasterService(t.dao)
        # choices 返回"大欧"事件：验证权重选择落到对应事件
        with patch_random(random=0.01, choices=dict(row)):
            r = await svc.trigger(
                "u1",
                "G1",
                0,
                0,
                lucky_probability=1.0,
                unlucky_probability=0.005,
                lucky_pity_count=200,
                unlucky_pity_count=200,
            )
            assert r["event"]["name"] == "大欧" and r["event"]["points"] == 99
    return "彩蛋：同类型多事件按权重选择"


async def test_easter_default_pity_no_force():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.easter_service import (
            EasterService,
        )

        svc = EasterService(t.dao)
        # 默认保底 200：遗留高计数（90）不触发强制事件，仅递增
        with patch_random(random=0.99):
            r = await svc.trigger(
                "u1",
                "G1",
                90,
                90,
                lucky_probability=0.005,
                unlucky_probability=0.005,
                lucky_pity_count=200,
                unlucky_pity_count=200,
            )
            assert r["event"] is None
            assert r["lucky_pity"] == 91 and r["unlucky_pity"] == 91
        # 保底关闭（0）：计数再高也不强制
        with patch_random(random=0.99):
            r = await svc.trigger(
                "u1",
                "G1",
                90,
                90,
                lucky_probability=0.005,
                unlucky_probability=0.005,
                lucky_pity_count=0,
                unlucky_pity_count=0,
            )
            assert r["event"] is None
            assert r["lucky_pity"] == 91 and r["unlucky_pity"] == 91
    return "彩蛋：默认/关闭保底下遗留高计数不触发强制事件"


async def test_easter_signin_lucky_integration():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.date_reward_service import (
            DateRewardService,
        )
        from astrbot_plugin_point_system_by_whleague.services.easter_service import (
            EasterService,
        )
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )
        from astrbot_plugin_point_system_by_whleague.services.sign_in_service import (
            SignInService,
        )

        cfg = base_cfg(
            signin_fixed_mode=True,
            signin_fixed_points=10,
            signin_first_bonus=50,
            signin_day_first_bonus=30,
            signin_consecutive_max=30,
            signin_consecutive_bonus_per_day=5,
            signin_weekly_bonus=100,
            birthday_bonus_points=0,
            easter_lucky_probability=0.02,
        )
        svc = SignInService(
            t.db,
            t.dao,
            PointService(t.db, t.dao),
            EasterService(t.dao),
            DateRewardService(t.dao),
            cfg,
        )
        with patch_random(random=0.01, randint=100):
            r = await svc.sign_in("u1", "G1", "aiocqhttp", "签到")
        # 基础10+首签50+日首签30+连签0(第1天无加成)+彩蛋100 = 190
        assert r["points"] == 190, r["points"]
        row = await t.dao.get_account("u1")
        assert row["points"] == 190 and row["total_earned"] == 190
        log = await t.db.fetchone(
            "SELECT easter_event_type, easter_points, points_earned FROM sign_in_log WHERE qq='u1'"
        )
        assert log["easter_event_type"] == "lucky" and log["easter_points"] == 100
        assert log["points_earned"] == 190
    return "签到×彩蛋：lucky 正事件入账一致"


async def test_easter_signin_unlucky_integration():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.date_reward_service import (
            DateRewardService,
        )
        from astrbot_plugin_point_system_by_whleague.services.easter_service import (
            EasterService,
        )
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )
        from astrbot_plugin_point_system_by_whleague.services.sign_in_service import (
            SignInService,
        )

        cfg = base_cfg(
            signin_fixed_mode=True,
            signin_fixed_points=10,
            signin_first_bonus=50,
            signin_day_first_bonus=30,
            signin_consecutive_max=30,
            signin_consecutive_bonus_per_day=5,
            signin_weekly_bonus=100,
            birthday_bonus_points=0,
            easter_lucky_probability=0.02,
            easter_unlucky_probability=0.03,
        )
        svc = SignInService(
            t.db,
            t.dao,
            PointService(t.db, t.dao),
            EasterService(t.dao),
            DateRewardService(t.dao),
            cfg,
        )
        with patch_random(random=0.025, randint=-100):
            r = await svc.sign_in("u1", "G1", "aiocqhttp", "签到")
        # 基础90(连签0) + 负彩蛋-100 = -10
        assert r["points"] == -10, r["points"]
        # 负总积分显示格式：-10 而非 "+-10"
        assert "获得 -10 积分" in r["msg"], r["msg"]
        assert "+-10" not in r["msg"]
        acct = await t.dao.get_account("u1")
        # 非酋负事件不计入累计获得：total_earned = 90
        assert acct["points"] == -10 and acct["total_earned"] == 90
        # 负余额自动补发负分头衔
        row = await t.dao.get_user("u1", "G1")
        assert row["negative_title_id"] == 1
        log = await t.db.fetchone(
            "SELECT easter_event_type, easter_points FROM sign_in_log WHERE qq='u1'"
        )
        assert log["easter_event_type"] == "unlucky" and log["easter_points"] == -100
    return "签到×彩蛋：unlucky 负事件→负余额→负分头衔、total_earned 排除"


async def test_date_reward_check_branches():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.date_reward_service import (
            DateRewardService,
        )

        svc = DateRewardService(t.dao)
        await t.dao.add_date_reward("01-01", None, "元旦", 50, 1.0)
        await t.dao.add_date_reward("01-01", None, "HAPPY", 10, 1.0)
        await t.dao.add_date_reward("01-01", None, "概率奖", 7, 0.5)
        assert await svc.check("01-01", "元旦快乐") == 50
        assert await svc.check("01-02", "元旦快乐") == 0  # 日期不匹配
        assert await svc.check("01-01", "happy day") == 10  # 关键词大小写不敏感
        # 概率：random() <= 0.5 才发奖
        with patch_random(random=0.6):
            assert await svc.check("01-01", "概率奖") == 0
        with patch_random(random=0.4):
            assert await svc.check("01-01", "概率奖") == 7
        # 停用后排除
        await t.db.execute("UPDATE date_rewards SET is_active=0 WHERE keyword='元旦'")
        assert await svc.check("01-01", "元旦快乐") == 0
    return "日期奖励：单日/大小写/概率/停用排除"


async def test_date_reward_cross_year_range():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.date_reward_service import (
            DateRewardService,
        )

        svc = DateRewardService(t.dao)
        await t.dao.add_date_reward("12-30", "01-02", "跨年", 5, 1.0)
        assert await svc.check("12-31", "跨年") == 5
        assert await svc.check("01-01", "跨年") == 5
        assert await svc.check("01-02", "跨年") == 5
        assert await svc.check("07-01", "跨年") == 0
    return "日期奖励：跨年区间包含端点、区间外为 0"


async def test_date_reward_signin_integration():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.date_reward_service import (
            DateRewardService,
        )
        from astrbot_plugin_point_system_by_whleague.services.easter_service import (
            EasterService,
        )
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )
        from astrbot_plugin_point_system_by_whleague.services.sign_in_service import (
            SignInService,
        )
        from astrbot_plugin_point_system_by_whleague.utils.helpers import today_mmdd

        await t.db.execute("UPDATE easter_events SET is_active=0")
        await t.dao.add_date_reward(today_mmdd(), None, "龙", 30, 1.0)
        cfg = base_cfg(
            signin_fixed_mode=True,
            signin_fixed_points=10,
            signin_first_bonus=50,
            signin_day_first_bonus=30,
            signin_consecutive_max=30,
            signin_consecutive_bonus_per_day=5,
            signin_weekly_bonus=100,
            birthday_bonus_points=0,
        )
        svc = SignInService(
            t.db,
            t.dao,
            PointService(t.db, t.dao),
            EasterService(t.dao),
            DateRewardService(t.dao),
            cfg,
        )
        r = await svc.sign_in("u1", "G1", "aiocqhttp", "龙年大吉")
        assert r["points"] == 90 + 30, r["points"]
        log = await t.db.fetchone("SELECT points_earned FROM sign_in_log WHERE qq='u1'")
        assert log["points_earned"] == 120
    return "签到×日期奖励：消息命中关键词加分入账"


async def test_easter_zero_probability_events():
    """概率为 0 的事件被过滤：random.choices 不会因全零权重崩溃。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.easter_service import (
            EasterService,
        )

        # 所有事件概率置 0（含保底触发路径）
        await t.db.execute("UPDATE easter_events SET probability=0")
        svc = EasterService(t.dao)
        with patch_random(random=0.01):
            r = await svc.trigger(
                "u1",
                "G1",
                0,
                0,
                lucky_probability=0.02,
                unlucky_probability=0.03,
                lucky_pity_count=200,
                unlucky_pity_count=200,
            )
            assert r["event"] is None
            assert r["lucky_pity"] == 1 and r["unlucky_pity"] == 1
        # 保底强制触发时也不崩溃（事件全被过滤 → event None，保底照常清零）
        with patch_random(random=0.99):
            r = await svc.trigger(
                "u1",
                "G1",
                1,
                0,
                lucky_probability=0.02,
                unlucky_probability=0.03,
                lucky_pity_count=2,
                unlucky_pity_count=200,
            )
            assert r["event"] is None
            assert r["lucky_pity"] == 0
    return "彩蛋：0 概率事件被过滤、概率与保底路径均不崩溃"


TESTS = [
    ("easter_no_events", test_easter_no_events),
    ("easter_probability_branches", test_easter_probability_branches),
    ("easter_pity_force", test_easter_pity_force),
    ("easter_weighted_choice", test_easter_weighted_choice),
    ("easter_default_pity_no_force", test_easter_default_pity_no_force),
    ("easter_zero_probability", test_easter_zero_probability_events),
    ("easter_signin_lucky", test_easter_signin_lucky_integration),
    ("easter_signin_unlucky", test_easter_signin_unlucky_integration),
    ("date_reward_check", test_date_reward_check_branches),
    ("date_reward_cross_year", test_date_reward_cross_year_range),
    ("date_reward_signin", test_date_reward_signin_integration),
]
