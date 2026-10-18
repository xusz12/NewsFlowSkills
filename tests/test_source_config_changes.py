"""Offline multi-run regressions for source changes and legacy daily state."""

import argparse
import contextlib
import copy
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_news_workflow import EXPORT_SCRIPT, load_incremental_module, read_json, write_json


def metadata(section, *, display=None, source_type="twitter"):
    return {
        "display_name": display or f"显示名 {section}",
        "source_name": f"来源 {section}",
        "source_type": source_type,
        "source_handle": section,
        "translation_policy": "auto",
    }


def story(section, number, **fields):
    return {
        "section": section,
        "title": f"中文新闻 {number}",
        "url": f"https://x.com/{section}/status/{number}?s=20",
        "time": "2026-10-18 10:00:00",
        "source_type": "twitter",
        "source_name": f"来源 {section}",
        "source_handle": section,
        "translation_policy": "auto",
        **fields,
    }


class Workflow:
    def __init__(self, root):
        self.root = root
        self.state_dir = root / ".news_state"
        self.out_dir = root / "out"
        self.module = load_incremental_module()
        self.counter = 0

    def state_path(self, day="2026-10-18"):
        return self.state_dir / f"{day}.json"

    def invoke(self, handler, **kwargs):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert handler(argparse.Namespace(**{k: str(v) for k, v in kwargs.items()})) == 0
        return json.loads(output.getvalue())

    def current(self, sections, items=(), *, day="2026-10-18", meta=None, errors=()):
        self.counter += 1
        run_id = f"run-{self.counter}"
        run_dir = self.state_dir / "runs" / run_id
        now = f"{day} 12:{self.counter:02d}:00"
        write_json(run_dir / "current.json", {
            "run_id": run_id,
            "started_at": now,
            "finished_at": now,
            "generated_at": now,
            "timezone": "Asia/Shanghai",
            "section_order": sections,
            "section_metadata": meta if meta is not None else {s: metadata(s) for s in sections},
            "deduped_items": list(items),
            "errors": list(errors),
        })
        return run_dir

    def prepare(self, run_dir):
        return self.invoke(
            self.module.prepare_incremental,
            current_json=run_dir / "current.json", state_dir=self.state_dir,
            out_json=run_dir / "incremental.json",
        )

    def finalize(self, run_dir, translations=None):
        write_json(run_dir / "translated.json", translations or {})
        return self.invoke(
            self.module.finalize_incremental,
            incremental_json=run_dir / "incremental.json",
            translated_json=run_dir / "translated.json", state_dir=self.state_dir,
            out_dir=self.out_dir,
        )

    def run(self, sections, items=(), **kwargs):
        translations = kwargs.pop("translations", None)
        run_dir = self.current(sections, items, **kwargs)
        self.prepare(run_dir)
        return self.finalize(run_dir, translations)


@pytest.fixture
def flow(tmp_path):
    return Workflow(tmp_path)


@pytest.mark.parametrize("legacy_state", [False, True])
def test_removal_preserves_news_dedupe_and_five_prior_runs(flow, legacy_state):
    old_sections = ["keep", "aleabitoreddit", "Areskapitalon"]
    quote = story("aleabitoreddit", 1, quoted_text="Привет мир")
    summary = story("Areskapitalon", 2, source_type="bloomberg", summary="Original summary")
    flow.run(old_sections, [quote, summary], translations={
        quote["url"]: {"quoted_text_zh": "你好，世界"},
        summary["url"]: {"summary_zh": "中文摘要"},
    }, errors=[{"section": "keep", "error": "历史采集错误", "command_str": "test"}])
    for _ in range(4):
        flow.run(old_sections)
    old = read_json(flow.state_path())
    if legacy_state:
        old.pop("section_metadata")
        old.pop("active_section_order")
    old["scheduler_note"] = {"slot": "morning"}
    old["today_first_seen_items"][0]["custom_provenance"] = {"id": 123}
    old["daily_errors"][0]["trace_id"] = "keep-this-diagnostic"
    old["runs"][0]["run_sidecar_path"] = "legacy-sidecar.json"
    write_json(flow.state_path(), old)
    # The removed source's URL reappears via a different collection account.
    duplicate = {**quote, "section": "keep", "source_handle": "keep"}
    new = story("keep", 3)
    rd = flow.current(["keep"], [duplicate, new])
    before_prepare = flow.state_path().read_bytes()
    prepared = flow.prepare(rd)
    assert flow.state_path().read_bytes() == before_prepare
    assert [i["url"] for i in prepared["run_fresh_items_raw"]] == [new["url"]]
    assert prepared["active_section_order"] == ["keep"]
    assert set(prepared["section_metadata"]) == {"keep"}
    flow.finalize(rd)
    # A second run must also pass: historic items must never reintroduce the error.
    result = flow.run(["keep"])
    state = read_json(flow.state_path())
    assert state["runs"][:5] == old["runs"]
    assert len(state["runs"]) == 7
    assert state["today_seen_urls"] == old["today_seen_urls"] + [new["url"]]
    assert state["daily_errors"] == old["daily_errors"]
    assert state["scheduler_note"] == old["scheduler_note"]
    for prior, retained in zip(old["today_first_seen_items"], state["today_first_seen_items"]):
        assert prior.items() <= retained.items()
    assert len(state["today_first_seen_items"]) == 3
    assert state["section_metadata"]["aleabitoreddit"]["display_name"] == "显示名 aleabitoreddit"
    daily = (flow.out_dir / "dailyFreshNews_2026-10-18.md").read_text()
    fresh = (flow.root / result["run_fresh_path"]).read_text()
    assert "显示名 aleabitoreddit" in daily
    assert "显示名 Areskapitalon" in daily
    assert "aleabitoreddit" not in fresh and "Areskapitalon" not in fresh
    sidecar = read_json(flow.out_dir / "dailyFreshNews_2026-10-18.newsreader.json")
    by_url = {i["url"]: i for i in sidecar["items"]}
    assert by_url[quote["url"]]["quoted_text_zh"] == "你好，世界"
    assert by_url[summary["url"]]["summary_raw"] == "Original summary"
    assert by_url[summary["url"]]["summary"] == "中文摘要"


