"""S24 排行榜剔除已退群成员：成员列表过滤、降级不过滤、自排名/发言榜同口径。

背景：users 关联行在退群后长期残留（群清空也刻意保留），排行榜原先只按
users.group_id 过滤，退群账号会一直挂在榜上（昵称拿不到回退 QQ 号）。
修复口径：展示时实时拉 get_group_member_list 过滤；接口不可用时降级不过滤。
"""

import types

from .common import FakeBot, FakeEvent, TempDB, base_cfg, collect


def _texts(event) -> list:
    return [str(c) for c in event.sent]


def _ranking_plugin(t):
    from astrbot_plugin_point_system_by_whleague.services.ranking_service import (
        RankingService,
    )

    return types.SimpleNamespace(
        ranking_service=RankingService(t.dao), config_cache=base_cfg()
    )


def _speak_plugin(t, **over):
    from astrbot_plugin_point_system_by_whleague.db.speak_dao import SpeakDAO

    cfg = base_cfg(**{"speak_stat_enabled": True, **over})
    return types.SimpleNamespace(config_cache=cfg, speak_dao=SpeakDAO(t.db)), cfg


async def _seed_points(t, qq, group_id, points):
    await t.db.execute(
        "INSERT INTO accounts (qq, points) VALUES (?,?)", (qq, points)
    )
    await t.db.execute(
        "INSERT INTO users (qq, group_id) VALUES (?,?)", (qq, group_id)
    )


async def _seed_speak(t, qq, group_id, total):
    await t.db.execute(
        "INSERT INTO speak_daily (qq, group_id, stat_date, msg_count) VALUES "
        "(?,?,date('now','localtime'),?) "
        "ON CONFLICT(qq, group_id, stat_date) DO UPDATE SET "
        "msg_count=excluded.msg_count",
        (str(qq), str(group_id), total),
    )


async def test_fetch_member_qqs_contract():
    from astrbot_plugin_point_system_by_whleague.utils.group_info import (
        fetch_member_qqs,
    )

    # 正常返回：QQ 转 str 集合
    qqs = await fetch_member_qqs(FakeBot(member_qqs={"1", "2"}), "100000")
    assert qqs == {"1", "2"}
    # 未配置成员列表（接口返回 None）→ 无法判定，不过滤
    assert await fetch_member_qqs(FakeBot(), "100000") is None
    # 无 call_action 的 bot → None
    assert await fetch_member_qqs(object(), "100000") is None
    # bot=None → None
    assert await fetch_member_qqs(None, "100000") is None

    # 协议端抛异常 → None（静默降级）
    class _BrokenBot:
        async def call_action(self, action, **kwargs):
            raise RuntimeError("network down")

    assert await fetch_member_qqs(_BrokenBot(), "100000") is None

    # 返回空列表（异常形态）→ None；列表混入脏项只取合法 user_id
    class _WeirdBot:
        def __init__(self, payload):
            self._payload = payload

        async def call_action(self, action, **kwargs):
            return self._payload

    assert await fetch_member_qqs(_WeirdBot([]), "100000") is None
    assert await fetch_member_qqs(_WeirdBot("not-a-list"), "100000") is None
    assert await fetch_member_qqs(
        _WeirdBot([{"user_id": 7}, "garbage", {}]), "100000"
    ) == {"7"}
    return "成员列表获取：正常转 str 集合，None/异常/非列表/空列表一律降级为 None"


async def test_ranking_filters_left_members():
    from astrbot_plugin_point_system_by_whleague.handlers.ranking import (
        RankingHandler,
    )

    async with TempDB() as t:
        # u1/u3 已退群（不在成员列表），users 行残留且积分够高
        for qq, pts in (("u0", 100), ("u1", 80), ("u2", 60), ("u3", 40), ("u4", 20)):
            await _seed_points(t, qq, "100000", pts)
        handler = RankingHandler(_ranking_plugin(t))
        ev = FakeEvent(
            "u2",
            "100000",
            msg="/排行",
            bot=FakeBot(member_qqs={"u0", "u2", "u4"}),
        )
        text = "\n".join(await collect(handler.handle(ev)))
        assert "本群排行" in text
        assert "u0" in text and "u2" in text and "u4" in text
        assert "u1" not in text and "u3" not in text
        assert "(Top 3)" in text  # 过滤后剩 3 人，仍 ≥3 不回退全局
        # 自排名：u1（80 分，退群）不占位 → u2 第 2 名（不过滤应为第 3）
        assert "第 2 名" in text and "60 积分" in text
    return "本群积分榜：退群账号不出现在榜单，名次补位、自排名同口径剔除"


