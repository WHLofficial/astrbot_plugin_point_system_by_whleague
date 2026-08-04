"""S19 打劫系统：服务层门槛/收益公式/防刷/负分联动 + handler @解析/文案 + 互斥集成。"""

from .common import FakeBot, FakeEvent, TempDB, base_cfg, collect, patch_random


class _DailyKwStub:
    def __init__(self):
        self.called = 0

    async def check_and_claim(self, *a, **k):
        self.called += 1
        return {}


class _PluginStub:
    def __init__(
        self, cfg, rob_service=None, daily_kw=None, limiter=None, point_service=None
    ):
        self.config_cache = cfg
        self.rob_service = rob_service
        self.daily_keyword_service = daily_kw
        self.rate_limiter = limiter
        self.point_service = point_service


async def _build(t, cfg=None):
    from astrbot_plugin_point_system_by_whleague.services.point_service import (
        PointService,
    )
    from astrbot_plugin_point_system_by_whleague.services.rob_service import (
        RobService,
    )
    from astrbot_plugin_point_system_by_whleague.utils.rate_limiter import RateLimiter

    cfg = base_cfg() if cfg is None else cfg
    limiter = RateLimiter()
    ps = PointService(t.db, t.dao)
    rs = RobService(t.db, t.dao, ps, cfg, limiter)
    return cfg, ps, rs, limiter


async def _grant(ps, qq, group, points):
    await ps.add(qq, group, points, "admin_add", admin_override=True)


async def _handle_rob(cfg, event, rs=None):
    from astrbot_plugin_point_system_by_whleague.handlers.rob import RobHandler

    plugin = _PluginStub(cfg, rob_service=rs)
    return await collect(RobHandler(plugin).handle(event))


# ─── 服务层 ──────────────────────────────────────────────


async def test_success_reward_formula():
    async with TempDB() as t:
        cfg, ps, rs, lim = await _build(t)
        await _grant(ps, "10001", "1001", 1000)
        await _grant(ps, "10002", "1001", 2000)
        with patch_random(random=0.1):  # 0.1 < 0.35 成功
            result = await rs.rob("10001", "10002", "1001")
        assert result["performed"] is True and result["success"] is True
        assert result["stolen"] == 100  # 锚点 2000 → dynamic=50 → 50+50
        assert result["balance"] == 1100
        assert result["target_balance"] == 1900
        rec = await t.db.fetchone("SELECT * FROM rob_records WHERE qq='10001'")
        assert rec["success"] == 1 and rec["stolen"] == 100 and rec["cost"] == 50
        # round 取整：500 分目标 → dynamic=round(50*0.25^1.2)=9 → 59
        await _grant(ps, "10003", "1001", 1000)
        await _grant(ps, "10004", "1001", 500)
        with patch_random(random=0.1):
            result2 = await rs.rob("10003", "10004", "1001")
        assert result2["success"] is True and result2["stolen"] == 59
        assert result2["target_balance"] == 441
        # cap 触顶：10000 分目标 → dynamic=round(50*5^1.2)=345 → min(200, 395)=200
        await _grant(ps, "10005", "1001", 1000)
        await _grant(ps, "10006", "1001", 10000)
        with patch_random(random=0.1):
            result3 = await rs.rob("10005", "10006", "1001")
        assert result3["success"] is True and result3["stolen"] == 200
        # 成功不扣成本（纯收益）
        rows = await t.db.fetchall(
            "SELECT reason FROM point_transactions WHERE qq='10005' ORDER BY id"
        )
        assert rows[-1]["reason"] == "rob_reward"
    return "打劫：收益公式锚点/round/封顶，成功纯收益"


async def test_failure_cost_and_record():
    async with TempDB() as t:
        cfg, ps, rs, lim = await _build(t)
        await _grant(ps, "10001", "1001", 1000)
        await _grant(ps, "10002", "1001", 2000)
        with patch_random(random=0.9):  # 0.9 >= 0.35 失败
            result = await rs.rob("10001", "10002", "1001")
        assert result["performed"] is True and result["success"] is False
        assert result["stolen"] == 0
        assert result["balance"] == 950  # 扣成本 50，不退还
        assert result["target_balance"] == 2000  # 目标未动
        rows = await t.db.fetchall(
            "SELECT * FROM point_transactions WHERE qq='10001' ORDER BY id"
        )
        assert rows[-1]["reason"] == "rob_cost" and rows[-1]["amount"] == -50
        rec = await t.db.fetchone(
            "SELECT * FROM rob_records WHERE qq='10001' ORDER BY id DESC"
        )
        assert rec["success"] == 0 and rec["stolen"] == 0 and rec["cost"] == 50
    return "打劫：失败扣成本不退还、目标不动、success=0 记录"


