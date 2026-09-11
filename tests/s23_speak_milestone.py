"""S23 发言里程碑播报：逐档幂等、惰性静默基线、一次只报最高档、文案渲染。

档位与称号同源（speak_stat_titles），播报本体只依赖 config_cache + speak_dao。
"""

import asyncio
import json
import os
import types
from unittest import mock

from .common import PLUGIN_ROOT, FakeEvent, TempDB, base_cfg


def _plugin(t, **over):
    """最小插件桩：里程碑播报只依赖 config_cache 与 speak_dao。"""
    from astrbot_plugin_point_system_by_whleague.db.speak_dao import SpeakDAO

    cfg = base_cfg(
        **{
            "speak_stat_enabled": True,
            "speak_milestone_enabled": True,
            **over,
        }
    )
    return types.SimpleNamespace(config_cache=cfg, speak_dao=SpeakDAO(t.db)), cfg


def _handler(plugin):
    from astrbot_plugin_point_system_by_whleague.handlers.speak_stat import (
        SpeakStatHandler,
    )

    return SpeakStatHandler(plugin)


async def _seed(t, qq, group_id, count: int):
    """直接写一行今日明细（覆盖同日已有值），绕开 handler 造历史样本。"""
    from astrbot_plugin_point_system_by_whleague.utils.helpers import today_str

    await t.db.execute(
        "INSERT INTO speak_daily (qq, group_id, stat_date, msg_count) VALUES (?,?,?,?) "
        "ON CONFLICT(qq, group_id, stat_date) DO UPDATE SET msg_count=excluded.msg_count",
        (str(qq), str(group_id), today_str(), count),
    )


async def _milestones(t, qq, group_id) -> list:
    rows = await t.db.fetchall(
        "SELECT milestone FROM speak_milestone_log WHERE qq=? AND group_id=? "
        "ORDER BY milestone",
        (str(qq), str(group_id)),
    )
    return [int(r["milestone"]) for r in rows]


def _texts(event) -> list:
    return [str(c) for c in event.sent]


async def test_milestone_record_claim():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.db.speak_dao import SpeakDAO
        from astrbot_plugin_point_system_by_whleague.utils.speak_milestones import (
            parse_milestones,
        )

        dao = SpeakDAO(t.db)
        # 不传档位 = 纯计数，只回累计数
        assert await dao.record_and_claim("1001", "G1", "2026-09-11") == (1, None)
        # 跨群不合并
        assert await dao.record_and_claim("1001", "G2", "2026-09-11") == (1, None)
        # 同一次调用里完成计数与取档：第 2 条恰好跨过 2 档（最低档 1 不播报）
        tiers = parse_milestones(["1:🌱 新面孔", "2:🥈 银牌", "5:🥇 金牌"])
        assert tiers == [(2, "🥈 银牌"), (5, "🥇 金牌")]
        assert await dao.record_and_claim("1001", "G1", "2026-09-11", tiers) == (2, 2)
        assert await dao.record_and_claim("1001", "G1", "2026-09-11", tiers) == (
            3,
            None,
        )
        assert await dao.record_and_claim("1001", "G1", "2026-09-11", tiers) == (
            4,
            None,
        )
        assert await dao.record_and_claim("1001", "G1", "2026-09-11", tiers) == (5, 5)
        # 与其他 DAO 方法口径一致
        assert await dao.get_total("1001", "G1") == 5
        assert await _milestones(t, "1001", "G1") == [2, 5]
    return "里程碑：record_and_claim 同事务里写计数+取档，跨群互相独立"


async def test_milestone_disabled_no_send():
    async with TempDB() as t:
        plugin, _ = _plugin(t, speak_milestone_enabled=False)
        h = _handler(plugin)
        await _seed(t, "1001", "G1", 99)
        ev = FakeEvent("1001", "G1", msg="第一百条")
        await h.handle(ev)
        assert _texts(ev) == []
        assert await _milestones(t, "1001", "G1") == []
        assert await t.count("speak_daily") == 1
    return "里程碑：开关关闭时不播报，但计数照常写入"


