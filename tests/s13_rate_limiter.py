"""S13 限流器：用户/群冷却语义、cooldown<=0 直通、剪枝、clear。"""

import time


async def test_user_cooldown_semantics():
    from astrbot_plugin_point_system_by_whleague.utils.rate_limiter import RateLimiter

    lim = RateLimiter()
    assert lim.check_user("act", "u1", "G1", 60) is True
    assert lim.check_user("act", "u1", "G1", 60) is False  # 冷却期内
    assert lim.check_user("act", "u1", "G1", 60) is False
    # 其他用户/动作互不影响
    assert lim.check_user("act", "u2", "G1", 60) is True
    assert lim.check_user("other", "u1", "G1", 60) is True
    # 冷却过期后放行
    key = "act:u1:G1"
    lim._user_cooldowns[key] = time.time() - 61
    assert lim.check_user("act", "u1", "G1", 60) is True
    return "限流器：用户冷却/动作与用户隔离/过期放行"


async def test_group_cooldown_semantics():
    from astrbot_plugin_point_system_by_whleague.utils.rate_limiter import RateLimiter

    lim = RateLimiter()
    assert lim.check_group("act", "G1", 10) is True
    assert lim.check_group("act", "G1", 10) is False  # 全局冷却
    assert lim.check_group("act", "G2", 10) is True  # 群间独立
    assert lim.check_group("other", "G1", 10) is True  # 动作独立
    return "限流器：全局冷却/群间独立/动作独立"


async def test_zero_cooldown_bypass():
    from astrbot_plugin_point_system_by_whleague.utils.rate_limiter import RateLimiter

    lim = RateLimiter()
    for _ in range(5):
        assert lim.check_user("act", "u1", "G1", 0) is True
        assert lim.check_group("act", "G1", 0) is True
    return "限流器：cooldown<=0 始终放行"


async def test_prune_and_clear():
    from astrbot_plugin_point_system_by_whleague.utils.rate_limiter import RateLimiter

    lim = RateLimiter()
    now = time.time()
    # 超过阈值 2048 后触发剪枝
    for i in range(2500):
        lim._user_cooldowns[f"k{i}"] = now - 7200  # 全部过期
    lim._group_cooldowns["g1"] = now - 7200
    assert lim.check_user("act", "u1", "G1", 60) is True  # 触发 _prune
    assert len(lim._user_cooldowns) <= 2048, len(lim._user_cooldowns)
    assert "g1" not in lim._group_cooldowns
    # 未过期键保留
    lim._user_cooldowns["fresh"] = now - 10
    lim._user_cooldowns["fresh2"] = now - 10
    lim.check_user("act", "u1", "G1", 60)
    assert "fresh" in lim._user_cooldowns and "fresh2" in lim._user_cooldowns
    # clear
    lim.clear()
    assert lim._user_cooldowns == {} and lim._group_cooldowns == {}
    return "限流器：剪枝只删过期键、clear 清空"


TESTS = [
    ("user_cooldown", test_user_cooldown_semantics),
    ("group_cooldown", test_group_cooldown_semantics),
    ("zero_cooldown", test_zero_cooldown_bypass),
    ("prune_and_clear", test_prune_and_clear),
]
