# 积分系统插件 — 完整设计文档

> 版本: v1.4  
> 更新时间: 2026-08-03

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
| 2 | **彩蛋事件** | 签到概率触发欧皇/非酋事件，±大量积分；触发概率与保底次数为配置项（v0.3.0 起，欧皇/非酋各自独立，默认 0.005 / 200，旧数据不再强制触发） |
| 3 | **活跃奖励** | 群成员发送合规普通消息（非空、含指令前缀、达到字数下限），概率获得积分，有用户冷却 + 全局冷却 |
| 4 | **每日口令** | 管理员当天动态设置关键词+积分，消息含该关键词即得（每人每天限 1 次） |
| 5 | **日期口令** | 预配置日期范围+关键词+概率，签到联动触发，支持跨年 |
| 6 | **无前缀触发** | 不需要命令前缀，消息含配置关键词即可触发的签到/抽奖/排行 |
| 7 | **群内排行** | 优先当前群正积分用户 Top 10，群注册用户不足 3 人时回退全局 Top 10 |
| 8 | **个人抽奖** | 固定消耗+口令验证，五档权重概率（特等奖/一等奖/二等奖/三等奖/参与奖） |
| 9 | **兑换玩法** | 积分换物品，库存管理（原子扣减），兑换记录核销/驳回三态（pending/verified/rejected 可互切，驳回退回积分并恢复库存），核销/驳回后群内 @ 通知兑换者，限时折扣 |
| 10 | **管理员指令** | 独立管理员名单（bot 主人自动为管理员），@或 QQ 号增减积分，管理兑换/口令/配置 |
| 11 | **生日系统** | 记录生日（MM-DD / MM月DD日），生日签到奖励，定时播报当日寿星 |
| 12 | **负分联动** | 负分仅可签到恢复积分，不能抽奖/兑换/活跃奖励，自动分配/撤销"群女仆X号"头衔 |
| 13 | **自动备份** | 多本地目标目录（`backup_dirs` 配置），定时备份（默认 04:00），`VACUUM INTO` 一致性快照，每目录仅保留最近 `backup_keep_count` 份 |
| 14 | **积分流水** | 每笔积分变动自动记录（时间、原因、变动值、余额），用户可查明细 |
| 15 | **签到统计** | 查询今日签到人数、签到率、首签用户、连签王 |
| 16 | **每日运势** | 签到回复尾部自动附带运势文本（同用户同天一致，纯趣味不涉及积分） |
| 17 | **兑换折扣** | 管理员可为兑换物品设置限时折扣价 |
| 18 | **反馈增强（v0.2.2）** | 签到反馈含当日排名/连签/当前积分；抽奖反馈含消耗/积分变化/当前积分；兑换反馈含订单号/剩余库存/积分余额/核销提示；`/查生日` 显示群昵称 |
| 19 | **我的积分（v0.3.0）** | 无前缀关键词「我的积分 / 积分查询」，展示群昵称+QQ / 当前积分 / 累计签到 / 连签 / 今日签到 / 本群排名 / 最近 5 条本群流水 |
| 20 | **打劫（v0.4.0）** | 无前缀「打劫 @目标」（At 段解析，排除 AtAll 与 bot 自身），成功抢得目标部分积分（凸曲线收益公式），失败扣成本；同用户冷却 + 每日上限防刷；目标可被抢成负分并联动负分头衔 |
| 21 | **打劫防集火（v0.4.2，v0.4.3 增动态方案）** | 目标每日被劫上限（全部次数口径，按 QQ 全局）+ 收益衰减（每被成功打劫一次后续收益递减），防止高积分玩家被集火掠夺；`rob_target_limit_dynamic=true` 时上限 = 基准（最小 1）+ 该人主动发起打劫次数 |

---

## 2. 数据表设计

> 当前 schema **v5**（v1 → v2：负分头衔原名片 `negative_title_prev_card`、流水操作人 `admin_qq`；
> v2 → v3：积分一号跨群共享，accounts 按 QQ 全局唯一，users 瘦身为群级数据；
> v3 → v4：redeem_records 新增驳回审计列 `rejected_at` / `rejected_by`；
> v4 → v5：新增 rob_records 打劫记录表）。
> 旧库首次加载自动迁移（`db/schema._migrate`），升级前建议先备份数据库。

### 2.1 accounts — 全局账户表（v0.2.0 新增，一号跨群共享）

```sql
CREATE TABLE IF NOT EXISTS accounts (
    qq TEXT PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT '',
    points INTEGER NOT NULL DEFAULT 0,          -- 全局共享余额
    total_earned INTEGER NOT NULL DEFAULT 0,    -- 累计获得（全局）
    last_sign_date TEXT,                         -- 全局签到日（全局限签 1 次）
    consecutive_days INTEGER NOT NULL DEFAULT 0, -- 全局连签
    total_sign_days INTEGER NOT NULL DEFAULT 0,  -- 全局累计签到天数
    birthday TEXT,
    birthday_year INTEGER,
    lucky_pity INTEGER NOT NULL DEFAULT 0,       -- 彩蛋保底（全局）
    unlucky_pity INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_accounts_points ON accounts(points DESC);
```

