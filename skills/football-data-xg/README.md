# football-data-xg（含 football-charts 第二源）

自研 Skill：从 football-data.co.uk 提取实测 xG/xGa（Opta 系），并接入 football-charts.com 作为第二 xG 源做交叉校验与赛果对账。

## 目录

```
SKILL.md                  # 工作流、口径边界、判读规则
references/leagues.md     # 联赛代码表（E0/SP1/I1/D1/F1/N1/P1…）
scripts/xg_fetch.py       # 主源：football-data.co.uk 实测 xG/xGa
scripts/fc_fetch.py       # 第二源：football-charts REST 适配（leagues/results/table/team/fixtures/acheck/dual/resultcheck）
```

## 两个源的分工

| 能力 | football-data.co.uk | football-charts.com |
|---|---|---|
| 队级 xG/xGa | 五大联赛 | 98 联赛（含荷甲/葡超/低级别） |
| 逐场 xG / 技术统计 | ✅ | ❌（免费档只有队级；逐场为付费功能） |
| 半场比分 / 首球分钟 | 半场有，首球无 | ✅ 两者都有 |
| 未来赛程 + 校准概率 | ❌ | ✅ |
| 欧战（欧冠/欧罗巴） | ✅ | ❌ |
| 历史赛季 | 多年 | 只有当季 + 上一季 |
| 赔率 | 欧赔/亚盘 2.5 盘 | ❌ 全无 |

## 每日动作

```bash
python3 automation/scripts/fc_fetch.py dual --top 6 --outdir ./out      # 双源 xG 对照
python3 automation/scripts/fc_fetch.py resultcheck --outdir ./out        # 赛果对账
```

判读：ΔxG 与 ΔxGa 同时 > 0.10 记 `⚠双差` 回主源复核；赛果不一致必须为 0，出现即回官方赛果核对。

## 凭据

- football-charts 免费 key：`~/.fc_api_key`（600 权限，勿入库），脚本按「FC_API_KEY 环境变量 → 该文件」两级读取
- 免费档 200 次/天、60 次/分钟；dual 一轮约 49 次，resultcheck 一轮 7 次

## 归属

football-charts 数据 © football-charts.com，个人/研究用途免费，引用须署名 `Data by football-charts.com`，禁止转售。football-data.co.uk 按其站点条款使用。
