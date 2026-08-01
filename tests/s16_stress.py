"""S16 压力/浸泡/随机化：混合并发全局对账、跨业务日浸泡、随机操作序列 fuzz 不变量、随机配置一致性。"""

import asyncio
import json
import random
import time
from datetime import datetime, timedelta

from .common import TempDB, base_cfg


async def _stack(t, overrides=None):
    from astrbot_plugin_point_system_by_whleague.services.daily_keyword_service import (
        DailyKeywordService,
    )
    from astrbot_plugin_point_system_by_whleague.services.date_reward_service import (
        DateRewardService,
    )
    from astrbot_plugin_point_system_by_whleague.services.easter_service import (
        EasterService,
    )
    from astrbot_plugin_point_system_by_whleague.services.lottery_service import (
        LotteryService,
    )
    from astrbot_plugin_point_system_by_whleague.services.point_service import (
        PointService,
    )
    from astrbot_plugin_point_system_by_whleague.services.redeem_service import (
        RedeemService,
    )
    from astrbot_plugin_point_system_by_whleague.services.sign_in_service import (
        SignInService,
    )

    cfg = base_cfg(
        lottery_enabled=True,
        lottery_cost=1,
        lottery_daily_limit=0,
        lottery_passphrase="whl",
        lottery_tiers=json.dumps(
            {
                "tiers": [
                    {
                        "label": "参与奖",
                        "weight": 1,
                        "points_min": 0,
                        "points_max": 0,
                        "emoji": "",
                    }
                ]
            }
        ),
        negative_disable_lottery=False,
        signin_fixed_mode=True,
        signin_fixed_points=5,
        signin_first_bonus=0,
        signin_day_first_bonus=0,
        signin_consecutive_max=30,
        signin_consecutive_bonus_per_day=0,
        signin_weekly_bonus=0,
        birthday_bonus_points=0,
    )
    cfg.update(overrides or {})
    ps = PointService(t.db, t.dao)
    easter = EasterService(t.dao)
    dater = DateRewardService(t.dao)
    return {
        "cfg": cfg,
        "point": ps,
        "sign_in": SignInService(t.db, t.dao, ps, easter, dater, cfg),
        "lottery": LotteryService(t.db, t.dao, ps, cfg),
        "redeem": RedeemService(t.db, t.dao, ps),
        "keyword": DailyKeywordService(t.db, t.dao, ps),
    }


async def _assert_reconcile(t, seeds: dict):
    """全局余额对账不变量：points == 初始注入 + Σ流水。"""
    rows = await t.db.fetchall(
        "SELECT u.qq, u.group_id, u.points, "
        "COALESCE((SELECT SUM(pt.amount) FROM point_transactions pt "
        "WHERE pt.qq=u.qq AND pt.group_id=u.group_id), 0) AS flow "
        "FROM users u"
    )
    for r in rows:
        expected = seeds.get((r["qq"], r["group_id"]), 0) + r["flow"]
        assert r["points"] == expected, (r["qq"], r["points"], expected, r["flow"])


async def test_stress_mixed_concurrency_reconcile():
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")
        s = await _stack(t)
        seeds = {}
        for i in range(30):
            await t.db.execute(
                "INSERT INTO users (qq, group_id, points) VALUES (?,?,100)",
                (f"u{i}", "G1"),
            )
            seeds[(f"u{i}", "G1")] = 100
        await t.dao.set_daily_keyword("G1", "红包", 1, "admin")
        item_id = await t.dao.add_item("商品", 1, -1)

        async def op(i):
            if i % 4 == 0:
                await s["sign_in"].sign_in(f"s{i}", "G1", "aiocqhttp", "签到")
            elif i % 4 == 1:
                await s["lottery"].draw(f"u{i % 30}", "G1")
            elif i % 4 == 2:
                await s["redeem"].redeem(f"u{i % 30}", "G1", item_id, 1)
            else:
                await s["keyword"].check_and_claim(f"u{i % 30}", "G1", "抢到红包")

        results = await asyncio.gather(
            *[op(i) for i in range(300)], return_exceptions=True
        )
        assert not [r for r in results if isinstance(r, Exception)], results
        await _assert_reconcile(t, seeds)
        await t.db.fetchall("SELECT 1")  # 库仍可查询
    return "压力：300 混合并发（签到/抽奖/兑换/口令）后全局余额与流水严格对账"


