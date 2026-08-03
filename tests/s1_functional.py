"""S1 功能正确性回归：签到/抽奖/兑换/口令/排行/头衔/配置/清空。"""

import json

from .common import FakeBot, FakeEvent, TempDB, base_cfg, collect


async def test_signin_fixed_mode():
    async with TempDB() as t:
        await t.db.execute(
            "UPDATE easter_events SET is_active=0"
        )  # 停用彩蛋，精确断言积分
        cfg = base_cfg(
            signin_fixed_mode=True,
            signin_fixed_points=10,
            signin_random_min=1,
            signin_random_max=20,
            signin_first_bonus=50,
            signin_day_first_bonus=30,
            signin_consecutive_max=30,
            signin_consecutive_bonus_per_day=5,
            signin_weekly_bonus=100,
            birthday_bonus_points=100,
        )
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

        ps = PointService(t.db, t.dao)
        easter = EasterService(t.dao)
        dater = DateRewardService(t.dao)
        svc = SignInService(t.db, t.dao, ps, easter, dater, cfg)

        r = await svc.sign_in("u1", "G1", "aiocqhttp", "签到")
        assert not r["already_signed"]
        # 固定 10 + 首次 50 + 每日首签 30 + 连签(第1天无加成) 0 = 90
        assert r["points"] == 90, r["points"]
        # v0.2.2：反馈含当日签到排名/连签天数/当前积分
        assert "今日第 1 位签到" in r["msg"], r["msg"]
        assert "连签: 第 1 天" in r["msg"], r["msg"]
        assert "当前积分: 90" in r["msg"], r["msg"]
        # 同日再签被拒
        r2 = await svc.sign_in("u1", "G1", "aiocqhttp", "签到")
        assert r2["already_signed"]
        # 流水入账（accounts 全局账户）
        row = await t.db.fetchone(
            "SELECT points, total_earned, total_sign_days FROM accounts WHERE qq='u1'"
        )
        assert (
            row["points"] == 90
            and row["total_earned"] == 90
            and row["total_sign_days"] == 1
        )
    return "固定模式签到：首次奖励/连签/去重/入账一致"


async def test_signin_random_mode():
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")
        cfg = base_cfg(
            signin_fixed_mode=False,
            signin_fixed_points=10,
            signin_random_min=1,
            signin_random_max=20,
            signin_first_bonus=0,
            signin_day_first_bonus=0,
            signin_consecutive_max=30,
            signin_consecutive_bonus_per_day=0,
            signin_weekly_bonus=0,
            birthday_bonus_points=0,
        )
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

        ps = PointService(t.db, t.dao)
        svc = SignInService(
            t.db, t.dao, ps, EasterService(t.dao), DateRewardService(t.dao), cfg
        )
        for i in range(200):
            r = await svc.sign_in(f"u{i}", "G1", "aiocqhttp", "签到")
            assert not r["already_signed"] and 1 <= r["points"] <= 20
    return "随机模式签到：200 次均在 [1,20] 区间"


