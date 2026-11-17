# my-cli-tool

个人 CLI / Agent Skill 工具仓库。

## 目录

- `skills/` — 自研 Skill 源码（唯一源，本地用软链接指向）
  - `drive-doc-to-md` — 云盘文件夹批量转 Markdown 并归档原件
- `docs/` — 规范文档
  - `doc-format-spec.md` — 竞彩文档统一格式规范（MD 唯一源格式）

## 约定

- Skill 更新流程：本地验证 → 提交本仓库 → 同步回本地部署目录。
- 不提交内部凭据；云盘/应用 token 用占位符。
