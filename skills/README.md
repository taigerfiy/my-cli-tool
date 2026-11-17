# skills

自研 Skill 统一存放目录。每个 Skill 一个子目录，含 `SKILL.md` 与 `scripts/`。

| Skill | 用途 | 依赖 |
|---|---|---|
| [drive-doc-to-md](drive-doc-to-md/SKILL.md) | 飞书/豆包云盘文件夹批量转 Markdown 并归档原件，带数字级保真闸门 | `lark-cli`、`pdftotext` |

约定：

1. 本目录是 Skill 的**唯一源**；本地部署目录用软链接指向，不做双份维护。
2. 更新流程：本地改 → 跑一次真实/`--dry-run` 验证 → 本仓库提交 → 同步回本地。
3. 公开仓库中不写入内部云盘 token、app secret 等凭据，一律用占位符。

## 验证记录

- 261117：`drive-doc-to-md` 在云盘 001 目录跑通 88 份（84 PDF + 4 在线文档），保真闸门 88/88 通过，原件全部归档。