> 积分、签到状态、生日、彩蛋保底均为**账号级**数据，按 QQ 全局唯一。
> 所有积分变动只允许经 `PointService.change_balance` 落账（见 7.14）。

### 2.2 users — 用户表（仅存群级数据）

```sql
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    group_id TEXT NOT NULL,
    negative_title_id INTEGER,
    negative_title_prev_card TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(qq, group_id)
);
CREATE INDEX IF NOT EXISTS idx_users_qq ON users(qq);
```

> 每行 = 一个 QQ 在一个群的**成员关系**（含负分头衔群级状态）。
> v0.2.0 起 users 不再存积分/签到/生日字段（迁入 accounts）；
> `updated_at` 在签到事务内刷新，供全局榜"最近活跃群"归属判断。

### 2.3 sign_in_log — 签到流水表

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
CREATE UNIQUE INDEX IF NOT EXISTS idx_sign_in_log_qq_date ON sign_in_log(qq, sign_date);
```

> `idx_sign_in_log_qq_date` 在数据库层强制"全局限签 1 次"（配合事务内查重）；
> 迁移时先清理同 (qq, sign_date) 重复行（保留最早一条）再建索引。

### 2.4 lottery_record — 抽奖流水表

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
CREATE INDEX IF NOT EXISTS idx_lottery_qq_date ON lottery_record(qq, created_at);
```

> `idx_lottery_qq_date` 加速"每日抽奖限次按 QQ 全局统计"（v0.2.1）。

### 2.5 point_transactions — 积分流水表

```sql
CREATE TABLE IF NOT EXISTS point_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    group_id TEXT NOT NULL,
    amount INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    reason TEXT NOT NULL,
    ref_id INTEGER,
    admin_qq TEXT,
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
| `redeem_refund` | 兑换驳回退款（不计入累计获得） |
| `active_reward` | 活跃奖励 |
| `daily_keyword` | 每日口令奖励 |
| `admin_add` | 管理员加分 |
| `admin_sub` | 管理员扣分 |
| `rob_cost` | 打劫成本（仅失败记录，不计入累计获得） |
| `rob_reward` | 打劫成功收益 |
| `rob_lost` | 被打劫损失（目标扣分，不计入累计获得） |

### 2.6 redeem_items — 兑换物品表

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

### 2.7 redeem_records — 兑换记录表

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

### 2.8 admins — 管理员表

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

### 2.9 date_rewards — 日期口令配置表

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

### 2.10 easter_events — 彩蛋事件配置表

> v0.3.0 起：触发概率与保底次数改由配置项驱动（见 §6），本表 `probability` 仅作同类多事件
> 加权选择权重，`pity_count` 列不再参与触发判定。

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
CREATE INDEX IF NOT EXISTS idx_dk_claim_qq_date ON daily_keyword_claim(qq, created_at);
```

> `idx_dk_claim_qq_date` 加速"每日口令按 QQ 全局限领 1 次"（v0.2.1）。

### 2.14 plugin_config — KV 运行时配置表

```sql
CREATE TABLE IF NOT EXISTS plugin_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
```

### 2.15 rob_records — 打劫记录表（v0.4.0 新增，schema v5）

```sql
CREATE TABLE IF NOT EXISTS rob_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    target_qq TEXT NOT NULL,
    group_id TEXT NOT NULL,
    cost INTEGER NOT NULL,               -- 配置成本（审计）
    stolen INTEGER NOT NULL DEFAULT 0,   -- 实际得失（成功=收益，失败=0）
    success INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_rob_qq_date ON rob_records(qq, created_at);   -- 每日限次
CREATE INDEX IF NOT EXISTS idx_rob_target ON rob_records(target_qq);
```

> 纯新增表：`init_schema` 幂等建表，无需 ALTER 迁移逻辑，仅 `SCHEMA_VERSION` bump 到 5。

---

## 3. 配置项表