async def test_gates():
    async with TempDB() as t:
        cfg, ps, rs, lim = await _build(t)
        await _grant(ps, "10010", "1001", 1000)
        await _grant(ps, "10002", "1001", 2000)
        # 开关关闭
        cfg["rob_enabled"] = False
        r = await rs.rob("10010", "10002", "1001")
        assert r["performed"] is False and "关闭" in r["msg"]
        cfg["rob_enabled"] = True
        # 非群聊
        r = await rs.rob("10010", "10002", "")
        assert r["performed"] is False and "群聊" in r["msg"]
        # 打劫自己
        r = await rs.rob("10010", "10010", "1001")
        assert r["performed"] is False and "自己" in r["msg"]
        # 打劫者积分不足（低于 rob_min_points=100）
        await _grant(ps, "10011", "1001", 50)
        r = await rs.rob("10011", "10002", "1001")
        assert r["performed"] is False and "积分不足" in r["msg"] and "≥100" in r["msg"]
        # 打劫者负分
        await _grant(ps, "10012", "1001", 10)
        await ps.subtract("10012", "1001", 20, "admin_sub")
        r = await rs.rob("10012", "10002", "1001")
        assert r["performed"] is False and "积分为负" in r["msg"]
        # 目标积分不足（低于 rob_target_min_points=50）
        await _grant(ps, "10013", "1001", 40)
        r = await rs.rob("10010", "10013", "1001")
        assert r["performed"] is False and "低于 50" in r["msg"]
        # 目标负分
        await _grant(ps, "10014", "1001", 10)
        await ps.subtract("10014", "1001", 20, "admin_sub")
        r = await rs.rob("10010", "10014", "1001")
        assert r["performed"] is False and "目标积分为负" in r["msg"]
        # 门槛拦截不产生记录
        cnt = await t.db.fetchone("SELECT COUNT(*) AS c FROM rob_records")
        assert cnt["c"] == 0
    return "打劫：开关/群聊/自己/打劫者与目标门槛"


async def test_daily_limit():
    async with TempDB() as t:
        cfg = base_cfg(rob_cooldown=0, rob_daily_limit=3)
        _, ps, rs, lim = await _build(t, cfg)
        await _grant(ps, "10021", "1001", 1000)
        await _grant(ps, "10002", "1001", 2000)
        for _ in range(3):
            with patch_random(random=0.9):
                r = await rs.rob("10021", "10002", "1001")
            assert r["performed"] is True
        r4 = await rs.rob("10021", "10002", "1001")
        assert r4["performed"] is False and "上限" in r4["msg"] and "3 次" in r4["msg"]
        # 按 QQ 全局：其他 QQ 不受影响
        await _grant(ps, "10022", "1001", 1000)
        with patch_random(random=0.9):
            r = await rs.rob("10022", "10002", "1001")
        assert r["performed"] is True
    return "打劫：每日限次按 QQ 全局统计、达上限拒绝"


async def test_cooldown_enters_after_success_and_failure():
    async with TempDB() as t:
        cfg = base_cfg(rob_cooldown=600)
        _, ps, rs, lim = await _build(t, cfg)
        await _grant(ps, "10031", "1001", 1000)
        await _grant(ps, "10002", "1001", 2000)
        with patch_random(random=0.1):
            r = await rs.rob("10031", "10002", "1001")
        assert r["performed"] is True
        r2 = await rs.rob("10031", "10002", "1001")
        assert r2["performed"] is False and "冷却中" in r2["msg"]
        assert lim.get_remaining("rob", "10031", "1001", 600) > 0
        # 失败同样进入冷却
        await _grant(ps, "10032", "1001", 1000)
        with patch_random(random=0.9):
            r3 = await rs.rob("10032", "10002", "1001")
        assert r3["performed"] is True and r3["success"] is False
        r4 = await rs.rob("10032", "10002", "1001")
        assert r4["performed"] is False and "冷却中" in r4["msg"]
    return "打劫：成功/失败均进入冷却、get_remaining 实时剩余"