@pytest.mark.parametrize("legacy_state", [False, True])
def test_removing_empty_source_does_not_leave_a_no_update_group(flow, legacy_state):
    flow.run(["keep", "empty"])
    if legacy_state:
        state = read_json(flow.state_path())
        state.pop("section_metadata")
        write_json(flow.state_path(), state)
    for _ in range(2):
        result = flow.run(["keep"])
        assert read_json(flow.state_path())["section_order"] == ["keep"]
        assert "显示名 empty" not in (flow.root / result["daily_fresh_path"]).read_text()


def test_add_rename_reorder_disable_and_reenable_preserve_first_seen_identity(flow):
    first = story("old", 1)
    flow.run(["old", "keep"], [first])
    old = read_json(flow.state_path())
    new = story("added", 2)
    same_url = {**first, "section": "renamed"}
    result = flow.run(["added", "keep", "renamed"], [same_url, new])
    state = read_json(flow.state_path())
    assert state["section_order"] == ["added", "keep", "renamed", "old"]
    assert state["today_first_seen_items"][0]["section"] == "old"
    assert state["runs"][:1] == old["runs"]
    assert result["stats"]["run_fresh_count"] == 1
    result = flow.run(["renamed", "keep", "added"])
    assert result["section_order"] == ["renamed", "keep", "added", "old"]
    flow.run([])  # All collection sources temporarily disabled.
    assert read_json(flow.state_path())["section_order"] == ["added", "old"]
    result = flow.run(["old", "keep"], [first])
    assert result["stats"]["run_fresh_count"] == 0
    assert len(read_json(flow.state_path())["today_seen_urls"]) == 2


@pytest.mark.parametrize("artifact_mode", [
    "current", "incremental", "missing", "corrupt", "wrong_run", "outside_runs", "invalid_path",
])
def test_legacy_metadata_recovery_is_best_effort_and_identity_checked(flow, artifact_mode):
    item = story("retired", 1)
    flow.run(["retired"], [item], meta={"retired": metadata("retired", display="原始自定义显示名")})
    state = read_json(flow.state_path())
    state.pop("section_metadata")
    record = state["runs"][0]
    current = flow.state_dir / "runs" / "run-1" / "current.json"
    incremental = current.with_name("incremental.json")
    if artifact_mode == "incremental":
        current.unlink()
    elif artifact_mode != "current":
        incremental.unlink()
        if artifact_mode == "missing":
            current.unlink()
        elif artifact_mode == "corrupt":
            current.write_text("{broken")
        elif artifact_mode == "wrong_run":
            snapshot = read_json(current)
            snapshot["run_id"] = "unrelated-run"
            write_json(current, snapshot)
        elif artifact_mode == "outside_runs":
            external = flow.root / "outside.json"
            external.write_bytes(current.read_bytes())
            record["current_json_path"] = str(external)
            current.unlink()
        elif artifact_mode == "invalid_path":
            record["current_json_path"] = "invalid\x00path"
            current.unlink()
    write_json(flow.state_path(), state)
    flow.run(["keep"])
    retained = read_json(flow.state_path())
    expected = "原始自定义显示名" if artifact_mode in {"current", "incremental"} else "来源 retired"
    assert retained["section_metadata"]["retired"]["display_name"] == expected
    assert retained["section_metadata"]["retired"]["source_handle"] == "retired"
    assert retained["today_seen_urls"] == [item["url"]]
    assert retained["runs"][:1] == state["runs"]


