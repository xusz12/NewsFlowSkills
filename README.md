# newsflow（canonical source）

本目录是 newsflow 的唯一维护源（canonical source）。

## What's Changed

### 2026-06-03 — docs: 明确 Twitter 翻译主推文与引用推文字段分离规则
- **文件**
  - *SKILL.md（+4 −0）*
    - 新增规则：`title`=主推文翻译，`quoted_text`=引用推文翻译，不可互换
    - 长推文必须完整翻译，禁止压缩为摘要
  - *adapters/claude.md（+7 −2）*
    - 新增步骤 5：Twitter 翻译字段分离约束
  - *adapters/codex.md（+10 −1）*
    - 新增第 6 节「Codex 翻译执行补充」
  - *tests/test_news_workflow.py（+54 −0）*
    - 新增字段分离验证测试
- **影响**：Twitter 翻译不再出现主推文与引用推文混淆或长推文被压缩

### 2026-05-30 — refactor: 移除 Bloomberg Politics/Economics 新闻源
- **文件**
  - *references/commands.json（+0 −32）*
    - 移除 `bloomberg_politics` 命令配置
    - 移除 `bloomberg_economics` 命令配置
- **影响**：newsflow 不再抓取 Bloomberg Politics/Economics 板块

### 2026-05-15 — docs: 补充Claude适配器可选导出步骤与兼容性说明
- **文件**
	- *adapters/claude.md（+16 −0）*
		- 新增 4.3 可选导出（post-finalize），说明 `export_outputs.py` 在 Claude Code 下的调用方式
		- 补充路径引号包裹规则与导出失败不影响产物的说明
		- 兼容性章节新增导出脚本行为一致性的备注
- **影响**：Claude Code 端 finalize 后可执行可选导出到 DailyNews 归档目录

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

## 导出功能（可选后处理）

newsflow `finalize` 生成本地产物后，可选执行导出脚本：

```bash
python3 /Users/x/.skills/newsflow/scripts/export_outputs.py \
  --daily <daily_fresh_path> \
  --fresh <run_fresh_path>
```

导出规则：
- 固定根目录：`/Users/x/Library/Mobile Documents/iCloud~md~obsidian/Documents/DailyNews`
- 根目录必须已存在；不存在则导出失败并返回明确错误。
- 按月份归档到 `YYYY年M月` 子目录（子目录不存在会自动创建）。
- daily 与 fresh 文件名日期必须属于同一年月。
- 同名文件默认覆盖。

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