async def test_target_negative_title_linkage():
    async with TempDB() as t:
        cfg, ps, rs, lim = await _build(t)
        await _grant(ps, "10041", "1001", 1000)
        await _grant(ps, "10042", "1001", 50)  # 最低门槛，被抢成负
        bot = FakeBot(member_card="小目标")
        with patch_random(random=0.1):
            result = await rs.rob("10041", "10042", "1001", bot=bot)
        assert result["success"] is True
        assert result["target_balance"] < 0
        # 负分头衔联动：set_group_card 应用「群女仆X号」
        calls = [c for c in bot.calls if c[0] == "set_group_card"]
        assert calls, bot.calls
        action, kwargs = calls[0]
        assert kwargs["group_id"] == 1001 and kwargs["user_id"] == 10042
        assert "群女仆" in kwargs["card"]
        # 打劫者余额为正：不设置头衔
        assert not [
            c for c in bot.calls if c[0] == "set_group_card" and c[1]["user_id"] == 10041
        ]
    return "打劫：目标被抢成负分后自动联动负分头衔"


async def test_failure_guard_atomic_rollback():
    async with TempDB() as t:
        cfg = base_cfg(rob_min_points=0, rob_cooldown=0)
        _, ps, rs, lim = await _build(t, cfg)
        await _grant(ps, "10051", "1001", 10)  # 余额低于 cost=50
        await _grant(ps, "10002", "1001", 500)
        with patch_random(random=0.9):
            result = await rs.rob("10051", "10002", "1001")
        assert result["performed"] is False and "积分不足" in result["msg"]
        # 事务原子回滚：无 rob 记录、无 rob_cost 流水
        cnt = await t.db.fetchone(
            "SELECT COUNT(*) AS c FROM rob_records WHERE qq='10051'"
        )
        assert cnt["c"] == 0
        rows = await t.db.fetchall(
            "SELECT reason FROM point_transactions WHERE qq='10051'"
        )
        assert all(r["reason"] != "rob_cost" for r in rows)
    return "打劫：守卫失败整事务回滚（成本+记录原子）"


async def test_no_cooldown_when_zero():
    async with TempDB() as t:
        cfg = base_cfg(rob_cooldown=0)
        _, ps, rs, lim = await _build(t, cfg)
        await _grant(ps, "10061", "1001", 1000)
        await _grant(ps, "10002", "1001", 2000)
        with patch_random(random=0.1):
            r1 = await rs.rob("10061", "10002", "1001")
            r2 = await rs.rob("10061", "10002", "1001")
        assert r1["performed"] is True and r2["performed"] is True
    return "打劫：rob_cooldown=0 不拦截"


# ─── 防集火：目标每日被劫上限 + 收益衰减 ──────────────────


async def test_target_daily_limit():
    """目标每日被劫上限（默认 6，全部次数口径）：6 人集火后第 7 个被拒，
    被拒打劫者已消耗冷却。"""
    async with TempDB() as t:
        cfg, ps, rs, lim = await _build(t)
        await _grant(ps, "10002", "1001", 2000)  # 目标
        for i in range(6):
            robber = f"1010{i + 1}"
            await _grant(ps, robber, "1001", 1000)
            with patch_random(random=0.1):
                r = await rs.rob(robber, "10002", "1001")
            assert r["performed"] is True and r["success"] is True, (i, r)
        # 第 7 个打劫者被目标限次拦截（精确次数文案）
        await _grant(ps, "10107", "1001", 1000)
        r7 = await rs.rob("10107", "10002", "1001")
        assert r7["performed"] is False
        assert "目标今日已被打劫 6 次" in r7["msg"]
        # 拦截已消耗打劫者冷却：再打劫其他目标也被冷却拦截
        await _grant(ps, "90001", "1001", 500)
        r8 = await rs.rob("10107", "90001", "1001")
        assert r8["performed"] is False and "冷却中" in r8["msg"]
        # 拦截不产生记录
        cnt = await t.db.fetchone(
            "SELECT COUNT(*) AS c FROM rob_records WHERE qq='10107'"
        )
        assert cnt["c"] == 0
        # 固定方案拦截文案不带「今日上限」（与 v0.4.2 文案一致）
        assert "今日上限" not in r7["msg"]
    return "防集火：目标每日被劫上限拦截、精确次数文案、拦截消耗冷却"


