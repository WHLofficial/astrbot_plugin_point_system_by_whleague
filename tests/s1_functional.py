"""S1 功能正确性回归：签到/抽奖/兑换/口令/排行/头衔/配置/清空。"""
import asyncio
import json

from .common import TempDB, FakeEvent, collect


async def test_signin_fixed_mode():
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")  # 停用彩蛋，精确断言积分
        cfg = {
            "signin_fixed_mode": True,
            "signin_fixed_points": 10,
            "signin_random_min": 1,
            "signin_random_max": 20,
            "signin_first_bonus": 50,
            "signin_day_first_bonus": 30,
            "signin_consecutive_max": 30,
            "signin_consecutive_bonus_per_day": 5,
            "signin_weekly_bonus": 100,
            "birthday_bonus_points": 100,
        }
        from astrbot_plugin_point_system_by_whleague.services.point_service import PointService
        from astrbot_plugin_point_system_by_whleague.services.easter_service import EasterService
        from astrbot_plugin_point_system_by_whleague.services.date_reward_service import DateRewardService
        from astrbot_plugin_point_system_by_whleague.services.sign_in_service import SignInService

        ps = PointService(t.db, t.dao)
        easter = EasterService(t.dao)
        dater = DateRewardService(t.dao)
        svc = SignInService(t.db, t.dao, ps, easter, dater, cfg)

        r = await svc.sign_in("u1", "G1", "aiocqhttp", "签到")
        assert not r["already_signed"]
        # 固定 10 + 首次 50 + 每日首签 30 + 连签 5 = 95
        assert r["points"] == 95, r["points"]
        # 同日再签被拒
        r2 = await svc.sign_in("u1", "G1", "aiocqhttp", "签到")
        assert r2["already_signed"]
        # 流水入账
        row = await t.db.fetchone(
            "SELECT points, total_earned, total_sign_days FROM users WHERE qq='u1' AND group_id='G1'"
        )
        assert row["points"] == 95 and row["total_earned"] == 95 and row["total_sign_days"] == 1
    return "固定模式签到：首次奖励/连签/去重/入账一致"


async def test_signin_random_mode():
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")
        cfg = {
            "signin_fixed_mode": False,
            "signin_fixed_points": 10,
            "signin_random_min": 1,
            "signin_random_max": 20,
            "signin_first_bonus": 0,
            "signin_day_first_bonus": 0,
            "signin_consecutive_max": 30,
            "signin_consecutive_bonus_per_day": 0,
            "signin_weekly_bonus": 0,
            "birthday_bonus_points": 0,
        }
        from astrbot_plugin_point_system_by_whleague.services.point_service import PointService
        from astrbot_plugin_point_system_by_whleague.services.easter_service import EasterService
        from astrbot_plugin_point_system_by_whleague.services.date_reward_service import DateRewardService
        from astrbot_plugin_point_system_by_whleague.services.sign_in_service import SignInService

        ps = PointService(t.db, t.dao)
        svc = SignInService(t.db, t.dao, ps, EasterService(t.dao), DateRewardService(t.dao), cfg)
        for i in range(200):
            r = await svc.sign_in(f"u{i}", "G1", "aiocqhttp", "签到")
            assert not r["already_signed"] and 1 <= r["points"] <= 20
    return "随机模式签到：200 次均在 [1,20] 区间"