async def test_milestone_crossing_announced():
    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        await _seed(t, "1001", "G1", 99)
        ev = FakeEvent("1001", "G1", msg="第一百条")
        await h.handle(ev)
        assert len(ev.sent) == 1
        text = _texts(ev)[0]
        assert "100 条发言里程碑" in text
        assert "🗣 话题担当" in text and "第 1 名" in text
        assert await _milestones(t, "1001", "G1") == [20, 100]
    return "里程碑：累计刚好跨过 100 档即播报（文案含累计数/新称号/本群排名）"


async def test_milestone_skip_lowest_tier():
    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        # 第一条消息累计=1，命中最低档（阈值 1）但不播报
        ev1 = FakeEvent("1001", "G1", msg="第一条")
        await h.handle(ev1)
        assert _texts(ev1) == []
        # 第 20 条落到第二档，正常播报
        await _seed(t, "1001", "G1", 19)
        ev2 = FakeEvent("1001", "G1", msg="第二十条")
        await h.handle(ev2)
        assert len(ev2.sent) == 1
        assert "20 条发言里程碑" in _texts(ev2)[0]
    return "里程碑：最低档（阈值 1）不播报，第二档起正常播报"


async def test_milestone_first_check_baseline():
    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        # 老成员：开启开关前已累计 500 条，首次检查静默登记已跨过的档位
        await _seed(t, "1001", "G1", 500)
        ev = FakeEvent("1001", "G1", msg="继续水")
        await h.handle(ev)
        assert _texts(ev) == []
        assert await _milestones(t, "1001", "G1") == [20, 100, 400]
    return "里程碑：首次检查静默基线，老成员不回溯播报"


async def test_milestone_first_check_exact_cross():
    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        # 399 → 400：这条消息恰好跨过 400 档，首次检查也要播报
        await _seed(t, "1001", "G1", 399)
        ev = FakeEvent("1001", "G1", msg="第 400 条")
        await h.handle(ev)
        assert len(ev.sent) == 1
        assert "400 条发言里程碑" in _texts(ev)[0]
        assert await _milestones(t, "1001", "G1") == [20, 100, 400]
    return "里程碑：首次检查恰好跨档仍播报（静默基线只登记更低的档位）"


async def test_milestone_batch_jump_highest_only():
    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        # 先走正式路径把 20 档与 100 档真的播报掉
        await _seed(t, "1001", "G1", 19)
        ev1 = FakeEvent("1001", "G1", msg="第二十条")
        await h.handle(ev1)
        assert len(ev1.sent) == 1 and "20 条发言里程碑" in _texts(ev1)[0]
        await _seed(t, "1001", "G1", 99)
        ev2 = FakeEvent("1001", "G1", msg="第一百条")
        await h.handle(ev2)
        assert len(ev2.sent) == 1 and "100 条发言里程碑" in _texts(ev2)[0]
        # 已播报过 100，之后一次跨到 1200 档：只报最高的那一档
        await _seed(t, "1001", "G1", 1199)
        ev = FakeEvent("1001", "G1", msg="跨档")
        await h.handle(ev)
        assert len(ev.sent) == 1
        text = _texts(ev)[0]
        assert "1200 条发言里程碑" in text
        assert "🌊 水群之王" in text
        assert await _milestones(t, "1001", "G1") == [20, 100, 1200]
    return "里程碑：一次跨多档只播报最高的那一档"


async def test_milestone_single_tier():
    async with TempDB() as t:
        plugin, _ = _plugin(t, speak_stat_titles=["7:🥇 单档"])
        h = _handler(plugin)
        # 只配一档时保留该档（否则「跳过最低档」会让功能永不触发）
        from astrbot_plugin_point_system_by_whleague.utils.speak_milestones import (
            parse_milestones,
        )

        assert parse_milestones(["7:🥇 单档"]) == [(7, "🥇 单档")]
        await _seed(t, "1001", "G1", 6)
        ev = FakeEvent("1001", "G1", msg="第七条")
        await h.handle(ev)
        assert len(ev.sent) == 1
        assert "🥇 单档" in _texts(ev)[0]
    return "里程碑：只配一档时该档仍会播报"