配置由 AstrBot 托管（`data/config/*_config.json`，WebUI 可视化），与 `/设置` 指令双向同步、热生效（`_conf_schema.json` 为唯一默认值来源）。

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
| signin_refresh_time | str | 04:00 | 每日刷新时刻（签到/口令/抽奖次数等每日逻辑） |
| **彩蛋（v0.3.0 起配置化）** | | | |
| easter_lucky_probability | float | 0.005 | 欧皇触发概率 (0~1) |
| easter_lucky_pity_count | int | 200 | 欧皇保底签到次数（0=关闭保底） |
| easter_unlucky_probability | float | 0.005 | 非酋触发概率 (0~1) |
| easter_unlucky_pity_count | int | 200 | 非酋保底签到次数（0=关闭保底） |
| **活跃奖励** | | | |
| active_reward_enabled | bool | true | 开关 |
| active_reward_probability | float | 0.05 | 触发概率 (0~1) |
| active_reward_points_min | int | 1 | 每次奖励随机下限 |
| active_reward_points_max | int | 5 | 每次奖励随机上限 |
| active_reward_cooldown | int | 60 | 同用户冷却秒数 |
| active_reward_min_length | int | 3 | 消息最小字数 |
| active_reward_global_cooldown | int | 10 | 全群全局冷却秒数 |
| **抽奖** | | | |
| lottery_enabled | bool | true | 开关 |
| lottery_cost | int | 20 | 单次消耗积分 |
| lottery_daily_limit | int | 10 | 每日抽奖次数上限（按 QQ 全局统计） |
| lottery_passphrase | str | "whl" | 抽奖口令 |
| lottery_tiers | json | (见下) | 五档配置 |
| **打劫（v0.4.0）** | | | |
| rob_enabled | bool | true | 总开关 |
| rob_cost | int | 50 | 打劫成本（仅失败时扣除，成功纯收益） |
| rob_success_rate | float | 0.35 | 成功率 (0~1) |
| rob_reward_fixed | int | 50 | 成功固定收益（= 成本） |
| rob_reward_base_points | int | 2000 | 收益锚点目标积分（目标积分为此值时动态收益 = 固定收益） |
| rob_reward_power | float | 1.2 | 收益幂指数（>1 凸曲线，0~2 合法） |
| rob_reward_cap | int | 200 | 单次收益总上限 |
| rob_min_points | int | 100 | 打劫者积分门槛 |
| rob_target_min_points | int | 50 | 目标积分门槛 |
| rob_cooldown | int | 600 | 同用户打劫冷却秒数（0=不限，成功/失败均进入） |
| rob_daily_limit | int | 3 | 每日打劫次数上限（按 QQ 全局统计，0=不限） |
| rob_target_daily_limit | int | 6 | 目标每日被劫次数上限（按 target_qq 全局跨群统计，成功与失败均计数，固定方案下 0=不限；动态方案开启时作为固定基准值，不能为 0，最小 1：WebUI 输入 0 按 1 处理，/设置 拒绝 0） |
| rob_target_limit_dynamic | bool | false | 目标被劫上限动态方案开关（true：上限 = rob_target_daily_limit + 该人今日主动发起打劫次数，成功与失败均计数；开启需 rob_target_daily_limit ≥ 1） |
| rob_reward_decay | float | 0.25 | 打劫收益衰减比例：目标每被**成功**打劫一次，后续收益 ×(1-decay)^n（0~1，0=不衰减；目标上限关闭时仍生效） |
| keyword_rob | json | ["打劫"] | 打劫触发关键词（注意：修改后需同步 main.py 的 on_rob 粗筛正则） |
| **负分** | | | |
| negative_disable_lottery | bool | true | 负分禁止抽奖 |
| **生日** | | | |
| birthday_bonus_points | int | 100 | 生日签到奖励 |
| birthday_announce_time | str | "08:00" | 每日播报时间 |
| **备份** | | | |
| backup_enabled | bool | true | 开关 |
| backup_time | str | 04:00 | 每日自动备份时刻 |
| backup_dirs | json | [] | 备份目标目录列表（相对路径基于插件数据目录） |
| backup_keep_count | int | 30 | 每备份目录保留份数（0=不清理） |
| **关键词** | | | |
| keyword_sign | json | ["签到","sign","打卡"] | 签到触发关键词列表 |
| keyword_lottery | json | ["抽奖","lottery"] | 抽奖触发关键词列表 |
| keyword_rob | json | ["打劫"] | 打劫触发关键词列表 |
| **指令图** | | | |
| cmd_map_user_cooldown | int | 30 | 同用户指令图生成冷却（秒，0=不限） |
| cmd_map_group_cooldown | int | 10 | 同群指令图生成冷却（秒，0=不限） |
| cmd_map_cache_ttl_hours | int | 24 | 指令图缓存有效期（小时，0=禁用缓存） |

### lottery_tiers JSON 默认值

```json
{
  "tiers": [
    {"label":"特等奖", "weight":2,  "points_min":100, "points_max":100, "emoji":"👑"},
    {"label":"一等奖", "weight":18, "points_min":31,  "points_max":45,  "emoji":"🥇"},
    {"label":"二等奖", "weight":37.5, "points_min":21, "points_max":30, "emoji":"🥈"},
    {"label":"三等奖", "weight":27.5, "points_min":11, "points_max":20, "emoji":"🥉"},
    {"label":"四等奖", "weight":15, "points_min":1,  "points_max":10,  "emoji":"💫"}
  ]
}
```

权重决定概率，命中档位后在 `points_min` ~ `points_max` 闭区间内随机获得积分。

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
│   ├── rob.py                    # 打劫：@ 目标解析 + 反馈格式化（v0.4.0）
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
│   ├── rob_service.py            # 打劫：门槛/冷却/限次/收益公式/记录（v0.4.0）
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
│   ├── group_info.py             # 平台成员信息获取（get_group_member_info 唯一入口）
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
    ├─ @filter.regex(打劫关键词) ───────────── main.py 委托 →（handler 内 At 解析 + 严格匹配）
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
| 打劫 | `@filter.regex("打劫")` | 无前缀触发（handler 内 At 解析 + 严格匹配 keyword_rob） |
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
  3. 否 → 打劫形态（有效 @ + 关键词严格匹配）→ rob 处理 → stop_event；active_reward 同步跳过打劫形态（防双收益刷分）
  4. 否 → active_reward（额外跳过含签到关键词 或 同时含抽奖关键词+口令的消息）
