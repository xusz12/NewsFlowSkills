# NewsFlowSkills

`newsflow` 按配置顺序采集多个新闻源，做全局 URL 去重、日内增量状态管理与模型内中文翻译，最终生成 daily 与 per-run 两类 Markdown；finalize 后可选导出。

## 什么是 newsflow

newsflow 是一套面向多新闻源的可恢复增量采集与中文化工作流。它把“采集、规范化、去重、日内增量、验证、落盘”拆成确定性的脚本阶段，并把自然语言翻译留给当前 agent 的模型完成。

核心能力：

- 按 `commands.json` 的顺序串行执行多个采集命令；单个来源失败不会阻断后续来源。
- `v1.2.2` 默认配置包含 19 个采集入口，其中 X/Twitter 11 个。
- 统一 Reuters、Bloomberg、TechCrunch、Ars 与 Twitter/X 等来源的数据结构。
- 按绝对 URL 全局去重，并结合前一日与当日状态只输出本轮新增内容。
- 通过模型生成中文标题、Twitter 引用翻译与 Bloomberg 摘要翻译，不接入第三方翻译 API。
- 为每轮运行保留独立工件、翻译验证证据和恢复信息，避免旧工件或并发状态造成静默数据错误。
- 同时生成每日累计简报、本轮增量简报与每日累计 news-reader sidecar，并可选导出。

## 适用场景与边界

- 适合定时或手动生成 fresh/daily 新闻产物、避免重复阅读，并保留每日累计视图与来源错误。
- 采集命令由配置决定；newsflow 不替用户选择账号、栏目或运行频率。
- 翻译由宿主模型完成，不调用第三方翻译 API。
- newsflow 负责新闻采集与中文化产物，不负责后续的简报提炼、重点排序或观点总结。
- 导出到 DailyNews 是 finalize 之后的可选后处理，不是本地产物成功的必要条件。

## 输入与产物

每次运行使用三类输入：

- 安装 payload 内的 `references/commands.json`，或用户通过 `--config` 提供的覆盖配置。每个 source command 的输出都会进入同一条规范化 pipeline。
- 当前目标 workspace；本地产物与隐藏状态都写在这里。
- 已有 `.news_state/`；prepare 用它识别前一日 URL、当日已见 URL、已完成 runs 与最新 state snapshot。首次运行时可以不存在。

finalize 成功后，workspace 中会出现：

```text
<workdir>/
├── dailyFreshNews_YYYY-MM-DD.md
├── dailyFreshNews_YYYY-MM-DD.newsreader.json
├── YYYY-MM-DD-HH-mm-ss-<sha256前12位>_freshNews.md
└── .news_state/
    ├── YYYY-MM-DD.json
    └── runs/<run-dir>/
        ├── current.json
        ├── incremental.json
        ├── translated.json
        └── translation-*.json
```

- `dailyFreshNews_YYYY-MM-DD.md` 是当日 rolling 累计文件。
- `YYYY-MM-DD-HH-mm-ss-<sha256前12位>_freshNews.md` 只包含本轮新增内容；export 同时兼容旧版分钟级文件名。
- 只为每日累计文件生成 `dailyFreshNews_YYYY-MM-DD.newsreader.json`；per-run freshNews 不生成 sidecar。
- `.news_state/`、run artifacts 与翻译验证记录属于隐藏运行状态，不是用户简报。
- 本地 finalize 与可选 DailyNews export 相互独立；export 复制两个 Markdown 与 daily sidecar，不改变本地状态。导出根目录优先级为 `--target-root` > `NEWSFLOW_EXPORT_ROOT` > 旧个人默认路径，使用旧默认时会输出兼容性警告。

## 工作流概览

1. **读取配置并顺序采集**：读取包含显示名、来源身份、命令、重试门槛和翻译策略的单一来源配置，按顺序执行；单个来源失败会被记录，但不会阻断后续来源。
2. **全局去重并生成 current**：按绝对 URL 保留首次出现的条目，并把本轮规范化 `section_metadata` 快照与不可变 run metadata 一起写入 `current.json`。
3. **Prepare 日内增量**：对比已有 `.news_state`，过滤前一日和当日已见 URL，得到本轮新增条目、待翻译字段、采集错误与 state snapshot，写入 `incremental.json`。
4. **Initial translation plan、capacity batches 与 exact merge**：脚本按标题、引用和摘要的源文字总量规划批次；12,000 字符以内使用单批，超出时才拆分，且不截断长文本。模型只翻译计划要求的字段，脚本验证每批 URL 集合与字段后原子合并到 `translated.json`。
5. **Validate 与一次 repair**：检查翻译覆盖和必需中文字段。若有缺口，只允许生成一次 repair plan、补剩余字段并再 validate 一次；不无限循环。
6. **Finalize 原子落盘**：确认工件路径、run metadata 与 state snapshot 仍有效，再原子写 daily/per-run Markdown、daily sidecar 和日状态；不生成 per-run sidecar。
7. **可选 export**：finalize 成功后，可显式把两个 Markdown 与 daily sidecar 复制到 DailyNews 的对应月份目录。

## 恢复与安全语义