async def test_milestone_idempotent():
    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        await _seed(t, "1001", "G1", 99)
        ev1 = FakeEvent("1001", "G1", msg="第一百条")
        await h.handle(ev1)
        assert len(ev1.sent) == 1
        # 同一档位再来一条（累计继续涨）不再播报
        ev2 = FakeEvent("1001", "G1", msg="第一百零一条")
        await h.handle(ev2)
        assert _texts(ev2) == []
        assert await _milestones(t, "1001", "G1") == [20, 100]
    return "里程碑：同档位幂等，重复发言不重复播报"


async def test_milestone_concurrent_first_check_no_loss():
    """首次检查（该 QQ 在本群还没有任何登记）窗口内并发发言不得漏播。

    计数与取档若分属两个事务，后到的那次会读到「还没有登记」的旧状态，把恰好被
    前一条跨过的 100 档当成早该静默的基线登记掉，100 档就永久丢失（可用旧实现复现）。
    """
    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        await _seed(t, "1001", "G1", 99)
        ev_a = FakeEvent("1001", "G1", msg="并发 a")
        ev_b = FakeEvent("1001", "G1", msg="并发 b")
        await asyncio.gather(h.handle(ev_a), h.handle(ev_b))
        # 两条消息把累计推到 100 与 101，100 档必须恰好播报一次
        assert len(ev_a.sent) + len(ev_b.sent) == 1
        assert await _milestones(t, "1001", "G1") == [20, 100]
    return "里程碑：首次检查窗口并发发言不漏播、不重复（100 档恰好一次）"


async def test_milestone_title_from_tier():
    async with TempDB() as t:
        plugin, _ = _plugin(t, speak_stat_titles=["50:🥉 铜牌", "150:🥈 银牌"])
        h = _handler(plugin)
        await _seed(t, "1001", "G1", 149)
        ev = FakeEvent("1001", "G1", msg="第 150 条")
        await h.handle(ev)
        assert len(ev.sent) == 1
        text = _texts(ev)[0]
        assert "150 条发言里程碑" in text and "🥈 银牌" in text
        assert "🥉 铜牌" not in text
    return "里程碑：称号取自命中的档位，且与「我的发言」口径一致"


async def test_milestone_template_placeholders():
    async with TempDB() as t:
        plugin, _ = _plugin(t, speak_milestone_template="{total}/{title}/{rank}")
        h = _handler(plugin)
        await _seed(t, "1001", "G1", 99)
        await _seed(t, "1002", "G1", 500)
        ev = FakeEvent("1001", "G1", msg="第一百条")
        await h.handle(ev)
        # 链首是 @，正文紧随其后
        assert _texts(ev)[0] == "1001" + "100/🗣 话题担当/2"
    return "里程碑：自定义文案的三个占位符按实际值渲染"


async def test_milestone_template_unknown_kept():
    from astrbot_plugin_point_system_by_whleague.utils.speak_milestones import (
        render_milestone,
    )

    text = render_milestone("{total} 条 {unknown} 称号", total=100, title="T", rank=1)
    assert text == "100 条 {unknown} 称号"
    return "里程碑：未知占位符原样保留，不因配置写错而发不出消息"


async def test_milestone_template_invalid_fallback():
    from astrbot_plugin_point_system_by_whleague.utils.speak_milestones import (
        DEFAULT_TEMPLATE,
        render_milestone,
    )

    # 回退到默认模板后是「渲染结果」，不是带占位符的原文
    expected = (
        DEFAULT_TEMPLATE.replace("{total}", "100")
        .replace("{title}", "T")
        .replace("{rank}", "3")
    )
    for bad in ("{total", "{}", "", None, "   "):
        assert render_milestone(bad, total=100, title="T", rank=3) == expected
    return "里程碑：文案为空或花括号非法时回退内置默认模板"


