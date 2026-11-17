# skills

自研 Skill 统一存放目录。每个 Skill 一个子目录，含 `SKILL.md` 与 `scripts/`。

| Skill | 用途 | 依赖 |
|---|---|---|
| [drive-doc-to-md](drive-doc-to-md/SKILL.md) | 飞书/豆包云盘文件夹批量转 Markdown 并归档原件，带数字级保真闸门 | `lark-cli`、`pdftotext` |
| [football-match-data-collector](football-match-data-collector/SKILL.md) | 竞彩足球赛前数据采集（状态/交锋/伤停/盘口/环境），按 facet 归档 | `lark-cli` |
| [jczq-match-pack](jczq-match-pack/SKILL.md) | 竞彩赛前情报包（L0–L6 六层匹配：主表/001/006/画像/xG/交锋/联赛基线） | `lark-cli` |
| [skill-scout](skill-scout/SKILL.md) | 技能侦察：按上下文检索已装技能，判断需求是否已覆盖；评估外部候选的重复度与依赖可跑性 | 纯标准库 |

约定：

1. 本目录是 Skill 的**唯一源**；本地部署目录用软链接指向，不做双份维护。
2. 更新流程：本地改 → 跑一次真实/`--dry-run` 验证 → 本仓库提交 → 同步回本地。
3. 公开仓库中不写入内部云盘 token、app secret 等凭据，一律用占位符。

## 验证记录

- 261117：`drive-doc-to-md` 在云盘 001 目录跑通 88 份（84 PDF + 4 在线文档），保真闸门 88/88 通过，原件全部归档。
- 261117：`skill-scout` 索引 69 个已装技能；5 条回归用例（联赛报告 0.67 / 云盘转 md 0.82 / xG 0.83 / 周报摘要 0.22 / 海报 0.25）全部命中预期技能。
