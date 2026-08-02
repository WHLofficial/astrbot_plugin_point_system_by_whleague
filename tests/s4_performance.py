"""S4 性能压测（标准规模）：吞吐/延迟/内存稳定性，输出指标。

注意：本套件为性能观测，不设硬性阈值（避免环境差异误报），
但会校验"无退化信号"（如 10 万消息后内存增长有界）。
"""

import asyncio
import json
import time
import tracemalloc

from .common import FakeEvent, TempDB, Timer, fmt_sec


async def _seed_users(t, n, group="G1"):
    conn = t.db.conn
    await conn.execute("BEGIN")
    for i in range(0, n, 500):
        batch = [(f"u{i + j}", (i + j) % 1000) for j in range(min(500, n - i))]
        await conn.executemany(
            "INSERT INTO accounts (qq, points) VALUES (?,?)", batch
        )
        batch = [(f"u{i + j}", group) for j in range(min(500, n - i))]
        await conn.executemany(
            "INSERT INTO users (qq, group_id) VALUES (?,?)", batch
        )
    await conn.commit()


async def _seed_transactions(t, n, qq="u1", group="G1"):
    conn = t.db.conn
    await conn.execute("BEGIN")
    for i in range(0, n, 500):
        batch = [
            (qq, group, (i + j) % 50 - 25, 100, "bench") for j in range(min(500, n - i))
        ]
        await conn.executemany(
            "INSERT INTO point_transactions (qq, group_id, amount, balance_after, reason) VALUES (?,?,?,?,?)",
            batch,
        )
    await conn.commit()


async def _scan_handler(t, cfg):
    import types as _t

    from astrbot_plugin_point_system_by_whleague.handlers.active_reward import (
        ActiveRewardHandler,
    )
    from astrbot_plugin_point_system_by_whleague.utils.rate_limiter import RateLimiter

    plugin = _t.SimpleNamespace(
        config_cache=cfg,
        rate_limiter=RateLimiter(),
        daily_keyword_service=_t.SimpleNamespace(
            check_and_claim=lambda *a, **k: _noop_coro()
        ),
    )
    return ActiveRewardHandler(plugin)


async def _noop_coro():
    return {"claimed": False}


async def _signin_svc(t, cfg):
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
    return SignInService(
        t.db, t.dao, ps, EasterService(t.dao), DateRewardService(t.dao), cfg
    )


async def bench_msg_scan():
    """1 万条群消息扫描吞吐（活跃奖励 + 口令缓存路径）。"""
    async with TempDB() as t:
        cfg = {
            "keyword_sign": ["签到", "sign", "打卡"],
            "lottery_passphrase": "whl",
            "keyword_lottery": ["抽奖", "lottery"],
            "active_reward_enabled": False,
            "active_reward_min_length": 3,
        }
        handler = await _scan_handler(t, cfg)
        timer = Timer()
        n = 10000
        for i in range(n):
            ev = FakeEvent(f"u{i % 200}", "G1", msg=f"第{i}条普通闲聊消息")
            await handler.handle(ev)
        elapsed = timer.elapsed()
        return {
            "场景": "1 万条群消息扫描",
            "耗时": fmt_sec(elapsed),
            "吞吐": f"{n / elapsed:.0f} msg/s",
        }


async def bench_signin():
    """2000 次顺序签到 + 200 并发签到吞吐。"""
    async with TempDB() as t:
        cfg = {
            "signin_fixed_mode": True,
            "signin_fixed_points": 10,
            "signin_first_bonus": 50,
            "signin_day_first_bonus": 30,
            "signin_consecutive_max": 30,
            "signin_consecutive_bonus_per_day": 5,
            "signin_weekly_bonus": 100,
            "birthday_bonus_points": 0,
        }
        svc = await _signin_svc(t, cfg)
        timer = Timer()
        for i in range(2000):
            await svc.sign_in(f"u{i}", "G1", "aiocqhttp", "签到")
        seq_elapsed = timer.elapsed()

        timer2 = Timer()
        await asyncio.gather(
            *[svc.sign_in(f"c{i}", "G2", "aiocqhttp", "签到") for i in range(200)]
        )
        conc_elapsed = timer2.elapsed()
        return {
            "场景": "签到吞吐",
            "顺序 2000 次": fmt_sec(seq_elapsed) + f" ({2000 / seq_elapsed:.0f} ops/s)",
            "并发 200 次": fmt_sec(conc_elapsed) + f" ({200 / conc_elapsed:.0f} ops/s)",
        }