async def test_target_limit_dynamic():
    """动态方案（rob_target_limit_dynamic=true）：上限 = 基准 6 + 主动打劫 2 = 8，
    第 9 个打劫者被拒且文案附动态上限；主动打劫全部口径（成功/失败均计数）。"""
    async with TempDB() as t:
        cfg = base_cfg(
            rob_cooldown=0, rob_daily_limit=0, rob_target_limit_dynamic=True
        )
        _, ps, rs, lim = await _build(t, cfg)
        await _grant(ps, "10002", "1001", 2000)  # 目标
        await _grant(ps, "90001", "1001", 5000)  # 目标主动打劫的对象
        await _grant(ps, "90002", "1001", 5000)
        # 目标主动打劫 2 次（成功），主动口径 = 2
        for dummy in ("90001", "90002"):
            with patch_random(random=0.1):
                r = await rs.rob("10002", dummy, "1001")
            assert r["performed"] is True and r["success"] is True, (dummy, r)
        # 上限 = 6 + 2 = 8：前 8 个打劫者全部放行
        for i in range(8):
            robber = f"1020{i + 1}"
            await _grant(ps, robber, "1001", 1000)
            with patch_random(random=0.1):
                r = await rs.rob(robber, "10002", "1001")
            assert r["performed"] is True, (i, r)
        # 第 9 个被拒：文案附动态上限「今日上限 8」
        await _grant(ps, "10209", "1001", 1000)
        r9 = await rs.rob("10209", "10002", "1001")
        assert r9["performed"] is False
        assert "目标今日已被打劫 8 次（今日上限 8）" in r9["msg"]
    return "防集火：动态方案上限=基准+主动打劫次数、文案附上限"


async def test_target_limit_dynamic_base_zero_clamped():
    """动态方案 + 基准 0（绕过配置层直配注入）：服务层 max(...,1) 防御按 1 处理（非不限）。"""
    async with TempDB() as t:
        cfg = base_cfg(
            rob_cooldown=0,
            rob_daily_limit=0,
            rob_target_daily_limit=0,
            rob_target_limit_dynamic=True,
        )
        _, ps, rs, lim = await _build(t, cfg)
        await _grant(ps, "10211", "1001", 1000)
        await _grant(ps, "90001", "1001", 10000)
        with patch_random(random=0.1):
            r1 = await rs.rob("10211", "90001", "1001")
        assert r1["performed"] is True and r1["success"] is True
        # 上限 = max(0,1) + 0 = 1：第 2 次打劫被拒（而非不限）
        r2 = await rs.rob("10211", "90001", "1001")
        assert r2["performed"] is False
        assert "目标今日已被打劫 1 次（今日上限 1）" in r2["msg"]
    return "防集火：动态方案基准 0 服务层按 1 钳位（非不限）"


async def test_target_limit_dynamic_failure_counts():
    """主动打劫失败同样计入动态上限（全部口径）：失败 1 次后上限 = 6 + 1 = 7。"""
    async with TempDB() as t:
        cfg = base_cfg(
            rob_cooldown=0, rob_daily_limit=0, rob_target_limit_dynamic=True
        )
        _, ps, rs, lim = await _build(t, cfg)
        await _grant(ps, "10002", "1001", 2000)  # 目标
        await _grant(ps, "90001", "1001", 5000)
        # 目标主动打劫失败 1 次（random=0.9 ≥ 成功率 0.35），仍计入主动口径
        with patch_random(random=0.9):
            r = await rs.rob("10002", "90001", "1001")
        assert r["performed"] is True and r["success"] is False
        # 上限 = 6 + 1 = 7：前 7 个打劫者放行
        for i in range(7):
            robber = f"1030{i + 1}"
            await _grant(ps, robber, "1001", 1000)
            with patch_random(random=0.1):
                r = await rs.rob(robber, "10002", "1001")
            assert r["performed"] is True, (i, r)
        # 第 8 个被拒：文案附「今日上限 7」
        await _grant(ps, "10308", "1001", 1000)
        r8 = await rs.rob("10308", "10002", "1001")
        assert r8["performed"] is False
        assert "目标今日已被打劫 7 次（今日上限 7）" in r8["msg"]
    return "防集火：主动打劫失败也计入动态上限（全部口径）"


async def test_target_limit_zero_decay_still_active():
    """rob_target_daily_limit=0（不限）时放行，但收益衰减仍生效（防长期集火）。"""
    async with TempDB() as t:
        cfg = base_cfg(rob_cooldown=0, rob_target_daily_limit=0, rob_daily_limit=0)
        _, ps, rs, lim = await _build(t, cfg)
        await _grant(ps, "10111", "1001", 5000)
        await _grant(ps, "90001", "1001", 10000)  # cap 触顶目标
        stolen_seq = []
        for _ in range(7):
            with patch_random(random=0.1):
                r = await rs.rob("10111", "90001", "1001")
            assert r["performed"] is True and r["success"] is True
            stolen_seq.append(r["stolen"])
        # 衰减 0.75^n：200/150/112/84/63/47/36（round 银行家舍入）
        assert stolen_seq == [200, 150, 112, 84, 63, 47, 36], stolen_seq
    return "防集火：上限 0=不限放行且衰减仍生效"