def test_metadata_is_frozen_at_prepare_and_keeps_retired_display_names(flow):
    flow.run(["retired"], [story("retired", 1)], meta={"retired": metadata("retired", display="旧显示名")})
    rd = flow.current(["keep"], meta={"keep": metadata("keep", display="本轮显示名")})
    flow.prepare(rd)
    # Neither later config changes nor artifact pruning may alter this snapshot.
    for path in (flow.state_dir / "runs").glob("*/current.json"):
        path.unlink()
    flow.finalize(rd)
    state = read_json(flow.state_path())
    assert state["section_metadata"]["retired"]["display_name"] == "旧显示名"
    assert state["section_metadata"]["keep"]["display_name"] == "本轮显示名"


def test_missing_active_metadata_still_fails_before_any_write(flow):
    flow.run(["keep"], [story("keep", 1)])
    before = flow.state_path().read_bytes()
    rd = flow.current(["keep", "new"], meta={"keep": metadata("keep")})
    with pytest.raises(flow.module.IncrementalNewsError, match="PREPARE_BAD_CURRENT_JSON.*new"):
        flow.prepare(rd)
    assert flow.state_path().read_bytes() == before
    rd = flow.current(["keep"])
    flow.prepare(rd)
    inc = read_json(rd / "incremental.json")
    inc["section_metadata"] = {}  # Daily/history metadata must not mask this.
    write_json(rd / "incremental.json", inc)
    with pytest.raises(flow.module.IncrementalNewsError, match="FINALIZE_BAD_INCREMENTAL_METADATA.*keep"):
        flow.finalize(rd)
    assert flow.state_path().read_bytes() == before


def test_old_mixed_incremental_can_be_reprepared_without_changing_current_or_state(flow):
    flow.run(["keep", "retired"], [story("retired", 1)])
    rd = flow.current(["keep"])
    flow.prepare(rd)
    inc = read_json(rd / "incremental.json")
    inc.pop("active_section_order")
    inc.pop("daily_section_metadata")
    write_json(rd / "incremental.json", inc)
    current_before = (rd / "current.json").read_bytes()
    state_before = flow.state_path().read_bytes()
    flow.prepare(rd)
    assert (rd / "current.json").read_bytes() == current_before
    assert flow.state_path().read_bytes() == state_before
    flow.finalize(rd)
    flow.run(["keep"])


def test_next_day_uses_new_config_and_preserves_yesterday_dedupe(flow):
    item = story("retired", 1)
    flow.run(["keep", "retired"], [item])
    yesterday = flow.state_path().read_bytes()
    result = flow.run(["keep"], [{**item, "section": "keep"}], day="2026-10-19")
    assert result["stats"]["run_fresh_count"] == 0
    assert result["stats"]["daily_fresh_count"] == 0
    assert result["section_order"] == ["keep"]
    assert flow.state_path().read_bytes() == yesterday
    state = read_json(flow.state_path("2026-10-19"))
    assert state["today_seen_urls"] == [item["url"]]


def test_state_conflict_reprepare_keeps_both_runs_and_removed_source(flow):
    flow.run(["retired"], [story("retired", 1)])
    earlier = flow.current(["other"], [story("other", 2)])
    later = flow.current(["keep"], [story("keep", 3)])
    flow.prepare(earlier)
    flow.prepare(later)
    flow.finalize(earlier)
    before = copy.deepcopy(read_json(flow.state_path()))
    with pytest.raises(flow.module.IncrementalNewsError, match="FINALIZE_STATE_CHANGED_SINCE_PREPARE"):
        flow.finalize(later)
    flow.prepare(later)
    flow.finalize(later)
    state = read_json(flow.state_path())
    assert state["runs"][:2] == before["runs"]
    assert len(state["today_seen_urls"]) == len(state["today_first_seen_items"]) == 3
    assert state["section_order"] == ["keep", "other", "retired"]


@pytest.mark.parametrize("field,value", [
    ("active_section_order", {}),
    ("section_metadata", None),
    ("daily_section_metadata", None),
    ("daily_section_metadata", {}),
])
def test_invalid_prepared_source_snapshots_do_not_modify_outputs(flow, field, value):
    flow.run(["retired"], [story("retired", 1)])
    rd = flow.current(["keep"])
    flow.prepare(rd)
    inc = read_json(rd / "incremental.json")
    inc[field] = value
    write_json(rd / "incremental.json", inc)
    before = {p: p.read_bytes() for p in flow.out_dir.iterdir()}
    state_before = flow.state_path().read_bytes()
    with pytest.raises(flow.module.IncrementalNewsError, match="FINALIZE_BAD_INCREMENTAL_METADATA"):
        flow.finalize(rd)
    assert flow.state_path().read_bytes() == state_before
    assert {p: p.read_bytes() for p in flow.out_dir.iterdir()} == before