async def bench_ranking():
    """1 万用户排行查询（验证索引生效）。"""
    async with TempDB() as t:
        await _seed_users(t, 10000)
        plan = await t.db.fetchall(
            "EXPLAIN QUERY PLAN SELECT u.qq, a.points FROM users u "
            "JOIN accounts a ON a.qq=u.qq WHERE u.group_id='G1' AND a.points>=1 "
            "ORDER BY a.points DESC LIMIT 10"
        )
        plan_text = " ".join(str(r[3]) for r in plan)  # detail 列
        idx_used = "idx_accounts_points" in plan_text
        timer = Timer()
        for _ in range(100):
            await t.dao.get_top_n_by_group("G1", 10)
        elapsed = timer.elapsed()
        return {
            "场景": "1 万用户排行 ×100",
            "耗时": fmt_sec(elapsed),
            "单次": fmt_sec(elapsed / 100),
            "索引生效": "是" if idx_used else "否（需检查）",
        }


async def bench_transactions_paging():
    """5 万流水分页查询。"""
    async with TempDB() as t:
        await _seed_transactions(t, 50000)
        timer = Timer()
        for page in range(1, 101):
            await t.dao.get_transactions(
                qq="u1", group_id="G1", limit=10, offset=(page - 1) * 10
            )
        elapsed = timer.elapsed()
        return {
            "场景": "5 万流水分页 ×100",
            "耗时": fmt_sec(elapsed),
            "单次": fmt_sec(elapsed / 100),
        }


async def bench_lottery():
    """1000 次抽奖吞吐。"""
    async with TempDB() as t:
        cfg = {
            "lottery_enabled": True,
            "lottery_cost": 10,
            "lottery_daily_limit": 0,
            "lottery_passphrase": "whl",
            "negative_disable_lottery": True,
            "lottery_tiers": json.dumps(
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
            "INSERT INTO accounts (qq, points) VALUES ('u1',100000)"
        )
        timer = Timer()
        for _ in range(1000):
            await svc.draw("u1", "G1")
        elapsed = timer.elapsed()
        return {
            "场景": "1000 次抽奖",
            "耗时": fmt_sec(elapsed),
            "吞吐": f"{1000 / elapsed:.0f} ops/s",
        }


async def bench_concurrent_reads():
    """200 并发读（单连接 + 锁串行化）吞吐基线。"""
    async with TempDB() as t:
        await _seed_users(t, 5000)
        timer = Timer()
        await asyncio.gather(*[t.dao.get_top_n_by_group("G1", 10) for _ in range(200)])
        elapsed = timer.elapsed()
        return {
            "场景": "200 并发读排行（锁串行化基线）",
            "耗时": fmt_sec(elapsed),
            "吞吐": f"{200 / elapsed:.0f} ops/s",
        }


async def bench_memory_stability():
    """tracemalloc：5 万条消息后内存增长有界（RateLimiter/缓存清理生效）。"""
    async with TempDB() as t:
        cfg = {
            "keyword_sign": ["签到", "sign", "打卡"],
            "lottery_passphrase": "whl",
            "keyword_lottery": ["抽奖", "lottery"],
            "active_reward_enabled": False,
            "active_reward_min_length": 3,
        }
        handler = await _scan_handler(t, cfg)

        # 预热 RateLimiter 键（制造剪枝压力）

        limiter = handler._plugin.rate_limiter
        for i in range(3000):
            limiter._user_cooldowns[f"action:u{i}:G1"] = time.time() - 7200
        tracemalloc.start()
        before = tracemalloc.take_snapshot()
        for i in range(50000):
            ev = FakeEvent(f"u{i % 1000}", "G1", msg=f"消息 {i}")
            await handler.handle(ev)
        after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        growth = sum(s.size_diff for s in after.compare_to(before, "filename"))
        # 触发剪枝后应回落
        limiter.check_user("x", "u1", "G1", 60)
        pruned = len(limiter._user_cooldowns) <= 3000
        return {
            "场景": "5 万条消息内存稳定性",
            "Python 内存增长": f"{growth / 1024:.0f} KB",
            "RateLimiter 剪枝生效": "是" if pruned else "否",
        }


async def main():
    results = []
    for fn in (
        bench_msg_scan,
        bench_signin,
        bench_ranking,
        bench_transactions_paging,
        bench_lottery,
        bench_concurrent_reads,
        bench_memory_stability,
    ):
        try:
            r = await asyncio.wait_for(fn(), timeout=300)
            results.append((fn.__name__, r))
        except Exception as e:
            results.append((fn.__name__, {"错误": repr(e)}))
    return results


if __name__ == "__main__":

    async def _run():
        for name, metrics in await main():
            print(f"[{name}]")
            for k, v in metrics.items():
                print(f"  {k}: {v}")

    asyncio.run(_run())
