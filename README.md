# 积分系统插件 by WHLeague

> 一个功能完整的群聊积分系统，支持签到、抽奖、兑换、排行、生日、口令等多项功能。当前版本 **v0.1.1**。

## 功能列表

**积分获取**

- 每日签到：固定/随机积分，首签、每日首签、连签（有上限）、每 7 天奖励，附每日运势；每日刷新时刻可配置（默认 04:00）
- 彩蛋事件：签到概率触发欧皇/非酋事件，±大量积分，独立保底计数器
- 活跃奖励：合规群消息概率获得随机积分（默认 1~5），用户冷却 + 全局冷却
- 每日口令：当日动态关键词，消息命中即得（每人每天 1 次）
- 日期口令：日期范围 + 关键词 + 概率，签到联动触发，支持跨年
- 生日奖励：生日当天签到额外积分，定时播报寿星

**积分消费**

- 个人抽奖：口令验证，五档权重概率（特等奖/一等奖/二等奖/三等奖/参与奖）
- 兑换玩法：库存原子扣减，兑换记录 + 核销双向切换，限时折扣

**排行与统计**

- 群内排行：Top 10，注册用户不足 3 人自动回退全局排行
- 积分流水、签到统计（人数/签到率/首签/连签王）

**管理**

- 管理员指令：独立管理员名单，增减积分，兑换/口令/配置/日期奖励管理
- 数据清空：`/清空数据`（本群）与 `/清空全部数据`（全局），验证码二次确认，清空前自动备份并恢复负分头衔名片

**系统机制**

- 无前缀触发：消息含配置关键词即可触发签到/抽奖/排行
- 负分联动：负分禁止抽奖/兑换/活跃奖励/口令，自动设置"群女仆X号"名片，回正后恢复
- 自动备份：多目标目录，`VACUUM INTO` 一致性快照（默认每天 04:00，可配置）
- 配置可视化：AstrBot WebUI 与 `/设置` 指令双向同步，热生效

## 快速开始

### 安装

将本插件目录放入 `AstrBot/data/plugins/` 下，重启 AstrBot 或热重载插件即可。

依赖会自动通过 `requirements.txt` 安装（仅 `aiosqlite`）。

### 基本用法

```
# 签到（无前缀触发）
签到

# 抽奖（需要口令，默认 whl）
whl抽奖

# 查看排行
排行

# 查看可兑换物品
/兑换

# 兑换物品
/兑换 1

# 查看积分流水
/流水

# 查看签到统计
/签到统计

# 设置生日
/设置生日 08-15

# 管理员：加分
/加分 @用户 50

# 管理员：设置每日口令
/设置今日口令 红包 10
```

## 完整指令表

### 成员指令

| 触发方式 | 指令/关键词 | 说明 |
|----------|------------|------|
| 无前缀 | `签到` / `sign` / `打卡` | 签到（回复含运势） |
| 无前缀 | `{口令}抽奖` / `抽奖{口令}` | 抽奖（口令默认 whl） |
| 无前缀 | `排行` / `排名` / `积分榜` | 群排行 |
| 有前缀 | `/兑换` | 查看可兑换物品 |
| 有前缀 | `/兑换 <物品ID> [数量]` | 兑换物品 |
| 有前缀 | `/兑换记录 [页码]` | 查看自己的兑换记录 |
| 有前缀 | `/兑换记录 <record_no>` | 查看单条详情 |
| 有前缀 | `/流水 [页码]` | 查看自己积分流水（纯数字视为页码） |
| 有前缀 | `/签到统计` | 今日签到数据 |
| 有前缀 | `/设置生日 <MM-DD / MM月DD日>` | 设置生日 |
| 有前缀 | `/查生日 [@用户]` | 查看生日 |

### 管理员指令

| 指令 | 说明 |
|------|------|
| `/加分 @用户/Q号 <分值>` | 增加积分 |
| `/扣分 @用户/Q号 <分值>` | 扣除积分 |
| `/添加兑换 <名称> <消耗> [库存]` | 新增兑换物品 |
| `/删除兑换 <物品ID>` | 软删除物品 |
| `/修改兑换 <ID> <字段> <值>` | 修改物品属性（cost/discount_price ≥ 1，stock ≥ -1，折扣时间格式 `YYYY-MM-DD HH:MM`） |
| `/核销 <记录编号> [备注]` | 切换核销状态（pending ↔ verified） |
| `/设置折扣 <ID> <折扣价> <截止时间>` | 设置兑换折扣 |
| `/清除折扣 <ID>` | 清除兑换折扣 |
| `/设置今日口令 <关键词> <积分>` | 设置每日口令 |
| `/清除今日口令` | 清除每日口令 |
| `/设置 <配置项> <值>` | 修改运行时配置 |
| `/查看配置` | 查看当前配置 |
| `/兑换记录 all [页码]` | 查看全部兑换记录 |
| `/兑换记录 pending [页码]` | 查看未核销记录 |
| `/流水 @用户 [页码]` | 查看指定用户流水（支持 `@QQ` / `@昵称(QQ)`，仅管理员） |
| `/流水 all [页码]` | 查看本群全部用户流水（仅管理员） |
| `/添加管理 <QQ号>` | 添加本群积分系统管理员（仅群主/全局管理员） |
| `/删除管理 <QQ号>` | 移除本群管理员（仅群主/全局管理员） |
| `/添加日期奖励 <MM-DD\|MM-DD~MM-DD> <关键词> <积分> [概率]` | 新增日期奖励 |
| `/删除日期奖励 <ID>` | 软删除日期奖励 |
| `/查看日期奖励` | 查看日期奖励列表 |
| `/清空数据` | 清空本群积分数据（用户/积分/流水/抽奖/兑换/口令），需验证码二次确认，清空前自动备份 |
| `/清空全部数据` | 清空全部数据（含商店、日期奖励、管理名单），仅 AstrBot 全局管理员，需验证码二次确认，清空前自动备份 |
| `/确认清空 <验证码>` | 确认执行待清空操作（5 分钟内有效，验证码单次有效） |

