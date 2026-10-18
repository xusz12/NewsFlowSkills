# Changelog

版本标签使用 `vMAJOR.MINOR.PATCH`。日期采用 `Asia/Shanghai`，历史版本日期以对应 Git 提交记录为准。

## v1.2.5（2026-10-18）— 源清单变动兼容与运行说明

### 修复

- 分离本轮启用来源和日报历史分组，修复同日删除／改名来源后 `section_metadata is missing configured section`；当前配置排序立即生效，已采新闻继续保留在原分组，停用的空分组不再显示无更新。
- 日状态持久保存历史来源元数据；旧状态从身份匹配的历史 run 快照恢复，缺失或损坏快照使用条目元数据回退。当前来源元数据缺失仍拒绝，不降低配置校验要求。
- 保留日状态和新闻条目的扩展字段，避免后续运行丢失原文摘要、中文引用或诊断字段；保留既有运行记录和精确 URL 去重规则。

### 流程说明

- 更新浏览器预检、中文字段和定时运行说明，明确逐批串行 merge、按 required_fields 翻译、从 plan 读取 URL 和按 finalize 精确路径导出。
- 明确浏览器／网络诊断的证据边界、补采真实性，以及多文件写入失败后的核对要求；来源清单仍为 17 个采集入口，其中 X/Twitter 9 个。

### 升级兼容

- 旧日状态无需手工删分组或清空数据；后续成功 finalize 会持久保存兼容后的来源元数据。
- 升级前生成的 incremental 若混合了当前与历史分组，使用原 current 重新 prepare，重建翻译计划并复用已有译文；原有过期工件和状态冲突检查继续生效。

### 验证

- 增加旧／新状态删源后连续运行、五轮记录保留、改名／排序／重启用、跨日去重、状态冲突恢复、历史快照回退及翻译批次回归测试。
- 验证：143 项离线 pytest 测试通过（新增 27 项）；技能格式校验通过。未执行真实采集或修改生产新闻状态。

## v1.2.4（2026-10-18）— RSS 单层重试与来源调整

### 变更

- 移除 Serenity（`aleabitoreddit`）和 Aelia Capitolina（`kaptonia`）两个 X 来源及其备用命令；默认配置现有 17 个采集入口，其中 X/Twitter 9 个。
- 三个 RSS 来源单独设置 25 秒命令超时，保留其他来源的超时配置；配套 clis v0.9.0，每个 RSS adapter 调用只发一次请求。
- 识别 opencli 2.0 的 `ok/data/error` 包装与版本化 RSS 错误元数据；仅临时失败最多补试一次，等待 5–7 秒并遵守服务器冷却要求。
- 超出 30 秒等待预算则跳过重试并继续后续来源；人工验证、权限、404、格式和空结果不做无效重试。
- Reuters 列表由其 adapter 自己恢复，关闭 NewsFlow 的重复外层重试；保留 X 的备用采集路径。
- 增加等待/尝试诊断；三个 RSS adapter 的实现位于独立 clis 仓库，未包含在此 skill payload 中。

### 修复

- `ok: false` 即使命令退出码为 0 也判为失败，避免错误包装被当作成功数据。
- 显式权限、格式和缺失页面错误优先于文本中的超时关键词，避免误重试；可执行文件缺失作为来源失败处理，不中断后续来源。
- 长冷却不会被 fallback 立即绕过；记录服务器要求的 UTC 截止时间，便于后续处理。独立新运行不继承冷却状态。
- 同步 README、SKILL 与测试中的来源数量和版本说明，移除旧版候选状态描述。

### 验证

- 发布前 116 项 pytest 测试全部通过。
- 离线回归覆盖错误包装、分类、次数和退避、服务器冷却、超时覆盖、fallback、来源清单与安装 payload 合同。
- 2026-10-18 使用真实 NewsFlow pipeline 在隔离目录验证三个 RSS 来源：Bloomberg tech 7 条、TechCrunch 10 条、Ars 10 条，共 27 条；每个来源仅调用一次，错误为 0。
- 实测仅执行采集阶段；翻译、finalize 和真实 DailyNews 导出不属于本轮联网验证。

## v1.2.3（2026-08-31）— Markdown 隐藏已恢复采集告警

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

## v1.1.2（2026-07-30）— 分离 X 采集入口与内容作者

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
