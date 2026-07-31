# 积分系统插件 — 完整设计文档

> 版本: v1.0  
> 更新时间: 2026-07-30

---

## 目录

1. [功能总览](#1-功能总览)
2. [数据表设计](#2-数据表设计)
3. [配置项表](#3-配置项表)
4. [代码架构](#4-代码架构)
5. [分层调用链](#5-分层调用链)
6. [Handler 注册方案](#6-handler-注册方案)
7. [核心业务逻辑](#7-核心业务逻辑)
8. [后台定时任务](#8-后台定时任务)
9. [安全与性能措施](#9-安全与性能措施)
10. [文件清单与预估代码量](#10-文件清单与预估代码量)
11. [完整指令清单](#11-完整指令清单)

---

## 1. 功能总览

| # | 功能 | 简述 |
|---|---|---|
| 1 | **每日签到** | 固定/随机积分，首次签到奖励、每日首签奖励、连签奖励（有上限）、每 7 天奖励，签到回复尾附每日运势 |
| 2 | **彩蛋事件** | 签到触发欧皇/非酋事件，±大量积分，有保底计数器（欧皇优先，计数器独立） |
| 3 | **活跃奖励** | 群成员发送合规普通消息（非空、含指令前缀、达到字数下限），概率获得积分，有用户冷却 + 全局冷却 |
| 4 | **每日口令** | 管理员当天动态设置关键词+积分，消息含该关键词即得（每人每天限 1 次） |
| 5 | **日期口令** | 预配置日期范围+关键词+概率，签到联动触发，支持跨年 |
| 6 | **无前缀触发** | 不需要命令前缀，消息含配置关键词即可触发的签到/抽奖/排行 |
| 7 | **群内排行** | 优先当前群正积分用户 Top 10，群注册用户不足 3 人时回退全局 Top 10 |
| 8 | **个人抽奖** | 固定消耗+口令验证，五档权重概率（特等奖/一等奖/二等奖/三等奖/参与奖） |
| 9 | **兑换玩法** | 积分换物品，库存管理（原子扣减），兑换记录+核销状态双向切换，限时折扣 |
| 10 | **管理员指令** | 独立管理员名单（bot 主人自动为管理员），@或 QQ 号增减积分，管理兑换/口令/配置 |
| 11 | **生日系统** | 记录生日（MM-DD / MM月DD日），生日签到奖励，定时播报当日寿星 |
| 12 | **负分联动** | 负分仅可签到恢复积分，不能抽奖/兑换/活跃奖励，自动分配/撤销"群女仆X号"头衔 |
| 13 | **自动备份** | 多本地目标路径，定时备份（默认凌晨 3:00），备份前 wal_checkpoint |
| 14 | **积分流水** | 每笔积分变动自动记录（时间、原因、变动值、余额），用户可查明细 |
| 15 | **签到统计** | 查询今日签到人数、签到率、首签用户、连签王 |
| 16 | **每日运势** | 签到回复尾部自动附带运势文本（同用户同天一致，纯趣味不涉及积分） |
| 17 | **兑换折扣** | 管理员可为兑换物品设置限时折扣价 |

---

## 2. 数据表设计

### 2.1 users — 用户表

```sql
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    group_id TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    points INTEGER NOT NULL DEFAULT 0,
    total_earned INTEGER NOT NULL DEFAULT 0,
    last_sign_date TEXT,
    consecutive_days INTEGER NOT NULL DEFAULT 0,
    max_consecutive_days INTEGER NOT NULL DEFAULT 0,
    total_sign_days INTEGER NOT NULL DEFAULT 0,
    birthday TEXT,
    birthday_year INTEGER,
    birthday_bonus_claimed INTEGER NOT NULL DEFAULT 0,
    lucky_pity INTEGER NOT NULL DEFAULT 0,
    unlucky_pity INTEGER NOT NULL DEFAULT 0,
    negative_title_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(qq, group_id)
);
CREATE INDEX IF NOT EXISTS idx_users_group_points ON users(group_id, points DESC);
CREATE INDEX IF NOT EXISTS idx_users_qq ON users(qq);
```

### 2.2 sign_in_log — 签到流水表

```sql
CREATE TABLE IF NOT EXISTS sign_in_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    group_id TEXT NOT NULL,
    sign_date TEXT NOT NULL,
    points_earned INTEGER NOT NULL DEFAULT 0,
    base_points INTEGER NOT NULL DEFAULT 0,
    bonus_first_sign INTEGER NOT NULL DEFAULT 0,
    bonus_day_first INTEGER NOT NULL DEFAULT 0,
    bonus_consecutive INTEGER NOT NULL DEFAULT 0,
    bonus_weekly INTEGER NOT NULL DEFAULT 0,
    easter_event_type TEXT,
    easter_points INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(qq, group_id, sign_date)
);
CREATE INDEX IF NOT EXISTS idx_sign_in_date ON sign_in_log(group_id, sign_date);
```

### 2.3 lottery_record — 抽奖流水表

```sql
CREATE TABLE IF NOT EXISTS lottery_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    group_id TEXT NOT NULL,
    cost INTEGER NOT NULL,
    reward_amount INTEGER NOT NULL,
    is_win INTEGER NOT NULL DEFAULT 0,
    tier_label TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_lottery_group ON lottery_record(group_id, created_at DESC);
```

### 2.4 point_transactions — 积分流水表

```sql
CREATE TABLE IF NOT EXISTS point_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    group_id TEXT NOT NULL,
    amount INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    reason TEXT NOT NULL,
    ref_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_pt_qq_group ON point_transactions(qq, group_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pt_reason ON point_transactions(reason);
```

#### reason 编码表

| 编码 | 含义 |
|---|---|
| `signin_base` | 签到基础分 |
| `signin_first` | 首次签到奖励 |
| `signin_day_first` | 每日首签奖励 |
| `signin_consecutive` | 连签奖励 |
| `signin_weekly` | 每7天奖励 |
| `signin_birthday` | 生日签到奖励 |
| `signin_date_reward` | 日期口令奖励 |
| `easter_lucky` | 彩蛋·欧皇 |
| `easter_unlucky` | 彩蛋·非酋 |
| `lottery_cost` | 抽奖消耗 |
| `lottery_reward` | 抽奖奖励 |
| `redeem_cost` | 兑换消耗 |
| `active_reward` | 活跃奖励 |
| `daily_keyword` | 每日口令奖励 |
| `admin_add` | 管理员加分 |
| `admin_sub` | 管理员扣分 |

### 2.5 redeem_items — 兑换物品表

```sql
CREATE TABLE IF NOT EXISTS redeem_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    cost INTEGER NOT NULL,
    stock INTEGER NOT NULL DEFAULT -1,
    discount_price INTEGER,
    discount_end_time TEXT,
    image_url TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
```

### 2.6 redeem_records — 兑换记录表

```sql
CREATE TABLE IF NOT EXISTS redeem_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_no TEXT NOT NULL UNIQUE,
    qq TEXT NOT NULL,
    group_id TEXT NOT NULL,
    item_id INTEGER NOT NULL REFERENCES redeem_items(id),
    item_name TEXT NOT NULL,
    item_cost INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    admin_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    verified_at TEXT,
    verified_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_redeem_records_qq ON redeem_records(qq);
CREATE INDEX IF NOT EXISTS idx_redeem_records_status ON redeem_records(status);
CREATE INDEX IF NOT EXISTS idx_redeem_records_group ON redeem_records(group_id);
```

### 2.7 admins — 管理员表

```sql
CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    group_id TEXT,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(qq, group_id)
);
```

### 2.8 date_rewards — 日期口令配置表

```sql
CREATE TABLE IF NOT EXISTS date_rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_date TEXT NOT NULL,
    end_date TEXT,
    keyword TEXT NOT NULL,
    points INTEGER NOT NULL,
    probability REAL NOT NULL DEFAULT 1.0,
    description TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
```

### 2.9 easter_events — 彩蛋事件配置表

```sql
CREATE TABLE IF NOT EXISTS easter_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    probability REAL NOT NULL,
    points_min INTEGER NOT NULL,
    points_max INTEGER NOT NULL,
    pity_count INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
```

### 2.10 backup_configs — 备份配置表

```sql
CREATE TABLE IF NOT EXISTS backup_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_path TEXT NOT NULL,
    schedule_time TEXT NOT NULL DEFAULT '03:00',
    is_active INTEGER NOT NULL DEFAULT 1,
    last_backup_time TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
```

### 2.11 birthday_announce_log — 生日播报去重表

```sql
CREATE TABLE IF NOT EXISTS birthday_announce_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL,
    announce_date TEXT NOT NULL,
    announced_qqs TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(group_id, announce_date)
);
```

### 2.12 daily_keyword — 每日口令配置表

```sql
CREATE TABLE IF NOT EXISTS daily_keyword (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    points INTEGER NOT NULL,
    set_by TEXT NOT NULL,
    set_date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(group_id, set_date)
);
```

### 2.13 daily_keyword_claim — 每日口令领取记录表

```sql
CREATE TABLE IF NOT EXISTS daily_keyword_claim (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kw_id INTEGER NOT NULL REFERENCES daily_keyword(id),
    qq TEXT NOT NULL,
    group_id TEXT NOT NULL,
    points_earned INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(kw_id, qq)
);
CREATE INDEX IF NOT EXISTS idx_dk_claim_group ON daily_keyword_claim(group_id, qq);
```

### 2.14 plugin_config — KV 运行时配置表

```sql
CREATE TABLE IF NOT EXISTS plugin_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
```

---

## 3. 配置项表

所有配置存储在 `plugin_config` 表中，管理员通过 `/设置` 指令动态修改，热生效。

| Key | 类型 | 默认值 | 说明 |
|---|---|---|---|
| **签到** | | | |
| signin_fixed_mode | bool | false | true=固定 / false=随机 |
| signin_fixed_points | int | 10 | 固定签到积分 |
| signin_random_min | int | 1 | 随机下限 |
| signin_random_max | int | 20 | 随机上限 |
| signin_first_bonus | int | 50 | 首次签到额外奖励 |
| signin_day_first_bonus | int | 30 | 每日首签额外奖励 |
| signin_consecutive_max | int | 30 | 连签天数上限 |
| signin_consecutive_bonus_per_day | int | 5 | 连签每日递加值 |
| signin_weekly_bonus | int | 100 | 每 7 天额外奖励 |
| **活跃奖励** | | | |
| active_reward_enabled | bool | true | 开关 |
| active_reward_probability | float | 0.05 | 触发概率 (0~1) |
| active_reward_points | int | 1 | 每次奖励积分 |
| active_reward_cooldown | int | 60 | 同用户冷却秒数 |
| active_reward_min_length | int | 3 | 消息最小字数 |
| active_reward_global_cooldown | int | 10 | 全群全局冷却秒数 |
| **抽奖** | | | |
| lottery_enabled | bool | true | 开关 |
| lottery_cost | int | 100 | 单次消耗积分 |
| lottery_passphrase | str | "whl" | 抽奖口令 |
| lottery_tiers | json | (见下) | 五档配置 |
| **负分** | | | |
| negative_disable_lottery | bool | true | 负分禁止抽奖 |
| **生日** | | | |
| birthday_bonus_points | int | 100 | 生日签到奖励 |
| birthday_announce_time | str | "08:00" | 每日播报时间 |
| **备份** | | | |
| backup_enabled | bool | true | 开关 |
| **关键词** | | | |
| keyword_sign | json | ["签到","sign","打卡"] | 签到触发关键词列表 |
| keyword_lottery | json | ["抽奖","lottery"] | 抽奖触发关键词列表 |

### lottery_tiers JSON 默认值

```json
{
  "tiers": [
    {"label":"特等奖", "weight":1,  "multiplier":10.0, "emoji":"👑"},
    {"label":"一等奖", "weight":5,  "multiplier":5.0,  "emoji":"🥇"},
    {"label":"二等奖", "weight":15, "multiplier":2.0,  "emoji":"🥈"},
    {"label":"三等奖", "weight":30, "multiplier":1.2,  "emoji":"🥉"},
    {"label":"参与奖", "weight":49, "multiplier":0.0,  "emoji":"💫"}
  ]
}
```

权重决定概率，`reward = cost × multiplier`，参与奖 ×0.0 即消耗不返还。

---

## 4. 代码架构

```
astrbot_plugin_point_system_by_whleague/
├── __init__.py
├── main.py                       # 插件入口，Star 子类，所有 @filter.* 装饰器方法（~15 个 stub）
├── metadata.yaml
├── requirements.txt              # aiosqlite
├── DESIGN.md                     # 本设计文档
├── db/
│   ├── __init__.py
│   ├── connection.py             # 连接管理器（单例/WAL/Lock + 自动重试）
│   ├── schema.py                 # DDL 初始化 + 版本管理
│   └── dao.py                    # 数据访问层（~35 个参数化查询方法）
├── handlers/                     # 消息处理层（普通类，由 main.py 委托调用）
│   ├── __init__.py
│   ├── sign_in.py                # 签到：参数解析 + 回复格式化
│   ├── lottery.py                # 抽奖：口令验证 + 金额固定 + 回复格式化
│   ├── redeem.py                 # 兑换：物品增删改查 + 兑换 + 核销 + 记录查询
│   ├── ranking.py                # 排行：群 → 全局回退
│   ├── admin.py                  # 管理指令：加/减分、管理物品、设置口令/配置
│   ├── birthday.py               # 生日：设置、查询
│   └── active_reward.py          # 活跃奖励 + 每日口令（拦截群消息）
├── services/                     # 业务逻辑层
│   ├── __init__.py
│   ├── point_service.py          # 核心积分操作（加/减/查/负分联动/自动记流水）
│   ├── sign_in_service.py        # 签到积分计算（协调彩蛋/生日/日期口令/连签/周签）
│   ├── lottery_service.py        # 五档权重随机抽奖
│   ├── easter_service.py         # 彩蛋概率引擎 + 独立保底计数器
│   ├── redeem_service.py         # 库存原子递减 + 编号生成
│   ├── ranking_service.py        # 排行查询 + 签到统计
│   ├── date_reward_service.py    # 日期匹配（支持跨年）+ 概率判定
│   ├── daily_keyword_service.py  # 每日口令领取 + 去重
│   ├── birthday_service.py       # 生日检测 + 定时播报
│   └── backup_service.py         # 多目标文件拷贝备份
├── utils/
│   ├── __init__.py
│   ├── keyword_matcher.py        # 无前缀关键词匹配器
│   ├── rate_limiter.py           # 内存限速器（每用户冷却 + 全局冷却）
│   ├── security.py               # 输入清洗 + 数值校验
│   ├── fortune.py                # 每日运势文本生成
│   └── helpers.py                # 日期格式化、编号生成等
└── config/
    ├── __init__.py
    └── defaults.py               # 全量默认配置字典
```

---

## 5. 分层调用链

```
[群消息]
    │
    ├─ @filter.regex(签到关键词) ───────────── main.py 委托 →
    ├─ @filter.regex(抽奖关键词) ───────────── main.py 委托 →
    ├─ @filter.regex(排行关键词) ───────────── main.py 委托 →
    ├─ @filter.command(兑换/设置生日/加分...) ─── main.py 委托 →
    ├─ @filter.event_message_type(GROUP) ───── main.py 委托 →
    │
    ▼
handler/*.py（参数解析 + 权限校验 + 回复格式化）
    │
    ▼
services/*.py（业务规则 / 概率计算 / 跨表事务）
    │
    ▼
dao.py（参数化 SQL，零业务逻辑）
    │
    ▼
SQLite（WAL 模式 + asyncio.Lock + 原子 UPDATE）
    │
    ▼
yield event.plain_result(...) → 回复用户
```

### main.py stub 示例

```python
# main.py — 仅注册 + 委托，不包含具体业务逻辑
class PointPlugin(Star):
    async def initialize(self):
        self.db = DatabaseManager()
        await self.db.init()
        self.limiter = RateLimiter()
        await self._init_services()
        await self._start_cron_jobs()

    @filter.regex(r"签到|sign|打卡")
    async def on_sign_in(self, event: AstrMessageEvent):
        await self.handlers["sign_in"].handle(event)

    @filter.regex(r"抽奖|lottery")
    async def on_lottery(self, event: AstrMessageEvent):
        await self.handlers["lottery"].handle(event)

    @filter.command("加分")
    @filter.permission_type(PermissionType.ADMIN)
    async def cmd_add_points(self, event: AstrMessageEvent):
        await self.handlers["admin"].handle_add_points(event)

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        await self.handlers["active_reward"].handle(event)
```

---

## 6. Handler 注册方案

| 功能 | 注册方式 | 说明 |
|---|---|---|
| 签到 | `@filter.regex("签到\|sign\|打卡")` | 无前缀触发 |
| 抽奖 | `@filter.regex("抽奖\|lottery")` | 无前缀触发（handler 内验口令） |
| 排行 | `@filter.regex("排行\|排名\|积分榜")` | 无前缀触发 |
| 兑换系列 | `@filter.command("兑换")` | 需命令前缀 |
| 加分 | `@filter.command("加分")` + `@filter.permission_type(ADMIN)` | 需前缀 + 管理员 |
| 扣分 | `@filter.command("扣分")` + `@filter.permission_type(ADMIN)` | 需前缀 + 管理员 |
| 修改兑换 | `@filter.command("修改兑换")` + `@filter.permission_type(ADMIN)` | 需前缀 + 管理员 |
| 核销 | `@filter.command("核销")` + `@filter.permission_type(ADMIN)` | 需前缀 + 管理员 |
| 设置生日 | `@filter.command("设置生日")` | 需前缀 |
| 查生日 | `@filter.command("查生日")` | 需前缀 |
| 设置今日口令 | `@filter.command("设置今日口令")` + `@filter.permission_type(ADMIN)` | 需前缀 + 管理员 |
| 清除今日口令 | `@filter.command("清除今日口令")` + `@filter.permission_type(ADMIN)` | 需前缀 + 管理员 |
| 设置配置 | `@filter.command("设置")` + `@filter.permission_type(ADMIN)` | 需前缀 + 管理员 |
| 查看配置 | `@filter.command("查看配置")` + `@filter.permission_type(ADMIN)` | 需前缀 + 管理员 |
| 流水 | `@filter.command("流水")` | 需前缀 |
| 签到统计 | `@filter.command("签到统计")` | 需前缀 |
| 设置折扣 | `@filter.command("设置折扣")` + `@filter.permission_type(ADMIN)` | 需前缀 + 管理员 |
| 清除折扣 | `@filter.command("清除折扣")` + `@filter.permission_type(ADMIN)` | 需前缀 + 管理员 |
| 活跃奖励 | `@filter.event_message_type(GROUP_MESSAGE)` | 拦截全群消息 |

### 处理器互斥策略

```
消息进入 → 按优先级判定：
  1. 签到关键词命中 → sign_in 处理 → stop_event（阻止后续）
  2. 否 → 抽奖关键词+口令同时命中 → lottery 处理（但额外检查是否含签到关键词，含则跳过）
  3. 否 → active_reward（额外跳过含签到关键词 或 同时含抽奖关键词+口令的消息）
```

---

## 7. 核心业务逻辑

### 7.1 签到积分计算

```
points_earned = 0

# 1. 基础分
if signin_fixed_mode:
    base = signin_fixed_points
else:
    base = random(signin_random_min, signin_random_max)
points_earned += base

# 2. 首次签到奖励（total_sign_days == 0）
if total_sign_days == 0:
    points_earned += signin_first_bonus

# 3. 每日首签奖励（本群今日第一个签到）
if no sign_in_log for today in this group:
    points_earned += signin_day_first_bonus

# 4. 连签奖励（有上限）
effective = min(consecutive_days, signin_consecutive_max)
points_earned += effective * signin_consecutive_bonus_per_day

# 5. 每7天奖励
if consecutive_days > 0 and consecutive_days % 7 == 0:
    points_earned += signin_weekly_bonus

# 6. 彩蛋（欧皇/非酋，最多一个，欧皇优先，计数器独立）
easter = easter_service.trigger(user.lucky_pity, user.unlucky_pity)
points_earned += easter.points

# 7. 生日奖励
if today == birthday:
    points_earned += birthday_bonus_points

# 8. 日期口令
date_bonus = date_reward_service.check(sign_date, message)
points_earned += date_bonus

# 统一写流水
point_service.add(qq, group_id, points_earned, reason="签到", ref_id=sign_in_log_id)
```

### 7.2 彩蛋保底逻辑

```python
def trigger(lucky_pity, unlucky_pity):
    lucky_pity += 1
    unlucky_pity += 1
    
    max_lucky_pity = max(e.pity_count for e in active_lucky_events)
    max_unlucky_pity = max(e.pity_count for e in active_unlucky_events)
    
    force_lucky = lucky_pity >= max_lucky_pity
    force_unlucky = unlucky_pity >= max_unlucky_pity
    
    # Lucky checked first (priority)
    if force_lucky or (not force_unlucky and random() <= lucky_prob):
        lucky_pity = 0  # only lucky resets
        return pick_random(active_lucky_events)
    elif force_unlucky or random() <= unlucky_prob:
        unlucky_pity = 0  # only unlucky resets
        return pick_random(active_unlucky_events)
    else:
        return None  # no event
```

### 7.3 抽奖——五档权重

```python
weights = [tier.weight for tier in tiers]
total = sum(weights)
rand = random() * total
cumulative = 0
for tier in tiers:
    cumulative += tier.weight
    if rand < cumulative:
        reward = int(lottery_cost * tier.multiplier)
        # reward = cost × multiplier, 参与奖 ×0.0
        point_service.subtract(qq, group_id, lottery_cost, reason="lottery_cost")
        point_service.add(qq, group_id, reward, reason="lottery_reward")
        break
```

### 7.4 无前缀匹配规则

```python
# 签到：消息包含 keyword_sign 列表中的任一关键词
any(kw in msg for kw in config["keyword_sign"])

# 抽奖：消息同时包含 lottery_passphrase 和 keyword_lottery 任一关键词
(passphrase in msg) and any(kw in msg for kw in config["keyword_lottery"])
```

### 7.5 负分头衔联动

```
变负（points 从 >=0 变为 <0）：
  1. BEGIN TRANSACTION
  2. SELECT negative_title_id FROM users WHERE group_id=? AND negative_title_id IS NOT NULL
  3. 找到最小未占用的正整数 → new_id
  4. UPDATE users SET negative_title_id=new_id WHERE qq=? AND group_id=?
  5. set_group_special_title(qq, group_id, f"群女仆{new_id}号")
  6. COMMIT

回正（points 从 <0 变为 >=0）：
  1. 读取 negative_title_id
  2. set_group_special_title(qq, group_id, "")
  3. UPDATE users SET negative_title_id=NULL

懒加载恢复（交互时检测）：
  用户 points < 0 但 negative_title_id IS NULL → 重新分配
```

### 7.6 负分用户限制

```python
if user.points < 0:
    allowed_operations = ["sign_in", "admin_add"]
    # lottery/ redeem/ active_reward/ daily_keyword → 拒绝
```

### 7.7 活跃奖励判定流程

```
@filter.event_message_type(GROUP_MESSAGE)
     ↓
1. 消息发送者是 bot 自身？→ 跳过
2. 消息字数 < active_reward_min_length？→ 跳过
3. 消息含签到关键词？→ 跳过（签到优先，由 sign_in 处理）
4. 消息同时含口令+抽奖关键词？→ 跳过（由 lottery 处理）
5. 用户 points < 0？→ 跳过（负分仅可签到）
6. 用户冷却未到？→ 跳过（rate_limiter）
7. 全局冷却未到？→ 跳过（rate_limiter, 全群每 N 秒最多 1 次）
8. 随机概率命中？→ 发奖 + 回复消息

每日口令独立于活跃奖励并列检查（同一消息可触发两者）
```

### 7.8 排名回退逻辑

```python
def get_ranking(group_id, top_n=10):
    group_users = dao.get_top_n_by_group(group_id, top_n, min_points=1)
    if len(group_users) >= 3:
        return group_users  # 群排行
    # 本群不足 3 人，回退全局
    global_users = dao.get_top_n_global(top_n, min_points=1)
    return global_users  # 全局排行
```

### 7.9 兑换核销（双向切换）

```python
@filter.command("核销")
async def cmd_verify(self, event: AstrMessageEvent):
    record_no = extract_arg(event)
    record = dao.get_redeem_record(record_no)
    if record.status == 'pending':
        dao.update_record_status(record_no, 'verified', admin_qq, note)
    else:
        dao.update_record_status(record_no, 'pending', None, note)
```

### 7.10 兑换折扣

```python
def get_effective_price(item):
    if item.discount_price is not None and item.discount_end_time is not None:
        if datetime.now() < parse_time(item.discount_end_time):
            return item.discount_price
    return item.cost
```

### 7.11 兑换编号生成

```python
async def generate_record_no(dao):
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"R{today}-"
    count = await dao.count_records_by_prefix(prefix)
    return f"{prefix}{count + 1:04d}"
# R20260730-0001, R20260730-0002, ...
```

### 7.12 库存原子扣减

```python
async def deduct_stock(item_id, quantity):
    sql = """
    UPDATE redeem_items 
    SET stock = stock - ? 
    WHERE id = ? AND (stock = -1 OR stock >= ?)
    """
    affected = await dao.execute(sql, (quantity, item_id, quantity))
    if affected == 0:
        raise InsufficientStockError("库存不足")
```

### 7.13 每日运势

```python
# utils/fortune.py
import random

LEVELS = [
    ("上上签", 5, "宜签到，宜抽奖，今日鸿运当头！"),
    ("上签", 10, "运势不错，适合大胆一搏！"),
    ("中吉", 15, "平稳中有惊喜，值得期待。"),
    ("中平", 25, "平淡是真，稳扎稳打。"),
    ("末吉", 25, "稍安勿躁，好运在路上。"),
    ("末签", 15, "诸事不宜？睡大觉才是正道。"),
    ("大凶", 5, "非酋附体，建议签到转运。"),
]

def get_fortune(qq: str, date_str: str) -> dict:
    seed = hash(f"{qq}_{date_str}")
    rng = random.Random(seed)
    level, weight, _ = rng.choices(LEVELS, weights=[w for _, w, _ in LEVELS])[0]
    lucky_num = rng.randint(1, 99)
    advice = rng.choice(ADVICE_LIST)
    return {"level": level, "lucky_number": lucky_num, "advice": advice}

def format_fortune(qq, date_str, user_name) -> str:
    f = get_fortune(qq, date_str)
    return (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔮 {user_name} 的今日运势\n"
        f"🍀 {f['level']}\n"
        f"📝 {f['advice']}\n"
        f"🔢 幸运数字: {f['lucky_number']}"
    )
```

---

## 8. 后台定时任务

| 任务 | 调度方式 | Cron | 说明 |
|---|---|---|---|
| 备份 | APScheduler cron | `config.backup_time` | 遍历 `backup_dirs`，`VACUUM INTO` 生成一致快照到各目标 |

### 定时任务注意事项

- 备份/生日播报/每日刷新统一按**宿主机本地时区**（cron 不指定时区，跟随系统）
- `terminate()` 中清理所有定时任务
- 备份使用 `VACUUM INTO`（含 WAL 数据），目标已存在时自动追加序号
- 备份目标目录不存在时自动创建

---

## 9. 安全与性能措施

### 性能

| 层级 | 措施 |
|---|---|
| 数据库 | WAL 模式（读写不互锁） |
| 索引 | `(group_id, points DESC)` 加速排行；`(group_id, sign_date)` 加速签到查重；`(qq, group_id, created_at DESC)` 流水翻页 |
| 查询 | 只查必要列（不用 `SELECT *`）；所有列表查询带 `LIMIT`；用 `COUNT` 而非取全行 |
| 缓存 | 配置项和管理员列表启动时读入内存字典，写入时同步刷新 |
| 冷却 | 限速全程内存操作（dict），不写 DB |
| 连接 | 单连接 + `asyncio.Lock` 串行化写操作 |

### 鲁棒性

| 层级 | 措施 |
|---|---|
| 并发写 | `asyncio.Lock` 包裹整个事务 + 写冲突自动重试（最多 3 次） |
| 原子操作 | `UPDATE users SET points = points + ? WHERE ...` 不先 SELECT |
| 库存安全 | `UPDATE redeem_items SET stock = stock - ? WHERE id=? AND (stock=-1 OR stock>=?)` |
| 容错 | 每个 handler try-except → 记日志 → 回复用户友好提示，不崩溃 |
| 数据完整 | 签到多步操作（积分+流水+日志）在一个事务内完成 |
| 防重复 | 签到日期 UNIQUE 约束防同天重复 |
| 防滥用 | 每用户每操作独立冷却 + 全群全局冷却 |
| 输入校验 | 积分范围检查、QQ 号数字校验、字符串截断（最长 200 字） |
| SQL 注入 | 所有 DAO 使用 `?` 占位符，零字符串拼接 |
| 生命周期 | `initialize()` 建表+启动任务；`terminate()` 关闭连接+清理任务 |
| 备份 | 备份前 `wal_checkpoint`；目录不存在时自动创建 |

---

## 10. 文件清单与预估代码量

| 文件 | 预估行数 | 说明 |
|---|---|---|
| `__init__.py` | 0 | 空 |
| `main.py` | 120 | Star 子类，注册 handler stub + 后台任务 + 生命周期 |
| `metadata.yaml` | 10 | 插件元数据 |
| `requirements.txt` | 1 | `aiosqlite` |
| `db/connection.py` | 60 | 连接管理 + WAL + Lock + 重试 |
| `db/schema.py` | 120 | 13 表 DDL + 索引 + 默认彩蛋/配置数据 |
| `db/dao.py` | 350 | ~35 个参数化查询方法 |
| `config/defaults.py` | 60 | 全量配置字典 |
| `utils/keyword_matcher.py` | 30 | 关键词匹配 |
| `utils/rate_limiter.py` | 50 | 双限速（用户级+全局级） |
| `utils/security.py` | 40 | 输入校验 |
| `utils/fortune.py` | 50 | 每日运势生成 |
| `utils/helpers.py` | 40 | 工具函数 |
| `handlers/sign_in.py` | 70 | 签到入口（参数解析 + 回复格式） |
| `handlers/lottery.py` | 70 | 抽奖入口（口令验证 + 取金额） |
| `handlers/redeem.py` | 180 | 兑换物品 CRUD + 兑换 + 核销 + 记录查询 + 折扣 |
| `handlers/ranking.py` | 50 | 排行 + 签到统计 |
| `handlers/admin.py` | 150 | 加/减分 + 配置管理 |
| `handlers/birthday.py` | 60 | 设置/查询生日 |
| `handlers/active_reward.py` | 90 | 活跃奖励 + 每日口令拦截 |
| `services/point_service.py` | 100 | 积分加减 + 负分联动 + 自动记流水 |
| `services/sign_in_service.py` | 130 | 签到积分计算（协调所有子奖励+运势） |
| `services/lottery_service.py` | 60 | 五档权重 |
| `services/easter_service.py` | 60 | 彩蛋 + 独立保底 |
| `services/redeem_service.py` | 80 | 库存 + 编号生成 + 折扣 |
| `services/ranking_service.py` | 60 | 排行逻辑 + 签到统计 |
| `services/date_reward_service.py` | 50 | 日期匹配（跨年） |
| `services/daily_keyword_service.py` | 40 | 每日口令 |
| `services/birthday_service.py` | 60 | 定时播报 |
| `services/backup_service.py` | 60 | 文件拷贝备份 |
| **合计** | **~2200** | |

---

## 11. 完整指令清单

| 触发方式 | 指令/关键词 | 说明 | 权限 |
|---|---|---|---|
| 无前缀 | `签到` / `sign` / `打卡` | 签到（回复含运势） | 成员 |
| 无前缀 | `{口令}抽奖` / `抽奖{口令}` | 抽奖 | 成员 |
| 无前缀 | `排行` / `排名` / `积分榜` | 群排行 | 成员 |
| 有前缀 | `/兑换` | 查看可兑换物品 | 成员 |
| 有前缀 | `/兑换 <物品ID> [数量]` | 兑换物品 | 成员 |
| 有前缀 | `/兑换记录 [页码]` | 查看自己的兑换记录 | 成员 |
| 有前缀 | `/兑换记录 all [页码]` | 查看全部记录 | 管理员 |
| 有前缀 | `/兑换记录 pending [页码]` | 查看未核销记录 | 管理员 |
| 有前缀 | `/兑换记录 <record_no>` | 查看单条详情 | 成员(自己)/管理员(全部) |
| 有前缀 | `/流水 [页码]` | 查看自己积分流水 | 成员 |
| 有前缀 | `/流水 @用户 [页码]` | 查看指定用户流水 | 管理员 |
| 有前缀 | `/流水 all [页码]` | 查看全群流水 | 管理员 |
| 有前缀 | `/签到统计` | 今日签到数据 | 成员 |
| 有前缀 | `/设置生日 <MM-DD / MM月DD日>` | 设置生日 | 成员 |
| 有前缀 | `/查生日 [@用户]` | 查看生日 | 成员 |
| 有前缀 | `/加分 @用户/Q号 <分值>` | 增加积分 | 管理员 |
| 有前缀 | `/扣分 @用户/Q号 <分值>` | 扣除积分 | 管理员 |
| 有前缀 | `/添加兑换 <名称> <消耗> [库存]` | 新增兑换物品 | 管理员 |
| 有前缀 | `/删除兑换 <物品ID>` | 软删除物品 | 管理员 |
| 有前缀 | `/修改兑换 <ID> <字段> <值>` | 修改物品属性 | 管理员 |
| 有前缀 | `/核销 <记录编号> [备注]` | 切换核销状态 | 管理员 |
| 有前缀 | `/设置折扣 <ID> <折扣价> <截止时间>` | 设置兑换折扣 | 管理员 |
| 有前缀 | `/清除折扣 <ID>` | 清除兑换折扣 | 管理员 |
| 有前缀 | `/设置今日口令 <关键词> <积分>` | 设置每日口令 | 管理员 |
| 有前缀 | `/清除今日口令` | 清除每日口令 | 管理员 |
| 有前缀 | `/设置 <配置项> <值>` | 修改运行时配置 | 管理员 |
| 有前缀 | `/查看配置` | 查看当前配置 | 管理员 |

---

> 设计结束。如有任何修改需求，请提交 Issue 或联系开发者。
