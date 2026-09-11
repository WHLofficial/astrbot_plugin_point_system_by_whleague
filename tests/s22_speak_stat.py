"""S22 发言统计：静默计数、业务日边界、周期区间、排名画像、称号、跨群隔离、schema 迁移。"""

import types
from datetime import datetime, timedelta
from unittest import mock

from .common import (
    FakeBot,
    FakeEvent,
    TempDB,
    base_cfg,
    collect,
    restore_day_boundary,
    snapshot_day_boundary,
)


def _plugin(t, **over):
    """最小插件桩：发言统计只依赖 config_cache 与 speak_dao。"""
    from astrbot_plugin_point_system_by_whleague.db.speak_dao import SpeakDAO

    cfg = base_cfg(**{"speak_stat_enabled": True, **over})
    return types.SimpleNamespace(config_cache=cfg, speak_dao=SpeakDAO(t.db)), cfg


def _handler(plugin):
    from astrbot_plugin_point_system_by_whleague.handlers.speak_stat import (
        SpeakStatHandler,
    )

    return SpeakStatHandler(plugin)


async def _seed(t, qq, group_id, stat_date: str, count: int):
    """直接写一行明细（覆盖同日已有值），绕开 handler 造历史样本。"""
    await t.db.execute(
        "INSERT INTO speak_daily (qq, group_id, stat_date, msg_count) VALUES (?,?,?,?) "
        "ON CONFLICT(qq, group_id, stat_date) DO UPDATE SET msg_count=excluded.msg_count",
        (str(qq), str(group_id), stat_date, count),
    )


class _NoComponentEvent(FakeEvent):
    """notice/request 类协议事件：没有任何消息段。"""

    def get_messages(self):
        return []


class _FrozenDatetime(datetime):
    """冻结 now() 的真 datetime 子类（保证 date()/isoformat() 仍是真值）。"""

    fixed = None

    @classmethod
    def now(cls, tz=None):
        return cls.fixed


async def test_speak_count_hits():
    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        await h.handle(FakeEvent("1001", "G1", msg="大家好"))
        await h.handle(FakeEvent("1001", "G1", msg="", at_targets=["1002"]))  # 含 @
        await h.handle(FakeEvent("1001", "G1", msg=""))  # 纯图片/表情形态
        rows = await t.db.fetchall(
            "SELECT qq, stat_date, msg_count FROM speak_daily"
        )
        assert len(rows) == 1
        assert rows[0]["qq"] == "1001"
        assert rows[0]["msg_count"] == 3

        from astrbot_plugin_point_system_by_whleague.utils.helpers import today_str

        assert rows[0]["stat_date"] == today_str()
    return "发言统计：文本/@/纯图片消息均计一条，同日聚合成一行"


async def test_speak_invalid_sources_skipped():
    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        await h.handle(_NoComponentEvent("1001", "G1"))  # 真空消息（协议事件）
        await h.handle(FakeEvent("1001", None, msg="私聊消息"))  # 非群聊
        await h.handle(FakeEvent("", "G1", msg="无发送者"))  # 取不到 QQ
        assert await t.count("speak_daily") == 0
    return "发言统计：真空消息/非群聊/无发送者一律不计数"


async def test_speak_bot_self_skipped():
    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        await h.handle(FakeEvent("bot_self_qq", "G1", msg="我是机器人"))
        await h.handle(FakeEvent("9999", "G1", msg="我是机器人", self_qq="9999"))
        assert await t.count("speak_daily") == 0
        await h.handle(FakeEvent("1001", "G1", msg="我是群友"))
        assert await t.count("speak_daily") == 1
    return "发言统计：机器人自身消息不计数，群友消息正常计数"


async def test_speak_same_day_single_row():
    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        for i in range(5):
            await h.handle(FakeEvent("1001", "G1", msg=f"第{i}条"))
        rows = await t.db.fetchall("SELECT msg_count FROM speak_daily")
        assert len(rows) == 1  # UPSERT 而非逐条插入
        assert rows[0]["msg_count"] == 5
    return "发言统计：同日多次发言只有一行且累加"