async def test_signin_day_first_and_streak():
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")
        cfg = base_cfg(
            signin_fixed_mode=True,
            signin_fixed_points=10,
            signin_first_bonus=0,
            signin_day_first_bonus=30,
            signin_consecutive_max=30,
            signin_consecutive_bonus_per_day=5,
            signin_weekly_bonus=100,
            birthday_bonus_points=0,
        )
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

        svc = SignInService(
            t.db,
            t.dao,
            PointService(t.db, t.dao),
            EasterService(t.dao),
            DateRewardService(t.dao),
            cfg,
        )
        r = await svc.sign_in("a", "G1", "aiocqhttp", "签到")
        assert r["points"] == 10 + 30 + 0, r  # 基础 10 + 每日首签 30 + 连签第1天无加成
        r = await svc.sign_in("b", "G1", "aiocqhttp", "签到")
        assert r["points"] == 10 + 0, r  # 非首签：基础 10 + 连签第1天无加成
        # 断签：旧日期 → 连签重置为 1
        await t.db.execute("DELETE FROM sign_in_log WHERE qq='a' AND group_id='G1'")
        await t.db.execute(
            "UPDATE accounts SET last_sign_date=?, consecutive_days=2 WHERE qq='a'",
            ("2020-01-01",),
        )
        r = await svc.sign_in("a", "G1", "aiocqhttp", "签到")
        assert r["consecutive"] == 1, r
        # 连签 6 → 7 触发周奖励
        await t.db.execute("DELETE FROM sign_in_log WHERE qq='a' AND group_id='G1'")
        from datetime import datetime, timedelta

        from astrbot_plugin_point_system_by_whleague.utils.helpers import today_str

        yesterday = (
            datetime.strptime(today_str(), "%Y-%m-%d") - timedelta(days=1)
        ).strftime("%Y-%m-%d")
        await t.db.execute(
            "UPDATE accounts SET last_sign_date=?, consecutive_days=6 WHERE qq='a'",
            (yesterday,),
        )
        r = await svc.sign_in("a", "G1", "aiocqhttp", "签到")
        assert r["consecutive"] == 7, r
        assert r["points"] == 10 + 6 * 5 + 100, r["points"]  # 基础+连签(第7天=6×5)+周奖励
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
            "lottery_enabled": True,
            "lottery_cost": 10,
            "lottery_daily_limit": 5,
            "lottery_passphrase": "whl",
            "negative_disable_lottery": True,
            "lottery_tiers": json.dumps(
                {
                    "tiers": [
                        {
                            "label": "一等奖",
                            "weight": 1,
                            "points_min": 30,
                            "points_max": 30,
                            "emoji": "🏆",
                        },
                        {
                            "label": "参与奖",
                            "weight": 99,
                            "points_min": 0,
                            "points_max": 0,
                            "emoji": "✨",
                        },
                    ]
                }
            ),
        }
        from astrbot_plugin_point_system_by_whleague.services.lottery_service import (
            LotteryService,
        )
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        ps = PointService(t.db, t.dao)
        svc = LotteryService(t.db, t.dao, ps, cfg)
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u1',10000)"
        )
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
        await t.db.execute("UPDATE accounts SET points=-1 WHERE qq='u1'")
        r = await svc.draw("u1", "G1")
        assert not r["success"]
    return "抽奖：每日限额/开关/负分拦截"


async def test_redeem_stock_discount_verify():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )
        from astrbot_plugin_point_system_by_whleague.services.redeem_service import (
            RedeemService,
        )

        ps = PointService(t.db, t.dao)
        svc = RedeemService(t.db, t.dao, ps)
        item_id = await t.dao.add_item("商品A", 100, 2)
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u1',500)"
        )
        r = await svc.redeem("u1", "G1", item_id, 2)
        assert r["success"], r
        rec = await t.db.fetchone("SELECT * FROM redeem_records WHERE qq='u1'")
        assert rec["item_cost"] == 200 and rec["quantity"] == 2
        # v0.2.2：反馈含订单号/剩余库存/积分余额/核销提示，与 DB 一致
        assert r["record_no"] == rec["record_no"], r
        assert r["remaining_stock"] == 0 and r["balance"] == 300, r
        assert rec["record_no"] in r["msg"], r["msg"]
        assert "剩余库存: 0" in r["msg"], r["msg"]
        assert "积分余额: 300" in r["msg"], r["msg"]
        assert "联系管理员核销" in r["msg"], r["msg"]
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
        # 核销/驳回三态：通过（积分库存不变）→ 驳回（退分+恢复库存）→ 通过（扣分+扣库存）
        r = await svc.set_record_status(rec["record_no"], "verified", "admin", "G1")
        assert r["success"] and r["changed"] and r["status"] == "verified"
        assert "已核销" in r["msg"]
        row = await t.db.fetchone("SELECT points FROM accounts WHERE qq='u1'")
        assert row["points"] == 300  # 通过不涉及积分变动
        row = await t.db.fetchone("SELECT stock FROM redeem_items WHERE id=?", (item_id,))
        assert row["stock"] == 0
        # 驳回：退分 + 恢复库存，管理员确认消息回显备注
        r = await svc.set_record_status(rec["record_no"], "rejected", "admin", "G1", "无货")
        assert r["success"] and r["status"] == "rejected"
        assert "（无货）" in r["msg"], r["msg"]
        row = await t.db.fetchone("SELECT points FROM accounts WHERE qq='u1'")
        assert row["points"] == 500  # 退回消耗的 200
        row = await t.db.fetchone("SELECT stock FROM redeem_items WHERE id=?", (item_id,))
        assert row["stock"] == 2  # 库存恢复
        # 无效动作白名单拒绝：状态与积分不变
        r = await svc.set_record_status(rec["record_no"], "invalid", "admin", "G1")
        assert not r["success"] and "无效操作" in r["msg"]
        rec2 = await t.dao.get_redeem_record(rec["record_no"])
        assert rec2["status"] == "rejected"
        # 退款不计入累计获得
        acct = await t.dao.get_account("u1")
        assert acct["total_earned"] == 0
        # 驳回 → 通过：重新扣分 + 扣库存
        r = await svc.set_record_status(rec["record_no"], "verified", "admin", "G1")
        assert r["success"] and r["status"] == "verified"
        row = await t.db.fetchone("SELECT points FROM accounts WHERE qq='u1'")
        assert row["points"] == 300
        row = await t.db.fetchone("SELECT stock FROM redeem_items WHERE id=?", (item_id,))
        assert row["stock"] == 0
        # 幂等：同态提示不重复处理
        r = await svc.set_record_status(rec["record_no"], "verified", "admin", "G1")
        assert r["success"] and not r["changed"]
        # 库存不足：驳回→通过失败，积分/状态零变更
        r = await svc.set_record_status(rec["record_no"], "rejected", "admin", "G1")
        assert r["success"] and r["status"] == "rejected"
        await t.db.execute("UPDATE redeem_items SET stock=0 WHERE id=?", (item_id,))
        r = await svc.set_record_status(rec["record_no"], "verified", "admin", "G1")
        assert not r["success"] and "库存不足" in r["msg"]
        rec2 = await t.dao.get_redeem_record(rec["record_no"])
        assert rec2["status"] == "rejected"
        row = await t.db.fetchone("SELECT points FROM accounts WHERE qq='u1'")
        assert row["points"] == 500
    return "兑换：库存/折扣时效/核销驳回三态"