async def test_decay_sequence():
    """收益衰减序列：cap 触顶目标连续成功 → 200/150/112/84/63（0.75^n）。"""
    async with TempDB() as t:
        cfg = base_cfg(rob_cooldown=0, rob_daily_limit=0)
        _, ps, rs, lim = await _build(t, cfg)
        await _grant(ps, "10121", "1001", 1000)
        await _grant(ps, "90001", "1001", 10000)
        stolen_seq = []
        for _ in range(5):
            with patch_random(random=0.1):
                r = await rs.rob("10121", "90001", "1001")
            assert r["performed"] is True and r["success"] is True
            stolen_seq.append(r["stolen"])
        assert stolen_seq == [200, 150, 112, 84, 63], stolen_seq
    return "防集火：收益衰减序列 200/150/112/84/63"


async def test_decay_failure_not_counted():
    """衰减仅按成功次数：失败打劫不压低后续成功收益（仍 100 全额）。"""
    async with TempDB() as t:
        cfg, ps, rs, lim = await _build(t)
        await _grant(ps, "10131", "1001", 1000)
        await _grant(ps, "10132", "1001", 1000)
        await _grant(ps, "10002", "1001", 2000)
        with patch_random(random=0.9):
            r_fail = await rs.rob("10131", "10002", "1001")
        assert r_fail["performed"] is True and r_fail["success"] is False
        with patch_random(random=0.1):
            r_win = await rs.rob("10132", "10002", "1001")
        assert r_win["performed"] is True and r_win["success"] is True
        assert r_win["stolen"] == 100  # win_hits=0 → 全额，失败不计数
    return "防集火：衰减仅成功次数（失败不压低收益）"


async def test_decay_zero():
    """rob_reward_decay=0：不衰减，序列恒 200。"""
    async with TempDB() as t:
        cfg = base_cfg(rob_cooldown=0, rob_reward_decay=0)
        _, ps, rs, lim = await _build(t, cfg)
        await _grant(ps, "10141", "1001", 1000)
        await _grant(ps, "90001", "1001", 10000)
        seq = []
        for _ in range(3):
            with patch_random(random=0.1):
                r = await rs.rob("10141", "90001", "1001")
            assert r["performed"] is True and r["success"] is True
            seq.append(r["stolen"])
        assert seq == [200, 200, 200], seq
    return "防集火：decay=0 不衰减"


async def test_decay_one_floor():
    """rob_reward_decay=1.0：首次全额（0^0=1），后续收益归零走防御路径不报错。"""
    async with TempDB() as t:
        cfg = base_cfg(rob_cooldown=0, rob_reward_decay=1.0)
        _, ps, rs, lim = await _build(t, cfg)
        await _grant(ps, "10151", "1001", 1000)
        await _grant(ps, "90001", "1001", 10000)
        with patch_random(random=0.1):
            r1 = await rs.rob("10151", "90001", "1001")
        assert r1["performed"] is True and r1["stolen"] == 200
        with patch_random(random=0.1):
            r2 = await rs.rob("10151", "90001", "1001")
        assert r2["performed"] is True and r2["success"] is True
        assert r2["stolen"] == 0  # 衰减归零
        assert r2["balance"] == 1000 + 200  # 无变动
        assert r2["target_balance"] == 9800
        rec = await t.db.fetchone(
            "SELECT success, stolen FROM rob_records WHERE qq='10151' ORDER BY id DESC"
        )
        assert rec["success"] == 1 and rec["stolen"] == 0
    return "防集火：decay=1.0 归零走防御路径不报错"


async def test_zero_stolen_config_defensive():
    """极端配置（rob_reward_fixed/cap=0）下收益为 0：不触发 change_balance(0) 报错。"""
    async with TempDB() as t:
        cfg = base_cfg(rob_reward_fixed=0, rob_cooldown=0)
        _, ps, rs, lim = await _build(t, cfg)
        await _grant(ps, "10051", "1001", 1000)
        await _grant(ps, "10052", "1001", 100)
        with patch_random(random=0.1):
            result = await rs.rob("10051", "10052", "1001")
        assert result["performed"] is True and result["success"] is True
        assert result["stolen"] == 0
        assert result["balance"] == 1000  # 无变动
        assert result["target_balance"] == 100
        rec = await t.db.fetchone("SELECT * FROM rob_records WHERE qq='10051'")
        assert rec["success"] == 1 and rec["stolen"] == 0
        # rob_reward_cap=0 同样防御
        cfg2 = base_cfg(rob_reward_cap=0, rob_cooldown=0)
        _, ps2, rs2, lim2 = await _build(t, cfg2)
        await _grant(ps2, "10053", "1001", 1000)
        with patch_random(random=0.1):
            result2 = await rs2.rob("10053", "10052", "1001")
        assert result2["performed"] is True and result2["success"] is True
        assert result2["stolen"] == 0
    return "打劫：零收益配置（fixed/cap=0）防御不报错"