async def test_speak_disabled_no_write():
    async with TempDB() as t:
        plugin, _ = _plugin(t, speak_stat_enabled=False)
        h = _handler(plugin)
        await h.handle(FakeEvent("1001", "G1", msg="关闭开关时的发言"))
        assert await t.count("speak_daily") == 0

        # 开关只关写入热路径：历史数据仍可查
        from astrbot_plugin_point_system_by_whleague.utils.helpers import today_str

        await _seed(t, "1001", "G1", today_str(), 4)
        text = "\n".join(await collect(h.handle_query(FakeEvent("1001", "G1"))))
        assert "累计发言: 4 条" in text
    return "发言统计：speak_stat_enabled=false 不写库，但历史仍可查询"


async def test_speak_business_day_boundary():
    from astrbot_plugin_point_system_by_whleague.utils import helpers as helpers_mod

    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        snap = snapshot_day_boundary()
        try:
            helpers_mod.set_day_boundary("04:00")
            with mock.patch.object(helpers_mod, "datetime", _FrozenDatetime):
                _FrozenDatetime.fixed = datetime(2026, 9, 11, 3, 59, 30)
                assert helpers_mod.today_str() == "2026-09-10"
                await h.handle(FakeEvent("1001", "G1", msg="凌晨发言"))
                _FrozenDatetime.fixed = datetime(2026, 9, 11, 4, 1, 0)
                assert helpers_mod.today_str() == "2026-09-11"
                await h.handle(FakeEvent("1001", "G1", msg="早上发言"))
        finally:
            restore_day_boundary(snap)

        rows = await t.db.fetchall(
            "SELECT stat_date, msg_count FROM speak_daily ORDER BY stat_date"
        )
        assert [r["stat_date"] for r in rows] == ["2026-09-10", "2026-09-11"]
        assert all(r["msg_count"] == 1 for r in rows)
    return "发言统计：04:00 分界下 03:59 与 04:01 落入不同业务日"


async def test_speak_period_totals():
    from astrbot_plugin_point_system_by_whleague.utils.helpers import (
        month_bounds,
        today_str,
        week_start_str,
    )

    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        today = today_str()
        week_from = week_start_str()
        month_from, month_to = month_bounds(0)
        prev_from, prev_to = month_bounds(-1)
        old = (datetime.fromisoformat(today) - timedelta(days=100)).date().isoformat()
        # 写库语义为同日覆盖，期望值同样按「后来者覆盖」计算
        by_date = {today: 3, week_from: 5, month_from: 7, prev_to: 11, old: 13}
        for date_str, n in by_date.items():
            await _seed(t, "1001", "G1", date_str, n)

        def in_range(start: str, end: str) -> int:
            return sum(n for d, n in by_date.items() if start <= d <= end)

        text = "\n".join(await collect(h.handle_query(FakeEvent("1001", "G1"))))
        assert f"· 今日发言: {in_range(today, today)} 条" in text
        assert f"· 本周发言: {in_range(week_from, today)} 条" in text
        assert f"· 本月发言: {in_range(month_from, month_to)} 条" in text
        assert f"· 上月发言: {in_range(prev_from, prev_to)} 条" in text
        assert f"· 累计发言: {sum(by_date.values())} 条" in text
    return "发言统计：今日/本周/本月/上月/累计五档区间口径正确（含跨月样本）"


async def test_speak_ranking_and_profile():
    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        from astrbot_plugin_point_system_by_whleague.utils.helpers import today_str

        for qq, n in (("1001", 100), ("1002", 50), ("1003", 50)):
            await _seed(t, qq, "100001", today_str(), n)
        await _seed(t, "2001", "200001", today_str(), 999)  # 跨群不进本群榜

        text = "\n".join(
            await collect(
                h.handle_query(FakeEvent("1001", "100001", bot=FakeBot(member_card="甲")))
            )
        )
        assert "💬 甲 (1001)" in text
        assert "· 累计发言: 100 条 · 本群第 1 名" in text
        assert "占本群 50.0%，超过 67% 的群友" in text  # 100/200；round(2/3*100)

        # 同分同名次：1002 与 1003 并列第 2，没人比它少
        text2 = "\n".join(await collect(h.handle_query(FakeEvent("1002", "100001"))))
        assert "· 累计发言: 50 条 · 本群第 2 名" in text2
        assert "超过 0% 的群友" in text2
        assert "999" not in text2
    return "发言统计：本群排名/同分并列/占比/百分位正确，跨群数据不计入"


