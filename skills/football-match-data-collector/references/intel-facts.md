# 情报 facts 协议与时间闸

本文件是伤停与阵容模块的字段契约。目标只有一个：让每条赛前事实都能被程序校验、去重、追溯，而不是一段无法复核的自由文本。

## 〇、schema 版本

| 项目 | 值 |
|---|---|
| 当前版本 | `v1` |
| 落点 | facts JSON 根对象 `"schema": "v1"`；MD 头部 `facts_schema：v1` |
| 含义 | 只要事件枚举、必填字段或时间闸口径有任何改动，版本号必须递增 |
| 校验 | `validate_intel.py` 只接受 `v1`；遇到未知版本直接 FAIL，防止旧脚本误判新结构 |
| 旧包处理 | 版本号低于当前且需重跑的，按新协议重采，不就地兼容 |

改协议的流程固定为三步：改字段 → 升版本号 → 更新 `tests/` 里的回归样本与期望值。

## 一、时间闸（最先执行）

采集开始前先确定两个时间点：

| 变量 | 含义 | 规则 |
|---|---|---|
| `kickoff_at` | 开赛时间 | 以竞彩官方开赛时间为准，带时区 |
| `collect_as_of` | 本次采集截点 | 必须早于 `kickoff_at`，且整份包只用同一个截点 |

每条事实必须满足：

```text
observed_at <= collect_as_of < kickoff_at
```

- `observed_at` 指**来源的发布时间**，不是你的采集时间。
- 页面无发布时间、只有"最近更新"这类模糊标注 → 不进 facts，写 `missing`，注明"发布时间不可确认"。
- 截点后发布的首发、伤退、赛果一律拒收，记入"越界拒收"清单，注明原来源与发布时间。
- 赛后统计（控球、射门、xG 等）不得作为赛前特征使用，即使它挂在同一场比赛页面上。

## 二、来源分级

| 级别 | 典型来源 | credibility 上限 | 单独确认 |
|---|---|---|---|
| L1 | 俱乐部/赛事官方公告、官方首发、主帅发布会 | 0.95 | 可以 |
| L2 | 通讯社、长期跟队记者、主流体育媒体 | 0.85 | 需两条 L2 一致 |
| L3 | API-Football 等结构化伤病源、Transfermarkt 伤停页 | 0.75 | 需与 L1/L2 互证 |
| L4 | 聚合站、社媒、球迷论坛 | 0.40 | 只作线索，不入 facts |

补充规则：

- 更衣室矛盾、将帅失和、欠薪等负面传闻，至少两条 L2 一致才记录，`credibility ≤ 0.7`。
- 同一事件在不同级别来源出现时，保留高可信那条，低级别来源写进 `reason` 作为佐证，不重复建条目。
- 单一 L2 支撑的高影响事实（主力中锋缺阵、门将伤停）标 `[待确认]`，不得直接写成确定缺阵。

## 三、facts 结构

```json
{
  "match_id": "周四001",
  "kickoff_at": "2026-11-17T22:00:00+08:00",
  "collect_as_of": "2026-11-17T14:00:00+08:00",
  "completeness": 0.75,
  "missing": ["客队最终首发：官网未更新、跟队记者未发、懂球帝暂无阵容"],
  "facts": [
    {
      "event_type": "player_unavailable",
      "team": "阿森纳",
      "player": "球员A",
      "status": "confirmed_out",
      "position": "ST",
      "expected_minutes_delta": -75,
      "source_url": "https://example.com/official-news",
      "source_level": "L1",
      "observed_at": "2026-11-17T09:00:00+08:00",
      "credibility": 0.95,
      "reason": "俱乐部公告确认肌肉伤缺",
      "event_fingerprint": "ars-playerA-20261117"
    }
  ]
}
```

字段约束：

| 字段 | 必填 | 约束 |
|---|---|---|
| `event_type` | 是 | 见下表枚举，不得自造 |
| `team` | 是 | 用队名全称，与基础信息一致 |
| `player` | 视类型 | 阵容类事件可空 |
| `status` | 是 | `confirmed_out` / `doubtful` / `expected_return` / `confirmed_available` |
| `expected_minutes_delta` | 否 | 仅 `-90 ~ 0`，表达预计缺失分钟；不确定填 null |
| `source_url` | 是 | 原文可点开的链接 |
| `source_level` | 是 | L1/L2/L3/L4 |
| `observed_at` | 是 | ISO 8601 带时区，且满足时间闸 |
| `credibility` | 是 | 0–1 小数，不超过来源级别上限 |
| `reason` | 是 | 一句话说明依据，不写影响判断 |
| `event_fingerprint` | 否 | 缺省按 `team+player+event_type+日期` 生成 |

## 四、事件类型枚举

| event_type | 用途 | 关键字段 |
|---|---|---|
| `player_unavailable` | 伤、停、私人原因缺阵 | status、expected_minutes_delta |
| `player_return` | 伤愈回归、解禁复出 | expected_minutes_delta（预计出场分钟） |
| `lineup_confirmed` | 官方首发已公布 | 11 人 + 阵型 |
| `lineup_expected` | 媒体/跟队预计首发 | credibility ≤ 0.7 |
| `coach_statement` | 主帅战术或轮换表态 | reason 内含原话摘句 |
| `rotation_signal` | 明确轮换信号（杯赛夹联赛、赛程密集） | reason 内含依据 |
| `environment` | 天气、场地、裁判变更 | 具体值 + observed_at |

不设"影响程度""胜率变化""xG 调整"字段。这些属于分析层，采集层只交事实。

## 五、去重与完整性

- 同一 `event_fingerprint` 只保留一条；重复时保留 `observed_at` 较新、`credibility` 较高者。
- 同一球员同一原因的多篇报道视为同一事件，不因媒体数量增加条目。
- `completeness` = 已采必采字段数 / 必采字段总数，必采为模块 1、2、3、5、5.1、6、9。
- `completeness < 0.6` 时 `missing` 必须非空，否则视为造假。
- C 级赛事允许 `facts` 为空，但 `missing` 必须写"未深挖（C 级）"。

## 六、交付前自检

1. 每条 fact 是否都有 `source_url / observed_at / credibility`？
2. 是否存在 `observed_at > collect_as_of` 的条目？（应为 0）
3. 是否存在重复 `event_fingerprint`？（应为 0）
4. `completeness < 0.6` 时 `missing` 是否非空？
5. 是否混入了赛后统计、去水概率、价值判断？（应为 0）

五项断言（含 schema 版本）可用 `python3 scripts/validate_intel.py <intel.json>` 自动跑，FAIL 即不得交付。