def test_three_translation_batches_preserve_exact_urls_and_required_fields(flow):
    items = [
        story("x", 1, title="https://t.co/abc"),
        story("x", 2, title="RT @user: https://t.co/def"),
        story("x", 3, title="Привет " * 950),
        story("x", 4, quoted_text="Привет " * 950),
        story("bb", 5, source_type="bloomberg", summary="Привет " * 950),
    ]
    rd = flow.current(["x", "bb"], items)
    flow.prepare(rd)
    plan_path = rd / "translation-plan.json"
    translated_path = rd / "translated.json"
    plan = flow.invoke(flow.module.plan_translations,
        incremental_json=rd / "incremental.json", translated_json=translated_path,
        out_json=plan_path, phase="initial")
    assert len(plan["batches"]) == 3
    fields = {i["url"]: i["required_fields"] for b in plan["batches"] for i in b["items"]}
    assert fields[items[3]["url"]] == ["quoted_text_zh"]
    assert fields[items[4]["url"]] == ["summary"]
    assert all(fields[i["url"]] == ["title"] for i in items[:3])
    merged_urls = set()
    for batch in plan["batches"]:
        batch_path = rd / f"translation-initial-{batch['batch_id']}.json"
        payload = {
            i["url"]: {field: "中文译文" for field in i["required_fields"]}
            for i in batch["items"]
        }
        if items[0]["url"] in payload:
            payload[items[0]["url"]]["title"] = "（链接）https://t.co/abc"
            payload[items[1]["url"]]["title"] = "转发 @user：（链接）https://t.co/def"
        write_json(batch_path, payload)
        flow.invoke(flow.module.merge_translation_batch, plan_json=plan_path,
            batch_id=batch["batch_id"], batch_json=batch_path, translated_json=translated_path)
        merged_urls.update(batch["expected_urls"])
        assert set(read_json(translated_path)) == merged_urls
    translated = read_json(translated_path)
    assert "title" not in translated[items[3]["url"]]
    assert "title" not in translated[items[4]["url"]]
    validation = flow.invoke(flow.module.validate_translations,
        incremental_json=rd / "incremental.json", translated_json=translated_path)
    assert validation["ok"] is True
    result = flow.finalize(rd, translated)
    assert result["stats"]["run_fresh_count"] == 5
    daily = Path(result["daily_fresh_path"]).read_text()
    assert "（链接）https://t.co/abc" in daily
    assert "转发 @user：（链接）https://t.co/def" in daily


@pytest.mark.parametrize("source", ["https://t.co/abc", "RT @user: https://t.co/abc", "Привет мир"])
def test_untranslated_links_and_non_english_prose_fail_chinese_validation(flow, source):
    item = story("x", 1, title=source)
    rd = flow.current(["x"], [item])
    flow.prepare(rd)
    write_json(rd / "translated.json", {item["url"]: {"title": source}})
    validation = flow.invoke(flow.module.validate_translations,
        incremental_json=rd / "incremental.json", translated_json=rd / "translated.json")
    assert validation["ok"] is False
    assert any(i["reason"] == "non_chinese_translation" for i in validation["issues"])


def test_previously_cleaned_section_order_cannot_hide_historic_news(flow):
    flow.run(["retired", "keep"], [story("retired", 1)])
    state = read_json(flow.state_path())
    state["section_order"] = ["keep"]
    state.pop("section_metadata")
    write_json(flow.state_path(), state)
    for _ in range(2):
        flow.run(["keep"])
        retained = read_json(flow.state_path())
        assert retained["section_order"] == ["keep", "retired"]
        assert len(retained["today_first_seen_items"]) == 1


def test_export_uses_exact_finalize_paths_among_multiple_runs(flow):
    first = flow.run(["retired"], [story("retired", 1)])
    result = flow.run(["keep"], [story("keep", 2)])
    export_root = flow.root / "export with spaces"
    export_root.mkdir()
    process = subprocess.run([
        sys.executable, "-B", str(EXPORT_SCRIPT),
        "--daily", result["daily_fresh_path"], "--fresh", result["run_fresh_path"],
        "--target-root", str(export_root),
    ], capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    month = export_root / "2026年10月"
    expected = [Path(result[k]) for k in ("daily_fresh_path", "run_fresh_path", "daily_sidecar_path")]
    assert {p.name for p in month.iterdir()} == {p.name for p in expected}
    assert not (month / Path(first["run_fresh_path"]).name).exists()
    for source in expected:
        assert (month / source.name).read_bytes() == source.read_bytes()