```
> 口令保留字（v0.4.0 起含 `keyword_rob`）：`/设置今日口令` 关键词与签到/抽奖/打劫/排行触发词相同或构成「口令±触发词」组合时拒绝设置，杜绝口令死锁。

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

# 4. 连签奖励（有上限，从第 2 天起算：第 N 天 = (N-1) × per_day）
effective = min(consecutive_days, signin_consecutive_max)
points_earned += max(0, effective - 1) * signin_consecutive_bonus_per_day

# 5. 每7天奖励
if consecutive_days > 0 and consecutive_days % 7 == 0:
    points_earned += signin_weekly_bonus

# 6. 彩蛋（欧皇/非酋，最多一个，欧皇优先，计数器独立；概率/保底取配置项）
easter = easter_service.trigger(user.lucky_pity, user.unlucky_pity,
                                cfg.easter_lucky_probability, cfg.easter_unlucky_probability,
                                cfg.easter_lucky_pity_count, cfg.easter_unlucky_pity_count)
points_earned += easter.points

# 7. 生日奖励
if today == birthday:
    points_earned += birthday_bonus_points

# 8. 日期口令
date_bonus = date_reward_service.check(sign_date, message)
points_earned += date_bonus

# 统一写流水（accounts 全局余额 + point_transactions 记发生群）
# 全局限签：签到前查 accounts.last_sign_date / 事务内查 sign_in_log(qq, sign_date)，
#           同用户跨群同日只允许 1 次；每日首签奖励仍按群判定
```

> v0.2.0 起签到状态（连签/累计天数/保底计数器）存于 accounts，跨群延续；
> 积分入账走 `PointService.change_balance`（earned_amount 排除非酋负事件）。

### 7.2 彩蛋保底逻辑（v0.3.0 起配置驱动）

触发概率与保底次数由配置项控制（欧皇/非酋各自独立，见 §6 配置表）；`easter_events` 表
的 `probability` 列仅保留作同类多事件加权选择权重，`pity_count` 列不再参与判定。

```python
def trigger(lucky_pity, unlucky_pity,
            lucky_probability, unlucky_probability,   # 配置：欧皇/非酋概率
            lucky_pity_count, unlucky_pity_count):    # 配置：欧皇/非酋保底（0=关闭）
    lucky_pity += 1
    unlucky_pity += 1

    force_lucky = lucky_pity_count > 0 and lucky_pity >= lucky_pity_count
    force_unlucky = unlucky_pity_count > 0 and unlucky_pity >= unlucky_pity_count

    # Lucky checked first (priority)
    if force_lucky:
        lucky_pity = 0  # only lucky resets
        return pick_random(active_lucky_events)
    elif force_unlucky:
        unlucky_pity = 0  # only unlucky resets
        return pick_random(active_unlucky_events)
    elif random() < lucky_probability:
        lucky_pity = 0
        return pick_random(active_lucky_events)
    elif random() < unlucky_probability:
        unlucky_pity = 0
        return pick_random(active_unlucky_events)
    else:
        return None  # no event
```

> 默认保底 200 / 概率 0.005：旧系统迁移的高保底计数（≤90）远低于阈值，不再强制触发。

### 7.3 抽奖——五档权重

```python
weights = [tier.weight for tier in tiers]
total = sum(weights)
rand = random() * total
cumulative = 0
for tier in tiers:
    cumulative += tier.weight
    if rand < cumulative:
        reward = randint(tier.points_min, tier.points_max)
        # reward = 档位内随机积分（闭区间）
        point_service.subtract(qq, group_id, lottery_cost, reason="lottery_cost")
        point_service.add(qq, group_id, reward, reason="lottery_reward")
        break
```

### 7.4 无前缀触发规则（v0.2.1 起为严格匹配）

消息压缩全部空白后（大小写不敏感），必须与合法形态**完全相等**才触发：

```python
norm(s) = "".join(s.split())

# 签到：消息 == 某签到关键词
norm(msg) == norm("签到") or norm(msg) == norm("sign") or ...

# 抽奖：消息 == 抽奖关键词 / 口令+关键词 / 关键词+口令
norm(msg) == norm("抽奖")
        or norm(msg) == norm(passphrase) + norm("抽奖")
        or norm(msg) == norm("抽奖") + norm(passphrase)

# 排行：消息 == 某排行关键词（排行 / 排名 / 积分榜）
norm(msg) == norm("排行") or ...
```

> - 带附加文本的消息（"我要签到"、"whl 今天抽奖"）**不触发**，作为普通聊天消息继续流转；
>   触发 handler 仅在**产生实际输出**后才调用 `stop_event()`，普通消息不再被静默吞掉。
> - 每日口令/活跃奖励仍为"包含"匹配（消息内含口令关键词即命中），与触发词严格匹配不冲突。
> - `/设置今日口令` 有保留字校验：关键词不得等于触发词或构成「口令±触发词」组合，
>   否则该形态消息会被触发词拦截、口令永远领不到（死锁）。
>   校验口令取自 `config_cache["lottery_passphrase"]`（动态），口令变更后保留字形态随之变化
>   （如口令改"喵喵"后拦截 `喵喵签到`/`签到喵喵`/`喵喵抽奖`/`抽奖喵喵`）。

