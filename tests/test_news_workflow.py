#!/usr/bin/env python3
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = Path(
    os.environ.get("NEWSFLOW_SKILL_ROOT", REPOSITORY_ROOT / "skills" / "newsflow")
).resolve()
PIPELINE_SCRIPT = SKILL_ROOT / "scripts" / "run_news_pipeline.py"
INCREMENTAL_SCRIPT = SKILL_ROOT / "scripts" / "run_incremental_news.py"
EXPORT_SCRIPT = SKILL_ROOT / "scripts" / "export_outputs.py"
TIMEZONE = "Asia/Shanghai"


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_incremental_module() -> object:
    spec = importlib.util.spec_from_file_location("run_incremental_news", INCREMENTAL_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_pipeline_module() -> object:
    spec = importlib.util.spec_from_file_location("run_news_pipeline", PIPELINE_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_state_payload(*, runs: list[dict], today_seen_urls: list[str] | None = None) -> dict:
    return {
        "date": "2026-04-09",
        "timezone": TIMEZONE,
        "section_order": [],
        "today_seen_urls": today_seen_urls or [],
        "today_first_seen_items": [],
        "daily_errors": [],
        "runs": runs,
    }


class NewsWorkflowSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.state_dir = self.root / ".news_state"
        self.runs_root = self.state_dir / "runs"
        self.out_dir = self.root / "out"
        self.today_state_path = self.state_dir / "2026-04-09.json"

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def run_cmd(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *args],
            capture_output=True,
            text=True,
        )

    def make_run_dir(self, name: str) -> Path:
        run_dir = self.runs_root / name
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def expected_run_stem(
        self, run_id: str, generated_at: str = "2026-04-09 12:05:00"
    ) -> str:
        module = load_incremental_module()
        parsed = module.parse_timestamp(generated_at, field_name="generated_at")
        return module.make_run_output_stem(parsed, run_id)

    def run_markdown_path(self, run_id: str) -> Path:
        return self.out_dir / f"{self.expected_run_stem(run_id)}_freshNews.md"

    def make_current_payload(
        self,
        *,
        run_id: str,
        started_at: str,
        finished_at: str,
        generated_at: str | None = None,
        deduped_items: list[dict] | None = None,
        errors: list[dict] | None = None,
    ) -> dict:
        return {
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "generated_at": generated_at or finished_at,
            "timezone": TIMEZONE,
            "section_order": [],
            "grouped_items": {},
            "deduped_items": deduped_items or [],
            "errors": errors or [],
            "recovered_attempts": [],
            "stats": {
                "command_count": 1,
                "section_count": 0,
                "collected_before_dedup": len(deduped_items or []),
                "after_dedup": len(deduped_items or []),
                "error_count": len(errors or []),
                "recovered_count": 0,
            },
        }

    def make_incremental_payload(
        self,
        *,
        run_dir: Path,
        run_id: str,
        started_at: str,
        finished_at: str,
        latest_generated_at: str = "",
        latest_run_id: str = "",
        run_fresh_items: list[dict] | None = None,
    ) -> dict:
        incremental_path = run_dir / "incremental.json"
        return {
            "date": "2026-04-09",
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "generated_at": finished_at,
            "timezone": TIMEZONE,
            "section_order": [],
            "run_file_timestamp": "2026-04-09-12-05",
            "current_run_items_raw": run_fresh_items or [],
            "current_run_first_seen_items_raw": run_fresh_items or [],
            "run_fresh_items_raw": run_fresh_items or [],
            "current_run_errors": [],
            "daily_errors": [],
            "items_to_translate": run_fresh_items or [],
            "paths": {
                "run_artifact_dir": str(run_dir),
                "current_json_path": str(run_dir / "current.json"),
                "incremental_json_path": str(incremental_path),
            },
            "state_snapshot": {
                "latest_finalized_run_id": latest_run_id,
                "latest_finalized_generated_at": latest_generated_at,
                "prepared_at": "2026-04-09 12:04:59",
            },
            "state": {
                "today_state_path": str(self.today_state_path),
                "yesterday_state_path": str(self.state_dir / "2026-04-08.json"),
            },
            "stats": {
                "current_run_count": len(run_fresh_items or []),
                "current_run_first_seen_count": len(run_fresh_items or []),
                "run_fresh_count": len(run_fresh_items or []),
                "daily_fresh_count": len(run_fresh_items or []),
                "current_error_count": 0,
                "daily_error_count": 0,
            },
        }

    def test_pipeline_writes_run_identity(self) -> None:
        run_dir = self.make_run_dir("manual-run")
        current_json = run_dir / "current.json"
        config_path = self.root / "commands.json"
        write_json(
            config_path,
            [
                {
                    "section": "world",
                    "command": [
                        sys.executable,
                        "-c",
                        (
                            "import json; "
                            "print(json.dumps([{'title':'Hello','url':'https://example.com/a',"
                            "'time':'2026-04-09 12:00:00'}]))"
                        ),
                    ],
                }
            ],
        )

        result = self.run_cmd(
            str(PIPELINE_SCRIPT),
            "--config",
            str(config_path),
            "--out-json",
            str(current_json),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = read_json(current_json)
        self.assertIn("run_id", payload)
        self.assertIn("started_at", payload)
        self.assertIn("finished_at", payload)
        self.assertEqual(payload["generated_at"], payload["finished_at"])
        self.assertEqual(
            Path(payload["current_json_path"]).resolve(),
            current_json.resolve(),
        )

    def test_isolated_shortest_complete_workflow(self) -> None:
        run_dir = self.make_run_dir("shortest-chain")
        current_json = run_dir / "current.json"
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        plan_json = run_dir / "translation-plan.json"
        batch_json = run_dir / "translation-initial-batch-001.json"
        config_path = self.root / "commands.json"
        url = "https://example.com/shortest-chain"
        write_json(
            config_path,
            [{
                "section": "custom",
                "display_name": "Custom Source",
                "source_type": "site",
                "source_name": "Custom",
                "translation_policy": "always",
                "command": [
                    sys.executable,
                    "-c",
                    (
                        "import json; print(json.dumps(["
                        f"{{'title':'English title','url':'{url}',"
                        "'time':'2026-04-09 12:00:00'}]))"
                    ),
                ],
            }],
        )
        pipeline = self.run_cmd(
            str(PIPELINE_SCRIPT), "--config", str(config_path),
            "--out-json", str(current_json),
        )
        self.assertEqual(pipeline.returncode, 0, pipeline.stderr)
        prepare = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "prepare", "--current-json", str(current_json),
            "--state-dir", str(self.state_dir), "--out-json", str(incremental_json),
        )
        self.assertEqual(prepare.returncode, 0, prepare.stderr)
        self.assertEqual(
            read_json(incremental_json)["section_metadata"]["custom"]["display_name"],
            "Custom Source",
        )
        plan = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "plan-translations",
            "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json),
            "--out-json", str(plan_json), "--phase", "initial",
        )
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertEqual(len(read_json(plan_json)["batches"]), 1)
        write_json(batch_json, {url: {"title": "中文标题"}})
        merge = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "merge-translation-batch",
            "--plan-json", str(plan_json), "--batch-id", "batch-001",
            "--batch-json", str(batch_json), "--translated-json", str(translated_json),
        )
        self.assertEqual(merge.returncode, 0, merge.stderr)
        validate = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "validate-translations",
            "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json),
        )
        self.assertEqual(validate.returncode, 0, validate.stderr)
        self.assertTrue(json.loads(validate.stdout)["ok"])
        finalize = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "finalize",
            "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json),
            "--state-dir", str(self.state_dir), "--out-dir", str(self.out_dir),
        )
        self.assertEqual(finalize.returncode, 0, finalize.stderr)
        markdown = Path(json.loads(finalize.stdout)["run_fresh_path"]).read_text(encoding="utf-8")
        self.assertIn("## Custom Source（1条）", markdown)
        self.assertIn("### [中文标题]", markdown)

    def test_default_commands_use_opencli_twitter_with_native_fallback(self) -> None:
        config_path = SKILL_ROOT / "references" / "commands.json"
        entries = read_json(config_path)
        assert isinstance(entries, list)

        twitter_entries = [
            entry
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("command", [None, None])[:2] == ["opencli", "twitter"]
        ]
        self.assertTrue(twitter_entries, "expected twitter sources in default commands.json")
        self.assertEqual(len(entries), 19)
        self.assertEqual(len(twitter_entries), 11)
        handles = [entry["command"][3] for entry in twitter_entries]
        self.assertEqual(len(handles), len(set(handles)), "twitter handles must be unique")
        self.assertEqual(
            handles,
            [
                "ilyasut",
                "mingchikuo",
                "ivanalog_com",
                "fxtrader",
                "Time_HorizonX",
                "WaylandZhang",
                "aleabitoreddit",
                "LinQingV",
                "Areskapitalon",
                "ChinaMacroFacts",
                "MacroMargin",
            ],
        )
        expected_names = {
            "ilyasut": "Ilya Sutskever",
            "mingchikuo": "郭明錤",
            "ivanalog_com": "seekinganythingbutalpha",
            "fxtrader": "外汇交易员",
            "Time_HorizonX": "Time Horizon",
            "WaylandZhang": "WaylandZhang",
            "aleabitoreddit": "Serenity",
            "LinQingV": "Macro_Lin",
            "Areskapitalon": "Aelia Capitolina",
            "ChinaMacroFacts": "中国政经事实ChinaFacts",
            "MacroMargin": "宏观边际MacroMargin",
        }
        removed_handles = {"jakevin7", "cyrilxuq", "HuXijin_GT"}
        self.assertTrue(removed_handles.isdisjoint(handles))
        self.assertTrue(
            all(
                removed_handle not in json.dumps(entries, ensure_ascii=False)
                for removed_handle in removed_handles
            )
        )
        wayland = next(entry for entry in twitter_entries if entry["source_handle"] == "WaylandZhang")
        self.assertEqual(
            {key: wayland[key] for key in ("section", "display_name", "source_name")},
            {
                "section": "WaylandZhang",
                "display_name": "WaylandZhang",
                "source_name": "WaylandZhang",
            },
        )
        for entry in twitter_entries:
            handle = entry["command"][3]
            self.assertEqual(entry["command"][2], "tweets")
            self.assertEqual(entry["command"][4:6], ["--limit", "10"])
            self.assertEqual(entry["translation_policy"], "auto")
            self.assertEqual(entry["source_type"], "twitter")
            self.assertEqual(entry["source_handle"], handle)
            self.assertEqual(entry["source_name"], expected_names[handle])
            self.assertEqual(entry.get("fallback_command", [None])[0], "twitter")
            self.assertEqual(entry["fallback_command"][1], "user-posts")
            self.assertEqual(entry["fallback_command"][2], entry["command"][3])
            self.assertEqual(entry["fallback_command"][3:5], ["-n", "10"])
            self.assertNotIn("retry_once", entry, f"twitter source should not add retry_once: {entry}")

    def test_normalize_row_supports_opencli_twitter_schema(self) -> None:
        module = load_pipeline_module()
        row = {
            "id": "2061869602154717517",
            "author": "mingchikuo",
            "name": "郭明錤｜Ming-Chi Kuo",
            "text": "Main tweet body",
            "created_at": "Tue Jun 02 17:56:35 +0000 2026",
            "url": "https://x.com/mingchikuo/status/2061869602154717517",
            "quoted_tweet": {
                "text": "Quoted tweet body",
            },
        }

        item = module.normalize_row(
            "郭明錤",
            row,
            translation_policy="auto",
            output_timezone="Asia/Shanghai",
            source_type="twitter",
            source_handle="mingchikuo",
            source_name="郭明錤",
        )

        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item["title"], "Main tweet body")
        self.assertEqual(item["quoted_text_raw"], "Quoted tweet body")
        self.assertEqual(item["author_name"], "郭明錤｜Ming-Chi Kuo")
        self.assertEqual(item["author_screen_name"], "mingchikuo")
        self.assertEqual(item["source_type"], "twitter")
        self.assertEqual(item["source_handle"], "mingchikuo")
        self.assertEqual(item["source_name"], "郭明錤")
        self.assertEqual(item["url"], "https://x.com/mingchikuo/status/2061869602154717517?s=20")
        self.assertEqual(item["time"], "2026-06-03 01:56")

    def test_normalize_row_keeps_native_twitter_schema(self) -> None:
        module = load_pipeline_module()
        row = {
            "id": "42",
            "text": "Native tweet body",
            "createdAtLocal": "2026-06-27 09:30",
            "author": {
                "screenName": "ilyasut",
                "name": "Ilya Sutskever",
            },
            "quotedTweet": {
                "text": "Native quoted tweet",
            },
        }

        item = module.normalize_row(
            "Ilya Sutskever",
            row,
            translation_policy="auto",
            output_timezone="Asia/Shanghai",
            source_type="twitter",
            source_handle="ilyasut",
            source_name="Ilya Sutskever",
        )

        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item["quoted_text_raw"], "Native quoted tweet")
        self.assertEqual(item["url"], "https://x.com/ilyasut/status/42?s=20")
        self.assertEqual(item["time"], "2026-06-27 09:30")
        self.assertEqual(item["source_handle"], "ilyasut")

    def test_default_commands_define_translation_policy(self) -> None:
        config_path = SKILL_ROOT / "references" / "commands.json"
        entries = read_json(config_path)
        assert isinstance(entries, list)
        for entry in entries:
            assert isinstance(entry, dict)
            self.assertIn("translation_policy", entry)
            self.assertIn(entry["translation_policy"], {"always", "auto", "never"})
            self.assertIn(entry.get("source_type"), {"reuters", "bloomberg", "site", "twitter"})
            self.assertTrue(str(entry.get("source_name", "")).strip())
            self.assertTrue(str(entry.get("display_name", "")).strip())

        module = load_pipeline_module()
        parsed = module.load_config(config_path)
        self.assertEqual(len(parsed), len(entries))
        self.assertTrue(all(entry["display_name"] for entry in parsed))

    def test_pipeline_snapshots_normalized_section_metadata(self) -> None:
        module = load_pipeline_module()
        entries = [
            {
                "section": "custom",
                "command": ["fake"],
                "fallback_command": None,
                "retry_once": False,
                "treat_empty_as_failure": False,
                "min_valid_items": 1,
                "translation_policy": "always",
                "source_type": "site",
                "source_handle": "",
                "source_name": "Custom Source",
                "display_name": "Custom Display",
            }
        ]
        original = module.execute_command_once
        module.execute_command_once = lambda **kwargs: {
            "ok": True,
            "command": ["fake"],
            "command_str": "fake",
            "error": "",
            "failed_reason": "",
            "items": [],
            "raw_count": 0,
            "valid_count": 0,
        }
        try:
            result = module.run_pipeline(entries, timeout_seconds=1, output_timezone=TIMEZONE)
        finally:
            module.execute_command_once = original
        self.assertEqual(
            result["section_metadata"]["custom"],
            {
                "display_name": "Custom Display",
                "source_type": "site",
                "source_name": "Custom Source",
                "source_handle": "",
                "translation_policy": "always",
            },
        )

    def test_unicode_han_timezone_and_markdown_boundaries(self) -> None:
        incremental = load_incremental_module()
        pipeline = load_pipeline_module()
        self.assertTrue(incremental.contains_han("𠮷"))
        self.assertTrue(incremental.contains_han("日本語"))
        self.assertFalse(incremental.contains_han("かなだけ"))
        self.assertEqual(
            pipeline.normalize_twitter_local_time(
                "2026-06-02T17:56:35+00:00", output_timezone=TIMEZONE
            ),
            "2026-06-03 01:56",
        )
        self.assertEqual(
            pipeline.normalize_twitter_local_time(
                "2026-06-03 01:56", output_timezone=TIMEZONE
            ),
            "2026-06-03 01:56",
        )
        markdown = incremental.build_markdown(
            ["custom"],
            [{
                "section": "custom",
                "title": "A [title] \\",
                "time": "2026-06-03 01:56",
                "url": "https://example.com/a path/(x)",
            }],
            [],
            {"custom": {"display_name": "Custom"}},
        )
        self.assertIn(r"[A \[title\] \\](https://example.com/a%20path/%28x%29)", markdown)

    def test_twitter_config_requires_explicit_collector_identity(self) -> None:
        module = load_pipeline_module()
        config_path = self.root / "commands.json"
        write_json(
            config_path,
            [
                {
                    "section": "missing-metadata",
                    "translation_policy": "auto",
                    "source_type": "twitter",
                    "command": ["opencli", "twitter", "tweets", "example"],
                }
            ],
        )

        with self.assertRaisesRegex(
            ValueError,
            "source_type=twitter requires source_handle and source_name",
        ):
            module.load_config(config_path)

        write_json(
            config_path,
            [
                {
                    "section": "implicit-twitter",
                    "translation_policy": "auto",
                    "command": ["opencli", "twitter", "tweets", "example"],
                }
            ],
        )
        with self.assertRaisesRegex(
            ValueError,
            "Twitter command requires source_type=twitter",
        ):
            module.load_config(config_path)

    def test_all_configured_twitter_sources_infer_from_item_metadata(self) -> None:
        module = load_incremental_module()
        self.assertFalse(hasattr(module, "TWITTER_SECTIONS"))
        entries = read_json(SKILL_ROOT / "references" / "commands.json")
        twitter_entries = [
            entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("source_type") == "twitter"
        ]

        self.assertEqual(len(twitter_entries), 11)
        for index, entry in enumerate(twitter_entries, start=1):
            item = {
                "section": entry["section"],
                "url": f"https://x.com/external_author/status/{index}?s=20",
                "source_type": entry["source_type"],
                "source_handle": entry["source_handle"],
                "source_name": entry["source_name"],
            }
            with self.subTest(handle=entry["source_handle"]):
                self.assertEqual(
                    module.infer_source_metadata(item),
                    ("twitter", entry["source_name"]),
                )

    def test_pipeline_propagates_configured_collector_identity(self) -> None:
        run_dir = self.make_run_dir("collector-identity")
        current_json = run_dir / "current.json"
        config_path = self.root / "commands.json"
        write_json(
            config_path,
            [
                {
                    "section": "WaylandZhang",
                    "translation_policy": "auto",
                    "source_type": "twitter",
                    "source_handle": "WaylandZhang",
                    "source_name": "WaylandZhang",
                    "command": [
                        sys.executable,
                        "-c",
                        (
                            "import json; "
                            "print(json.dumps([{'id':'101','author':'external_author',"
                            "'name':'External Author','text':'Reposted body',"
                            "'created_at':'Tue Jun 02 17:56:35 +0000 2026'}]))"
                        ),
                    ],
                }
            ],
        )

        result = self.run_cmd(
            str(PIPELINE_SCRIPT),
            "--config",
            str(config_path),
            "--out-json",
            str(current_json),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = read_json(current_json)
        item = payload["deduped_items"][0]
        self.assertEqual(
            payload["section_metadata"],
            {
                "WaylandZhang": {
                    "display_name": "WaylandZhang",
                    "source_type": "twitter",
                    "source_name": "WaylandZhang",
                    "source_handle": "WaylandZhang",
                    "translation_policy": "auto",
                }
            },
        )
        self.assertEqual(item["source_type"], "twitter")
        self.assertEqual(item["source_handle"], "WaylandZhang")
        self.assertEqual(item["source_name"], "WaylandZhang")
        self.assertEqual(item["author_screen_name"], "external_author")
        self.assertEqual(
            item["url"],
            "https://x.com/external_author/status/101?s=20",
        )

    def test_pipeline_propagates_translation_policy_into_items(self) -> None:
        run_dir = self.make_run_dir("policy-propagation")
        current_json = run_dir / "current.json"
        config_path = self.root / "commands.json"
        write_json(
            config_path,
            [
                {
                    "section": "world",
                    "translation_policy": "always",
                    "command": [
                        sys.executable,
                        "-c",
                        (
                            "import json; "
                            "print(json.dumps([{'title':'Hello','url':'https://example.com/a',"
                            "'time':'2026-04-09 12:00:00'}]))"
                        ),
                    ],
                }
            ],
        )

        result = self.run_cmd(
            str(PIPELINE_SCRIPT),
            "--config",
            str(config_path),
            "--out-json",
            str(current_json),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = read_json(current_json)
        deduped_items = payload.get("deduped_items", [])
        self.assertEqual(len(deduped_items), 1)
        self.assertEqual(deduped_items[0]["translation_policy"], "always")

    def test_prepare_requires_run_scoped_paths(self) -> None:
        current_json = self.state_dir / "tmp_current.json"
        incremental_json = self.state_dir / "tmp_incremental.json"
        write_json(
            current_json,
            self.make_current_payload(
                run_id="run-flat",
                started_at="2026-04-09 12:00:00",
                finished_at="2026-04-09 12:05:00",
            ),
        )

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "prepare",
            "--current-json",
            str(current_json),
            "--state-dir",
            str(self.state_dir),
            "--out-json",
            str(incremental_json),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("must be stored under", result.stderr)
        self.assertIn("[PREPARE_BAD_ARTIFACT_PATH]", result.stderr)

    def test_prepare_rejects_stale_current_json(self) -> None:
        run_dir = self.make_run_dir("stale-run")
        current_json = run_dir / "current.json"
        incremental_json = run_dir / "incremental.json"
        write_json(
            self.today_state_path,
            make_state_payload(
                runs=[
                    {
                        "run_id": "latest-run",
                        "generated_at": "2026-04-09 12:30:00",
                        "run_fresh_path": "unused",
                        "daily_fresh_path": "unused",
                        "run_fresh_count": 1,
                        "daily_fresh_count": 1,
                        "error_count": 0,
                    }
                ]
            ),
        )
        write_json(
            current_json,
            self.make_current_payload(
                run_id="stale-run",
                started_at="2026-04-09 12:00:00",
                finished_at="2026-04-09 12:05:00",
            ),
        )

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "prepare",
            "--current-json",
            str(current_json),
            "--state-dir",
            str(self.state_dir),
            "--out-json",
            str(incremental_json),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("older than the latest finalized run", result.stderr)
        self.assertIn("[PREPARE_STALE_CURRENT_JSON]", result.stderr)

    def test_prepare_marks_duplicate_run_id_with_error_code(self) -> None:
        run_dir = self.make_run_dir("duplicate-run-id")
        current_json = run_dir / "current.json"
        incremental_json = run_dir / "incremental.json"
        write_json(
            self.today_state_path,
            make_state_payload(
                runs=[
                    {
                        "run_id": "duplicate-run",
                        "generated_at": "2026-04-09 12:00:00",
                        "run_fresh_path": "unused",
                        "daily_fresh_path": "unused",
                        "run_fresh_count": 1,
                        "daily_fresh_count": 1,
                        "error_count": 0,
                    }
                ]
            ),
        )
        write_json(
            current_json,
            self.make_current_payload(
                run_id="duplicate-run",
                started_at="2026-04-09 12:01:00",
                finished_at="2026-04-09 12:05:00",
            ),
        )

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "prepare",
            "--current-json",
            str(current_json),
            "--state-dir",
            str(self.state_dir),
            "--out-json",
            str(incremental_json),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("[PREPARE_RUN_ID_ALREADY_FINALIZED]", result.stderr)

    def test_prepare_allows_distinct_run_id_at_same_generated_at(self) -> None:
        run_dir = self.make_run_dir("duplicate-generated-at")
        current_json = run_dir / "current.json"
        incremental_json = run_dir / "incremental.json"
        write_json(
            self.today_state_path,
            make_state_payload(
                runs=[
                    {
                        "run_id": "same-generated-at",
                        "generated_at": "2026-04-09 12:05:00",
                        "run_fresh_path": "unused",
                        "daily_fresh_path": "unused",
                        "run_fresh_count": 1,
                        "daily_fresh_count": 1,
                        "error_count": 0,
                    },
                    {
                        "run_id": "latest-run",
                        "generated_at": "2026-04-09 12:04:00",
                        "run_fresh_path": "unused",
                        "daily_fresh_path": "unused",
                        "run_fresh_count": 1,
                        "daily_fresh_count": 2,
                        "error_count": 0,
                    },
                ]
            ),
        )
        write_json(
            current_json,
            self.make_current_payload(
                run_id="new-run",
                started_at="2026-04-09 12:01:00",
                finished_at="2026-04-09 12:05:00",
            ),
        )

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "prepare",
            "--current-json",
            str(current_json),
            "--state-dir",
            str(self.state_dir),
            "--out-json",
            str(incremental_json),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        prepared = read_json(incremental_json)
        self.assertEqual(
            prepared["run_output_stem"],
            self.expected_run_stem("new-run"),
        )

    def test_prepare_marks_unreadable_current_json_with_error_code(self) -> None:
        run_dir = self.make_run_dir("missing-current-json")
        current_json = run_dir / "current.json"
        incremental_json = run_dir / "incremental.json"

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "prepare",
            "--current-json",
            str(current_json),
            "--state-dir",
            str(self.state_dir),
            "--out-json",
            str(incremental_json),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("[PREPARE_CURRENT_JSON_UNREADABLE]", result.stderr)

    def test_prepare_marks_bad_current_json_with_error_code(self) -> None:
        run_dir = self.make_run_dir("bad-current-json")
        current_json = run_dir / "current.json"
        incremental_json = run_dir / "incremental.json"
        write_json(current_json, [])

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "prepare",
            "--current-json",
            str(current_json),
            "--state-dir",
            str(self.state_dir),
            "--out-json",
            str(incremental_json),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("[PREPARE_BAD_CURRENT_JSON]", result.stderr)

    def test_prepare_marks_bad_run_metadata_with_error_code(self) -> None:
        run_dir = self.make_run_dir("bad-metadata")
        current_json = run_dir / "current.json"
        incremental_json = run_dir / "incremental.json"
        write_json(
            current_json,
            self.make_current_payload(
                run_id="bad-metadata",
                started_at="2026-04-09 12:00:00",
                finished_at="2026-04-09 12:05:00",
                generated_at="2026-04-09 12:06:00",
            ),
        )

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "prepare",
            "--current-json",
            str(current_json),
            "--state-dir",
            str(self.state_dir),
            "--out-json",
            str(incremental_json),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("[PREPARE_BAD_RUN_METADATA]", result.stderr)

    def test_prepare_marks_bad_state_with_error_code(self) -> None:
        run_dir = self.make_run_dir("bad-state")
        current_json = run_dir / "current.json"
        incremental_json = run_dir / "incremental.json"
        self.today_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.today_state_path.write_text("{bad json", encoding="utf-8")
        write_json(
            current_json,
            self.make_current_payload(
                run_id="bad-state",
                started_at="2026-04-09 12:00:00",
                finished_at="2026-04-09 12:05:00",
            ),
        )

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "prepare",
            "--current-json",
            str(current_json),
            "--state-dir",
            str(self.state_dir),
            "--out-json",
            str(incremental_json),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("[PREPARE_BAD_STATE]", result.stderr)

    def test_prepare_error_code_recoverability_table(self) -> None:
        module = load_incremental_module()
        recoverable_codes = [
            "PREPARE_STALE_CURRENT_JSON",
            "PREPARE_RUN_ID_ALREADY_FINALIZED",
            "PREPARE_CURRENT_JSON_UNREADABLE",
        ]
        non_recoverable_codes = [
            "PREPARE_BAD_ARTIFACT_PATH",
            "PREPARE_BAD_CURRENT_JSON",
            "PREPARE_BAD_RUN_METADATA",
            "PREPARE_BAD_STATE",
            "PREPARE_WRITE_FAILED",
        ]

        for code in recoverable_codes:
            self.assertTrue(module.is_recoverable_prepare_error_code(code), code)
        for code in non_recoverable_codes:
            self.assertFalse(module.is_recoverable_prepare_error_code(code), code)

    def test_finalize_rejects_existing_run_file(self) -> None:
        run_dir = self.make_run_dir("finalize-run")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        existing_run_file = self.run_markdown_path("finalize-run")
        existing_run_file.write_text("existing\n", encoding="utf-8")
        write_json(self.today_state_path, make_state_payload(runs=[]))
        write_json(
            incremental_json,
            self.make_incremental_payload(
                run_dir=run_dir,
                run_id="finalize-run",
                started_at="2026-04-09 12:00:00",
                finished_at="2026-04-09 12:05:00",
            ),
        )
        write_json(translated_json, {})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "finalize",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
            "--state-dir",
            str(self.state_dir),
            "--out-dir",
            str(self.out_dir),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("Refusing to overwrite existing run file", result.stderr)
        self.assertIn("[FINALIZE_OUTPUT_EXISTS]", result.stderr)
        self.assertEqual(existing_run_file.read_text(encoding="utf-8"), "existing\n")

    def test_finalize_writes_new_daily_filename_and_records_it_in_state(self) -> None:
        run_dir = self.make_run_dir("new-daily-name")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        write_json(self.today_state_path, make_state_payload(runs=[]))
        write_json(
            incremental_json,
            self.make_incremental_payload(
                run_dir=run_dir,
                run_id="new-daily-name",
                started_at="2026-04-09 12:00:00",
                finished_at="2026-04-09 12:05:00",
            ),
        )
        write_json(translated_json, {})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "finalize",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
            "--state-dir",
            str(self.state_dir),
            "--out-dir",
            str(self.out_dir),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        daily_path = self.out_dir / "dailyFreshNews_2026-04-09.md"
        old_daily_path = self.out_dir / "2026-04-09_dailyFreshNews.md"
        run_path = self.run_markdown_path("new-daily-name")
        state_payload = read_json(self.today_state_path)

        self.assertTrue(daily_path.exists())
        self.assertFalse(old_daily_path.exists())
        self.assertTrue(run_path.exists())
        self.assertEqual(Path(payload["daily_fresh_path"]).resolve(), daily_path.resolve())
        self.assertEqual(
            Path(state_payload["runs"][0]["daily_fresh_path"]).resolve(),
            daily_path.resolve(),
        )

    def test_finalize_writes_only_daily_newsreader_sidecar_with_structured_fields(self) -> None:
        run_dir = self.make_run_dir("newsreader-sidecar")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        reuters_item = {
            "section": "world",
            "title": "Original Reuters Title",
            "raw_title": "Original Reuters Title",
            "time": "2026-04-09 12:00:00",
            "url": "https://example.com/reuters-story",
            "translation_policy": "always",
            "source_type": "reuters",
            "source_name": "Reuters",
        }
        bloomberg_item = {
            "section": "bloomberg_main",
            "title": "Original Bloomberg Title",
            "raw_title": "Original Bloomberg Title",
            "time": "2026-04-09 12:01:00",
            "url": "https://www.bloomberg.com/news/articles/example-sidecar",
            "summary": "Original Bloomberg summary",
            "translation_policy": "always",
        }
        twitter_item = {
            "section": "Ilya Sutskever",
            "title": "Original main tweet text with enough detail",
            "raw_title": "Original main tweet text with enough detail",
            "time": "2026-04-09 12:02:00",
            "url": "https://x.com/ilyasut/status/99?s=20",
            "quoted_text_raw": "Original quoted tweet text with enough detail",
            "author_name": "Ilya Sutskever",
            "author_screen_name": "ilyasut",
            "translation_policy": "auto",
        }
        payload = self.make_incremental_payload(
            run_dir=run_dir,
            run_id="newsreader-sidecar",
            started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00",
            run_fresh_items=[reuters_item, bloomberg_item, twitter_item],
        )
        payload["section_order"] = ["world", "bloomberg_main", "Ilya Sutskever"]
        payload["current_run_errors"] = [
            {
                "section": "world",
                "generated_at": "2026-04-09 12:05:00",
                "error": "Recovered degradation example",
            }
        ]
        payload["daily_errors"] = list(payload["current_run_errors"])
        write_json(self.today_state_path, make_state_payload(runs=[]))
        write_json(incremental_json, payload)
        write_json(
            translated_json,
            {
                reuters_item["url"]: {"title": "路透标题"},
                bloomberg_item["url"]: {
                    "title": "彭博标题",
                    "summary": "Bloomberg English summary raw copy",
                    "summary_zh": "彭博中文摘要",
                },
                twitter_item["url"]: {
                    "title": "主推文完整中文翻译",
                    "quoted_text": "引用推文完整中文翻译",
                },
            },
        )

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "finalize",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
            "--state-dir",
            str(self.state_dir),
            "--out-dir",
            str(self.out_dir),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload_out = json.loads(result.stdout)
        daily_sidecar = read_json(self.out_dir / "dailyFreshNews_2026-04-09.newsreader.json")
        state_payload = read_json(self.today_state_path)

        assert isinstance(daily_sidecar, dict)
        self.assertEqual(daily_sidecar["schema_version"], "newsreader.daily.v1")
        self.assertEqual(
            Path(daily_sidecar["source_markdown"]).resolve(),
            (self.out_dir / "dailyFreshNews_2026-04-09.md").resolve(),
        )
        self.assertEqual(
            Path(payload_out["daily_sidecar_path"]).resolve(),
            (self.out_dir / "dailyFreshNews_2026-04-09.newsreader.json").resolve(),
        )
        self.assertNotIn("run_sidecar_path", payload_out)
        self.assertNotIn("run_sidecar_path", state_payload["runs"][0])
        self.assertFalse(
            self.run_markdown_path("newsreader-sidecar")
            .with_suffix(".newsreader.json")
            .exists()
        )

        items = daily_sidecar["items"]
        self.assertEqual([item["item_order"] for item in items], [1, 2, 3])

        reuters_sidecar = items[0]
        self.assertEqual(reuters_sidecar["source_type"], "reuters")
        self.assertEqual(reuters_sidecar["source_name"], "Reuters")
        self.assertEqual(reuters_sidecar["title"], "路透标题")
        self.assertEqual(reuters_sidecar["title_raw"], "Original Reuters Title")

        bloomberg_sidecar = items[1]
        self.assertEqual(bloomberg_sidecar["source_type"], "bloomberg")
        self.assertEqual(bloomberg_sidecar["summary"], "彭博中文摘要")
        self.assertEqual(bloomberg_sidecar["summary_raw"], "Original Bloomberg summary")
        self.assertEqual(bloomberg_sidecar["summary_zh"], "彭博中文摘要")

        twitter_sidecar = items[2]
        self.assertEqual(twitter_sidecar["source_type"], "twitter")
        self.assertEqual(twitter_sidecar["source_name"], "X")
        self.assertEqual(twitter_sidecar["title"], "主推文完整中文翻译")
        self.assertEqual(twitter_sidecar["title_raw"], twitter_item["raw_title"])
        self.assertEqual(twitter_sidecar["quoted_text"], "引用推文完整中文翻译")
        self.assertEqual(twitter_sidecar["quoted_text_raw"], twitter_item["quoted_text_raw"])
        self.assertEqual(twitter_sidecar["quoted_text_zh"], "引用推文完整中文翻译")
        self.assertEqual(
            twitter_sidecar["canonical_url"],
            "https://x.com/ilyasut/status/99",
        )
        self.assertEqual(twitter_sidecar["author_name"], "Ilya Sutskever")
        self.assertEqual(twitter_sidecar["author_screen_name"], "ilyasut")
        self.assertEqual(
            twitter_sidecar["provenance"],
            {"translation_policy": "auto", "run_id": "newsreader-sidecar"},
        )

        self.assertEqual(
            daily_sidecar["errors"],
            [
                {
                    "label": "world",
                    "time": "2026-04-09 12:05:00",
                    "message": "Recovered degradation example",
                }
            ],
        )

    def test_legacy_run_sidecar_and_state_field_are_readable_but_untouched(self) -> None:
        module = load_incremental_module()
        legacy_sidecar = self.out_dir / "legacy_freshNews.newsreader.json"
        write_json(legacy_sidecar, {"schema_version": "legacy", "sentinel": True})
        legacy_state_path = self.state_dir / "legacy-state.json"
        write_json(
            legacy_state_path,
            make_state_payload(
                runs=[{
                    "run_id": "legacy-run",
                    "generated_at": "2026-04-09 11:00:00",
                    "run_sidecar_path": str(legacy_sidecar),
                }]
            ),
        )

        loaded = module.load_state(legacy_state_path, "2026-04-09", TIMEZONE)

        self.assertEqual(loaded["runs"][0]["run_sidecar_path"], str(legacy_sidecar))
        self.assertEqual(read_json(legacy_sidecar), {"schema_version": "legacy", "sentinel": True})

    def test_finalize_sidecar_separates_collector_from_four_content_authors(self) -> None:
        run_dir = self.make_run_dir("twitter-collector-contract")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        cases = [
            ("original", "WaylandZhang", "Wayland Zhang"),
            ("repost", "istdrc", "转发内容作者"),
            ("reply", "wey_gu", "回复内容作者"),
            ("quote", "aiandcloud", "引用内容作者"),
        ]
        twitter_items = []
        for index, (kind, author_handle, author_name) in enumerate(cases, start=1):
            item = {
                "section": "WaylandZhang",
                "title": f"{kind} 中文内容",
                "raw_title": f"{kind} 中文内容",
                "time": f"2026-04-09 12:0{index}:00",
                "url": f"https://x.com/{author_handle}/status/{index}?s=20",
                "translation_policy": "auto",
                "source_type": "twitter",
                "source_handle": "WaylandZhang",
                "source_name": "WaylandZhang",
                "author_name": author_name,
                "author_screen_name": author_handle,
            }
            if kind == "quote":
                item["quoted_text_raw"] = "被引用内容"
            twitter_items.append(item)

        payload = self.make_incremental_payload(
            run_dir=run_dir,
            run_id="twitter-collector-contract",
            started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00",
            run_fresh_items=twitter_items,
        )
        payload["section_order"] = ["WaylandZhang"]
        write_json(self.today_state_path, make_state_payload(runs=[]))
        write_json(incremental_json, payload)
        write_json(translated_json, {})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "finalize",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
            "--state-dir",
            str(self.state_dir),
            "--out-dir",
            str(self.out_dir),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        sidecar = read_json(self.out_dir / "dailyFreshNews_2026-04-09.newsreader.json")
        self.assertEqual(len(sidecar["items"]), 4)
        for index, (sidecar_item, (kind, author_handle, author_name)) in enumerate(
            zip(sidecar["items"], cases, strict=True),
            start=1,
        ):
            with self.subTest(kind=kind):
                self.assertEqual(sidecar_item["source_type"], "twitter")
                self.assertEqual(sidecar_item["source_handle"], "WaylandZhang")
                self.assertEqual(sidecar_item["source_name"], "WaylandZhang")
                self.assertEqual(sidecar_item["source"], "WaylandZhang")
                self.assertEqual(sidecar_item["author_screen_name"], author_handle)
                self.assertEqual(sidecar_item["author_name"], author_name)
                self.assertEqual(
                    sidecar_item["canonical_url"],
                    f"https://x.com/{author_handle}/status/{index}",
                )

    def test_finalize_rejects_state_drift_since_prepare(self) -> None:
        run_dir = self.make_run_dir("drift-run")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        write_json(
            self.today_state_path,
            make_state_payload(
                runs=[
                    {
                        "run_id": "old-run",
                        "generated_at": "2026-04-09 10:00:00",
                        "run_fresh_path": "unused",
                        "daily_fresh_path": "unused",
                        "run_fresh_count": 1,
                        "daily_fresh_count": 1,
                        "error_count": 0,
                    },
                    {
                        "run_id": "newer-run",
                        "generated_at": "2026-04-09 11:00:00",
                        "run_fresh_path": "unused",
                        "daily_fresh_path": "unused",
                        "run_fresh_count": 1,
                        "daily_fresh_count": 2,
                        "error_count": 0,
                    },
                ]
            ),
        )
        write_json(
            incremental_json,
            self.make_incremental_payload(
                run_dir=run_dir,
                run_id="drift-run",
                started_at="2026-04-09 12:00:00",
                finished_at="2026-04-09 12:05:00",
                latest_generated_at="2026-04-09 10:00:00",
                latest_run_id="old-run",
            ),
        )
        write_json(translated_json, {})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "finalize",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
            "--state-dir",
            str(self.state_dir),
            "--out-dir",
            str(self.out_dir),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("State changed since prepare", result.stderr)
        self.assertIn("[FINALIZE_STATE_CHANGED_SINCE_PREPARE]", result.stderr)

    def test_finalize_allows_untranslated_bloomberg_summary_with_warning(self) -> None:
        run_dir = self.make_run_dir("bloomberg-missing-summary")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        item = {
            "section": "bloomberg_main",
            "title": "Original Bloomberg Title",
            "raw_title": "Original Bloomberg Title",
            "time": "2026-04-09 12:00:00",
            "url": "https://www.bloomberg.com/news/articles/example-missing",
            "summary": "Original English summary",
        }
        payload = self.make_incremental_payload(
            run_dir=run_dir,
            run_id="bloomberg-missing-summary",
            started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00",
            run_fresh_items=[item],
        )
        payload["section_order"] = ["bloomberg_main"]
        write_json(self.today_state_path, make_state_payload(runs=[]))
        write_json(incremental_json, payload)
        write_json(
            translated_json,
            {
                item["url"]: {
                    "title": "彭博标题",
                }
            },
        )

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "finalize",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
            "--state-dir",
            str(self.state_dir),
            "--out-dir",
            str(self.out_dir),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("[FINALIZE_TRANSLATION_REPAIR_REQUIRED]", result.stderr)

    def test_finalize_uses_raw_title_when_translation_is_missing(self) -> None:
        run_dir = self.make_run_dir("missing-title-translation")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        item = {
            "section": "world",
            "title": "Original English Title",
            "raw_title": "Original English Title",
            "time": "2026-04-09 12:00:00",
            "url": "https://example.com/missing-title",
            "translation_policy": "always",
        }
        payload = self.make_incremental_payload(
            run_dir=run_dir,
            run_id="missing-title-translation",
            started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00",
            run_fresh_items=[item],
        )
        payload["section_order"] = ["world"]
        write_json(self.today_state_path, make_state_payload(runs=[]))
        write_json(incremental_json, payload)
        write_json(translated_json, {item["url"]: {}})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "finalize",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
            "--state-dir",
            str(self.state_dir),
            "--out-dir",
            str(self.out_dir),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("[FINALIZE_TRANSLATION_REPAIR_REQUIRED]", result.stderr)

    def test_finalize_uses_title_translation_even_if_not_chinese(self) -> None:
        run_dir = self.make_run_dir("non-chinese-title-translation")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        item = {
            "section": "world",
            "title": "Original English Title",
            "raw_title": "Original English Title",
            "time": "2026-04-09 12:00:00",
            "url": "https://example.com/non-chinese-title",
            "translation_policy": "always",
        }
        payload = self.make_incremental_payload(
            run_dir=run_dir,
            run_id="non-chinese-title-translation",
            started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00",
            run_fresh_items=[item],
        )
        payload["section_order"] = ["world"]
        write_json(self.today_state_path, make_state_payload(runs=[]))
        write_json(incremental_json, payload)
        write_json(translated_json, {item["url"]: {"title": "Still English"}})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "finalize",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
            "--state-dir",
            str(self.state_dir),
            "--out-dir",
            str(self.out_dir),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("[FINALIZE_TRANSLATION_REPAIR_REQUIRED]", result.stderr)

    def test_finalize_does_not_warn_for_required_chinese_title(self) -> None:
        run_dir = self.make_run_dir("chinese-title-translation")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        item = {
            "section": "world",
            "title": "Original English Title",
            "raw_title": "Original English Title",
            "time": "2026-04-09 12:00:00",
            "url": "https://example.com/chinese-title",
            "translation_policy": "always",
        }
        payload = self.make_incremental_payload(
            run_dir=run_dir,
            run_id="chinese-title-translation",
            started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00",
            run_fresh_items=[item],
        )
        payload["section_order"] = ["world"]
        write_json(self.today_state_path, make_state_payload(runs=[]))
        write_json(incremental_json, payload)
        write_json(translated_json, {item["url"]: {"title": "中文标题"}})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "finalize", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json), "--state-dir", str(self.state_dir),
            "--out-dir", str(self.out_dir),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        run_markdown = self.run_markdown_path("chinese-title-translation").read_text(encoding="utf-8")
        self.assertNotIn("title 未翻译", run_markdown)
        self.assertNotIn("title 翻译看起来仍非中文", run_markdown)

    def test_finalize_auto_audits_english_title_but_skips_cjk_title(self) -> None:
        run_dir = self.make_run_dir("auto-title-audit")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        english_item = {
            "section": "twitter", "title": "English only tweet", "raw_title": "English only tweet",
            "time": "2026-04-09 12:00:00", "url": "https://x.com/example/status/101?s=20",
            "translation_policy": "auto",
        }
        cjk_item = {
            "section": "twitter", "title": "Mixed 中文 tweet", "raw_title": "Mixed 中文 tweet",
            "time": "2026-04-09 12:01:00", "url": "https://x.com/example/status/102?s=20",
            "translation_policy": "auto",
        }
        payload = self.make_incremental_payload(
            run_dir=run_dir, run_id="auto-title-audit", started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00", run_fresh_items=[english_item, cjk_item],
        )
        payload["section_order"] = ["twitter"]
        write_json(self.today_state_path, make_state_payload(runs=[]))
        write_json(incremental_json, payload)
        write_json(translated_json, {english_item["url"]: {}, cjk_item["url"]: {}})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "finalize", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json), "--state-dir", str(self.state_dir),
            "--out-dir", str(self.out_dir),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("[FINALIZE_TRANSLATION_REPAIR_REQUIRED]", result.stderr)

    def test_finalize_uses_raw_quote_when_translation_is_missing(self) -> None:
        run_dir = self.make_run_dir("missing-quote-translation")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        item = {
            "section": "twitter",
            "title": "Original English Title",
            "raw_title": "Original English Title",
            "time": "2026-04-09 12:00:00",
            "url": "https://x.com/example/status/1?s=20",
            "quoted_text_raw": "Original quoted tweet text",
        }
        payload = self.make_incremental_payload(
            run_dir=run_dir,
            run_id="missing-quote-translation",
            started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00",
            run_fresh_items=[item],
        )
        payload["section_order"] = ["twitter"]
        write_json(self.today_state_path, make_state_payload(runs=[]))
        write_json(incremental_json, payload)
        write_json(translated_json, {item["url"]: {"title": "中文标题"}})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "finalize",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
            "--state-dir",
            str(self.state_dir),
            "--out-dir",
            str(self.out_dir),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("[FINALIZE_TRANSLATION_REPAIR_REQUIRED]", result.stderr)

    def test_finalize_uses_separate_twitter_title_and_quote_translations(self) -> None:
        run_dir = self.make_run_dir("twitter-title-quote-split")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        item = {
            "section": "twitter",
            "title": "Original main tweet text",
            "raw_title": "Original main tweet text",
            "time": "2026-04-09 12:00:00",
            "url": "https://x.com/example/status/99?s=20",
            "quoted_text_raw": "Original quoted tweet text",
        }
        payload = self.make_incremental_payload(
            run_dir=run_dir,
            run_id="twitter-title-quote-split",
            started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00",
            run_fresh_items=[item],
        )
        payload["section_order"] = ["twitter"]
        write_json(self.today_state_path, make_state_payload(runs=[]))
        write_json(incremental_json, payload)
        write_json(
            translated_json,
            {
                item["url"]: {
                    "title": "主推文完整中文翻译",
                    "quoted_text": "引用推文完整中文翻译",
                }
            },
        )

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "finalize",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
            "--state-dir",
            str(self.state_dir),
            "--out-dir",
            str(self.out_dir),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        run_markdown = self.run_markdown_path("twitter-title-quote-split").read_text(
            encoding="utf-8"
        )
        self.assertIn("### [主推文完整中文翻译]", run_markdown)
        self.assertIn("> 引用推文完整中文翻译", run_markdown)
        self.assertNotIn("Original main tweet text", run_markdown)
        self.assertNotIn("Original quoted tweet text", run_markdown)

    def test_finalize_does_not_warn_for_already_chinese_visible_text(self) -> None:
        run_dir = self.make_run_dir("already-chinese-text")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        item = {
            "section": "twitter",
            "title": "中文标题",
            "raw_title": "中文标题",
            "time": "2026-04-09 12:00:00",
            "url": "https://x.com/example/status/3?s=20",
            "quoted_text_raw": "中文引用",
        }
        payload = self.make_incremental_payload(
            run_dir=run_dir,
            run_id="already-chinese-text",
            started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00",
            run_fresh_items=[item],
        )
        payload["section_order"] = ["twitter"]
        write_json(self.today_state_path, make_state_payload(runs=[]))
        write_json(incremental_json, payload)
        write_json(translated_json, {})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "finalize",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
            "--state-dir",
            str(self.state_dir),
            "--out-dir",
            str(self.out_dir),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        run_markdown = self.run_markdown_path("already-chinese-text").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("title 未翻译", run_markdown)
        self.assertNotIn("quote 未翻译", run_markdown)

    def test_finalize_uses_original_bloomberg_summary_when_translation_is_not_chinese(self) -> None:
        run_dir = self.make_run_dir("bloomberg-non-chinese-summary")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        item = {
            "section": "bloomberg_main",
            "title": "Original Bloomberg Title",
            "raw_title": "Original Bloomberg Title",
            "time": "2026-04-09 12:00:00",
            "url": "https://www.bloomberg.com/news/articles/example-non-chinese",
            "summary": "Original English summary",
        }
        payload = self.make_incremental_payload(
            run_dir=run_dir,
            run_id="bloomberg-non-chinese-summary",
            started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00",
            run_fresh_items=[item],
        )
        payload["section_order"] = ["bloomberg_main"]
        write_json(self.today_state_path, make_state_payload(runs=[]))
        write_json(incremental_json, payload)
        write_json(
            translated_json,
            {
                item["url"]: {
                    "title": "彭博标题",
                    "summary_zh": "Bad English translation",
                }
            },
        )

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "finalize",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
            "--state-dir",
            str(self.state_dir),
            "--out-dir",
            str(self.out_dir),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("[FINALIZE_TRANSLATION_REPAIR_REQUIRED]", result.stderr)

    def test_finalize_marks_bad_translated_json_with_error_code(self) -> None:
        run_dir = self.make_run_dir("bad-translated-json")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        write_json(self.today_state_path, make_state_payload(runs=[]))
        write_json(
            incremental_json,
            self.make_incremental_payload(
                run_dir=run_dir,
                run_id="bad-translated-json",
                started_at="2026-04-09 12:00:00",
                finished_at="2026-04-09 12:05:00",
            ),
        )
        write_json(translated_json, [])

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "finalize",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
            "--state-dir",
            str(self.state_dir),
            "--out-dir",
            str(self.out_dir),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("[FINALIZE_BAD_TRANSLATED_JSON]", result.stderr)

    def test_finalize_marks_already_finalized_run_with_error_code(self) -> None:
        run_dir = self.make_run_dir("already-finalized")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        write_json(
            self.today_state_path,
            make_state_payload(
                runs=[
                    {
                        "run_id": "already-finalized",
                        "generated_at": "2026-04-09 12:05:00",
                        "run_fresh_path": "unused",
                        "daily_fresh_path": "unused",
                        "run_fresh_count": 1,
                        "daily_fresh_count": 1,
                        "error_count": 0,
                    }
                ]
            ),
        )
        write_json(
            incremental_json,
            self.make_incremental_payload(
                run_dir=run_dir,
                run_id="already-finalized",
                started_at="2026-04-09 12:00:00",
                finished_at="2026-04-09 12:05:00",
                latest_generated_at="2026-04-09 12:05:00",
                latest_run_id="already-finalized",
            ),
        )
        write_json(translated_json, {})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "finalize",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
            "--state-dir",
            str(self.state_dir),
            "--out-dir",
            str(self.out_dir),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("[FINALIZE_RUN_ALREADY_FINALIZED]", result.stderr)

    def test_finalize_error_code_recoverability_table(self) -> None:
        module = load_incremental_module()
        self.assertTrue(
            module.is_recoverable_finalize_error_code(
                "FINALIZE_STATE_CHANGED_SINCE_PREPARE"
            )
        )
        for code in [
            "FINALIZE_BAD_ARTIFACT_PATH",
            "FINALIZE_BAD_INCREMENTAL_JSON",
            "FINALIZE_BAD_TRANSLATED_JSON",
            "FINALIZE_BAD_RUN_METADATA",
            "FINALIZE_BAD_INCREMENTAL_METADATA",
            "FINALIZE_BAD_STATE",
            "FINALIZE_RUN_ALREADY_FINALIZED",
            "FINALIZE_OUTPUT_EXISTS",
            "FINALIZE_WRITE_FAILED",
        ]:
            self.assertFalse(module.is_recoverable_finalize_error_code(code), code)

    def test_finalize_help_no_longer_mentions_overwrite_flag(self) -> None:
        result = self.run_cmd(str(INCREMENTAL_SCRIPT), "finalize", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("--allow-overwrite-existing-run", result.stdout)

    def test_export_script_help(self) -> None:
        result = self.run_cmd(str(EXPORT_SCRIPT), "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--daily", result.stdout)
        self.assertIn("--fresh", result.stdout)
        self.assertIn("--target-root", result.stdout)

    def test_export_filename_parsing(self) -> None:
        spec = importlib.util.spec_from_file_location("export_outputs", EXPORT_SCRIPT)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        year, month = module.parse_daily_date(Path("dailyFreshNews_2026-03-31.md"))
        self.assertEqual((year, month), (2026, 3))
        year, month = module.parse_fresh_date(Path("2026-03-31-18-27_freshNews.md"))
        self.assertEqual((year, month), (2026, 3))
        year, month = module.parse_fresh_date(
            Path("2026-03-31-18-27-44-a1b2c3d4e5f6_freshNews.md")
        )
        self.assertEqual((year, month), (2026, 3))

        with self.assertRaises(ValueError):
            module.parse_daily_date(Path("daily_2026-03-31.md"))
        with self.assertRaises(ValueError):
            module.parse_fresh_date(Path("2026-03-31_freshNews.md"))

    def test_export_check_root_exists(self) -> None:
        spec = importlib.util.spec_from_file_location("export_outputs", EXPORT_SCRIPT)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with self.assertRaises(FileNotFoundError):
            module.check_root_exists(self.root / "not-exists")

    def test_export_copies_markdown_and_daily_sidecar_only(self) -> None:
        source_dir = self.root / "out"
        target_root = self.root / "DailyNews"
        month_dir = target_root / "2026年4月"
        month_dir.mkdir(parents=True, exist_ok=True)

        daily_path = source_dir / "dailyFreshNews_2026-04-09.md"
        fresh_path = source_dir / "2026-04-09-12-05_freshNews.md"
        daily_sidecar = source_dir / "dailyFreshNews_2026-04-09.newsreader.json"
        stale_fresh_sidecar = source_dir / "2026-04-09-12-05_freshNews.newsreader.json"
        write_text(daily_path, "# daily\n")
        write_text(fresh_path, "# fresh\n")
        write_json(daily_sidecar, {"schema_version": "newsreader.daily.v1", "items": []})
        write_json(stale_fresh_sidecar, {"schema_version": "legacy", "items": []})

        env = dict(os.environ)
        env["NEWSFLOW_EXPORT_ROOT"] = str(target_root)
        result = subprocess.run(
            [sys.executable, str(EXPORT_SCRIPT), "--daily", str(daily_path), "--fresh", str(fresh_path)],
            capture_output=True,
            text=True,
            env=env,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["exported"]), 3)
        self.assertTrue((month_dir / daily_path.name).exists())
        self.assertTrue((month_dir / fresh_path.name).exists())
        self.assertTrue((month_dir / daily_sidecar.name).exists())
        self.assertFalse((month_dir / "2026-04-09-12-05_freshNews.newsreader.json").exists())

    def test_export_cli_target_root_overrides_environment(self) -> None:
        source_dir = self.root / "out"
        env_root = self.root / "env-root"
        cli_root = self.root / "导出 root #1"
        env_root.mkdir(parents=True, exist_ok=True)
        cli_root.mkdir(parents=True, exist_ok=True)
        daily_path = source_dir / "dailyFreshNews_2026-04-09.md"
        fresh_path = source_dir / "2026-04-09-12-05-44-a1b2c3d4e5f6_freshNews.md"
        write_text(daily_path, "# daily\n")
        write_text(fresh_path, "# fresh\n")
        write_json(daily_path.with_suffix(".newsreader.json"), {"items": []})
        env = dict(os.environ)
        env["NEWSFLOW_EXPORT_ROOT"] = str(env_root)
        result = subprocess.run(
            [
                sys.executable,
                str(EXPORT_SCRIPT),
                "--daily",
                str(daily_path),
                "--fresh",
                str(fresh_path),
                "--target-root",
                str(cli_root),
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(cli_root), result.stdout)
        self.assertTrue((cli_root / "2026年4月" / fresh_path.name).exists())
        self.assertFalse((env_root / "2026年4月" / fresh_path.name).exists())

    def test_export_legacy_default_emits_compatibility_warning(self) -> None:
        env = dict(os.environ)
        env.pop("NEWSFLOW_EXPORT_ROOT", None)
        result = subprocess.run(
            [
                sys.executable,
                str(EXPORT_SCRIPT),
                "--daily",
                str(self.root / "missing-daily.md"),
                "--fresh",
                str(self.root / "missing-fresh.md"),
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("legacy personal export root", result.stderr)

    def test_export_fails_when_sidecar_is_missing(self) -> None:
        source_dir = self.root / "out"
        target_root = self.root / "DailyNews"
        target_root.mkdir(parents=True, exist_ok=True)

        daily_path = source_dir / "dailyFreshNews_2026-04-09.md"
        fresh_path = source_dir / "2026-04-09-12-05_freshNews.md"
        write_text(daily_path, "# daily\n")
        write_text(fresh_path, "# fresh\n")

        env = dict(os.environ)
        env["NEWSFLOW_EXPORT_ROOT"] = str(target_root)
        result = subprocess.run(
            [sys.executable, str(EXPORT_SCRIPT), "--daily", str(daily_path), "--fresh", str(fresh_path)],
            capture_output=True,
            text=True,
            env=env,
        )

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "source_sidecar_not_found")

    def test_validate_translations_reports_missing_entry_for_always_policy(self) -> None:
        run_dir = self.make_run_dir("validate-missing-entry")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        item = {
            "section": "world",
            "translation_policy": "always",
            "title": "Original English Title",
            "raw_title": "Original English Title",
            "time": "2026-04-09 12:00:00",
            "url": "https://example.com/always-missing",
        }
        payload = self.make_incremental_payload(
            run_dir=run_dir,
            run_id="validate-missing-entry",
            started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00",
            run_fresh_items=[item],
        )
        write_json(incremental_json, payload)
        write_json(translated_json, {})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "validate-translations",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data["ok"])
        self.assertGreaterEqual(data["issue_count"], 1)
        self.assertEqual(data["issues"][0]["field"], "url")
        self.assertEqual(data["issues"][0]["reason"], "missing_translation_entry")

    def test_validate_translations_checks_twitter_quote_for_auto_policy(self) -> None:
        run_dir = self.make_run_dir("validate-twitter-quote")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        item = {
            "section": "Ilya Sutskever",
            "translation_policy": "auto",
            "title": "Original English Title",
            "raw_title": "Original English Title",
            "time": "2026-04-09 12:00:00",
            "url": "https://x.com/example/status/9?s=20",
            "quoted_text_raw": "Original quoted tweet text",
        }
        payload = self.make_incremental_payload(
            run_dir=run_dir,
            run_id="validate-twitter-quote",
            started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00",
            run_fresh_items=[item],
        )
        write_json(incremental_json, payload)
        write_json(translated_json, {item["url"]: {"title": "中文标题"}})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "validate-translations",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data["ok"])
        issue_fields = {issue["field"] for issue in data["issues"]}
        self.assertIn("quoted_text", issue_fields)

    def test_quote_canonical_key_wins_and_legacy_warning_stays_hidden(self) -> None:
        run_dir = self.make_run_dir("quote-canonical")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        item = {
            "section": "Ilya Sutskever",
            "translation_policy": "auto",
            "title": "Original English Title",
            "raw_title": "Original English Title",
            "time": "2026-04-09 12:00:00",
            "url": "https://x.com/example/status/canonical?s=20",
            "quoted_text_raw": "Original quoted tweet text",
        }
        payload = self.make_incremental_payload(
            run_dir=run_dir,
            run_id="quote-canonical",
            started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00",
            run_fresh_items=[item],
        )
        write_json(incremental_json, payload)
        write_json(
            translated_json,
            {
                item["url"]: {
                    "title": "中文标题",
                    "quoted_text_zh": "规范中文引用",
                    "quoted_text": "Legacy English quote",
                }
            },
        )
        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "validate-translations",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = read_json(run_dir / "translation-validation.json")
        self.assertEqual(receipt["initial"]["warning_count"], 1)
        self.assertEqual(receipt["initial"]["warnings"][0]["reason"], "legacy_key_ignored")

        write_json(self.today_state_path, make_state_payload(runs=[]))
        finalized = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "finalize",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
            "--state-dir",
            str(self.state_dir),
            "--out-dir",
            str(self.out_dir),
        )
        self.assertEqual(finalized.returncode, 0, finalized.stderr)
        sidecar = read_json(self.out_dir / "dailyFreshNews_2026-04-09.newsreader.json")
        self.assertEqual(sidecar["items"][0]["quoted_text_zh"], "规范中文引用")
        self.assertEqual(sidecar["items"][0]["quoted_text"], "规范中文引用")

    def test_quote_distinct_chinese_keys_are_a_validation_conflict(self) -> None:
        run_dir = self.make_run_dir("quote-conflict")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        item = {
            "section": "Ilya Sutskever",
            "translation_policy": "auto",
            "title": "Original English Title",
            "raw_title": "Original English Title",
            "time": "2026-04-09 12:00:00",
            "url": "https://x.com/example/status/conflict?s=20",
            "quoted_text_raw": "Original quoted tweet text",
        }
        write_json(
            incremental_json,
            self.make_incremental_payload(
                run_dir=run_dir,
                run_id="quote-conflict",
                started_at="2026-04-09 12:00:00",
                finished_at="2026-04-09 12:05:00",
                run_fresh_items=[item],
            ),
        )
        write_json(
            translated_json,
            {
                item["url"]: {
                    "title": "中文标题",
                    "quoted_text_zh": "第一种中文",
                    "quoted_text": "第二种中文",
                }
            },
        )
        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "validate-translations",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data["ok"])
        self.assertIn("conflicting_translation_fields", {issue["reason"] for issue in data["issues"]})

    def test_same_second_runs_get_distinct_output_stems(self) -> None:
        write_json(self.today_state_path, make_state_payload(runs=[]))
        for index, run_id in enumerate(("same-second-a", "same-second-b")):
            run_dir = self.make_run_dir(run_id)
            incremental_json = run_dir / "incremental.json"
            translated_json = run_dir / "translated.json"
            payload = self.make_incremental_payload(
                run_dir=run_dir,
                run_id=run_id,
                started_at="2026-04-09 12:00:00",
                finished_at="2026-04-09 12:05:00",
            )
            if index:
                payload["state_snapshot"] = {
                    "latest_finalized_run_id": "same-second-a",
                    "latest_finalized_generated_at": "2026-04-09 12:05:00",
                }
            write_json(incremental_json, payload)
            write_json(translated_json, {})
            result = self.run_cmd(
                str(INCREMENTAL_SCRIPT),
                "finalize",
                "--incremental-json",
                str(incremental_json),
                "--translated-json",
                str(translated_json),
                "--state-dir",
                str(self.state_dir),
                "--out-dir",
                str(self.out_dir),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self.expected_run_stem("same-second-a"), self.expected_run_stem("same-second-b"))
        self.assertTrue(self.run_markdown_path("same-second-a").exists())
        self.assertTrue(self.run_markdown_path("same-second-b").exists())

    def test_finalize_rejects_tampered_run_output_stem(self) -> None:
        run_dir = self.make_run_dir("tampered-stem")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        payload = self.make_incremental_payload(
            run_dir=run_dir,
            run_id="tampered-stem",
            started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00",
        )
        payload["run_output_stem"] = "2026-04-09-12-05-00-000000000000"
        write_json(self.today_state_path, make_state_payload(runs=[]))
        write_json(incremental_json, payload)
        write_json(translated_json, {})
        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "finalize",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
            "--state-dir",
            str(self.state_dir),
            "--out-dir",
            str(self.out_dir),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("[FINALIZE_BAD_INCREMENTAL_METADATA]", result.stderr)

    def test_validate_translations_reports_bloomberg_summary_missing(self) -> None:
        run_dir = self.make_run_dir("validate-bloomberg-summary")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        item = {
            "section": "bloomberg_main",
            "translation_policy": "always",
            "title": "Bloomberg title",
            "raw_title": "Bloomberg title",
            "time": "2026-04-09 12:00:00",
            "url": "https://www.bloomberg.com/news/articles/example-summary",
            "summary": "Original English summary",
        }
        payload = self.make_incremental_payload(
            run_dir=run_dir,
            run_id="validate-bloomberg-summary",
            started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00",
            run_fresh_items=[item],
        )
        write_json(incremental_json, payload)
        write_json(translated_json, {item["url"]: {"title": "中文标题"}})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "validate-translations",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data["ok"])
        self.assertIn("summary", {issue["field"] for issue in data["issues"]})

    def test_validate_translations_returns_ok_after_patch(self) -> None:
        run_dir = self.make_run_dir("validate-ok")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        item = {
            "section": "world",
            "translation_policy": "always",
            "title": "Original English Title",
            "raw_title": "Original English Title",
            "time": "2026-04-09 12:00:00",
            "url": "https://example.com/always-ok",
        }
        payload = self.make_incremental_payload(
            run_dir=run_dir,
            run_id="validate-ok",
            started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00",
            run_fresh_items=[item],
        )
        write_json(incremental_json, payload)
        write_json(translated_json, {item["url"]: {"title": "中文标题"}})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT),
            "validate-translations",
            "--incremental-json",
            str(incremental_json),
            "--translated-json",
            str(translated_json),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["ok"])
        self.assertEqual(data["issue_count"], 0)
        self.assertEqual(data["issues"], [])

    def test_plan_translations_uses_one_capacity_batch_and_skips_han_auto_title(self) -> None:
        run_dir = self.make_run_dir("translation-plan")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        plan_json = run_dir / "translation-plan.json"
        items = [
            {
                "section": "world", "translation_policy": "auto", "title": f"English {index}",
                "raw_title": f"English {index}", "time": "2026-04-09 12:00:00",
                "url": f"https://example.com/plan-{index}",
            }
            for index in range(40)
        ]
        items.insert(2, {
            "section": "Ilya Sutskever", "translation_policy": "auto", "title": "x" * 1000,
            "raw_title": "x" * 1000, "time": "2026-04-09 12:00:00",
            "url": "https://x.com/example/status/long?s=20",
        })
        items.append({
            "section": "Ilya Sutskever", "translation_policy": "auto", "title": "Mixed 中文",
            "raw_title": "Mixed 中文", "quoted_text_raw": "English quote", "time": "2026-04-09 12:00:00",
            "url": "https://x.com/example/status/cjk?s=20",
        })
        write_json(incremental_json, self.make_incremental_payload(
            run_dir=run_dir, run_id="translation-plan", started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00", run_fresh_items=items,
        ))
        write_json(translated_json, {})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "plan-translations", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json), "--out-json", str(plan_json), "--phase", "initial",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        plan = read_json(plan_json)
        self.assertEqual(plan["batch_source_char_limit"], 12000)
        batches = plan["batches"]
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["source_chars"], plan["total_source_chars"])
        cjk_item = next(
            item for batch in batches for item in batch["items"]
            if item["url"] == "https://x.com/example/status/cjk?s=20"
        )
        self.assertEqual(cjk_item["required_fields"], ["quoted_text_zh"])

    def test_plan_translations_splits_only_after_source_character_capacity(self) -> None:
        run_dir = self.make_run_dir("translation-capacity")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        plan_json = run_dir / "translation-plan.json"
        items = [
            {
                "section": "world",
                "translation_policy": "always",
                "title": character * 7000,
                "raw_title": character * 7000,
                "time": "2026-04-09 12:00:00",
                "url": f"https://example.com/capacity-{index}",
            }
            for index, character in enumerate(("a", "b"), start=1)
        ]
        write_json(
            incremental_json,
            self.make_incremental_payload(
                run_dir=run_dir,
                run_id="translation-capacity",
                started_at="2026-04-09 12:00:00",
                finished_at="2026-04-09 12:05:00",
                run_fresh_items=items,
            ),
        )
        write_json(translated_json, {})
        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "plan-translations",
            "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json),
            "--out-json", str(plan_json), "--phase", "initial",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = read_json(plan_json)
        self.assertEqual(plan["total_source_chars"], 14000)
        self.assertEqual([batch["source_chars"] for batch in plan["batches"]], [7000, 7000])
        self.assertEqual(
            [batch["items"][0]["raw_title"] for batch in plan["batches"]],
            ["a" * 7000, "b" * 7000],
        )

    def test_merge_translation_batch_requires_exact_urls_and_is_atomic(self) -> None:
        run_dir = self.make_run_dir("translation-merge")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        plan_json = run_dir / "translation-plan.json"
        item = {
            "section": "world", "translation_policy": "always", "title": "English title",
            "raw_title": "English title", "time": "2026-04-09 12:00:00",
            "url": "https://example.com/merge",
        }
        write_json(incremental_json, self.make_incremental_payload(
            run_dir=run_dir, run_id="translation-merge", started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00", run_fresh_items=[item],
        ))
        original = {"https://example.com/existing": {"title": "已有中文"}}
        write_json(translated_json, original)
        self.assertEqual(self.run_cmd(
            str(INCREMENTAL_SCRIPT), "plan-translations", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json), "--out-json", str(plan_json), "--phase", "initial",
        ).returncode, 0)
        batch_json = run_dir / "translation-initial-batch-001.json"
        write_json(batch_json, {"https://example.com/extra": {"title": "中文"}})
        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "merge-translation-batch", "--plan-json", str(plan_json),
            "--batch-id", "batch-001", "--batch-json", str(batch_json), "--translated-json", str(translated_json),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("[MERGE_URL_SET_MISMATCH]", result.stderr)
        self.assertEqual(read_json(translated_json), original)

        write_json(batch_json, {item["url"]: {}})
        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "merge-translation-batch", "--plan-json", str(plan_json),
            "--batch-id", "batch-001", "--batch-json", str(batch_json), "--translated-json", str(translated_json),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("[MERGE_MISSING_REQUIRED_FIELD]", result.stderr)
        self.assertEqual(read_json(translated_json), original)

        write_json(batch_json, {item["url"]: {"title": "中文", "unexpected": "value"}})
        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "merge-translation-batch", "--plan-json", str(plan_json),
            "--batch-id", "batch-001", "--batch-json", str(batch_json), "--translated-json", str(translated_json),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("[MERGE_INVALID_FIELDS]", result.stderr)
        self.assertEqual(read_json(translated_json), original)

        write_json(batch_json, {item["url"]: {"title": "合并后的中文标题"}})
        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "merge-translation-batch", "--plan-json", str(plan_json),
            "--batch-id", "batch-001", "--batch-json", str(batch_json), "--translated-json", str(translated_json),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_json(translated_json)[item["url"]]["title"], "合并后的中文标题")

    def test_repair_plan_only_contains_remaining_fields_and_rejects_outside_artifact(self) -> None:
        run_dir = self.make_run_dir("translation-repair")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        repair_json = run_dir / "translation-repair-plan.json"
        item = {
            "section": "bloomberg_main", "translation_policy": "always", "title": "English title",
            "raw_title": "English title", "summary": "English summary", "time": "2026-04-09 12:00:00",
            "url": "https://www.bloomberg.com/news/articles/repair",
        }
        write_json(incremental_json, self.make_incremental_payload(
            run_dir=run_dir, run_id="translation-repair", started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00", run_fresh_items=[item],
        ))
        write_json(translated_json, {item["url"]: {"title": "中文标题", "summary_zh": "Still English"}})
        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "validate-translations", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["ok"])
        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "plan-translations", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json), "--out-json", str(repair_json), "--phase", "repair",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        repair = read_json(repair_json)
        self.assertEqual(repair["batches"][0]["items"][0]["required_fields"], ["summary"])

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "validate-translations", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("[VALIDATE_REPAIR_EVIDENCE_INCOMPLETE]", result.stderr)

        write_json(
            run_dir / "translation-repair-batch-001.json",
            {item["url"]: {"summary_zh": "Still English"}},
        )
        malformed_repair = read_json(repair_json)
        malformed_repair["batches"][0]["items"] = []
        write_json(repair_json, malformed_repair)
        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "validate-translations", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("[VALIDATE_REPAIR_EVIDENCE_INCOMPLETE]", result.stderr)

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "plan-translations", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json), "--out-json", str(repair_json), "--phase", "repair",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("[PLAN_REPAIR_ALREADY_EXISTS]", result.stderr)

        bad_plan = self.root / "translation-plan.json"
        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "plan-translations", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json), "--out-json", str(bad_plan), "--phase", "initial",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("[PLAN_BAD_ARTIFACT_PATH]", result.stderr)

    def test_initial_empty_plan_initializes_translation_map_for_cjk_auto_items(self) -> None:
        run_dir = self.make_run_dir("translation-empty-plan")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        plan_json = run_dir / "translation-plan.json"
        item = {
            "section": "Ilya Sutskever", "translation_policy": "auto", "title": "已有中文标题",
            "raw_title": "已有中文标题", "time": "2026-04-09 12:00:00",
            "url": "https://x.com/example/status/cjk-only?s=20",
        }
        write_json(incremental_json, self.make_incremental_payload(
            run_dir=run_dir, run_id="translation-empty-plan", started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00", run_fresh_items=[item],
        ))

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "plan-translations", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json), "--out-json", str(plan_json), "--phase", "initial",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_json(plan_json)["batches"], [])
        self.assertEqual(read_json(translated_json), {})

        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "validate-translations", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_finalize_requires_repair_then_silently_falls_back_after_one_repair(self) -> None:
        run_dir = self.make_run_dir("translation-repair-gate")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        repair_plan = run_dir / "translation-repair-plan.json"
        batch_json = run_dir / "translation-repair-batch-001.json"
        item = {"section": "world", "translation_policy": "always", "title": "English", "raw_title": "English", "time": "2026-04-09 12:00:00", "url": "https://example.com/gate"}
        write_json(self.today_state_path, make_state_payload(runs=[]))
        legacy_payload = self.make_incremental_payload(run_dir=run_dir, run_id="translation-repair-gate", started_at="2026-04-09 12:00:00", finished_at="2026-04-09 12:05:00", run_fresh_items=[item])
        legacy_payload.pop("items_to_translate", None)
        write_json(incremental_json, legacy_payload)
        write_json(translated_json, {item["url"]: {"title": "Still English"}})
        self.assertEqual(self.run_cmd(str(INCREMENTAL_SCRIPT), "validate-translations", "--incremental-json", str(incremental_json), "--translated-json", str(translated_json)).returncode, 0)
        blocked = self.run_cmd(str(INCREMENTAL_SCRIPT), "finalize", "--incremental-json", str(incremental_json), "--translated-json", str(translated_json), "--state-dir", str(self.state_dir), "--out-dir", str(self.out_dir))
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("[FINALIZE_TRANSLATION_REPAIR_REQUIRED]", blocked.stderr)
        self.assertEqual(self.run_cmd(str(INCREMENTAL_SCRIPT), "plan-translations", "--incremental-json", str(incremental_json), "--translated-json", str(translated_json), "--out-json", str(repair_plan), "--phase", "repair").returncode, 0)
        write_json(batch_json, {item["url"]: {"title": "Still English"}})
        self.assertEqual(self.run_cmd(str(INCREMENTAL_SCRIPT), "merge-translation-batch", "--plan-json", str(repair_plan), "--batch-id", "batch-001", "--batch-json", str(batch_json), "--translated-json", str(translated_json)).returncode, 0)
        self.assertEqual(self.run_cmd(str(INCREMENTAL_SCRIPT), "validate-translations", "--incremental-json", str(incremental_json), "--translated-json", str(translated_json)).returncode, 0)
        allowed = self.run_cmd(str(INCREMENTAL_SCRIPT), "finalize", "--incremental-json", str(incremental_json), "--translated-json", str(translated_json), "--state-dir", str(self.state_dir), "--out-dir", str(self.out_dir))
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertNotIn("翻译", self.run_markdown_path("translation-repair-gate").read_text(encoding="utf-8").split("## errors", 1)[1])

    def test_reusing_run_directory_does_not_reuse_old_repair_plan(self) -> None:
        run_dir = self.make_run_dir("same-path-recovery")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        initial_plan = run_dir / "translation-plan.json"
        repair_plan = run_dir / "translation-repair-plan.json"
        item = {
            "section": "world", "translation_policy": "always", "title": "English",
            "raw_title": "English", "time": "2026-04-09 12:00:00",
            "url": "https://example.com/same-path",
        }
        for run_id in ("run-a", "run-b"):
            write_json(incremental_json, self.make_incremental_payload(
                run_dir=run_dir, run_id=run_id, started_at="2026-04-09 12:00:00",
                finished_at="2026-04-09 12:05:00", run_fresh_items=[item],
            ))
            write_json(translated_json, {item["url"]: {}})
            self.assertEqual(self.run_cmd(
                str(INCREMENTAL_SCRIPT), "plan-translations", "--incremental-json", str(incremental_json),
                "--translated-json", str(translated_json), "--out-json", str(initial_plan), "--phase", "initial",
            ).returncode, 0)
            self.assertEqual(self.run_cmd(
                str(INCREMENTAL_SCRIPT), "validate-translations", "--incremental-json", str(incremental_json),
                "--translated-json", str(translated_json),
            ).returncode, 0)
            self.assertEqual(self.run_cmd(
                str(INCREMENTAL_SCRIPT), "plan-translations", "--incremental-json", str(incremental_json),
                "--translated-json", str(translated_json), "--out-json", str(repair_plan), "--phase", "repair",
            ).returncode, 0)
            if run_id == "run-a":
                write_json(run_dir / "translation-repair-batch-001.json", {item["url"]: {"title": "旧结果"}})
        self.assertEqual(read_json(repair_plan)["run_id"], "run-b")
        self.assertFalse((run_dir / "translation-repair-batch-001.json").exists())

    def test_initial_plan_cannot_masquerade_as_repair_evidence(self) -> None:
        run_dir = self.make_run_dir("initial-plan-masquerade")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        initial_plan = run_dir / "translation-plan.json"
        repair_plan = run_dir / "translation-repair-plan.json"
        item = {
            "section": "world", "translation_policy": "always", "title": "English",
            "raw_title": "English", "time": "2026-04-09 12:00:00",
            "url": "https://example.com/masquerade",
        }
        write_json(incremental_json, self.make_incremental_payload(
            run_dir=run_dir, run_id="masquerade", started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00", run_fresh_items=[item],
        ))
        write_json(translated_json, {item["url"]: {}})
        self.assertEqual(self.run_cmd(
            str(INCREMENTAL_SCRIPT), "plan-translations", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json), "--out-json", str(initial_plan), "--phase", "initial",
        ).returncode, 0)
        plan = read_json(initial_plan)
        write_json(repair_plan, plan)
        write_json(run_dir / "translation-initial-batch-001.json", {item["url"]: {"title": "中文"}})
        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "validate-translations", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("[VALIDATE_REPAIR_EVIDENCE_INCOMPLETE]", result.stderr)

    def test_repair_plan_cannot_omit_one_of_multiple_batches(self) -> None:
        run_dir = self.make_run_dir("repair-missing-batch")
        incremental_json = run_dir / "incremental.json"
        translated_json = run_dir / "translated.json"
        repair_plan = run_dir / "translation-repair-plan.json"
        items = [
            {
                "section": "world", "translation_policy": "always", "title": f"English {index} " + "x" * 1490,
                "raw_title": f"English {index} " + "x" * 1490, "time": "2026-04-09 12:00:00",
                "url": f"https://example.com/multi-{index}",
            }
            for index in range(9)
        ]
        write_json(incremental_json, self.make_incremental_payload(
            run_dir=run_dir, run_id="repair-missing-batch", started_at="2026-04-09 12:00:00",
            finished_at="2026-04-09 12:05:00", run_fresh_items=items,
        ))
        write_json(translated_json, {item["url"]: {"title": "Still English"} for item in items})
        self.assertEqual(self.run_cmd(
            str(INCREMENTAL_SCRIPT), "validate-translations", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json),
        ).returncode, 0)
        self.assertEqual(self.run_cmd(
            str(INCREMENTAL_SCRIPT), "plan-translations", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json), "--out-json", str(repair_plan), "--phase", "repair",
        ).returncode, 0)
        plan = read_json(repair_plan)
        self.assertEqual(len(plan["batches"]), 2)
        for batch in plan["batches"]:
            batch_path = run_dir / f"translation-repair-{batch['batch_id']}.json"
            write_json(batch_path, {url: {"title": "Still English"} for url in batch["expected_urls"]})
            self.assertEqual(self.run_cmd(
                str(INCREMENTAL_SCRIPT), "merge-translation-batch", "--plan-json", str(repair_plan),
                "--batch-id", batch["batch_id"], "--batch-json", str(batch_path),
                "--translated-json", str(translated_json),
            ).returncode, 0)
        plan["batches"] = plan["batches"][:1]
        write_json(repair_plan, plan)
        result = self.run_cmd(
            str(INCREMENTAL_SCRIPT), "validate-translations", "--incremental-json", str(incremental_json),
            "--translated-json", str(translated_json),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("[VALIDATE_REPAIR_EVIDENCE_INCOMPLETE]", result.stderr)


if __name__ == "__main__":
    unittest.main()