async def test_negative_target_points_defensive():
    """门槛检查后目标被并发扣成负：事务内负值不触发幂运算异常，按 0 计算收益。"""
    from unittest import mock

    async with TempDB() as t:
        cfg, ps, rs, lim = await _build(t)
        await _grant(ps, "10061", "1001", 1000)
        await _grant(ps, "10062", "1001", 5)
        await ps.subtract("10062", "1001", 10, "admin_sub")  # DB 中为 -5
        # 门槛检查只见正分（模拟并发窗口），事务内 SELECT 到负值：
        # 调用序：rob 打劫者余额、rob 目标余额、ensure_negative_title×2
        fake = mock.AsyncMock(side_effect=[1000, 100, 1000, -5])
        with mock.patch.object(ps, "get_balance", new=fake):
            with patch_random(random=0.1):
                result = await rs.rob("10061", "10062", "1001")
        assert result["performed"] is True and result["success"] is True
        assert result["stolen"] == 50  # max(负,0)=0 → dynamic=0 → fixed=50
        assert result["balance"] == 1050
        assert result["target_balance"] == -55
    return "打劫：目标并发转负时按 0 计算收益不崩溃"


# ─── 匹配器单元 ──────────────────────────────────────────


async def test_matcher_rob_message():
    from astrbot_plugin_point_system_by_whleague.utils.keyword_matcher import (
        is_rob_message,
        parse_rob_message,
    )

    from astrbot.api.message_components import At, AtAll, Plain

    assert is_rob_message([Plain("打劫"), At(qq="123")], ["打劫"], "bot") is True
    # 顺序无关：@ 在前
    assert is_rob_message([At(qq="123"), Plain("打劫")], ["打劫"], "bot") is True
    # 空白压缩
    assert is_rob_message([Plain(" 打劫 "), At(qq="123")], ["打劫"], "bot") is True
    # 多关键词命中任一
    assert is_rob_message([Plain("抢钱"), At(qq="123")], ["打劫", "抢钱"], "bot") is True
    # 无目标 / @all / @bot
    assert is_rob_message([Plain("打劫")], ["打劫"], "bot") is False
    assert is_rob_message([Plain("打劫"), AtAll()], ["打劫"], "bot") is False
    assert is_rob_message([Plain("打劫"), At(qq="bot")], ["打劫"], "bot") is False
    # 含字不触发
    assert is_rob_message([Plain("别打劫我"), At(qq="123")], ["打劫"], "bot") is False
    # 空关键词
    assert is_rob_message([Plain("打劫"), At(qq="123")], [], "bot") is False
    # parse 结构：@all 计入 invalid
    p = parse_rob_message([Plain("打劫"), At(qq="123"), AtAll()], ["打劫"], "bot")
    assert p["targets"] == ["123"]
    assert p["has_invalid_at"] is True and p["text_match"] is True
    return "匹配器：is_rob_message/parse_rob_message 形态判定"


# ─── handler 层 ──────────────────────────────────────────


async def test_matcher_real_enum_type():
    """真实平台组件的 type 是 str 枚举（str(枚举成员) 是全名而非成员值），
    解析必须用 == 直接比较；回归：v0.4.0 线上"打劫@群友没反应"。"""
    from enum import Enum

    from astrbot_plugin_point_system_by_whleague.utils.keyword_matcher import (
        is_rob_message,
        parse_rob_message,
    )

    class _T(str, Enum):
        Plain = "Plain"
        At = "At"

    class _P:
        type = _T.Plain
        text = "打劫"

    class _A:
        type = _T.At
        qq = "123"

    assert str(_T.Plain) != "Plain"  # 枚举 str() 是 "…Plain" 全名
    assert is_rob_message([_P(), _A()], ["打劫"], "bot") is True
    # @bot 自身被排除
    assert is_rob_message([_P(), _A()], ["打劫"], "123") is False
    # 文本不匹配不触发
    assert is_rob_message([type("_P2", (), {"type": _T.Plain, "text": "别打劫我"})(), _A()], ["打劫"], "bot") is False
    p = parse_rob_message([_P(), _A()], ["打劫"], "bot")
    assert p["targets"] == ["123"] and p["text_match"] is True
    return "匹配器：真实 str 枚举组件 type 兼容（回归 bug）"