async def test_speak_titles_thresholds():
    from astrbot_plugin_point_system_by_whleague.utils.speak_titles import (
        DEFAULT_TITLES,
        parse_title_rules,
        resolve_title,
    )

    # 闭区间下界：恰好等于阈值即命中
    for total, expected in (
        (0, "🌱 群内新面孔"),
        (1, "🌱 群内新面孔"),
        (19, "🌱 群内新面孔"),
        (20, "💬 常驻群友"),
        (99, "💬 常驻群友"),
        (100, "🗣 话题担当"),
        (399, "🗣 话题担当"),
        (400, "🔥 聊天主力"),
        (1200, "🌊 水群之王"),
        (4000, "👑 群聊之魂"),
        (11999, "👑 群聊之魂"),
        (12000, "🏆 传说话痨"),
    ):
        assert resolve_title(total, None) == expected, total
        assert resolve_title(total, []) == expected, total

    # 配置覆盖：非法项丢弃、同阈值保留先出现的、按阈值降序命中
    rules = parse_title_rules(
        ["10:🥇 大佬", "abc", "30:", "0:零档", "-3:负档", "无冒号", "10:重复"]
    )
    assert rules == [(10, "🥇 大佬")]
    assert resolve_title(9, ["10:🥇 大佬"]) == "🌱 群内新面孔"
    assert resolve_title(10, ["10:🥇 大佬"]) == "🥇 大佬"
    assert parse_title_rules(["5:低档", "50:高档"]) == [(50, "高档"), (5, "低档")]

    # 整表全非法 → 回退内置默认档位
    assert resolve_title(100, ["abc", None]) == DEFAULT_TITLES[2][1]
    assert resolve_title(100, "不是 json 也不是档位") == DEFAULT_TITLES[2][1]

    async with TempDB() as t:
        plugin, _ = _plugin(t, speak_stat_titles=["10:🥇 大佬"])
        h = _handler(plugin)
        from astrbot_plugin_point_system_by_whleague.utils.helpers import today_str

        await _seed(t, "1001", "G1", today_str(), 10)
        text = "\n".join(await collect(h.handle_query(FakeEvent("1001", "G1"))))
        assert "· 发言称号: 🥇 大佬" in text
    return "发言统计：称号 7 档阈值边界/配置覆盖/非法回退全部正确"


async def test_speak_count_error_swallowed():
    from astrbot_plugin_point_system_by_whleague.handlers.speak_stat import (
        SpeakStatHandler,
    )
    from astrbot_plugin_point_system_by_whleague.main import PointSystemPlugin

    class _BoomDAO:
        async def record(self, *args):
            raise RuntimeError("db down")

    calls = []

    class _Recorder:
        async def handle(self, event):
            calls.append("reward")

    obj = PointSystemPlugin.__new__(PointSystemPlugin)
    cfg = base_cfg(speak_stat_enabled=True)
    obj.config_cache = cfg
    obj.speak_stat_handler = SpeakStatHandler(
        types.SimpleNamespace(config_cache=cfg, speak_dao=_BoomDAO())
    )
    obj.active_reward_handler = _Recorder()

    await obj.on_group_message(FakeEvent("1001", "G1", msg="计数写库会炸"))
    assert calls == ["reward"]  # 计数异常被吞掉，后续 handler 照常执行
    return "发言统计：计数抛异常时被吞掉，活跃奖励仍被执行"