### 7.4b 反馈信息（v0.2.2）

```
签到成功反馈：
✅ 签到成功！获得 +10 积分
  · 今日第 3 位签到        ← 当日签到排名（事务内 COUNT+1）
  · 连签: 第 5 天           ← 无条件显示连签天数
  · 当前积分: 123          ← 变动后余额（change_balance 返回值）
  · 基础分: ...            ← 原有奖励明细保持不变

抽奖反馈：
👑 特等奖
  · 消耗: 20 积分          ← lottery_cost
  · 获得: +100 积分        ← lottery_reward（未中奖显示"未中奖"）
  · 积分变化: +80          ← reward - cost（带符号）
  · 当前积分: 1080         ← 事务内最终余额

兑换成功反馈：
兑换成功！获得 徽章 x2，消耗 200 积分
  · 订单号: R20260802-0001   ← 事务内生成（record_no）
  · 剩余库存: 3 (∞ 表示无限)  ← 扣减后同事务读取
  · 积分余额: 300            ← change_balance 返回值
  · 请联系管理员核销          ← 兑换记录待核销提示
```

### 7.4c 群昵称展示与防注入（v0.2.2）

所有将群昵称/发送者昵称拼入回复文案的位置，统一经 `utils.security.clean_display_name`
（剥离 `\x00-\x1f\x7f` 控制字符 + 去首尾空白），防止昵称构造多行伪造消息：

| 位置 | 昵称来源 | 回退 |
|---|---|---|
| 签到运势 `utils/fortune.format_fortune` | 发送者昵称 | - |
| 排行/签到统计 `handlers/ranking._fetch_names` | card → nickname | QQ 号 |
| `/查生日` `handlers/birthday.query_birthday` | card → nickname | QQ 号 |
| 活跃奖励 `handlers/active_reward` | 发送者昵称 | - |

### 7.5 负分头衔联动（v0.2.0：余额全局、头衔按群、回正全群清除）

```
变负（accounts.points 从 >=0 变为 <0）：
  1. 在触发操作的群懒加载头衔（其他群不预分配）
  2. BEGIN TRANSACTION
  3. SELECT negative_title_id FROM users WHERE group_id=? AND negative_title_id IS NOT NULL
  4. 找到最小未占用的正整数 → new_id
  5. UPDATE users SET negative_title_id=new_id, negative_title_prev_card=原名片 WHERE qq=? AND group_id=?
  6. set_group_card(qq, group_id, f"群女仆{new_id}号")
  7. COMMIT

回正（accounts.points 从 <0 变为 >=0）：
  1. 遍历 get_user_groups(qq)（该用户全部群）
  2. 对每个有头衔的群：set_group_card(qq, group_id, 原名片) + UPDATE users SET negative_title_id=NULL
```

> 余额判断使用**全局余额**（accounts），任一群回正即清除全部群的头衔（跨群联动）。

### 7.6 负分用户限制

```python
if account.points < 0:  # 全局余额
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
5. 全局余额 < 0？→ 跳过（负分仅可签到）
6. 用户冷却未到？→ 跳过（rate_limiter）
7. 全局冷却未到？→ 跳过（rate_limiter, 全群每 N 秒最多 1 次）
8. 随机概率命中？→ 发奖 + 回复消息

每日口令独立于活跃奖励并列检查（同一消息可触发两者）
```

### 7.8 排名回退逻辑（v0.2.0：共享积分 + 群昵称）

```python
def get_ranking(group_id, top_n=10):
    # 本群成员按全局积分排序（users × accounts JOIN）
    group_users = dao.get_top_n_by_group(group_id, top_n, min_points=1)
    if len(group_users) >= 3:
        return group_users  # 群排行
    # 本群不足 3 人，回退全局
    global_users = dao.get_top_n_global(top_n, min_points=1)
    return global_users  # 全局排行


# 展示昵称：并发调 get_group_member_info 取 card → nickname → 回退 QQ
# 全局榜每行附"最近活跃群"（users.updated_at 最大者）用于取昵称/展示
```

### 7.9 兑换核销 / 驳回（v0.3.0 三态）

状态机：`pending`（待处理）→ `verified`（通过）/ `rejected`（驳回），通过 ↔ 驳回可互切；
`/核销 [通过|pass|驳回|reject] <记录编号> [备注]`，未写动作词默认通过（旧格式兼容）。