async def test_handler_at_parsing():
    cfg = base_cfg()
    # 非群聊
    out = await _handle_rob(cfg, FakeEvent("u", msg="打劫"))
    assert out == ["打劫仅支持群聊"]
    # 无 @ + 关键词 → 用法
    out = await _handle_rob(cfg, FakeEvent("u", "1001", msg="打劫"))
    assert out == ["用法: 打劫 @目标"]
    # 无 @ + 非关键词 → 静默
    out = await _handle_rob(cfg, FakeEvent("u", "1001", msg="别打劫我"))
    assert out == []
    # @bot / @all
    out = await _handle_rob(cfg, FakeEvent("u", "1001", msg="打劫", at_self=True))
    assert out == ["不能打劫机器人/全体成员"]
    out = await _handle_rob(cfg, FakeEvent("u", "1001", msg="打劫", at_all=True))
    assert out == ["不能打劫机器人/全体成员"]
    # 打劫自己
    out = await _handle_rob(cfg, FakeEvent("u", "1001", msg="打劫", at_targets=["u"]))
    assert out == ["不能打劫自己"]
    # 多 @
    out = await _handle_rob(
        cfg, FakeEvent("u", "1001", msg="打劫", at_targets=["t1", "t2"])
    )
    assert out == ["一次只能打劫一个目标"]
    # 含字不触发（有 @ 但文本不匹配）
    out = await _handle_rob(
        cfg, FakeEvent("u", "1001", msg="别打劫我", at_targets=["t1"])
    )
    assert out == []
    return "打劫 handler：@ 解析（无/多/@bot/@all/自己/含字）"


async def test_handler_format_success_failure():
    async with TempDB() as t:
        cfg, ps, rs, lim = await _build(t)
        await _grant(ps, "10071", "1001", 1000)
        await _grant(ps, "10072", "1001", 2000)
        bot = FakeBot(member_card="小目标")
        with patch_random(random=0.1):
            ev = FakeEvent("10071", "1001", msg="打劫", at_targets=["10072"], bot=bot)
            out = await _handle_rob(cfg, ev, rs)
        text = "\n".join(out)
        assert "✅ 打劫小目标成功！" in text
        assert "抢得: +100 积分" in text
        assert "目标剩余: 1900 积分" in text
        assert "当前积分: 1100" in text
        assert "冷却: 10 分钟后可再次打劫" in text
        assert "固定" not in text and "动态" not in text  # 删去固定+动态明细
        # 失败反馈
        await _grant(ps, "10073", "1001", 1000)
        with patch_random(random=0.9):
            ev = FakeEvent("10073", "1001", msg="打劫", at_targets=["10072"], bot=bot)
            out = await _handle_rob(cfg, ev, rs)
        text = "\n".join(out)
        assert "💢 打劫失败！被小目标抓住了！" in text
        assert "成本: -50 积分" in text
        assert "当前积分: 950" in text
        assert "不退还" not in text  # 删去「不退还」标注
    return "打劫 handler：成功/失败反馈文案（含冷却行、无收益明细）"


async def test_handler_nickname_clean():
    async with TempDB() as t:
        cfg, ps, rs, lim = await _build(t)
        await _grant(ps, "10081", "1001", 1000)
        await _grant(ps, "10082", "1001", 2000)
        bot = FakeBot(member_card="恶意\n昵称\x00evil")
        with patch_random(random=0.1):
            ev = FakeEvent("10081", "1001", msg="打劫", at_targets=["10082"], bot=bot)
            out = await _handle_rob(cfg, ev, rs)
        text = "\n".join(out)
        assert "恶意昵称evil" in text  # 控制字符已剥离
        assert "\x00" not in text
    return "打劫 handler：恶意昵称控制字符剥离"


async def test_handler_no_cooldown_line():
    async with TempDB() as t:
        cfg = base_cfg(rob_cooldown=0)
        _, ps, rs, lim = await _build(t, cfg)
        await _grant(ps, "10091", "1001", 1000)
        await _grant(ps, "10092", "1001", 2000)
        with patch_random(random=0.1):
            ev = FakeEvent("10091", "1001", msg="打劫", at_targets=["10092"])
            out = await _handle_rob(cfg, ev, rs)
        assert "冷却" not in "\n".join(out)
    return "打劫 handler：rob_cooldown=0 无冷却行"


# ─── 集成 ────────────────────────────────────────────────