async def test_ranking_keeps_all_when_member_list_unavailable():
    from astrbot_plugin_point_system_by_whleague.handlers.ranking import (
        RankingHandler,
    )

    async with TempDB() as t:
        for qq, pts in (("u0", 100), ("u1", 80), ("u2", 60), ("u3", 40), ("u4", 20)):
            await _seed_points(t, qq, "100000", pts)
        handler = RankingHandler(_ranking_plugin(t))
        # 无成员列表能力（默认 bot 无 call_action）：降级不过滤，榜照常可用
        ev = FakeEvent("u2", "100000", msg="/排行")
        text = "\n".join(await collect(handler.handle(ev)))
        assert "(Top 5)" in text and "u1" in text and "u3" in text
        assert "第 3 名" in text
        # 接口返回 None（FakeBot 未配置成员列表）同样降级
        ev2 = FakeEvent("u2", "100000", msg="/排行", bot=FakeBot())
        text2 = "\n".join(await collect(handler.handle(ev2)))
        assert "(Top 5)" in text2 and "u1" in text2
    return "降级口径：成员接口不可用时不过滤，榜单与自排名保持旧行为"


async def test_ranking_falls_back_to_global_after_filter():
    from astrbot_plugin_point_system_by_whleague.handlers.ranking import (
        RankingHandler,
    )

    async with TempDB() as t:
        # 本群 3 人中 2 人已退群：过滤后仅 1 人 <3，应回退全局榜
        await _seed_points(t, "a1", "100000", 50)
        await _seed_points(t, "a2", "100000", 40)
        await _seed_points(t, "a3", "100000", 30)
        await _seed_points(t, "g1", "200000", 45)
        handler = RankingHandler(_ranking_plugin(t))
        ev = FakeEvent("a1", "100000", msg="/排行", bot=FakeBot(member_qqs={"a1"}))
        text = "\n".join(await collect(handler.handle(ev)))
        assert "全局排行" in text and "g1" in text
        # 全局榜是跨群口径，不过滤：退群的 a2/a3 仍可见（挂各自最近活跃群）
        assert "a2" in text and "a3" in text
    return "回退阈值在过滤后判断：本群剩 1 人回退全局；全局榜跨群口径不过滤"


async def test_dao_rank_in_group_with_member_filter():
    async with TempDB() as t:
        for qq, pts in (("u1", 100), ("u2", 60), ("u3", 60)):
            await _seed_points(t, qq, "100000", pts)
        # 无过滤：u2 第 2（u1 更高，u3 同分不占位）
        assert await t.dao.get_rank_in_group("u2", "100000") == 2
        # u1 已退群：u2 升到第 1
        assert await t.dao.get_rank_in_group("u2", "100000", member_qqs={"u2", "u3"}) == 1
        # 集合含 u1（都在群）：回到第 2
        assert (
            await t.dao.get_rank_in_group("u2", "100000", member_qqs={"u1", "u2", "u3"})
            == 2
        )
        # 同分并列不受过滤影响：u3 与 u2 同为第 1
        assert await t.dao.get_rank_in_group("u3", "100000", member_qqs={"u2", "u3"}) == 1
        # 自己已退群（不在成员集合）：仍能出排名（触发者必然在群，防御口径）
        assert await t.dao.get_rank_in_group("u1", "100000", member_qqs={"u2"}) == 1
    return "DAO 自排名：member_qqs 只统计在群的更高分者，None 时保持 COUNT 老口径"


async def test_speak_query_and_milestone_filter_left_members():
    from astrbot_plugin_point_system_by_whleague.handlers.speak_stat import (
        SpeakStatHandler,
    )

    async with TempDB() as t:
        plugin, _ = _speak_plugin(
            t, speak_milestone_enabled=True, speak_milestone_template=None
        )
        handler = SpeakStatHandler(plugin)
        # u1 已退群但历史发言最多；u2/u3 在群
        await _seed_speak(t, "u1", "100000", 100)
        await _seed_speak(t, "u2", "100000", 50)
        await _seed_speak(t, "u3", "100000", 30)
        bot = FakeBot(member_qqs={"u2", "u3"})

        # 「我的发言」：u2 第 1 名（不过滤应为第 2）；群总量 80（不含退群者 100）
        ev = FakeEvent("u2", "100000", msg="我的发言", bot=bot)
        text = "\n".join(await collect(handler.handle_query(ev)))
        assert "本群第 1 名" in text
        assert "占本群 62.5%" in text  # 50 / (50+30)

        # 里程碑播报同口径：u3 越过 20 档，名次取过滤后的第 2（不过滤应为第 3）
        ev2 = FakeEvent("u3", "100000", msg="发言", bot=bot)
        await handler._announce_milestone(ev2, "u3", "100000", 30)
        assert len(ev2.sent) == 1
        assert "本群第 2 名" in _texts(ev2)[0]
    return "发言榜同口径：「我的发言」与里程碑播报的名次/群总量均剔除退群者"


TESTS = [
    ("fetch_member_qqs_contract", test_fetch_member_qqs_contract),
    ("ranking_filters_left_members", test_ranking_filters_left_members),
    (
        "ranking_keeps_all_when_member_list_unavailable",
        test_ranking_keeps_all_when_member_list_unavailable,
    ),
    ("ranking_falls_back_to_global_after_filter", test_ranking_falls_back_to_global_after_filter),
    ("dao_rank_in_group_member_filter", test_dao_rank_in_group_with_member_filter),
    (
        "speak_query_and_milestone_filter_left_members",
        test_speak_query_and_milestone_filter_left_members,
    ),
]
