---
name: football-prediction-skill
description: Generate explainable China Sports Lottery football predictions with event-time snapshots, T-24h/T-6h/T-90m/closing model horizons, separate directional and value states, strict reference/target market isolation, sourced pre-match intelligence, hierarchical Dixon-Coles probabilities, multi-window model validation, rolling backtests, tournament simulations, and self-contained dark HTML reports. Use for 竞彩足球、足球预测、竞彩赛单、胜平负概率、让球分析、比分预测、赔率价值、串关研究、赛前情报、足球回测、模型训练、训练无法晋级、football-data CSV、模型更新、小组排名、淘汰赛对阵、football prediction or Sporttery analysis.
---

# 竞彩足球智能预测

使用确定性程序完成赛单归一化、事件时间校验、概率模型、价值策略、回测和 HTML 渲染。智能体只负责查找并抽取有来源的赛前事实。始终把自包含 HTML 作为主要交付物。

## 核心约束

- 固定预测截点 `as_of`；只允许 `observed_at <= as_of < kickoff_at` 的数据进入模型。
- 预测必须归属 `t24h / t6h / t90m / closing`；只有同时点模型可复用市场融合与校准。
- 明确区分 `reference_market`、`target_market` 和 `benchmark_market`。
- 不得用目标竞彩价格生成概率后，再用同一价格证明存在价值。
- 方向判断与价格价值必须分开输出；有有效概率时输出 `strong / moderate / slight`，只有中性占位先验才输出 `unavailable`。
- 只有独立概率、校准状态和价格优势都通过门槛时，才输出 `candidate`。
- 缺少独立概率或目标价格参与概率形成时，价值输出 `unverified`，不得因此隐藏方向。
- 正向价格差尚未过门槛时输出 `watch`，没有正向优势时输出 `no_edge`，目标价格缺失时输出 `unavailable`。
- 不执行下注，不承诺命中率或盈利，不使用“稳、必选、稳赚”等措辞。

## 选择工作流

- 分析某日竞彩：执行“每日流程”。
- 用户提供历史 CSV、询问训练或无法晋级：读取 [训练与晋级](references/training.md) 后执行数据体检、回测或训练。
- 用户询问更新 Skill：执行 `football-predict update --check`；需要安装时再执行 `football-predict update`。
- 用户询问小组排名或淘汰赛：读取 [赛事推演](references/tournament.md) 后运行 `tournament`。
- 单场不在竞彩赛单中：按 [数据契约](references/data-contracts.md) 构造输入 JSON，再运行 `daily --input`。

## 命令入口

优先检查已安装命令：

```bash
football-predict doctor
```

命令不存在时使用仓库入口：

```bash
npx -y github:JetQiao/football-prediction-skill doctor
```

下文统一以 `football-predict` 表示当前可用入口。

## 每日流程

1. 确定竞彩销售日期和预测截点。未指定时使用用户本地当前时间；历史日期必须使用明确赛前截点或让 CLI 选择最早开赛前 90 分钟。
2. 获取赛单并写入不可变快照：

```bash
football-predict sync \
  --date YYYY-MM-DD \
  --as-of "YYYY-MM-DDTHH:MM:SS+08:00" \
  --out /tmp/football-predict-YYYY-MM-DD
```

3. 核对源场次数与解析场次数。HAD 未开售、仅 HHAD 在售或玩法暂未开放的比赛也必须保留。
4. 读取 `intel_queue_*.json`。只对 A 级场做深度联网检索，并按 [情报流程](references/intelligence.md) 写入结构化 `facts[]`。不得使用截点后或开赛后的信息。
5. 校验情报：

```bash
football-predict validate-intel /tmp/football-predict-YYYY-MM-DD/intel.json
```

6. 使用相同 `as_of` 生成报告：

```bash
football-predict daily \
  --date YYYY-MM-DD \
  --as-of "YYYY-MM-DDTHH:MM:SS+08:00" \
  --input /tmp/football-predict-YYYY-MM-DD/matches_YYYY-MM-DD.json \
  --intel /tmp/football-predict-YYYY-MM-DD/intel.json
```

7. 回复只提供 HTML 绝对路径、场次数、明确/中等/轻微方向数量、价值候选数量、方向不可用数量和关键降级警告。概率不是承诺。

## 市场与特征

使用 `--features features.json` 注入赛前 Elo/xG，使用 `--market market.json` 注入独立参考市场。读取 [数据源与降级](references/sources.md) 确认来源和角色。

- 官方竞彩 HAD/HHAD/SP 默认是 `target_market`。
- 用户传入或 The Odds API 的独立市场默认是 `reference_market`。
- football-data 历史赔率默认是 `benchmark_market`。
- 只有竞彩自身多玩法时，可以展示“目标市场共识”方向，但价值必须标记为 `unverified`，并禁止独立价值候选。