async def test_daily_keyword():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.daily_keyword_service import (
            DailyKeywordService,
        )
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        ps = PointService(t.db, t.dao)
        svc = DailyKeywordService(t.db, t.dao, ps)
        await t.dao.set_daily_keyword("G1", "红包", 10, "admin")
        r = await svc.check_and_claim("u1", "G1", "今天抢到红包了")
        assert r.get("claimed") and r["points"] == 10
        r = await svc.check_and_claim("u1", "G1", "红包又来了")
        assert r.get("already") is True
        # 负分拦截（未领取过口令的新负分用户）
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u3',-5)"
        )
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
        from astrbot_plugin_point_system_by_whleague.services.ranking_service import (
            RankingService,
        )

        for i in range(5):
            await t.db.execute(
                "INSERT INTO accounts (qq, points) VALUES (?,?)",
                (f"u{i}", 100 - i * 10),
            )
            await t.db.execute(
                "INSERT INTO users (qq, group_id) VALUES (?,?)",
                (f"u{i}", "G1"),
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
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        ps = PointService(t.db, t.dao)
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u1',-10)"
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('u1','G1')"
        )
        # bot=None：仅维护 DB 状态
        new_id = await ps.ensure_negative_title("u1", "G1", bot=None)
        assert new_id == 1
        row = await t.dao.get_user("u1", "G1")
        assert row["negative_title_id"] == 1
        # 回正清除
        await t.db.execute("UPDATE accounts SET points=5 WHERE qq='u1'")
        r = await ps.ensure_negative_title("u1", "G1", bot=None)
        assert r is None
        row = await t.dao.get_user("u1", "G1")
        assert row["negative_title_id"] is None
    return "负分头衔：分配/回正清除"