async def test_speak_hook_order():
    from astrbot_plugin_point_system_by_whleague.main import PointSystemPlugin

    calls = []

    class _Recorder:
        def __init__(self, tag):
            self.tag = tag

        async def handle(self, event):
            calls.append(self.tag)

    obj = PointSystemPlugin.__new__(PointSystemPlugin)
    obj.speak_stat_handler = _Recorder("speak")
    obj.active_reward_handler = _Recorder("reward")
    ev = FakeEvent("1001", "G1", msg="普通发言")
    # 计数是独立入口（main.py 里 priority>0），真实宿主中先于全部触发 handler 执行
    await obj.on_group_message_count(ev)
    assert calls == ["speak"]
    await obj.on_group_message(ev)
    assert calls == ["speak", "reward"]
    return "群消息钩子：计数与活跃奖励拆成两个入口，计数单独高优先级执行"


async def test_speak_cross_group_isolated():
    from astrbot_plugin_point_system_by_whleague.utils.helpers import today_str

    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        await h.handle(FakeEvent("1001", "G1", msg="一群发言"))
        await h.handle(FakeEvent("1001", "G2", msg="二群发言"))
        await h.handle(FakeEvent("1001", "G2", msg="二群再发言"))
        rows = await t.db.fetchall(
            "SELECT group_id, msg_count FROM speak_daily ORDER BY group_id"
        )
        assert [(r["group_id"], r["msg_count"]) for r in rows] == [("G1", 1), ("G2", 2)]

        # 群内查询只看本群
        await _seed(t, "1002", "G1", today_str(), 9)
        text = "\n".join(await collect(h.handle_query(FakeEvent("1001", "G1"))))
        assert "累计发言: 1 条 · 本群第 2 名" in text
        text2 = "\n".join(await collect(h.handle_query(FakeEvent("1001", "G2"))))
        assert "累计发言: 2 条 · 本群第 1 名" in text2
    return "发言统计：同人在不同群各自独立计数、独立排名"


async def test_speak_trigger_strict_match():
    from astrbot_plugin_point_system_by_whleague.utils.keyword_matcher import (
        is_my_speak_message,
    )

    assert is_my_speak_message("我的发言")
    assert is_my_speak_message("发言统计")
    assert is_my_speak_message(" 我的发言 ")
    assert is_my_speak_message("我 的 发 言")
    for bad in (
        "查我的发言",
        "我的发言！",
        "我的发言记录",
        "发言统计一下",
        "发言",
        "我的积分",
        "",
        None,
    ):
        assert not is_my_speak_message(bad), bad
    return "发言统计：触发词严格匹配（附加文本/标点/近义词均不触发）"


async def test_speak_route():
    from astrbot_plugin_point_system_by_whleague.main import PointSystemPlugin

    class _Handler:
        def __init__(self):
            self.calls = 0

        async def handle_query(self, event):
            self.calls += 1
            yield event.plain_result("我的发言")

    obj = PointSystemPlugin.__new__(PointSystemPlugin)
    obj.config_cache = base_cfg()
    obj.speak_stat_handler = _Handler()

    msgs = await collect(
        obj.on_my_speak(FakeEvent("1001", "G1", msg="查我的发言", at_wake=True))
    )
    assert msgs == [] and obj.speak_stat_handler.calls == 0
    msgs = await collect(obj.on_my_speak(FakeEvent("1001", "G1", msg="我的发言")))
    assert msgs == ["我的发言"] and obj.speak_stat_handler.calls == 1
    msgs = await collect(obj.on_my_speak(FakeEvent("1001", "G1", msg="发言统计")))
    assert msgs == ["我的发言"] and obj.speak_stat_handler.calls == 2
    return "我的发言路由：严格匹配触发并委托 handler"


