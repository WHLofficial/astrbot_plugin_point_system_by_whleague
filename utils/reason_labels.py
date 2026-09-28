"""point_transactions.reason → 展示用中文标签。

流水落库的唯一入口是 services/point_service.py 的 change_balance，reason 由各
service 写入（签到/每日口令/活跃奖励/抽奖/兑换/打劫/管理员加减分/竞猜同步）。
这里只做展示层翻译，不参与任何写入或统计口径。

未登记的 reason 原样返回：新增业务 reason 时最多显示成英文码，不会显示为空，
也不会因为漏登记而丢行。tests/s20_sync.py 有断言保证竞猜两个码在表内。
"""

REASON_LABELS = {
    "签到": "签到",
    "daily_keyword": "每日口令",
    "active_reward": "活跃奖励",
    "admin_add": "管理员加分",
    "admin_sub": "管理员扣分",
    "rob_reward": "打劫所得",
    "rob_lost": "打劫损失",
    "rob_cost": "打劫花费",
    "lottery_cost": "抽奖消耗",
    "lottery_reward": "抽奖奖励",
    "redeem_cost": "兑换消耗",
    "redeem_refund": "兑换退回",
    # 竞猜系统同步入账（services/sync_service.py，伪群号 group_id='guess'）
    "guess_reward": "竞猜奖励",
    "guess_reversal": "竞猜冲正",
    "easter_unlucky": "彩蛋(非酋)",
}


def label_for(reason) -> str:
    """把流水 reason 转成展示文案；未知 reason 原样返回。

    Args:
        reason: point_transactions.reason 的值（可能为 None）。

    Returns:
        中文标签，或未登记时的原字符串。
    """
    if reason is None:
        return ""
    text = str(reason)
    return REASON_LABELS.get(text, text)