async def test_signin_day_first_and_streak():
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")
        cfg = {
            "signin_fixed_mode": True, "signin_fixed_points": 10,
            "signin_first_bonus": 0, "signin_day_first_bonus": 30,
            "signin_consecutive_max": 30, "signin_consecutive_bonus_per_day": 5,
            "signin_weekly_bonus": 100, "birthday_bonus_points": 0,
        }
        from astrbot_plugin_point_system_by_whleague.services.point_service import PointService
        from astrbot_plugin_point_system_by_whleague.services.easter_service import EasterService
        from astrbot_plugin_point_system_by_whleague.services.date_reward_service import DateRewardService
        from astrbot_plugin_point_system_by_whleague.services.sign_in_service import SignInService

        svc = SignInService(t.db, t.dao, PointService(t.db, t.dao), EasterService(t.dao), DateRewardService(t.dao), cfg)
        r = await svc.sign_in("a", "G1", "aiocqhttp", "签到")
        assert r["points"] == 10 + 30 + 5, r  # 基础 10 + 每日首签 30 + 连签第1天 5
        r = await svc.sign_in("b", "G1", "aiocqhttp", "签到")
        assert r["points"] == 10 + 5, r  # 非首签：基础 10 + 连签第1天 5
        # 断签：旧日期 → 连签重置为 1
        await t.db.execute("DELETE FROM sign_in_log WHERE qq='a' AND group_id='G1'")
        await t.db.execute(
            "UPDATE users SET last_sign_date=?, consecutive_days=2 WHERE qq='a' AND group_id='G1'",
            ("2020-01-01",),
        )
        r = await svc.sign_in("a", "G1", "aiocqhttp", "签到")
        assert r["consecutive"] == 1, r
        # 连签 6 → 7 触发周奖励
        await t.db.execute("DELETE FROM sign_in_log WHERE qq='a' AND group_id='G1'")
        from datetime import datetime, timedelta
        from astrbot_plugin_point_system_by_whleague.utils.helpers import today_str
        yesterday = (datetime.strptime(today_str(), "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        await t.db.execute(
            "UPDATE users SET last_sign_date=?, consecutive_days=6 WHERE qq='a' AND group_id='G1'",
            (yesterday,),
        )
        r = await svc.sign_in("a", "G1", "aiocqhttp", "签到")
        assert r["consecutive"] == 7, r
        assert r["points"] == 10 + 7 * 5 + 100, r["points"]  # 基础+连签+周奖励
    return "每日首签奖励唯一 / 连签断签重置 / 周奖励"


async def test_fortune_deterministic():
    from astrbot_plugin_point_system_by_whleague.utils.fortune import get_fortune
    a = get_fortune("12345", "2026-08-01")
    b = get_fortune("12345", "2026-08-01")
    c = get_fortune("12345", "2026-08-02")
    d = get_fortune("99999", "2026-08-01")
    assert a == b
    assert a != c
    assert a != d
    return "运势：同人同日确定、异日/异人不同"


async def test_lottery_tiers_and_limit():
    async with TempDB() as t:
        cfg = {
            "lottery_enabled": True, "lottery_cost": 10, "lottery_daily_limit": 5,
            "lottery_passphrase": "whl", "negative_disable_lottery": True,
            "lottery_tiers": json.dumps({
                "tiers": [
                    {"label": "一等奖", "weight": 1, "multiplier": 2.0, "emoji": "🏆"},
                    {"label": "参与奖", "weight": 99, "multiplier": 0.0, "emoji": "✨"},
                ]
            }),
        }
        from astrbot_plugin_point_system_by_whleague.services.point_service import PointService
        from astrbot_plugin_point_system_by_whleague.services.lottery_service import LotteryService

        ps = PointService(t.db, t.dao)
        svc = LotteryService(t.db, t.dao, ps, cfg)
        await t.db.execute("INSERT INTO users (qq, group_id, points) VALUES ('u1','G1',10000)")
        ok = 0
        for _ in range(8):
            r = await svc.draw("u1", "G1")
            if r["success"]:
                ok += 1
        assert ok == 5, ok  # 每日限额 5
        # 关闭抽奖
        cfg["lottery_enabled"] = False
        r = await svc.draw("u1", "G1")
        assert not r["success"]
        # 负分拦截
        cfg["lottery_enabled"] = True
        await t.db.execute("UPDATE users SET points=-1 WHERE qq='u1' AND group_id='G1'")
        r = await svc.draw("u1", "G1")
        assert not r["success"]
    return "抽奖：每日限额/开关/负分拦截"


async def test_redeem_stock_discount_verify():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.point_service import PointService
        from astrbot_plugin_point_system_by_whleague.services.redeem_service import RedeemService

        ps = PointService(t.db, t.dao)
        svc = RedeemService(t.db, t.dao, ps)
        item_id = await t.dao.add_item("商品A", 100, 2)
        await t.db.execute("INSERT INTO users (qq, group_id, points) VALUES ('u1','G1',500)")
        r = await svc.redeem("u1", "G1", item_id, 2)
        assert r["success"], r
        rec = await t.db.fetchone("SELECT * FROM redeem_records WHERE qq='u1'")
        assert rec["item_cost"] == 200 and rec["quantity"] == 2
        # 库存耗尽
        r = await svc.redeem("u1", "G1", item_id, 1)
        assert not r["success"] and "库存" in r["msg"]
        # 折扣设置与过期
        from datetime import datetime, timedelta
        end = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        r = await svc.set_discount(item_id, 50, end)
        assert r["success"]
        items = await svc.list_items()
        assert items[0]["cost"] == 50
        past = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        await svc.set_discount(item_id, 50, past)
        items = await svc.list_items()
        assert items[0]["cost"] == 100  # 已过期恢复原价
        # 折扣价 >= 原价被拒
        r = await svc.set_discount(item_id, 100, end)
        assert not r["success"]
        # 核销切换
        new_status = await t.dao.toggle_redeem_status(rec["record_no"], "admin")
        assert new_status == "verified"
        new_status = await t.dao.toggle_redeem_status(rec["record_no"], "admin")
        assert new_status == "pending"
    return "兑换：库存/折扣时效/核销切换"


async def test_daily_keyword():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.point_service import PointService
        from astrbot_plugin_point_system_by_whleague.services.daily_keyword_service import DailyKeywordService

        ps = PointService(t.db, t.dao)
        svc = DailyKeywordService(t.db, t.dao, ps)
        await t.dao.set_daily_keyword("G1", "红包", 10, "admin")
        r = await svc.check_and_claim("u1", "G1", "今天抢到红包了")
        assert r.get("claimed") and r["points"] == 10
        r = await svc.check_and_claim("u1", "G1", "红包又来了")
        assert r.get("already") is True
        # 负分拦截（未领取过口令的新负分用户）
        await t.db.execute("INSERT INTO users (qq, group_id, points) VALUES ('u3','G1',-5)")
        r = await svc.check_and_claim("u3", "G1", "红包")
        assert r.get("blocked") is True
        # 缓存失效
        svc.invalidate("G1")
        await t.dao.clear_daily_keyword("G1")
        r = await svc.check_and_claim("u2", "G1", "红包")
        assert not r.get("claimed")
    return "每日口令：命中/去重/负分拦截/缓存失效"


async def test_ranking_stats():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.ranking_service import RankingService
        for i in range(5):
            await t.db.execute(
                "INSERT INTO users (qq, group_id, points) VALUES (?,?,?)",
                (f"u{i}", "G1", 100 - i * 10),
            )
        svc = RankingService(t.dao)
        r = await svc.get_ranking("G1")
        assert not r["is_global"] and r["users"][0]["qq"] == "u0"
        assert len(r["users"]) == 5
        # 少于 3 人回退全局
        r = await svc.get_ranking("G2")
        assert r["is_global"]
    return "排行：本群 Top / 回退全局"


async def test_negative_title_lifecycle():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.point_service import PointService
        ps = PointService(t.db, t.dao)
        await t.db.execute("INSERT INTO users (qq, group_id, points) VALUES ('u1','G1',-10)")
        # bot=None：仅维护 DB 状态
        new_id = await ps.ensure_negative_title("u1", "G1", bot=None)
        assert new_id == 1
        row = await t.dao.get_user("u1", "G1")
        assert row["negative_title_id"] == 1
        # 回正清除
        await t.db.execute("UPDATE users SET points=5 WHERE qq='u1' AND group_id='G1'")
        r = await ps.ensure_negative_title("u1", "G1", bot=None)
        assert r is None
        row = await t.dao.get_user("u1", "G1")
        assert row["negative_title_id"] is None
    return "负分头衔：分配/回正清除"


async def test_config_validate_full():
    from astrbot_plugin_point_system_by_whleague.config.defaults import (
        DEFAULT_CONFIG, TYPE_MAP, validate_and_cast, _LIST_KEYS,
    )
    import json as _json
    schema = _json.load(open(__import__("os").path.join(
        __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))),
        "_conf_schema.json"), encoding="utf-8"))
    assert set(DEFAULT_CONFIG.keys()) == set(schema.keys())
    assert set(TYPE_MAP.keys()) == set(schema.keys())
    # 全键类型转换
    for key, meta in schema.items():
        if meta["type"] in ("int", "float", "bool"):
            sample = meta.get("default")
            assert validate_and_cast(key, str(sample)) == sample or isinstance(
                validate_and_cast(key, str(sample)), type(sample)
            )
    # 列表类
    assert validate_and_cast("keyword_sign", '["a","b"]') == ["a", "b"]
    assert validate_and_cast("keyword_sign", "a,b") == ["a", "b"]
    # HH:MM 规范化与拒绝
    assert validate_and_cast("backup_time", "4:00") == "04:00"
    for bad in ("25:00", "04:60", "x", "4"):
        try:
            validate_and_cast("backup_time", bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    # 概率边界
    assert validate_and_cast("active_reward_probability", "0.5") == 0.5
    for bad in ("-0.1", "1.5"):
        try:
            validate_and_cast("active_reward_probability", bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    # 非负整数
    for bad in ("-1", "abc", "1.5"):
        try:
            validate_and_cast("lottery_cost", bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    # lottery_tiers 恶意输入
    for bad in ("not json", "{}", '{"tiers":[]}', '{"tiers":[{"weight":1,"multiplier":0}]}',
                '{"tiers":[{"label":"x","weight":0,"multiplier":1}]}',
                '{"tiers":[{"label":"x","weight":1,"multiplier":101}]}'):
        try:
            validate_and_cast("lottery_tiers", bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    # 未知键
    try:
        validate_and_cast("nope", "1")
        raise AssertionError
    except ValueError:
        pass
    return "配置：全键校验/边界/恶意 JSON/未知键"


async def test_clear_feature_regression():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.backup_service import BackupService
        from astrbot_plugin_point_system_by_whleague.handlers.admin import AdminHandler

        import types as _t
        backup = BackupService(t.db, {"backup_dirs": []})
        plugin = _t.SimpleNamespace(
            db=t.db, dao=t.dao, backup_service=backup,
            point_service=_t.SimpleNamespace(_set_group_card=_async_noop),
        )
        handler = AdminHandler(plugin)
        await t.db.execute("INSERT INTO users (qq, group_id, points) VALUES ('u1','G1',10)")
        ev = FakeEvent("admin", "G1", is_admin=True)
        msgs = await collect(handler.clear_data(ev, "group"))
        assert any("/确认清空" in m for m in msgs)
        token = handler._pending_clears["admin"]["token"]
        ev2 = FakeEvent("admin", "G1", is_admin=True, msg=f"/确认清空 {token}")
        msgs = await collect(handler.confirm_clear(ev2))
        assert any("已清空本群数据" in m for m in msgs)
        assert await t.count("users") == 0
        # 权限
        ev3 = FakeEvent("member", "G1", is_admin=False)
        msgs = await collect(handler.clear_data(ev3, "global"))
        assert any("全局管理员" in m for m in msgs)
    return "清空功能回归：令牌/执行/权限"


async def _async_noop(*a, **k):
    return None


TESTS = [
    ("signin_fixed_mode", test_signin_fixed_mode),
    ("signin_random_mode", test_signin_random_mode),
    ("signin_day_first_streak", test_signin_day_first_and_streak),
    ("fortune_deterministic", test_fortune_deterministic),
    ("lottery_tiers_limit", test_lottery_tiers_and_limit),
    ("redeem_stock_discount", test_redeem_stock_discount_verify),
    ("daily_keyword", test_daily_keyword),
    ("ranking_stats", test_ranking_stats),
    ("negative_title", test_negative_title_lifecycle),
    ("config_validate", test_config_validate_full),
    ("clear_feature", test_clear_feature_regression),
]