async def test_speak_query_edges_and_track():
    from astrbot_plugin_point_system_by_whleague.utils.helpers import today_str

    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)

        # 非群聊
        msgs = await collect(h.handle_query(FakeEvent("1001", None)))
        assert msgs == ["我的发言仅支持群聊"]
        # 本群无记录
        msgs = await collect(h.handle_query(FakeEvent("1001", "G1")))
        assert msgs == ["你还没有发言记录"]

        # 活跃轨迹：昨天/前天连续，中间断一天后另有更早记录
        today = datetime.fromisoformat(today_str()).date()
        days = [today, today - timedelta(days=1), today - timedelta(days=2),
                today - timedelta(days=5)]
        for i, d in enumerate(days):
            await _seed(t, "1001", "G1", d.isoformat(), 3 + i)
        text = "\n".join(await collect(h.handle_query(FakeEvent("1001", "G1"))))
        assert "· 活跃轨迹: 最近 " in text
        assert "· 活跃 4 天 · 连续 3 天 · 最高单日 6 条" in text
        assert "发言称号" in text
        assert "· 今日发言: 3 条" in text
    return "我的发言：非群/无记录提示与活跃轨迹（活跃天数/连续/最高单日）"


async def test_speak_not_rewarded():
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

    async with TempDB() as t:
        cfg = base_cfg(
            active_reward_enabled=True,
            active_reward_min_length=3,
            active_reward_cooldown=60,
            active_reward_global_cooldown=10,
            active_reward_probability=1.0,
            active_reward_points_min=1,
            active_reward_points_max=5,
        )
        ps = PointService(t.db, t.dao)
        handler = ActiveRewardHandler(
            types.SimpleNamespace(
                config_cache=cfg,
                rate_limiter=RateLimiter(),
                point_service=ps,
                daily_keyword_service=DailyKeywordService(t.db, t.dao, ps),
            )
        )
        for msg in ("我的发言", "发言统计"):
            await handler.handle(FakeEvent("1001", "G1", msg=msg))
        assert await t.dao.get_user("1001", "G1") is None  # 不能靠反复查询刷活跃奖励
    return "活跃奖励：我的发言/发言统计消息跳过，防止刷分"


async def test_speak_schema_migration():
    from astrbot_plugin_point_system_by_whleague.db.schema import (
        SCHEMA_VERSION,
        init_schema,
    )
    from astrbot_plugin_point_system_by_whleague.db.speak_dao import SpeakDAO

    async with TempDB() as t:
        assert SCHEMA_VERSION == 6
        # 模拟旧库：无 speak_daily 表、版本号为 5
        await t.db.execute("DROP TABLE speak_daily")
        await t.db.execute("UPDATE plugin_config SET value='5' WHERE key='schema_version'")
        await init_schema(t.db)

        row = await t.db.fetchone(
            "SELECT value FROM plugin_config WHERE key='schema_version'"
        )
        assert row["value"] == "6"
        dao = SpeakDAO(t.db)
        await dao.record("1001", "G1", "2026-09-11")
        assert await dao.get_total("1001", "G1") == 1
        # 幂等：重复初始化不报错、不清数据
        await init_schema(t.db)
        assert await dao.get_total("1001", "G1") == 1
    return "发言统计：旧库（version=5）启动即建表并升到 6，无需迁移分支"


TESTS = [
    ("speak_count_hits", test_speak_count_hits),
    ("speak_invalid_sources", test_speak_invalid_sources_skipped),
    ("speak_bot_self_skipped", test_speak_bot_self_skipped),
    ("speak_same_day_single_row", test_speak_same_day_single_row),
    ("speak_disabled_no_write", test_speak_disabled_no_write),
    ("speak_business_day_boundary", test_speak_business_day_boundary),
    ("speak_period_totals", test_speak_period_totals),
    ("speak_ranking_and_profile", test_speak_ranking_and_profile),
    ("speak_titles_thresholds", test_speak_titles_thresholds),
    ("speak_count_error_swallowed", test_speak_count_error_swallowed),
    ("speak_hook_order", test_speak_hook_order),
    ("speak_cross_group_isolated", test_speak_cross_group_isolated),
    ("speak_trigger_strict_match", test_speak_trigger_strict_match),
    ("speak_route", test_speak_route),
    ("speak_query_edges_and_track", test_speak_query_edges_and_track),
    ("speak_not_rewarded", test_speak_not_rewarded),
    ("speak_schema_migration", test_speak_schema_migration),
]