async def test_negative_user_signin_recovers():
    """负分用户签到恢复：签到加分回正余额后，自动清除负分头衔（含 bot 名片恢复）。"""
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")
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
        from .common import FakeBot

        cfg = base_cfg(
            signin_fixed_mode=True,
            signin_fixed_points=30,
            signin_first_bonus=0,
            signin_day_first_bonus=0,
            signin_consecutive_max=30,
            signin_consecutive_bonus_per_day=0,
            signin_weekly_bonus=0,
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
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('10001',-10)"
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('10001','100001')"
        )
        bot = FakeBot()
        ps = svc._point
        # 先分配负分头衔（模拟签到负彩蛋/管理员扣分后状态）
        new_id = await ps.ensure_negative_title("10001", "100001", bot=bot)
        assert new_id == 1
        assert any(
            c["card"] == "群女仆1号"
            for a, c in bot.calls
            if a == "set_group_card"
        )
        bot.calls.clear()
        # 负分签到：+30 回正为 +20，头衔自动清除并恢复名片
        r = await svc.sign_in("10001", "100001", "aiocqhttp", "签到", bot=bot)
        assert r["points"] == 30, r["points"]  # 本次获得
        acct = await t.dao.get_account("10001")
        assert acct["points"] == -10 + 30, acct["points"]  # 余额回正为 +20
        user = await t.dao.get_user("10001", "100001")
        assert user["negative_title_id"] is None  # 头衔已清除
        restore_calls = [c for a, c in bot.calls if a == "set_group_card"]
        assert any(c["card"] == "" for c in restore_calls), bot.calls
        # 再次变负 → 头衔重新分配
        await t.db.execute("UPDATE accounts SET points=-5 WHERE qq='10001'")
        bot.calls.clear()
        new_id = await ps.ensure_negative_title("10001", "100001", bot=bot)
        assert new_id == 1
    return "负分签到恢复：签到回正清除头衔+名片、再负重新分配"


async def test_config_validate_full():
    import json as _json

    from astrbot_plugin_point_system_by_whleague.config.defaults import (
        DEFAULT_CONFIG,
        TYPE_MAP,
        validate_and_cast,
    )

    schema = _json.load(
        open(
            __import__("os").path.join(
                __import__("os").path.dirname(
                    __import__("os").path.dirname(
                        __import__("os").path.abspath(__file__)
                    )
                ),
                "_conf_schema.json",
            ),
            encoding="utf-8",
        )
    )
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
    for key in ("easter_lucky_probability", "easter_unlucky_probability"):
        assert validate_and_cast(key, "0.005") == 0.005
        assert validate_and_cast(key, "0") == 0.0
        assert validate_and_cast(key, "1") == 1.0
        for bad in ("-0.1", "1.5"):
            try:
                validate_and_cast(key, bad)
                raise AssertionError((key, bad))
            except ValueError:
                pass
    # 彩蛋保底：非负整数，0 表示关闭保底
    for key in ("easter_lucky_pity_count", "easter_unlucky_pity_count"):
        assert validate_and_cast(key, "200") == 200
        assert validate_and_cast(key, "0") == 0
        for bad in ("-1", "abc", "1.5"):
            try:
                validate_and_cast(key, bad)
                raise AssertionError((key, bad))
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
    for bad in (
        "not json",
        "{}",
        '{"tiers":[]}',
        '{"tiers":[{"weight":1,"points_min":1,"points_max":5}]}',
        '{"tiers":[{"label":"x","weight":0,"points_min":1,"points_max":5}]}',
        '{"tiers":[{"label":"x","weight":1,"points_min":5,"points_max":1}]}',
        '{"tiers":[{"label":"x","weight":1,"points_min":-1,"points_max":5}]}',
        '{"tiers":[{"label":"x","weight":1,"multiplier":2}]}',
    ):
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
        import types as _t

        from astrbot_plugin_point_system_by_whleague.handlers.admin import AdminHandler
        from astrbot_plugin_point_system_by_whleague.services.backup_service import (
            BackupService,
        )

        backup = BackupService(t.db, {"backup_dirs": []})
        plugin = _t.SimpleNamespace(
            db=t.db,
            dao=t.dao,
            backup_service=backup,
            point_service=_t.SimpleNamespace(_set_group_card=_async_noop),
        )
        handler = AdminHandler(plugin)
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u1',10)"
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('u1','G1')"
        )
        ev = FakeEvent("admin", "G1", is_admin=True)
        msgs = await collect(handler.clear_data(ev, "group"))
        assert any("/确认清空" in m for m in msgs)
        token = handler._pending_clears["admin"]["token"]
        ev2 = FakeEvent("admin", "G1", is_admin=True, msg=f"/确认清空 {token}")
        msgs = await collect(handler.confirm_clear(ev2))
        assert any("已清空本群数据" in m for m in msgs)
        # 群清空：成员积分归零、成员关系保留
        assert await t.count("users") == 1
        row = await t.db.fetchone("SELECT points FROM accounts WHERE qq='u1'")
        assert row["points"] == 0
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
    ("negative_user_signin_recovers", test_negative_user_signin_recovers),
    ("config_validate", test_config_validate_full),
    ("clear_feature", test_clear_feature_regression),
]