- pipeline 必须成功完成后才能执行 prepare，不能在采集仍运行时读取半成品。
- prepare 对 stale current、重复 run id/timestamp 或不可读工件等可恢复错误，只允许放弃失败 run 目录、重跑 pipeline + prepare 一次；坏路径、坏 metadata 或坏 state 保持硬失败。
- finalize 若发现 state drift，使用同一 `current.json` 重做 prepare，复用已有翻译并只补新缺口；不强制 finalize，也不自动重跑 pipeline。
- 每轮 fresh 文件禁止覆盖；旧 run 工件、混用 run 目录或已经 finalized 的 run 会被拒绝。
- 升级不会删除历史已存在的 per-run sidecar；旧 state 中的 `run_sidecar_path` 可继续读取但不再使用，新运行不写该字段。
- 翻译诊断只保存在隐藏 validation state，不污染用户 Markdown 或 sidecar `errors`。最终失败的采集来源错误会在 Markdown 与 sidecar 中呈现；retry 或 fallback 最终成功的 `recovered` 记录只保留在机器 JSON、日状态与 daily sidecar，不显示在 Markdown。
- export 失败不会回滚已成功生成的本地 Markdown、daily sidecar 或日状态。

## 快速开始

1. 在目标 workspace 按下方[安装](#安装)章节安装对应 runtime 的项目副本。
2. 在该 workspace 中让 agent 使用 `$newsflow` 运行默认配置，或同时提供自定义 config 路径。
3. 首次使用或验证新配置时，先在隔离 workspace 运行，确认来源命令、fresh/daily 产物与状态符合预期，再迁移到目标 workspace。

业务脚本负责确定性的采集、状态、验证和落盘；模型只负责翻译计划指定的文本。完整错误码、恢复条件、翻译 JSON schema 与输出合同见安装 payload 的 `SKILL.md`。`v1.2.3` 当前仍是本地候选版本，远端发布前不要把主分支安装结果表述为已验证的 v1.2.3。

## 仓库结构

```text
NewsFlowSkills/
├── README.md
├── CHANGELOG.md
├── tests/
└── skills/
    └── newsflow/
        ├── SKILL.md
        ├── agents/openai.yaml
        ├── references/commands.json
        └── scripts/
```

只有 `skills/newsflow/` 是安装 payload。根 README、CHANGELOG、测试与维护缓存不会进入 agent workspace。payload 使用同一份 runtime-neutral `SKILL.md`；`agents/openai.yaml` 仅为 Codex 可选界面 metadata，其他 runtime 可安全忽略。

历史记录见 [CHANGELOG.md](CHANGELOG.md)。

## 安装

在目标 workspace 根目录执行。以下命令使用稳定主分支，并安装到当前项目：

Codex：

```bash
npx -y skills add xusz12/NewsFlowSkills -a codex -y
```

Claude Code：

```bash
npx -y skills add xusz12/NewsFlowSkills -a claude-code -y
```

Kimi Code CLI：

```bash
npx -y skills add xusz12/NewsFlowSkills -a kimi-code-cli -y
```

Pi：

```bash
npx -y skills add xusz12/NewsFlowSkills -a pi -y
```

这些简化命令成立的前提是：仓库当前只有一个 skill，且主分支只接收已经 Review 的稳定内容，因此无需额外指定 `-s newsflow`。如果仓库以后出现第二个 skill，默认命令必须恢复 `-s newsflow`。

## 可复现发布与问题复现

只有在复现特定发布或排查 installer 差异时，才同时 pin installer 版本与 tag/commit：

```bash
npx -y skills@1.5.20 add xusz12/NewsFlowSkills#v1.1.0 -s newsflow -a codex --copy -y
```

其他 runtime 把 `codex` 替换为 `claude-code`、`kimi-code-cli` 或 `pi`。复现其他版本时，把 `v1.1.0` 替换成目标 `<tag>` 或 commit。升级时重新执行目标版本的完整 add 命令，不使用旧 lock ref 做原地 update。

从当前 workspace 移除：

```bash
npx -y skills remove newsflow -y
```

该项目级命令会删除当前 workspace 中 newsflow 的所有 project-local runtime copies 与 lock 记录。不要添加 `-a codex` 或 `--agent codex`：按 agent 移除在共享 `.agents/skills/` 目标上只清 lock、可能遗留物理目录。

## 本地开发验证

在隔离 workspace 根目录运行：

```bash
npx -y skills add /Users/x/.skills/newsflow -a codex -y
```

其他 runtime 把 `codex` 替换为 `claude-code`、`kimi-code-cli` 或 `pi`。Codex 与 Kimi Code CLI 写入 `.agents/skills/newsflow`，Claude Code 写入 `.claude/skills/newsflow`，Pi 写入 `.pi/skills/newsflow`。本地验证不得修改全局副本、联网采集或写真实 DailyNews。

## 执行边界

- 默认命令配置：安装 payload 内的 `references/commands.json`。
- 每次运行使用独立 `.news_state/runs/<run-dir>/` 工件目录。
- 翻译由模型完成，不调用第三方翻译 API。
- `finalize` 生成本地 daily/per-run Markdown；导出到 DailyNews 是显式可选后处理。
- 完整 pipeline、恢复、翻译证据、一次 repair、finalize 与 export 契约见安装 payload 的 `SKILL.md`。
