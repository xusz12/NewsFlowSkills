from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "newsflow"


def test_repository_has_one_nested_skill_payload() -> None:
    skill_files = sorted(
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("SKILL.md")
        if ".git" not in path.parts
    )
    assert skill_files == ["skills/newsflow/SKILL.md"]


def test_payload_contains_only_runtime_files() -> None:
    expected_files = {
        "SKILL.md",
        "agents/openai.yaml",
        "references/commands.json",
        "scripts/export_outputs.py",
        "scripts/newsflow_common.py",
        "scripts/run_incremental_news.py",
        "scripts/run_news_pipeline.py",
    }
    actual_files = {
        path.relative_to(SKILL_ROOT).as_posix()
        for path in SKILL_ROOT.rglob("*")
        if path.is_file()
    }
    assert actual_files == expected_files
    payload_names = {path.name for path in SKILL_ROOT.rglob("*")}
    assert "adapters" not in payload_names
    assert "__pycache__" not in payload_names
    assert not [path for path in SKILL_ROOT.rglob("*") if path.suffix == ".pyc"]


def test_main_skill_is_runtime_neutral_and_complete() -> None:
    content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    for runtime_name in ("Codex", "Claude", "Kimi", "Pi"):
        assert re.search(rf"\b{runtime_name}\b", content) is None
    for forbidden in (
        "adapters/",
        "/Users/x/.codex/skills/newsflow",
        "/Users/x/.claude/skills/newsflow",
        "/Users/x/.skills/newsflow",
    ):
        assert forbidden not in content
    for required in (
        "absolute directory containing this loaded `SKILL.md`",
        "command or shell execution capability",
        "Do not rely on environment variables or shell state surviving between calls.",
        "Prepare recovery policy:",
        "plan-translations",
        "merge-translation-batch",
        "section_metadata",
        "batch_source_char_limit",
        "Validation workflow:",
        "one permitted repair plan",
        "Finalize recovery policy:",
        "Optional export step (post-finalize):",
        "Translation diagnostics remain in the hidden validation state",
    ):
        assert required in content


def test_openai_metadata_is_optional_and_valid() -> None:
    content = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
    short_description = "Aggregate news with freshness tracking"
    assert 25 <= len(short_description) <= 64
    assert content.splitlines() == [
        "interface:",
        '  display_name: "Newsflow"',
        f'  short_description: "{short_description}"',
        '  default_prompt: "Use $newsflow to aggregate configured news sources and produce fresh Chinese digests."',
    ]


def test_readme_documents_project_lifecycle() -> None:
    content = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "[CHANGELOG.md](CHANGELOG.md)" in content
    assert " -g" not in content
    for heading in (
        "## 什么是 newsflow",
        "## 适用场景与边界",
        "## 输入与产物",
        "## 工作流概览",
        "## 恢复与安全语义",
        "## 快速开始",
        "## 仓库结构",
    ):
        assert heading in content
    for workflow_stage in (
        "**读取配置并顺序采集**",
        "**全局去重并生成 current**",
        "**Prepare 日内增量**",
        "**Initial translation plan、capacity batches 与 exact merge**",
        "**Validate 与一次 repair**",
        "**Finalize 原子落盘**",
        "**可选 export**",
    ):
        assert workflow_stage in content
    assert "不负责后续的简报提炼、重点排序或观点总结" in content
    assert "每个 source command 的输出都会进入同一条规范化 pipeline" in content
    assert "state drift" in content
    assert "export 失败不会回滚" in content
    assert "让 agent 使用 `$newsflow`" in content
    assert "当前仍是本地候选版本" in content
    assert "dailyFreshNews_YYYY-MM-DD.newsreader.json" in content
    assert "YYYY-MM-DD-HH-mm-ss-<sha256前12位>_freshNews.newsreader.json" not in content
    assert "per-run freshNews 不生成 sidecar" in content
    assert "--target-root" in content
    assert "NEWSFLOW_EXPORT_ROOT" in content
    for agent in ("codex", "claude-code", "kimi-code-cli", "pi"):
        assert (
            "npx -y skills add xusz12/NewsFlowSkills "
            f"-a {agent} -y"
        ) in content
    assert "仓库当前只有一个 skill" in content
    assert "主分支只接收已经 Review 的稳定内容" in content
    assert "必须恢复 `-s newsflow`" in content
    assert (
        "npx -y skills@1.5.20 add xusz12/NewsFlowSkills#v1.1.0 "
        "-s newsflow -a codex --copy -y"
    ) in content
    assert "npx -y skills add /Users/x/.skills/newsflow -a codex -y" in content
    assert "npx -y skills remove newsflow -y" in content
    assert "remove newsflow -a" not in content
    assert "remove newsflow --agent" not in content
    assert "`.agents/skills/newsflow`" in content
    assert "`.claude/skills/newsflow`" in content
    assert "`.pi/skills/newsflow`" in content


def test_legacy_sync_entrypoint_is_removed() -> None:
    assert not (ROOT / "tools" / "sync_install.sh").exists()