async def test_soak_multi_day():
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")
        s = await _stack(
            t,
            base_cfg(
                lottery_daily_limit=10,
                lottery_cost=1,
                lottery_passphrase="whl",
                lottery_tiers=json.dumps(
                    {
                        "tiers": [
                            {
                                "label": "参与奖",
                                "weight": 1,
                                "points_min": 0,
                                "points_max": 0,
                                "emoji": "",
                            }
                        ]
                    }
                ),
            ),
        )
        for i in range(3):
            await t.db.execute(
                "INSERT INTO users (qq, group_id, points) VALUES (?,?,100)",
                (f"u{i}", "G1"),
            )
        await t.dao.set_daily_keyword("G1", "红包", 1, "admin")

        # 模拟 3 个业务日：每天 3 人签到 + 每人 10 次抽奖（日限 10）+ 口令领取
        total_draws = 0
        for day in range(3):
            for i in range(3):
                r = await s["sign_in"].sign_in(f"u{i}", "G1", "aiocqhttp", "签到")
                assert not r["already_signed"], r
                claimed = await s["keyword"].check_and_claim(f"u{i}", "G1", "红包")
                assert claimed.get("claimed") or claimed.get("already"), claimed
                for _ in range(10):
                    r = await s["lottery"].draw(f"u{i}", "G1")
                    assert r["success"], r
                    total_draws += 1
                # 第 11 次超限
                r = await s["lottery"].draw(f"u{i}", "G1")
                assert not r["success"] and "上限" in r["msg"]
            # 模拟次日：把今日记录移到过去、回退签到日期（"昨天"按业务日计算）
            if day < 2:
                await t.db.execute(
                    "UPDATE lottery_record SET created_at='2000-01-01 04:00:00'"
                )
                from astrbot_plugin_point_system_by_whleague.utils.helpers import (
                    today_str,
                )

                yesterday = (
                    datetime.strptime(today_str(), "%Y-%m-%d") - timedelta(days=1)
                ).strftime("%Y-%m-%d")
                await t.db.execute(
                    "UPDATE users SET last_sign_date=? WHERE group_id='G1'",
                    (yesterday,),
                )
                await t.db.execute("DELETE FROM sign_in_log")
                await t.db.execute("DELETE FROM daily_keyword_claim")

        assert total_draws == 90  # 3 天 × 3 人 × 10 次
        assert await t.count("lottery_record") == 90
        # 仅最后一天 30 条属于"今日"
        rows = await t.db.fetchall(
            "SELECT COUNT(*) AS c FROM lottery_record WHERE created_at >= '2000-01-01 05:00:00'"
        )
        assert rows[0]["c"] == 30
        # 口令缓存淘汰：超过 256 上限后 stale 键被清除
        stale = {(f"G{99 - i}", "2000-01-01"): None for i in range(300)}
        s["keyword"]._cache.update(stale)
        await s["keyword"].check_and_claim("u0", "G1", "红包")
        assert not any(k[1] == "2000-01-01" for k in s["keyword"]._cache)
        assert len(s["keyword"]._cache) < 300
        # 限流器剪枝
        from astrbot_plugin_point_system_by_whleague.utils.rate_limiter import (
            RateLimiter,
        )

        lim = RateLimiter()
        for i in range(2500):
            lim._user_cooldowns[f"soak{i}"] = time.time() - 7200
        assert lim.check_user("x", "u0", "G1", 60) is True
        assert len(lim._user_cooldowns) <= 2048
    return "浸泡：3 个业务日限额重置/签到连签/口令/缓存淘汰/限流剪枝"


