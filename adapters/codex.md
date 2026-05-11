# Codex Adapter — Newsflow

当在 Codex 运行时中使用 newsflow 技能时，除了主 `SKILL.md` 中的通用流程外，还需遵循本文件的 Codex 专属执行约束。

## 1. Skill Root 解析

Codex 加载技能时，通过当前 SKILL.md 所在目录确定 skill root：

```text
SKILL_ROOT = <包含当前 SKILL.md 的目录绝对路径>
```

例如：如果 newsflow 安装在 `/Users/x/.codex/skills/newsflow/`，则 `SKILL_ROOT` = `/Users/x/.codex/skills/newsflow`。

## 2. 执行模式约束 (Execution Mode)

以下约束来自 Codex 运行时的行为特征，是确保技能在 Codex 下正确运行的必要条件：

### 2.1 直接命令调用 (argv style)
- 每个步骤作为一个独立的直接命令调用执行（argv 风格）
- **禁止**将整个工作流包装在一个 `bash -lc` 或 `zsh -lc` 脚本中
- 原因：Codex 的命令匹配机制（`prefix_rule`）需要识别离散的命令调用

### 2.2 脚本路径必须是字面绝对路径
- 流水线和增量脚本必须以**字面绝对路径**形式调用
- **禁止**依赖环境变量展开来解析脚本路径
- 原因：`prefix_rule` 需要匹配静态的、可预测的命令模式

正确示例：
```bash
python3 /Users/x/.codex/skills/newsflow/scripts/run_news_pipeline.py --config <commands.json> --out-json <CURRENT_JSON_PATH>
```

错误示例：
```bash
python3 $SKILL_ROOT/scripts/run_news_pipeline.py ...  # 不要用变量展开
```

### 2.3 包含空格的路径处理
- 当工件路径包含空格时，**显式引用**它们
- 优先使用当前 `workdir` 下的**相对工件路径**（如 `.news_state/runs/<run-dir>/current.json`）

## 3. Agent 配置

Codex 版 newsflow 需要 `agents/openai.yaml` 文件定义 agent 接口：

```yaml
interface:
  display_name: "newsflow"
  short_description: "Sequential opencli news pipeline with daily and per-run freshness tracking"
  default_prompt: "Run configured opencli news commands sequentially..."
```

此文件**仅存在于 Codex 安装目录**，不在通用核心包中。

## 4. 路径规范

### 4.1 脚本路径模板
所有 Python 脚本调用使用以下模板：

```bash
python3 <SKILL_ROOT>/scripts/run_news_pipeline.py --config <SKILL_ROOT>/references/commands.json --out-json <RUN_DIR>/current.json
python3 <SKILL_ROOT>/scripts/run_incremental_news.py prepare --current-json <RUN_DIR>/current.json --state-dir <STATE_DIR> --out-json <RUN_DIR>/incremental.json
python3 <SKILL_ROOT>/scripts/run_incremental_news.py finalize --incremental-json <RUN_DIR>/incremental.json --translated-json <RUN_DIR>/translated.json --state-dir <STATE_DIR> --out-dir <WORKDIR>
```

> **注意**：`<SKILL_ROOT>` 是模板占位符，不是可执行命令。Codex 实际执行前必须将其展开为字面绝对路径（如 `/Users/x/.codex/skills/newsflow`）。不要直接运行带 `<>` 的命令，也不要使用 `$SKILL_ROOT` 变量展开（见 2.2 节）。

### 4.2 工件路径
保持在 `workdir` 下，使用相对路径：

```text
WORKDIR=<当前工作目录绝对路径>
STATE_DIR=<WORKDIR>/.news_state
RUNS_DIR=<STATE_DIR>/runs
```

## 5. 与主 SKILL.md 的关系

- **主 SKILL.md**：包含完整的工作流描述、错误恢复策略、输出合同、状态模式说明、验证清单 — 这些是跨工具通用的
- **本文件**：仅包含 Codex 专属的执行约束和路径规范 — **不得复制或包含通用业务流程**
- 执行 newsflow 时，同时参考两个文件以确保正确运行

## 6. 兼容性说明

以下主 SKILL.md 中的功能在 Codex 下的行为：
- **翻译步骤（步骤8）**：由 Codex 的模型层完成，格式遵循主 SKILL.md 中定义的 JSON schema
- **错误恢复策略**：Python 脚本中的 `PREPARE_*` / `FINALIZE_*` 错误码处理逻辑在 Codex 下不变
- **输出文件**：生成的 `dailyFreshNews_YYYY-MM-DD.md` 和 `YYYY-MM-DD-HH-mm_freshNews.md` 格式与 Claude Code 完全一致