```python
# 单事务完成：条件状态迁移（WHERE status=? 防并发重复处理）+ 积分 + 库存
async def set_record_status(record_no, action, admin_qq, group_id, note):
    record = dao.get_redeem_record(record_no)
    if action not in ("verified", "rejected"):  # 白名单校验
        return failure(f"无效操作: {action}")
    if record.status == action:                 # 幂等
        return success("已是通过/驳回状态", changed=False)

    async with conn.execute(
        "UPDATE redeem_records SET status=?, verified_at/rejected_at=?, ... "
        "WHERE record_no=? AND status=?", (action, ..., record_no, record.status)
    ) as cur:
        if cur.rowcount == 0: raise ValueError("记录状态已变更，请刷新后重试")

    if action == "rejected":                    # 通过 → 驳回
        change_balance(+record.item_cost, reason="redeem_refund", earned_amount=0)
        restore_stock(item_id, quantity)        # -1 无限库存不变
    elif record.status == "rejected":           # 驳回 → 通过
        deduct_stock(guard: stock=-1 or stock>=qty)   # 不足则失败，积分/状态零变更
        change_balance(-record.item_cost, reason="redeem_cost", guard_balance=None)  # 允许负

# 状态变更成功后：群内 @ 通知兑换者（MessageChain.at(qq).message(文案)）
# 发送失败（如已退群）→ 发警告信息；状态结果不受通知成败影响
# 扣负/退分回正后联动 ensure_negative_title（负分头衔）
```

驳回通知文案：`❌ 你的兑换订单 {no}（{item} x{qty}）已被驳回，消耗的 {cost} 积分已退回（管理员驳回[:原因]）`；
通过通知：`✅ 你的兑换订单 {no}（{item} x{qty}）已通过核销[备注]`；管理员确认消息回显备注。

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

### 7.14 改分唯一入口 change_balance（v0.2.0）

所有积分变动（签到/抽奖/兑换/口令/管理加减分）必须经 `PointService.change_balance` 落账：

```python
@staticmethod
async def change_balance(conn, qq, group_id, amount, reason, *,
                         earned_amount=None, guard_balance=None,
                         ref_id=None, admin_qq=None) -> int:
    # 1. INSERT OR IGNORE accounts(qq) + users(qq, group_id)（自动建行）
    # 2. UPDATE accounts SET points=points+?
    #       [AND points>=guard_balance]（余额守卫，rowcount=0 抛 InsufficientPointsError）
    #    total_earned += earned_amount（默认 amount，负向传 0，签到传 earned_inc）
    # 3. SELECT points → balance
    # 4. INSERT point_transactions（记录发生群 group_id 与全局 balance_after）
    # 5. return balance
```

> `conn` 由调用方事务传入（与 `generate_record_no(conn)` 同一模式，不触碰 db.lock 重入）；
> 由此消灭 5 处重复的"UPDATE points + SELECT + INSERT 流水" SQL 模式。

### 7.15 打劫业务逻辑（v0.4.0）

```
rob(qq, target_qq, group_id, bot=None):
  # 1. rob_enabled 开关；qq == target_qq 拒绝；target 为 bot 自身由 handler 拒绝
  # 2. 打劫者门槛：balance >= rob_min_points 且非负分
  # 3. 目标门槛：target_balance >= rob_target_min_points 且非负分
  # 4. 用户冷却（rate_limiter，key="rob"）→ 拦截时返回剩余秒数供提示
  # 5. execute_transaction(_tx)：
  #    a. 打劫者每日次数：COUNT rob_records WHERE qq AND created_at >= period_start_str()
  #       （按 QQ 全局统计，与抽奖口径一致）
  #    b. 目标防集火：target_robs_today(conn, target_qq) → (总次数, 成功次数)（一次查询）
  #       - 上限：固定方案 limit = rob_target_daily_limit（0=不限）；动态方案
  #         （rob_target_limit_dynamic=true）limit = max(rob_target_daily_limit, 1) +
  #         count_robs_today(conn, target_qq)（该人主动发起打劫次数，全部口径，复用打劫者限次统计）
  #         ——动态方案基准最小 1：/设置 拒绝 0（handler 交叉校验）、WebUI/手改配置 0 在
  #         _load_config_cache 加载时按 1 处理并回写，服务层 max(...,1) 防御兜底
  #       - 总次数（全部次数口径，含失败）>= limit 时拒绝
  #         （"目标今日已被打劫 X 次，无法再被打劫"，动态方案附"（今日上限 Y）"，
  #         事务回滚，冷却已消耗）
  #       - 衰减：成功次数 win_hits（仅成功口径）用于收益递减
  #    c. SELECT 目标余额（事务内，max(负,0) 防御并发扣负；与扣分同源防偏差）
  #    d. success = random() < rob_success_rate
  #    e. 成功分支：stolen = min(cap, fixed + round(fixed * (target/base) ** power))
  #       → 若 decay>0 且 win_hits>0：stolen = round(stolen * (1-decay) ** win_hits)
  #       → change_balance(+stolen, "rob_reward")；目标 change_balance(-stolen, "rob_lost",
  #       earned_amount=0)（允许扣负；stolen=0 极端配置时跳过 change_balance 防 amount=0 报错）
  #       失败分支：change_balance(-cost, "rob_cost", earned_amount=0, guard_balance=cost)
  #       （守卫失败抛 RobError 整事务回滚）
  #    f. INSERT rob_records（cost=配置成本, stolen, success）
  # 6. ensure_negative_title(qq) 与 ensure_negative_title(target_qq)（负分头衔联动）
  # 7. 返回 success/stolen/balance/target_balance 等字段供 handler 格式化
```