async def test_milestone_at_prefix():
    from astrbot_plugin_point_system_by_whleague.utils.speak_milestones import (
        render_milestone,
    )

    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        await _seed(t, "1001", "G1", 99)
        ev = FakeEvent("1001", "G1", msg="第一百条")
        await h.handle(ev)
        text = _texts(ev)[0]
        # 链顺序：@ 本人固定在正文之前
        assert text.startswith("1001")
        assert text.endswith(
            render_milestone(
                plugin.config_cache["speak_milestone_template"],
                total=100,
                title="🗣 话题担当",
                rank=1,
            )
        )
    return "里程碑：消息链以 @ 本人开头，正文在后"


async def test_milestone_rank_ties_shared():
    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        await _seed(t, "1001", "G1", 99)
        await _seed(t, "1002", "G1", 100)
        ev = FakeEvent("1001", "G1", msg="第一百条")
        await h.handle(ev)
        # 并列 100 条时不比大小、并列第 1
        assert "第 1 名" in _texts(ev)[0]
    return "里程碑：排名沿用积分/发言排行口径，同分共享名次"


async def test_milestone_cross_group_isolated():
    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        await _seed(t, "1001", "G1", 99)
        await _seed(t, "1001", "G2", 99)
        ev1 = FakeEvent("1001", "G1", msg="一群第一百条")
        await h.handle(ev1)
        assert len(ev1.sent) == 1
        assert await _milestones(t, "1001", "G2") == []
        # 二群刚起步：一群的登记不会让二群提前播报
        await _seed(t, "1001", "G2", 5)
        ev2 = FakeEvent("1001", "G2", msg="二群第六条")
        await h.handle(ev2)
        assert _texts(ev2) == []
        assert await _milestones(t, "1001", "G2") == []
    return "里程碑：档位按群各自登记，跨群不串扰"


async def test_milestone_titles_invalid_fallback():
    async with TempDB() as t:
        plugin, _ = _plugin(t, speak_stat_titles=["bad", "0:零"])
        h = _handler(plugin)
        await _seed(t, "1001", "G1", 99)
        ev = FakeEvent("1001", "G1", msg="第一百条")
        await h.handle(ev)
        assert len(ev.sent) == 1
        assert "100 条发言里程碑" in _texts(ev)[0]
    return "里程碑：档位配置非法时整表回退内置默认档位"


async def test_milestone_error_swallowed():
    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        await _seed(t, "1001", "G1", 99)
        ev = FakeEvent("1001", "G1", msg="第一百条")
        with mock.patch.object(
            plugin.speak_dao,
            "get_group_ranking",
            mock.AsyncMock(side_effect=RuntimeError("boom")),
        ):
            await h.handle(ev)
        assert _texts(ev) == []
        # 计数已完成（活跃奖励紧随其后，不能被播报异常带崩）
        assert await t.count("speak_daily") == 1
    return "里程碑：播报异常被计数钩子吞掉，不影响计数与后续活跃奖励"


async def test_milestone_config_defaults():
    from astrbot_plugin_point_system_by_whleague.config.defaults import DEFAULT_CONFIG
    from astrbot_plugin_point_system_by_whleague.utils.speak_milestones import (
        DEFAULT_TEMPLATE,
    )

    with open(os.path.join(PLUGIN_ROOT, "_conf_schema.json"), encoding="utf-8") as f:
        schema = json.load(f)
    assert schema["speak_milestone_enabled"]["type"] == "bool"
    assert schema["speak_milestone_enabled"]["default"] is False
    assert schema["speak_milestone_template"]["type"] == "string"
    assert schema["speak_milestone_template"]["default"] == DEFAULT_TEMPLATE
    assert DEFAULT_CONFIG["speak_milestone_enabled"] is False
    assert DEFAULT_CONFIG["speak_milestone_template"] == DEFAULT_TEMPLATE
    return "里程碑：两个新配置默认关闭 / 与内置模板同文"