async def test_random_op_fuzz():
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")
        s = await _stack(t)
        seeds = {}
        for i in range(50):
            await t.db.execute(
                "INSERT INTO users (qq, group_id, points) VALUES (?,?,100)",
                (f"u{i}", "G1"),
            )
            seeds[(f"u{i}", "G1")] = 100
        item_id = await t.dao.add_item("商品", 1, -1)
        await t.dao.set_daily_keyword("G1", "红包", 1, "admin")

        rng = random.Random(42)
        unexpected = []
        for step in range(200):
            i = rng.randrange(50)
            kind = rng.randrange(6)
            try:
                if kind == 0:
                    await s["sign_in"].sign_in(f"u{i}", "G1", "aiocqhttp", "签到")
                elif kind == 1:
                    await s["lottery"].draw(f"u{i}", "G1")
                elif kind == 2:
                    await s["redeem"].redeem(
                        f"u{i}", "G1", item_id, rng.randrange(1, 5)
                    )
                elif kind == 3:
                    await s["keyword"].check_and_claim(f"u{i}", "G1", "红包")
                elif kind == 4:
                    # 非法负值（预期 ValueError）
                    await s["point"].add(f"u{i}", "G1", rng.randrange(-50, 0), "fuzz")
                else:
                    # 超额扣分：允许扣成负数（惩罚场景），对账不变量仍须成立
                    await s["point"].subtract(
                        f"u{i}", "G1", rng.randrange(1000, 100000), "fuzz_sub"
                    )
            except ValueError:
                pass  # 业务拒绝路径
            except Exception as e:  # noqa: BLE001
                unexpected.append((step, kind, type(e).__name__, str(e)))
                break
        assert not unexpected, unexpected
        await _assert_reconcile(t, seeds)
    return "随机 fuzz：200 步混合操作（含非法参数）不变量成立、无异常泄漏"


async def test_random_config_consistency():
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

        rng = random.Random(7)
        for g in range(30):
            cfg = base_cfg(
                signin_fixed_mode=rng.random() < 0.5,
                signin_fixed_points=rng.randrange(1, 50),
                signin_random_min=rng.randrange(1, 20),
                signin_random_max=rng.randrange(1, 20),  # 允许反向（业务层钳制）
                signin_first_bonus=rng.choice([0, 10, 50]),
                signin_day_first_bonus=rng.randrange(0, 30),
                signin_consecutive_max=rng.randrange(0, 30),
                signin_consecutive_bonus_per_day=rng.randrange(0, 5),
                signin_weekly_bonus=rng.choice([0, 100]),
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
            r = await svc.sign_in(f"u{g}", f"G{g}", "aiocqhttp", "签到")
            assert not r["already_signed"]
            log = await t.db.fetchone(
                "SELECT base_points, bonus_first_sign, bonus_day_first, bonus_consecutive, "
                "bonus_weekly, points_earned, easter_points, easter_event_type "
                "FROM sign_in_log WHERE qq=? AND group_id=?",
                (f"u{g}", f"G{g}"),
            )
            # 各组独立，全部为首签：日首签奖励必发
            expected = (
                log["base_points"]
                + log["bonus_first_sign"]
                + log["bonus_day_first"]
                + log["bonus_consecutive"]
                + log["bonus_weekly"]
            )
            assert log["points_earned"] == expected, (g, dict(log))
            assert r["points"] == expected
            assert log["easter_event_type"] is None and log["easter_points"] == 0
        # 对账：签到流水与余额一致
        await _assert_reconcile(t, {})
    return "随机配置：30 组随机配置下签到入账与奖励项严格自洽"


TESTS = [
    ("stress_mixed_concurrency", test_stress_mixed_concurrency_reconcile),
    ("soak_multi_day", test_soak_multi_day),
    ("random_op_fuzz", test_random_op_fuzz),
    ("random_config_consistency", test_random_config_consistency),
]