收益公式锚点：目标 = base(2000) 时 dynamic = fixed(50) → stolen = 100；cap(200) 触顶。
失败分支与成本扣除同一事务内完成，保证原子（无记录残留/无半扣款）。

### 7.15b 打劫触发形态（v0.4.0）

- 消息组件解析（`event.get_messages()`，组件属性 duck-typing，不依赖 isinstance）：
  - **类型判定必须用 `==` 直接比较**（如 `c.type == "At"` / `in ("At","at")`）：真实组件
    `type` 为 str 枚举（`str(ComponentType.Plain)` 返回全名 `"ComponentType.Plain"`），
    用 `str(c.type).lower()` 比较会永不命中（v0.4.1 修复）
  - `At` 段（排除 AtAll `qq=="all"` 与 `qq==self_qq`）→ 有效目标，取第一个；多个 → 提示"一次只能打劫一个目标"
  - 其余 `Plain` 段拼接 → 压缩空白后严格等于某打劫关键词（顺序无关）
  - 无有效目标：存在 @all/@bot → "不能打劫机器人/全体成员"；纯关键词无 @ → "用法: 打劫 @目标"；非打劫形态静默
- `utils/keyword_matcher.parse_rob_message / is_rob_message`：统一解析入口（handler 与 active_reward 跳过共用）
- 仅群聊（无 group_id 拒绝）
- 反馈昵称：`fetch_member_info`（card → nickname → QQ 回退）+ `clean_display_name` 防注入
- 冷却提示：成功/失败反馈用 `ceil(rob_cooldown/60)` 静态计算；冷却拦截用 `rate_limiter.get_remaining` 实时值

---

## 8. 后台定时任务

| 任务 | 调度方式 | Cron | 说明 |
|---|---|---|---|
| 备份 | APScheduler cron | `config.backup_time` | 遍历 `backup_dirs`，`VACUUM INTO` 生成一致快照到各目标 |
| 生日播报 | APScheduler cron | `config.birthday_announce_time` | 每日定时播报当日寿星（按群，去重表防重复） |

### 定时任务注意事项

- 备份/生日播报/每日刷新统一按**宿主机本地时区**（cron 不指定时区，跟随系统）
- `terminate()` 中清理所有定时任务
- 备份使用 `VACUUM INTO`（含 WAL 数据），目标已存在时自动追加序号
- 备份目标目录不存在时自动创建
- 备份保留策略：每目录仅保留最近 `backup_keep_count` 份（默认 30，0=不清理），超出删除最旧（仅清理本插件 `points_system_*.db` 命名文件）

---

## 9. 安全与性能措施

### 性能

| 层级 | 措施 |
|---|---|
| 数据库 | WAL 模式（读写不互锁） |
| 索引 | `idx_accounts_points` 加速排行；`(group_id, sign_date)` 加速签到查重；`(qq, sign_date)` 全局限签去重；`(qq, group_id, created_at DESC)` 流水翻页 |
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
| 防重复 | `sign_in_log (qq, sign_date)` 全局唯一索引 + 事务内查重（全局限签 1 次） |
| 防滥用 | 每用户每操作独立冷却 + 全群全局冷却；打劫另有打劫者每日上限（按 QQ 全局）+ 目标每日被劫上限（防集火，全部次数口径）+ 收益衰减（成功次数口径）+ 口令保留字防「打劫=口令」双收益 |
| 输入校验 | 积分范围检查、QQ 号数字校验、字符串截断（最长 200 字） |
| 昵称防注入 | 所有群昵称/发送者昵称拼装点统一 `clean_display_name` 剥离控制字符（运势/排行/查生日/活跃奖励/打劫） |
| SQL 注入 | 所有 DAO 使用 `?` 占位符，零字符串拼接 |
| 生命周期 | `initialize()` 建表+启动任务；`terminate()` 关闭连接+清理任务 |
| 备份 | `VACUUM INTO` 一致快照（含 WAL 数据），目标已存在自动追加序号；目录不存在时自动创建；每目录仅保留最近 `backup_keep_count` 份 |

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
| `handlers/rob.py` | 100 | 打劫入口（@ 解析 + 反馈格式化） |
| `handlers/redeem.py` | 180 | 兑换物品 CRUD + 兑换 + 核销 + 记录查询 + 折扣 |
| `handlers/ranking.py` | 50 | 排行 + 签到统计 |
| `handlers/admin.py` | 150 | 加/减分 + 配置管理 |
| `handlers/birthday.py` | 60 | 设置/查询生日 |
| `handlers/active_reward.py` | 90 | 活跃奖励 + 每日口令拦截 |
| `services/point_service.py` | 100 | 积分加减 + 负分联动 + 自动记流水 |
| `services/sign_in_service.py` | 130 | 签到积分计算（协调所有子奖励+运势） |
| `services/lottery_service.py` | 60 | 五档权重 |
| `services/rob_service.py` | 190 | 打劫事务（门槛/冷却/限次/收益公式/记录） |
| `services/easter_service.py` | 60 | 彩蛋 + 独立保底 |
| `services/redeem_service.py` | 80 | 库存 + 编号生成 + 折扣 |
| `services/ranking_service.py` | 60 | 排行逻辑 + 签到统计 |
| `services/date_reward_service.py` | 50 | 日期匹配（跨年） |
| `services/daily_keyword_service.py` | 40 | 每日口令 |
| `services/birthday_service.py` | 60 | 定时播报 |
| `services/backup_service.py` | 77 | VACUUM INTO 快照备份 + 保留策略 |
| **合计** | **~2200** | |

