# Claude Code Adapter — Newsflow

当在 Claude Code 运行时中使用 newsflow 技能时，除了主 `SKILL.md` 中的通用流程外，还需遵循本文件的 Claude Code 专属执行约束。

## 1. Skill Root 解析

Claude Code 加载 skill 时，通过当前 SKILL.md 所在目录确定 skill root：

```text
SKILL_ROOT = <包含当前 SKILL.md 的目录绝对路径>
```

例如：如果 newsflow 安装在 `/Users/x/.claude/skills/newsflow/`，则 `SKILL_ROOT` = `/Users/x/.claude/skills/newsflow`。

Claude Code 在 Bash tool 中可以使用 shell 变量来引用 skill root（与 Codex 不同，Claude Code 没有 `prefix_rule` 限制）：

```bash
SKILL_ROOT="/Users/x/.claude/skills/newsflow"  # 从 SKILL.md 实际加载路径获取
python3 "$SKILL_ROOT/scripts/run_news_pipeline.py" --config "$SKILL_ROOT/references/commands.json" --out-json "$CURRENT_JSON_PATH"
```

## 2. 执行模式约束 (Execution Mode)

### 2.1 独立步骤调用
- 使用独立的 Bash tool 调用，每个步骤一次调用。
- **禁止**将整个 workflow 包裹在单个 `bash -lc '...'` 中。
- 原因：Claude Code 需要在步骤间做模型判断（如检查 prepare 结果、执行翻译、写入 `translated.json`），这些无法在纯 shell 脚本中完成。

### 2.2 脚本路径使用 shell 变量
- 与 Codex 不同，Claude Code 的 Bash tool **允许**使用 shell 变量展开。
- 将 `SKILL_ROOT` 定义为 shell 变量，后续脚本路径通过变量拼接。
- 这使得同一份 adapter 在不同安装路径下无需修改。

### 2.3 包含空格的路径处理
- 用双引号包裹所有路径变量。
- 优先使用 `.news_state/runs/<run-dir>/` 下的相对路径作为 artifact 目录。

## 3. 工具权限需求

本技能执行时需要以下 Claude Code 工具：

| 工具 | 用途 |
|------|------|
| `Bash` | 执行 Python 脚本（pipeline, prepare, finalize） |
| `Read` | 读取 `commands.json`、中间 JSON 产物 |
| `Write` | 写入 `translated.json` |

建议 frontmatter 设置：

```yaml
allowed-tools: Bash, Read, Write
```

可选配置：
- `context: fork` — 在子代理中运行，避免阻塞主会话。适用于 newsflow 执行时间较长的场景。
- `model: sonnet` — 如需指定模型。默认继承当前会话模型即可。

## 4. 翻译步骤执行说明

翻译在模型内完成，不调用外部 API。Claude Code 下的具体操作：

1. 读取 `incremental.json` 中的 `items_to_translate`。
2. 对每条新闻的 `title` 进行翻译。
3. 对 Twitter 引用推文的 `quoted_text` 进行翻译。
4. 对 Bloomberg 的 `summary` 进行翻译。
5. Bloomberg summary 需遵循主 `SKILL.md` 中的验证与回退规则（缺译/非中文时修复一次；仍失败则回退原文并记录 warning）。
6. 将翻译结果写入 `translated.json`，格式参见主 `SKILL.md` 中的 JSON schema。
7. **关键**：即使 `items_to_translate` 为空，也必须写入 `{}` 到 `translated.json`，否则 finalize 会失败。

## 5. 与主 SKILL.md 的关系

- **主 SKILL.md**：包含完整的工作流描述、错误恢复策略、输出合同、状态模式说明、验证清单 — 这些是跨工具通用的
- **本文件**：仅包含 Claude Code 专属的执行约束和工具配置 — **不得复制或包含通用业务流程**
- 执行 newsflow 时，同时参考两个文件以确保正确运行

## 6. 兼容性说明

以下主 SKILL.md 中的功能在 Claude Code 下的行为：
- **翻译步骤（步骤8）**：由 Claude 的模型层完成，格式遵循主 SKILL.md 中定义的 JSON schema
- **错误恢复策略**：Python 脚本中的 `PREPARE_*` / `FINALIZE_*` 错误码处理逻辑在 Claude Code 下不变；Claude 可用 `Read` 工具检查中间 JSON 文件辅助诊断
- **输出文件**：生成的 `dailyFreshNews_YYYY-MM-DD.md` 和 `YYYY-MM-DD-HH-mm_freshNews.md` 格式与 Codex 完全一致

## 7. 安装方式

Claude Code skill 安装位置：

| 范围 | 路径 |
|------|------|
| 个人（全局） | `~/.claude/skills/newsflow/` |
| 项目 | `.claude/skills/newsflow/` |

从 canonical source 安装时，需要复制：
- `SKILL.md`
- `scripts/` 目录（完整）
- `references/commands.json`
- `tests/`（可选）
- `adapters/claude.md`（本文件）

不需要复制：
- `adapters/codex.md`
- `agents/openai.yaml`