## 回测与模型

先体检数据：

```bash
football-predict inspect-data season-1.csv season-2.csv season-3.csv
```

生产概率回放：

```bash
football-predict backtest season-1.csv season-2.csv season-3.csv \
  --pipeline production --no-open
```

独立价值策略回放：

```bash
football-predict backtest history.csv --pipeline independent-value --no-open
```

训练 challenger：

```bash
football-predict train season-1.csv season-2.csv season-3.csv \
  --competition 英超 \
  --alias "Premier League" \
  --no-open
```

训练产物包含模型、`dataset_profile.json` 和 `training_report.html`。优先把 HTML 绝对路径交付给用户，不要只复述一行指标。

查看模型注册表：

```bash
football-predict models
```

只有多窗口样本外样本量、市场覆盖、Brier、Log-loss、RPS、ECE 和稳定性门槛全部通过的模型才能使用 `--promote` 晋级。未通过时保留为 challenger，不得因为单段 ROI 漂亮而强行作为生产结论。football-data 历史赔率只能验证 `closing`；非收盘时点缺少同截点基线时不得晋级。

## 本地模型库与概率门禁（本机自建，2026-11-23 起）

本机模型库在 `~/.local/share/football-prediction-skill/models/`，注册表随训练自动写入。三个本地脚本在 `scripts/` 下，均为纯标准库，可直接在 cron 非交互 shell 里跑：

| 脚本 | 作用 |
|---|---|
| `scripts/build_models.py` | 批量训练/列模型/导出 `references/models_status.md` |
| `scripts/rewrite_matches.py` | 中文队名 → 模型规范名（不跑这步模型等于没接） |
| `scripts/check_prob.py` | 概率门禁：null 与“模型空转”都非零退出 |
| `references/team_alias_cn.csv` | 中文队名 → football-data 缩写名（116 条，五大联赛） |
| `references/models_status.md` | 模型库现状（自动生成，勿手改） |

```bash
# 五大联赛 × 4 季（2324/2425/2526/2627，数据在 /home/user/Doubao/jczq_shared/fd_raw）
python3 scripts/build_models.py --horizon t24h
python3 scripts/build_models.py --list
python3 scripts/build_models.py --status

# 中文赛单改名：必须在 daily 之前跑，否则模型对不上队名
python3 scripts/rewrite_matches.py /tmp/football-predict-YYYY-MM-DD/matches_YYYY-MM-DD.json \
  --out /tmp/football-predict-YYYY-MM-DD/matches_en_YYYY-MM-DD.json

# 发布前门禁：0 = 全有概率；1 = 有 null 或“模型空转”
python3 scripts/check_prob.py /tmp/football-predict-YYYY-MM-DD/prediction_YYYY-MM-DD.json
```

口径约定：

- **`closing` 模型**（市场对数池化）基本等于市场共识，在 closing 档几乎不可能“Brier 优于市场 0.5%”，长期停在 provisional 属正常；需要方向参考时可以 `models --promote VERSION --force`，但必须对外写“研究用途强制晋级 / 市场锚定型”，**不得表述为通过验证**。
- **`t24h` 模型**在训练时隔离收盘赔率，只保留 Dixon-Coles 队力与赛程结构，是真正的独立概率；它与竞彩 SP 的差才是价值来源。
- 模型与预测时点不一致时，`daily` 会打 `calibration_status=horizon-mismatch`、`data_quality=partial`；这不是故障，但要在报告里注明。
- 无可用模型且比赛输入没有 HAD 赔率时，`final_probs` 必为 null —— 这种产物一律由 `check_prob.py` 拦下，不许进入日报。
- **中文队名必须改写**：`sync` 产出的是“曼城 / 马竞 / 布伦特”这类中文短名，而模型键是 `Man City / Ath Madrid / Brentford`。不改名时模型会拿联赛均值硬算一篇 `statistical`，概率有值但完全不可用（“模型空转”）。`check_prob.py` 会把“statistical 但队名不在模型队列表”判为失败。
- 未被模型覆盖的联赛（瑞超/巴甲/美职/法乙…）保持中文原样即可，`daily` 自然走 `market_baseline`。


每日预测赛后可结算：

```bash
football-predict evaluate-daily prediction_YYYY-MM-DD.json results.json
```

## 输出规则

- 保留 `prediction_*.json`、`report_*.html`、`manifest_*.json` 和快照 ID。
- HTML 必须单文件、离线可用且不发网络请求。
- 报告保留 `as_of`、来源、模型版本、训练截止、校准状态、数据缺失、方向依据和价值状态原因。
- 不在回复中粘贴完整赛单表；HTML 是主要交付物。

## 更新

```bash
football-predict update --check
football-predict update
```

更新后告诉用户新建一个 Codex/Claude Code 任务加载新版 Skill，不要求先卸载或手工重装。