---

## 11. 完整指令清单

| 触发方式 | 指令/关键词 | 说明 | 权限 |
|---|---|---|---|
| 无前缀 | `签到` / `sign` / `打卡` | 签到（回复含运势） | 成员 |
| 无前缀 | `{口令}抽奖` / `抽奖{口令}` | 抽奖 | 成员 |
| 无前缀 | `排行` / `排名` / `积分榜` | 群排行 | 成员 |
| 无前缀 | `我的积分` / `积分查询` | 我的积分概览（群昵称+QQ/当前积分/累计签到/连签/今日签到/本群排名/最近流水） | 成员 |
| 无前缀 | `打劫 @目标` | 打劫群友抢积分（成功抢得部分积分、失败扣成本；冷却+每日限次） | 成员 |
| 有前缀 | `/兑换` | 查看可兑换物品 | 成员 |
| 有前缀 | `/兑换 <物品ID> [数量]` | 兑换物品 | 成员 |
| 有前缀 | `/兑换记录 [页码]` | 查看自己的兑换记录 | 成员 |
| 有前缀 | `/兑换记录 all/全部 [页码]` | 查看全部记录 | 管理员 |
| 有前缀 | `/兑换记录 pending/未核销 [页码]` | 查看未核销记录 | 管理员 |
| 有前缀 | `/兑换记录 <record_no>` | 查看单条详情 | 成员(自己)/管理员(全部) |
| 有前缀 | `/流水 [页码]` | 查看自己积分流水 | 成员 |
| 有前缀 | `/流水 @用户 [页码]` | 查看指定用户流水 | 管理员 |
| 有前缀 | `/流水 all/全部 [页码]` | 查看全群流水 | 管理员 |
| 有前缀 | `/签到统计` | 今日签到数据 | 成员 |
| 有前缀 | `/设置生日 <MM-DD / MM月DD日>` | 设置生日 | 成员 |
| 有前缀 | `/查生日 [@用户]` | 查看生日 | 成员 |
| 有前缀 | `/加分 @用户/Q号 <分值>` | 增加积分 | 管理员 |
| 有前缀 | `/扣分 @用户/Q号 <分值>` | 扣除积分 | 管理员 |
| 有前缀 | `/添加兑换 <名称> <消耗> [库存]` | 新增兑换物品 | 管理员 |
| 有前缀 | `/删除兑换 <物品ID>` | 软删除物品 | 管理员 |
| 有前缀 | `/修改兑换 <ID> <字段> <值>` | 修改物品属性（价格/库存/折扣价/折扣时间/名称/描述，支持中文，反馈全字段） | 管理员 |
| 有前缀 | `/核销 [通过\|驳回] <记录编号> [备注]` | 核销/驳回兑换订单并 @ 通知兑换者（未写动作默认通过，驳回退回积分恢复库存） | 管理员 |
| 有前缀 | `/设置折扣 <ID> <折扣价> <截止时间>` | 设置兑换折扣 | 管理员 |
| 有前缀 | `/清除折扣 <ID>` | 清除兑换折扣 | 管理员 |
| 有前缀 | `/设置今日口令 <关键词> <积分>` | 设置每日口令（当日已有口令时提示"已覆盖"） | 管理员 |
| 有前缀 | `/清除今日口令` | 清除每日口令 | 管理员 |
| 有前缀 | `/设置 <配置项> <值>` | 修改运行时配置 | 管理员 |
| 有前缀 | `/查看配置` | 查看当前配置 | 管理员 |
| 有前缀 | `/添加管理 <QQ号>` | 添加本群积分管理员 | 群主/全局管理员 |
| 有前缀 | `/删除管理 <QQ号>` | 移除本群积分管理员 | 群主/全局管理员 |
| 有前缀 | `/添加日期奖励 <MM-DD\|MM-DD~MM-DD> <关键词> <积分> [概率]` | 新增日期奖励 | 管理员 |
| 有前缀 | `/删除日期奖励 <ID>` | 软删除日期奖励 | 管理员 |
| 有前缀 | `/查看日期奖励` | 查看日期奖励列表 | 管理员 |
| 有前缀 | `/清空数据` | 清空本群数据（验证码二次确认，清空前自动备份） | 管理员 |
| 有前缀 | `/清空全部数据` | 清空全部数据（验证码二次确认） | 全局管理员 |
| 有前缀 | `/确认清空 <验证码>` | 确认执行待清空操作（5 分钟内有效） | 发起者 |
| 有前缀 | `/积分系统帮助` | 指令总览图（别名：指令图 / 命令图 / 帮助图） | 成员 |

---

> 设计结束。如有任何修改需求，请提交 Issue 或联系开发者。
