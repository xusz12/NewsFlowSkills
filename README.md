# newsflow（canonical source）

本目录是 newsflow 的唯一维护源（canonical source）。

## 目录角色

- 维护源（唯一）：`/Users/x/.skills/newsflow`
- Codex 安装目标：`/Users/x/.codex/skills/newsflow`
- Claude Code 安装目标：`/Users/x/.claude/skills/newsflow`

原则：平时只改维护源，不直接手改两个安装目标目录。

## 安装与同步

在维护源目录执行：

```bash
cd /Users/x/.skills/newsflow
bash tools/sync_install.sh --dry-run
bash tools/sync_install.sh
```

说明：
- `--dry-run` 先预览将要替换的受管目录。
- 实际同步会替换受管目录：`scripts/`、`references/`、`tests/`，并同步主 `SKILL.md` 与 adapter。
- Codex 侧会保留专属文件：`agents/openai.yaml`。

## 日常维护流程

1. 在 `/Users/x/.skills/newsflow` 修改通用内容：
   - `SKILL.md`
   - `scripts/`
   - `references/commands.json`
   - `tests/`
   - `adapters/`
   - `tools/`
2. 执行同步脚本安装到 Codex / Claude。
3. 在两侧做最小验证（结构、脚本语法、关键测试）。

## Codex 与 Claude newsflow 的区别

两侧共享同一套核心资产，差异仅在适配层与工具专属文件：

- 共同点：
  - 同步后的 `SKILL.md`、`scripts/`、`references/`、`tests/` 一致。
  - 业务逻辑一致。
- Codex 侧：
  - 使用 `adapters/codex.md`。
  - 保留 `agents/openai.yaml`（Codex 专属）。
- Claude Code 侧：
  - 使用 `adapters/claude.md`。
  - 不包含 `agents/openai.yaml`。

## Git 管理建议

- 推荐只为本维护源目录建 Git 仓库。
- 不要把 `.codex/skills/newsflow` 与 `.claude/skills/newsflow` 当作独立仓库长期维护，否则会再次漂移。
