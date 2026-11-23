# 台账字段契约（本地版，2026-11-23）

> 本文件上半部分是 Base2 旧 schema，**仅作历史参照**：Base2 已删除且不重建，脚本已改为读本地台账。
> 当前生效字段与文件：
>
> | 文件 | 作用 | 写入方 |
> |---|---|---|
> | `ledger/ledger_YYYY.csv` | 结算主表，25 列，`(date,num)` 幂等 | Step3 经 `settle` → `ledger.py append` |
> | `ledger/pending_judgments.json` | 赛前判断暂存（键 `date\|num`） | Step1/Step2 经 `write` |
> | `ledger/baseline.json` | 滚动基准（`A级.usable` 等） | `ledger.py stats --write` |
> | `ledger/stats_YYYYMMDD.json` | 当日滚动统计快照 | 同上 |
>
> 主表列：`date,num,level,league,home,away,kickoff,trigger_mode,direction,grade,gap,rule_ids,advice_sp,advice_state,score,result_1x2,result_hcp,sp_1x2,stake_pct,shadow_pnl,real_pnl,verdict,attrib,data_src,note`
> 枚举：`verdict ∈ 命中/未命中/部分/无法打分`；`attrib ∈ 判断对/判断错/数据缺/执行错/运气`；`level ∈ A/B/C（C 细分子层用 tiers 的 C1俱乐部/C1杯赛/C2国家队）/W女足`。
>
> 判定阈值：已结算 ≥20 才出结论；命中率 <60% → 只出方向不出仓位（30 场门槛属 Step5，不动）。

## 一、四张表

| 表 | table_id | 条数（2026-11-18） | 用途 |
|---|---|---|---|
| 赛事明细 | `tblm2MroEC6fXbez` | 725 | 每场一行：赛前预测 + 实际结果 + 命中标记 |
| 判断簿 | `tblSEPpqGH5xcq84` | 15 | 原子判断台账：一条判断一行，可结算 |
| 框架当前状态 | `tbly1BxFnT2eSekR` | 98 | 项目 / 当前值·说明，含降权开关与基线 |
| Step2建议追踪 | `tbl9WMhdpRXpxXpm` | 48 | 影子标的与实盘追踪 |

另有：统计汇总 `tbllSjuMvV85pVzA`（双 l，251 条，仅 指标/数值(text)/备注 三列，只能人读）、错判分析 `tbl77xij8CGgkLQr`（78 条，编号/错判原因摘要/共性总结）。

## 二、赛事明细关键字段

- 键与归属：`统一键`（`YYMMDD-周XNNN`）、`编号`、`批次`（文本，从统一键前 6 位回填）、`赛事等级`（A级/B级/C级）、`子层标签`、`联赛`、`主队`、`客队`、`比赛日期`
- 预测列：`预测方向`、`信心`、`预测首选比分`、`预测次选比分`、`进球数预测`、`预测大小球`、`让球数`、`EV值`、`初盘SP`、`临场SP`
- 结果列：`实际全场比分`、`半场比分`、`实际胜平负`、`实际总进球`、`实际大小球(2.5)`
- 命中列：`方向是否命中`、`比分是否命中`、`进球数是否命中`、`大小球是否命中`
- 归因列：`错误分类`、`偏差类型`、`规则触发`、`临场信号`
- 情报列：`伤停情报`、`预测首发`、`情报来源`（注意：017 仅覆盖五大联赛/欧冠/欧罗巴；首发是预测值，须标时效）
- 其它：`主表记录ID`、`数据来源`、`人工判断标记`、`是否精选场`、`主队xG信号`、`客队xG信号`、`xG差`

`子层标签` 选项：`A层` / `B层` / `C1俱乐部` / `C1杯赛` / `C2国家队` / `W女足`。

## 三、判断簿字段

| 字段 | 取值 | 说明 |
|---|---|---|
| `judgement_id` | `J<YYMMDD>-<编号>-<后缀>` | 幂等键，如 `J261117-006-DIR` |
| `match_id` | `261117-周二006` | 与赛事明细统一键对齐 |
| `jtype` | `direction` / `euro_asia` / `gap` / `market_shape` / `signal_tag` / `model_market` / `assumption` / `reverse_risk` / `score_parts` / `rule_hit` / `implied_prob` / `tactical_match` | 判断类型，决定可统计的维度 |
| `stage` | `S1` / `S2` / `S3` | 写入阶段 |
| `verdict` | `命中` / `证伪` / `部分` / `未验证` | **必须结算** |
| `attrib` | `判断错` / `数据缺` / `执行错` / `运气` | 证伪时必填 |
| `confidence` | `高` / `中` / `低` | 写入时的把握 |
| `strength` / `num` | number | 强度分；`num` 存该判断的关键数值（如赔率变动 pp、档差） |
| `gap_flag` / `cost_flag` | `Y` / `N` | 是否带缺口 / 是否价格吃掉优势 |
| `batch` / `match_label` / `basis_ref` / `rule_version` / `fw_version` / `note` / `content` / `as_of` / `fallback` / `key` | text | `as_of` 用 ISO 8601 带时区；`basis_ref` 写清取数来源（如 `008/21:00档 亚盘+欧赔`） |

## 四、写入模板

`write` 入参（`create_records`，一次 ≤200 条）：

```json
{"create_records":[{
  "judgement_id":"J261118-周二006-DIR","match_id":"261118-周二006","batch":"261118",
  "match_label":"欧国联 丹麦vs挪威","jtype":["direction"],"stage":["S2"],
  "content":"客胜信心中60%：竞彩/欧指/亚盘三向同移","key":"客胜","confidence":["中"],
  "strength":3,"gap_flag":["Y"],"cost_flag":["N"],"verdict":["未验证"],
  "rule_version":"RP-v2","fw_version":"v3.1","as_of":"2026-11-18T21:00:00+08:00",
  "basis_ref":"008/21:00档 欧指+亚盘三家+必发"
}]}
```

`settle` 入参（judgement_id → 待更新字段）：

```json
{"J261118-周二006-DIR":{"verdict":["命中"]},
 "J261118-周二006-TOT":{"verdict":["证伪"],"attrib":["判断错"],"note":"实际5球，区间给在盘口线下方"}}
```

## 五、口径与阈值

- 分层命中率分母只取 `方向是否命中` 非空；已结算 < 20 的词条不出结论。
- 降权线：已结算 ≥ 20 且命中率 < 60%（2026-11-18 实测 C1俱乐部 84/155=54.2%，首个触发项）。
- 子层回填规则：A级→A层；B级→B层；C级中 国家队/友谊/亚运→C2国家队、含“杯”→C1杯赛、含“女”→W女足、其余→C1俱乐部；`赛事等级` 为空按联赛推断（五大联赛/欧冠/欧罗巴/欧联→A层，其余→B层）。
- 批次不可用比赛日期反推：226 条无统一键记录批次留空是已知设计。