## 配置项

所有配置均可通过以下两种方式修改，双向同步、热生效：

1. **AstrBot WebUI**：插件设置页（`插件 → 积分系统 for WHL`）可视化查看与编辑
2. **指令**：`/设置 <键> <值>`、`/查看配置`

> 每日刷新时刻（`signin_refresh_time`）统一作用于签到、连签、每日首签、每日口令、抽奖次数等全部每日逻辑；一天区间为该时刻至次日同一时刻。刷新时刻、备份时间、生日播报时间均**按宿主机本地时区**计算。

| 键 | 类型 | 默认值 | 说明 |
|-----|------|--------|------|
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
| signin_refresh_time | str | 04:00 | 每日刷新时刻（签到/口令/抽奖次数等每日逻辑，按宿主机时区） |
| **活跃奖励** | | | |
| active_reward_enabled | bool | true | 开关 |
| active_reward_probability | float | 0.05 | 触发概率 (0~1) |
| active_reward_points_min | int | 1 | 每次奖励随机积分下限 |
| active_reward_points_max | int | 5 | 每次奖励随机积分上限 |
| active_reward_cooldown | int | 60 | 同用户冷却秒数 |
| active_reward_min_length | int | 3 | 消息最小字数 |
| active_reward_global_cooldown | int | 10 | 全群全局冷却秒数 |
| **抽奖** | | | |
| lottery_enabled | bool | true | 开关 |
| lottery_cost | int | 20 | 单次消耗积分 |
| lottery_daily_limit | int | 10 | 每日抽奖次数上限 |
| lottery_passphrase | str | whl | 抽奖口令 |
| lottery_tiers | json | (五档) | 权重+固定积分区间+标签+emoji |
| **负分** | | | |
| negative_disable_lottery | bool | true | 负分禁止抽奖 |
| **生日** | | | |
| birthday_bonus_points | int | 100 | 生日签到奖励 |
| birthday_announce_time | str | 08:00 | 每日播报时间 |
| **备份** | | | |
| backup_enabled | bool | true | 开关 |
| backup_time | str | 04:00 | 每日自动备份时刻（按宿主机时区） |
| backup_dirs | json | [] | 备份目标目录列表，支持相对路径（基于插件数据目录） |
| **关键词** | | | |
| keyword_sign | json | ["签到","sign","打卡"] | 签到触发关键词 |
| keyword_lottery | json | ["抽奖","lottery"] | 抽奖触发关键词 |

### 抽奖五档默认配置

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

## 数据存储

- **数据库文件**: `<AstrBot数据目录>/plugin_data/astrbot_plugin_point_system_by_whleague/points_system.db`（SQLite，WAL 模式），自动创建
- **路径定位**: 优先使用 AstrBot 官方路径 API 定位数据目录，不依赖进程工作目录，云服务器部署无需额外配置
- **自动备份**: 默认每天 04:00（配置项 `backup_time`）备份到配置项 `backup_dirs` 指定的目录，文件名含时间戳
- **备份路径**: 绝对路径直接使用；相对路径基于插件数据目录解析，支持 `~` 展开
- **备份方式**: `VACUUM INTO` 生成一致快照（含 WAL 数据）
- **数据库版本**: 当前 schema v2，旧库首次加载时自动迁移（负分头衔原名片、流水操作人字段），升级前建议先备份数据库

## 依赖

- `aiosqlite >= 0.20.0` — 异步 SQLite 驱动

## 开发

```bash
# 克隆项目
git clone https://github.com/WHLofficial/astrbot_plugin_point_system_by_whleague
# 放到 AstrBot 插件目录
cp -r astrbot_plugin_point_system_by_whleague AstrBot/data/plugins/
# 重启 AstrBot 或热重载
```

### 运行测试

插件内置零依赖测试套件（35 项功能/并发/安全/稳定性测试 + 性能基准），全部在临时库上运行，不触碰生产数据：

```bash
cd AstrBot/data/plugins/astrbot_plugin_point_system_by_whleague
python -m tests.run_all
```

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

MIT