async def test_milestone_schema_migration():
    from astrbot_plugin_point_system_by_whleague.db.schema import (
        SCHEMA_VERSION,
        init_schema,
    )
    from astrbot_plugin_point_system_by_whleague.db.speak_dao import SpeakDAO
    from astrbot_plugin_point_system_by_whleague.utils.speak_milestones import (
        parse_milestones,
    )

    async with TempDB() as t:
        assert SCHEMA_VERSION == 7
        # 模拟旧库：无里程碑表、版本号为 6
        await t.db.execute("DROP TABLE speak_milestone_log")
        await t.db.execute(
            "UPDATE plugin_config SET value='6' WHERE key='schema_version'"
        )
        await init_schema(t.db)

        row = await t.db.fetchone(
            "SELECT value FROM plugin_config WHERE key='schema_version'"
        )
        assert row["value"] == "7"
        index = await t.db.fetchone(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_speak_milestone_group_qq'"
        )
        assert index is not None
        dao = SpeakDAO(t.db)
        tiers = parse_milestones(["1:🌱 新面孔", "2:🥈 银牌"])
        assert await dao.record_and_claim("1001", "G1", "2026-09-11", tiers) == (
            1,
            None,
        )
        assert await dao.record_and_claim("1001", "G1", "2026-09-11", tiers) == (2, 2)
        # 幂等：重复初始化不报错、不清数据
        await init_schema(t.db)
        assert await _milestones(t, "1001", "G1") == [2]
    return "里程碑：旧库（version=6）启动即建表并升到 7，无需迁移分支"


async def test_milestone_no_cross_no_rows():
    async with TempDB() as t:
        plugin, _ = _plugin(t)
        h = _handler(plugin)
        # 第一条消息累计=1，未跨任何可播报档位：既不播报也不写登记行
        ev = FakeEvent("1001", "G1", msg="第一条")
        await h.handle(ev)
        assert _texts(ev) == []
        assert await _milestones(t, "1001", "G1") == []
    return "里程碑：未跨档时不写登记行（首次检查的空基线不落库）"


TESTS = [
    ("milestone_record_claim", test_milestone_record_claim),
    ("milestone_disabled_no_send", test_milestone_disabled_no_send),
    ("milestone_crossing_announced", test_milestone_crossing_announced),
    ("milestone_skip_lowest_tier", test_milestone_skip_lowest_tier),
    ("milestone_first_check_baseline", test_milestone_first_check_baseline),
    ("milestone_first_check_exact_cross", test_milestone_first_check_exact_cross),
    ("milestone_batch_jump_highest_only", test_milestone_batch_jump_highest_only),
    ("milestone_single_tier", test_milestone_single_tier),
    ("milestone_idempotent", test_milestone_idempotent),
    (
        "milestone_concurrent_first_check_no_loss",
        test_milestone_concurrent_first_check_no_loss,
    ),
    ("milestone_title_from_tier", test_milestone_title_from_tier),
    ("milestone_template_placeholders", test_milestone_template_placeholders),
    ("milestone_template_unknown_kept", test_milestone_template_unknown_kept),
    ("milestone_template_invalid_fallback", test_milestone_template_invalid_fallback),
    ("milestone_at_prefix", test_milestone_at_prefix),
    ("milestone_rank_ties_shared", test_milestone_rank_ties_shared),
    ("milestone_cross_group_isolated", test_milestone_cross_group_isolated),
    ("milestone_titles_invalid_fallback", test_milestone_titles_invalid_fallback),
    ("milestone_error_swallowed", test_milestone_error_swallowed),
    ("milestone_config_defaults", test_milestone_config_defaults),
    ("milestone_schema_migration", test_milestone_schema_migration),
    ("milestone_no_cross_no_rows", test_milestone_no_cross_no_rows),
]