async def test_active_reward_skip_rob():
    from astrbot_plugin_point_system_by_whleague.handlers.active_reward import (
        ActiveRewardHandler,
    )

    async with TempDB() as t:
        cfg, ps, rs, lim = await _build(t, base_cfg(active_reward_enabled=True))
        daily = _DailyKwStub()
        plugin = _PluginStub(
            cfg, rob_service=rs, daily_kw=daily, limiter=lim, point_service=ps
        )
        h = ActiveRewardHandler(plugin)
        # 打劫形态消息：不触发每日口令与活跃奖励
        ev = FakeEvent("u", "1001", msg="打劫", at_targets=["10002"])
        await h.handle(ev)
        assert daily.called == 0
        assert ev.sent == []
        # 普通长消息：正常走每日口令流程（对照）
        cfg["active_reward_enabled"] = False
        ev2 = FakeEvent("u", "1001", msg="今天天气真不错啊好想出去玩")
        await h.handle(ev2)
        assert daily.called == 1
    return "集成：active_reward 跳过打劫形态消息"


async def test_reserved_keyword_rob():
    from astrbot_plugin_point_system_by_whleague.handlers.admin import AdminHandler

    plugin = _PluginStub(base_cfg())
    h = AdminHandler(plugin)
    reason = h._reserved_keyword_reason("打劫")
    assert reason is not None and "打劫" in reason
    assert h._reserved_keyword_reason("红包") is None  # 非保留字放行
    assert h._reserved_keyword_reason("whl打劫") is not None  # 组合形态拦截
    return "集成：口令保留字含 keyword_rob"


async def test_command_map_entry():
    from astrbot_plugin_point_system_by_whleague.services.command_map import (
        build_map_data,
    )

    data = build_map_data(base_cfg())
    entries = [e for s in data["sections"] for e in s["entries"]]
    rob = [e for e in entries if "打劫" in e["usage"]]
    assert rob, "指令图缺少打劫条目"
    assert rob[0]["usage"] == "打劫 @目标"
    assert "打劫群友抢积分" in rob[0]["desc"]
    return "集成：指令图含打劫条目"


async def test_tx_conn_helpers_no_deadlock():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.db.dao import PointDAO

        async def _tx(conn):
            await PointDAO.insert_rob_record(conn, "a", "b", "1001", 50, 10, True)
            return await PointDAO.count_robs_today(conn, "a")

        cnt = await t.db.execute_transaction(_tx)
        assert cnt == 1
        rec = await t.db.fetchone("SELECT * FROM rob_records WHERE qq='a'")
        assert rec["target_qq"] == "b" and rec["stolen"] == 10 and rec["success"] == 1
    return "集成：事务回调内 count/insert 不触发死锁保护"


TESTS = [
    ("success_reward_formula", test_success_reward_formula),
    ("failure_cost_and_record", test_failure_cost_and_record),
    ("gates", test_gates),
    ("daily_limit", test_daily_limit),
    ("cooldown_success_failure", test_cooldown_enters_after_success_and_failure),
    ("target_negative_title", test_target_negative_title_linkage),
    ("failure_guard_atomic", test_failure_guard_atomic_rollback),
    ("no_cooldown_when_zero", test_no_cooldown_when_zero),
    ("target_daily_limit", test_target_daily_limit),
    ("target_limit_dynamic", test_target_limit_dynamic),
    ("target_limit_dynamic_base_zero_clamped", test_target_limit_dynamic_base_zero_clamped),
    ("target_limit_dynamic_failure_counts", test_target_limit_dynamic_failure_counts),
    ("target_limit_zero_decay", test_target_limit_zero_decay_still_active),
    ("decay_sequence", test_decay_sequence),
    ("decay_failure_not_counted", test_decay_failure_not_counted),
    ("decay_zero", test_decay_zero),
    ("decay_one_floor", test_decay_one_floor),
    ("zero_stolen_defensive", test_zero_stolen_config_defensive),
    ("negative_target_defensive", test_negative_target_points_defensive),
    ("matcher_rob", test_matcher_rob_message),
    ("matcher_real_enum", test_matcher_real_enum_type),
    ("handler_at_parsing", test_handler_at_parsing),
    ("handler_format", test_handler_format_success_failure),
    ("handler_nickname_clean", test_handler_nickname_clean),
    ("handler_no_cooldown_line", test_handler_no_cooldown_line),
    ("active_reward_skip", test_active_reward_skip_rob),
    ("reserved_keyword", test_reserved_keyword_rob),
    ("command_map_entry", test_command_map_entry),
    ("tx_conn_helpers", test_tx_conn_helpers_no_deadlock),
]
