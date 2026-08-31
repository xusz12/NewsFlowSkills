# Changelog

## v1.2.3（待发布）— Markdown 隐藏已恢复采集告警

- retry 或 fallback 最终成功的采集记录新增稳定的 `recovered: true` 标记，并继续保留在运行 JSON、日状态、daily sidecar 与错误计数中。
- 单轮与每日 Markdown 的 `## errors` 只显示最终失败；仅有已恢复记录时显示 `- 无`。
- 兼容缺少新标记但错误文本以 `已恢复：` 开头的 v1.2.2 日状态。
- 修正 Aelia Capitolina 的 X 采集 handle：主命令与 fallback 统一由失效的 `Areskapitalon` 改为 `kaptonia`。

## v1.2.2（2026-08-23）— 调整 X 来源清单

- 移除 `jakevin7`、`cyrilxuq` 与 `HuXijin_GT`，新增 `WaylandZhang`。
- 默认配置现有 19 个采集入口，其中 X/Twitter 11 个；新账号沿用 opencli 主路径、native fallback 与 `auto` 翻译策略。
- 采集、翻译、sidecar、导出与其余来源行为不变。

## v1.2.1（2026-08-22）— 来源快照、容量翻译与文本边界

- 将来源显示名、类型、身份、命令、重试门槛和翻译策略收敛到 `commands.json`；pipeline 生成本轮 `section_metadata` 快照，prepare/finalize 不再依赖可变配置。
- 翻译计划取消固定 8 URL 和长推文 1000 字符特例，改按必需标题、引用与摘要的源文字总量分批；默认 12,000 字符以内单批，长文本不截断。
- 抽取两个脚本完全相同的原子写入、JSON、时间与翻译策略 helper，并删除未调用的旧翻译 warning helper。
- Han 检测覆盖 Unicode 扩展区；带 offset、ISO 与无 offset 本地时间统一到目标时区；Markdown 标题和 URL 特殊字符安全转义。
- 只保留每日累计 `dailyFreshNews_YYYY-MM-DD.newsreader.json`；不再生成或导出 per-run `*_freshNews.newsreader.json`，但不会删除历史文件，旧 state 的 `run_sidecar_path` 继续兼容读取。

## v1.1.3（2026-08-22）— 稳定 per-run 输出与可移植导出

- per-run 文件名改为 `YYYY-MM-DD-HH-mm-ss-<sha256前12位>`，由不可变 `generated_at + run_id` 确定，避免同分钟或同秒运行互相覆盖。
- 统一 `quoted_text_zh` 为引用文本 canonical 字段；旧 `quoted_text` 仅作兼容回退，冲突值会被验证拦截并要求修复。
- export 新增 `--target-root`，并支持 `NEWSFLOW_EXPORT_ROOT`；CLI、环境变量、旧个人默认路径按优先级解析，同时兼容旧分钟级文件名。

## v1.1.2（待发布）— 分离 X 采集入口与内容作者

- 每个 Twitter command 显式声明 `source_type`、`source_handle` 与 `source_name`，并将采集账号身份贯穿到 normalized item 和 newsreader sidecar。
- 条目 URL、`author_name` 与 `author_screen_name` 继续表示实际内容作者；原创、转发、回复和引用不会覆盖采集入口身份。
- 删除脚本内旧 6 账号 `TWITTER_SECTIONS` 硬编码；以后新增账号只需修改 `commands.json`。

## v1.1.1（2026-07-26）— 增加 Twitter/X 来源

- 新增 `aleabitoreddit`、`LinQingV`、`cyrilxuq`、`Areskapitalon`、`ChinaMacroFacts`、`MacroMargin`、`HuXijin_GT` 七个账号。
- 每个账号沿用现有约定：最新 10 条、`translation_policy: "auto"`、opencli 主路径与 native fallback。
- pipeline、翻译状态机、finalize 与 export 行为不变。

## v1.1.0（2026-07-25）— runtime-neutral project-scope 架构

- 唯一安装 payload 迁移到 `skills/newsflow/`。
- 删除 runtime adapters 与全局同步脚本；所有环境使用同一完整 `SKILL.md`。
- 增加 Codex 可选 `agents/openai.yaml`，主流程不依赖该 metadata。
- README 改为 Codex、Claude Code、Kimi Code CLI、Pi 的简化项目安装命令。
- 三个业务脚本与 `references/commands.json` 保持 v1.0.1 byte-identical；业务行为不变。

## v1.0.1 — 翻译修复门禁与静默诊断

- 验证结果写入隐藏运行状态；未完成一次 repair 时禁止带翻译问题 finalize。
- 翻译诊断不再进入用户 Markdown 与 sidecar errors，采集错误仍正常展示。

## v1.0 — 脚本化翻译批次与 URL 对账

- 新增脚本驱动的 initial/一次 repair 翻译计划：每批最多 8 个 URL，超长 Twitter 单独成批。
- 新增单批 URL 集合精确对账、字段白名单/必填字段校验，以及通过后才执行的原子累计合并。
- 保留 CJK `auto` 规则；第二次校验仍失败时继续 finalize。

## 2026-06-03 — Twitter 翻译字段分离

- `title` 保持主推文翻译，`quoted_text` 保持引用推文翻译，不可互换。
- 长推文完整翻译，不压缩为摘要。

## 2026-05-30 — 移除 Bloomberg Politics/Economics

- 从 `references/commands.json` 移除 Bloomberg Politics 与 Economics 新闻源。

## 2026-05-15 — 增加可选导出

- finalize 后可选运行 `export_outputs.py`，将 daily/per-run 产物写入 DailyNews 月份目录。
