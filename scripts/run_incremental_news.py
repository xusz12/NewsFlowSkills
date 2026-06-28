#!/usr/bin/env python3
import argparse
import json
import os
import shlex
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "Asia/Shanghai"
RUNS_DIRNAME = "runs"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
TWITTER_SECTIONS = {
    "Ilya Sutskever",
    "郭明錤",
    "seekinganythingbutalpha",
    "外汇交易员",
    "Time Horizon",
    "卡比卡比",
}
PORTAL_SECTIONS = {
    "middle-east",
    "china",
    "world",
    "business",
    "technology",
    "bloomberg_main",
    "bloomberg_politics",
    "bloomberg_economics",
    "bloomberg_tech",
    "techcrunch",
    "arstechnica",
}
BLOOMBERG_SECTIONS = {
    "bloomberg_main",
    "bloomberg_politics",
    "bloomberg_economics",
    "bloomberg_tech",
}
SECTION_DISPLAY_NAMES = {
    "middle-east": "Reuters · Middle East",
    "china": "Reuters · China",
    "world": "Reuters · World",
    "business": "Reuters · Business",
    "technology": "Reuters · Technology",
    "bloomberg_main": "Bloomberg",
    "bloomberg_politics": "Bloomberg · Politics",
    "bloomberg_economics": "Bloomberg · Economics",
    "bloomberg_tech": "Bloomberg · Tech",
    "techcrunch": "TechCrunch",
    "arstechnica": "Ars Technica",
}
PREPARE_RECOVERABLE_ERROR_CODES = frozenset(
    {
        "PREPARE_STALE_CURRENT_JSON",
        "PREPARE_RUN_ID_ALREADY_FINALIZED",
        "PREPARE_GENERATED_AT_ALREADY_FINALIZED",
        "PREPARE_CURRENT_JSON_UNREADABLE",
    }
)
FINALIZE_RECOVERABLE_ERROR_CODES = frozenset(
    {
        "FINALIZE_STATE_CHANGED_SINCE_PREPARE",
    }
)
VALID_TRANSLATION_POLICIES = {"always", "auto", "never"}


class IncrementalNewsError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


def incremental_error(code: str, message: str) -> IncrementalNewsError:
    return IncrementalNewsError(code, message)


def is_recoverable_prepare_error_code(code: str) -> bool:
    return code in PREPARE_RECOVERABLE_ERROR_CODES


def is_recoverable_finalize_error_code(code: str) -> bool:
    return code in FINALIZE_RECOVERABLE_ERROR_CODES


def format_timestamp(dt: datetime) -> str:
    return dt.strftime(TIMESTAMP_FORMAT)


def parse_timestamp(text: str, *, field_name: str) -> datetime:
    value = str(text).strip()
    if not value:
        raise ValueError(f"Missing {field_name}")
    try:
        return datetime.strptime(value, TIMESTAMP_FORMAT)
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name} '{value}': {exc}") from exc


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def write_text_new_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)


def normalize_time(raw_time: Any) -> str:
    if raw_time is None:
        return "页面未显示"
    text = str(raw_time).strip()
    return text if text else "页面未显示"


def escape_md_title(title: str) -> str:
    return title.replace("[", "\\[").replace("]", "\\]")


def render_blockquote(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line:
            lines.append(f"> {line}")
        else:
            lines.append(">")
    return lines if lines else [">"]


def load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"JSON file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def write_json_file(path: Path, payload: Any) -> None:
    write_text_atomic(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def parse_command_str(command: Any, command_str: str) -> str:
    if command_str:
        return command_str
    if isinstance(command, list):
        parts = [str(part).strip() for part in command if str(part).strip()]
        if parts:
            return " ".join(shlex.quote(part) for part in parts)
    if isinstance(command, str):
        return command.strip()
    return ""


def contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def normalize_translation_policy(raw: Any) -> str:
    policy = str(raw or "auto").strip().lower() or "auto"
    if policy in VALID_TRANSLATION_POLICIES:
        return policy
    return "auto"


def normalize_item(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None

    section = str(item.get("section", "")).strip()
    url = str(item.get("url", "")).strip()
    raw_title = str(item.get("raw_title", item.get("title", ""))).strip()
    title = str(item.get("title", raw_title)).strip()
    if not section or not url or not raw_title:
        return None

    if not title:
        title = raw_title

    quoted_text_raw = str(item.get("quoted_text_raw", item.get("quoted_text", ""))).strip()
    quoted_text = str(item.get("quoted_text", quoted_text_raw)).strip()
    author_name = str(item.get("author_name", "")).strip()
    author_screen_name = str(item.get("author_screen_name", "")).strip()
    summary = str(item.get("summary", "")).strip()

    payload = {
        "section": section,
        "title": title,
        "raw_title": raw_title,
        "time": normalize_time(item.get("time")),
        "url": url,
        "translation_policy": normalize_translation_policy(item.get("translation_policy", "auto")),
    }
    if quoted_text_raw:
        payload["quoted_text_raw"] = quoted_text_raw
    if quoted_text:
        payload["quoted_text"] = quoted_text
    if author_name:
        payload["author_name"] = author_name
    if author_screen_name:
        payload["author_screen_name"] = author_screen_name
    if summary:
        payload["summary"] = summary
    return payload


def normalize_error(error: Any) -> dict[str, str] | None:
    if not isinstance(error, dict):
        return None

    section = str(error.get("section", "")).strip()
    message = str(error.get("error", "")).strip()
    generated_at = str(error.get("generated_at", "")).strip()
    command_str = parse_command_str(error.get("command"), str(error.get("command_str", "")).strip())

    if not section or not message:
        return None

    payload = {
        "section": section,
        "command_str": command_str,
        "error": message,
    }
    if generated_at:
        payload["generated_at"] = generated_at
    return payload


def extract_run_metadata(payload: dict[str, Any], *, source_label: str) -> dict[str, Any]:
    timezone_name = str(payload.get("timezone", DEFAULT_TIMEZONE)).strip() or DEFAULT_TIMEZONE
    try:
        timezone = ZoneInfo(timezone_name)
    except Exception as exc:
        raise ValueError(f"Invalid timezone '{timezone_name}': {exc}") from exc

    run_id = str(payload.get("run_id", "")).strip()
    if not run_id:
        raise ValueError(f"{source_label} is missing run_id")

    started_at_text = str(payload.get("started_at", "")).strip()
    finished_at_text = str(payload.get("finished_at", payload.get("generated_at", ""))).strip()
    generated_at_text = str(payload.get("generated_at", finished_at_text)).strip() or finished_at_text

    started_at = parse_timestamp(started_at_text, field_name=f"{source_label}.started_at").replace(
        tzinfo=timezone
    )
    finished_at = parse_timestamp(
        finished_at_text,
        field_name=f"{source_label}.finished_at",
    ).replace(tzinfo=timezone)
    generated_at = parse_timestamp(
        generated_at_text,
        field_name=f"{source_label}.generated_at",
    ).replace(tzinfo=timezone)

    if finished_at < started_at:
        raise ValueError(
            f"{source_label} has finished_at earlier than started_at: "
            f"{finished_at_text} < {started_at_text}"
        )
    if generated_at != finished_at:
        raise ValueError(
            f"{source_label} has mismatched generated_at and finished_at: "
            f"{generated_at_text} != {finished_at_text}"
        )

    return {
        "run_id": run_id,
        "timezone_name": timezone_name,
        "timezone": timezone,
        "started_at": started_at,
        "finished_at": finished_at,
        "generated_at": generated_at,
        "started_at_text": started_at_text,
        "finished_at_text": finished_at_text,
        "generated_at_text": generated_at_text,
    }


def get_runs_root(state_dir: Path) -> Path:
    return state_dir / RUNS_DIRNAME


def validate_run_artifact_path(
    path: Path,
    state_dir: Path,
    *,
    label: str,
    error_code: str | None = None,
) -> Path:
    runs_root = get_runs_root(state_dir)
    run_dir = path.parent
    if run_dir.parent != runs_root:
        message = f"{label} must be stored under {runs_root}/<run-dir>/..., got {path}"
        if error_code:
            raise incremental_error(error_code, message)
        raise ValueError(message)
    return run_dir


def latest_run_snapshot(state: dict[str, Any]) -> dict[str, str]:
    runs = state.get("runs", [])
    if not runs:
        return {
            "latest_finalized_run_id": "",
            "latest_finalized_generated_at": "",
        }

    latest = runs[-1]
    return {
        "latest_finalized_run_id": str(latest.get("run_id", "")).strip(),
        "latest_finalized_generated_at": str(latest.get("generated_at", "")).strip(),
    }


def normalize_section_order(raw_sections: Any) -> list[str]:
    sections: list[str] = []
    seen: set[str] = set()
    if not isinstance(raw_sections, list):
        return sections

    for section in raw_sections:
        text = str(section).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        sections.append(text)
    return sections


def merge_section_order(*orders: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for order in orders:
        for section in order:
            text = str(section).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
    return merged


def section_order_from_items(items: list[dict[str, str]]) -> list[str]:
    return merge_section_order([item["section"] for item in items])


def sort_sections(section_order: list[str]) -> list[str]:
    portal_sections: list[str] = []
    twitter_sections: list[str] = []
    other_sections: list[str] = []
    for section in section_order:
        if section in PORTAL_SECTIONS:
            portal_sections.append(section)
            continue
        if section in TWITTER_SECTIONS:
            twitter_sections.append(section)
            continue
        other_sections.append(section)
    return portal_sections + twitter_sections + other_sections


def parse_sortable_time(text: str) -> datetime | None:
    value = str(text or "").strip()
    if not value or value == "页面未显示":
        return None

    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def sort_section_items(section_items: list[dict[str, str]]) -> list[dict[str, str]]:
    indexed_items = list(enumerate(section_items))

    def sort_key(pair: tuple[int, dict[str, str]]) -> tuple[int, float, int]:
        index, item = pair
        parsed = parse_sortable_time(item.get("time", ""))
        if parsed is None:
            return (1, 0.0, index)
        return (0, -parsed.timestamp(), index)

    return [item for _, item in sorted(indexed_items, key=sort_key)]


def get_section_display_name(section: str) -> str:
    return SECTION_DISPLAY_NAMES.get(section, section)


def build_section_summary(section_items: list[dict[str, str]]) -> str:
    count = len(section_items)
    parsed_times = [
        (item["time"], parsed)
        for item in section_items
        for parsed in [parse_sortable_time(item.get("time", ""))]
        if parsed is not None
    ]
    if not parsed_times:
        return f"{count}条｜时间未显示"

    latest_text, _ = max(parsed_times, key=lambda pair: pair[1])
    earliest_text, _ = min(parsed_times, key=lambda pair: pair[1])
    return f"{count}条｜最新 {latest_text}｜最早 {earliest_text}｜时间倒序"


def load_state(state_path: Path, date_text: str, timezone_name: str) -> dict[str, Any]:
    if not state_path.exists():
        return {
            "date": date_text,
            "timezone": timezone_name,
            "section_order": [],
            "today_seen_urls": [],
            "today_first_seen_items": [],
            "daily_errors": [],
            "runs": [],
        }

    raw = load_json_file(state_path)
    if not isinstance(raw, dict):
        raise ValueError(f"State file must contain an object: {state_path}")

    return {
        "date": str(raw.get("date", date_text)).strip() or date_text,
        "timezone": str(raw.get("timezone", timezone_name)).strip() or timezone_name,
        "section_order": normalize_section_order(raw.get("section_order", [])),
        "today_seen_urls": [
            str(url).strip()
            for url in raw.get("today_seen_urls", [])
            if str(url).strip()
        ],
        "today_first_seen_items": [
            item
            for item in (
                normalize_item(entry) for entry in raw.get("today_first_seen_items", [])
            )
            if item is not None
        ],
        "daily_errors": [
            err
            for err in (normalize_error(entry) for entry in raw.get("daily_errors", []))
            if err is not None
        ],
        "runs": [
            entry
            for entry in raw.get("runs", [])
            if isinstance(entry, dict)
        ],
    }


def build_markdown(
    section_order: list[str],
    items: list[dict[str, str]],
    errors: list[dict[str, str]],
) -> str:
    sorted_section_order = sort_sections(section_order)
    grouped: dict[str, list[dict[str, str]]] = {section: [] for section in sorted_section_order}

    for item in items:
        grouped.setdefault(item["section"], []).append(item)

    sorted_grouped = {
        section: sort_section_items(grouped.get(section, [])) for section in sorted_section_order
    }
    non_empty_sections = [section for section in sorted_section_order if sorted_grouped[section]]
    empty_sections = [section for section in sorted_section_order if not sorted_grouped[section]]

    lines: list[str] = []
    for index, section in enumerate(non_empty_sections):
        section_items = sorted_grouped[section]
        lines.append(f"## {get_section_display_name(section)}（{len(section_items)}条）")
        lines.append("")
        lines.append(f"> {build_section_summary(section_items)}")
        lines.append("")
        for item in section_items:
            lines.append(f"### [{escape_md_title(item['title'])}]({item['url']})")
            quoted_text = str(item.get("quoted_text", "")).strip()
            if quoted_text:
                lines.extend(render_blockquote(quoted_text))
            lines.append(f"- 发布时间：{item['time']}")
            summary = str(item.get("summary", "")).strip()
            if item.get("section") in BLOOMBERG_SECTIONS and summary:
                lines.append(f"- 摘要：{summary}")
            lines.append("")
        if index < len(non_empty_sections) - 1:
            lines.append("---")
            lines.append("")

    lines.append(f"## 本次无更新的分组（{len(empty_sections)}个）")
    lines.append("")
    if empty_sections:
        for section in empty_sections:
            lines.append(f"- {get_section_display_name(section)}")
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## errors")
    lines.append("")
    if errors:
        for index, error in enumerate(errors, start=1):
            lines.append(f"### {index}. {error['section']}")
            if error.get("generated_at"):
                lines.append(f"- 抓取时间：{error['generated_at']}")
            lines.append(f"- 命令：`{error.get('command_str', '')}`")
            lines.append(f"- 错误：{error['error']}")
            lines.append("")
    else:
        lines.append("- 无")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def get_translation_map(path: Path) -> dict[str, dict[str, str]]:
    raw = load_json_file(path)
    if not isinstance(raw, dict):
        raise ValueError("translated-json must contain an object like {\"url\": \"translated title\"}")

    mapping: dict[str, dict[str, str]] = {}
    for url, value in raw.items():
        url_text = str(url).strip()
        if not url_text:
            continue
        if isinstance(value, str):
            title_text = value.strip()
            if not title_text:
                continue
            mapping[url_text] = {"title": title_text}
            continue
        if isinstance(value, dict):
            title_text = str(value.get("title", "")).strip()
            quoted_text = str(value.get("quoted_text", "")).strip()
            quoted_text_zh = str(value.get("quoted_text_zh", "")).strip()
            summary_text = str(value.get("summary", "")).strip()
            summary_zh = str(value.get("summary_zh", "")).strip()
            if not title_text and not quoted_text and not quoted_text_zh and not summary_text and not summary_zh:
                continue
            payload: dict[str, str] = {}
            if title_text:
                payload["title"] = title_text
            if quoted_text:
                payload["quoted_text"] = quoted_text
            if quoted_text_zh:
                payload["quoted_text_zh"] = quoted_text_zh
            if summary_text:
                payload["summary"] = summary_text
            if summary_zh:
                payload["summary_zh"] = summary_zh
            mapping[url_text] = payload
            continue
    return mapping


def translation_required_for_text(policy: str, source_text: str) -> bool:
    if policy == "never":
        return False
    if policy == "always":
        return True
    return bool(source_text.strip()) and (not contains_cjk(source_text))


def has_chinese_translated_title(translated: dict[str, str]) -> bool:
    title = str(translated.get("title", "")).strip()
    return bool(title) and contains_cjk(title)


def has_chinese_translated_quote(translated: dict[str, str]) -> bool:
    quoted_text_zh = str(translated.get("quoted_text_zh", "")).strip()
    quoted_text = str(translated.get("quoted_text", "")).strip()
    if quoted_text_zh and contains_cjk(quoted_text_zh):
        return True
    return bool(quoted_text) and contains_cjk(quoted_text)


def has_chinese_translated_summary(translated: dict[str, str]) -> bool:
    summary_zh = str(translated.get("summary_zh", "")).strip()
    summary = str(translated.get("summary", "")).strip()
    if summary_zh and contains_cjk(summary_zh):
        return True
    return bool(summary) and contains_cjk(summary)


def issue_for_item(
    *,
    item: dict[str, str],
    field: str,
    reason: str,
    source_text: str,
) -> dict[str, str]:
    return {
        "section": item["section"],
        "url": item["url"],
        "field": field,
        "reason": reason,
        "source_text": source_text,
    }


def validate_translations(args: argparse.Namespace) -> int:
    incremental_json_path = Path(args.incremental_json).expanduser().resolve()
    translated_json_path = Path(args.translated_json).expanduser().resolve()

    try:
        raw_payload = load_json_file(incremental_json_path)
    except (FileNotFoundError, ValueError) as exc:
        raise incremental_error("VALIDATE_BAD_INCREMENTAL_JSON", str(exc)) from exc
    if not isinstance(raw_payload, dict):
        raise incremental_error(
            "VALIDATE_BAD_INCREMENTAL_JSON",
            "incremental-json must contain an object",
        )

    try:
        translations = get_translation_map(translated_json_path)
    except (FileNotFoundError, ValueError) as exc:
        raise incremental_error("VALIDATE_BAD_TRANSLATED_JSON", str(exc)) from exc

    items_to_translate = [
        item
        for item in (
            normalize_item(entry) for entry in raw_payload.get("items_to_translate", [])
        )
        if item is not None
    ]

    issues: list[dict[str, str]] = []
    for item in items_to_translate:
        url = item["url"]
        policy = normalize_translation_policy(item.get("translation_policy", "auto"))
        translated = translations.get(url, {})
        raw_title = str(item.get("raw_title", item.get("title", ""))).strip()

        if policy != "never" and url not in translations:
            issues.append(
                issue_for_item(
                    item=item,
                    field="url",
                    reason="missing_translation_entry",
                    source_text=raw_title,
                )
            )
            continue

        if translation_required_for_text(policy, raw_title):
            title_text = str(translated.get("title", "")).strip()
            if not title_text:
                issues.append(
                    issue_for_item(
                        item=item,
                        field="title",
                        reason="missing_translation",
                        source_text=raw_title,
                    )
                )
            elif not has_chinese_translated_title(translated):
                issues.append(
                    issue_for_item(
                        item=item,
                        field="title",
                        reason="non_chinese_translation",
                        source_text=raw_title,
                    )
                )

        quoted_raw = str(item.get("quoted_text_raw", "")).strip()
        if quoted_raw and translation_required_for_text(policy, quoted_raw):
            if not (
                str(translated.get("quoted_text", "")).strip()
                or str(translated.get("quoted_text_zh", "")).strip()
            ):
                issues.append(
                    issue_for_item(
                        item=item,
                        field="quoted_text",
                        reason="missing_translation",
                        source_text=quoted_raw,
                    )
                )
            elif not has_chinese_translated_quote(translated):
                issues.append(
                    issue_for_item(
                        item=item,
                        field="quoted_text",
                        reason="non_chinese_translation",
                        source_text=quoted_raw,
                    )
                )

        summary = str(item.get("summary", "")).strip()
        if (
            item.get("section") in BLOOMBERG_SECTIONS
            and summary
            and translation_required_for_text(policy, summary)
        ):
            if not (
                str(translated.get("summary", "")).strip()
                or str(translated.get("summary_zh", "")).strip()
            ):
                issues.append(
                    issue_for_item(
                        item=item,
                        field="summary",
                        reason="missing_translation",
                        source_text=summary,
                    )
                )
            elif not has_chinese_translated_summary(translated):
                issues.append(
                    issue_for_item(
                        item=item,
                        field="summary",
                        reason="non_chinese_translation",
                        source_text=summary,
                    )
                )

    result = {
        "ok": len(issues) == 0,
        "issue_count": len(issues),
        "issues": issues,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def translated_summary_for(item: dict[str, str], translations: dict[str, dict[str, str]]) -> str:
    translated = translations.get(item["url"], {})
    return (
        str(translated.get("summary_zh", "")).strip()
        or str(translated.get("summary", "")).strip()
    )


def bloomberg_summary_translation_warnings(
    items: list[dict[str, str]],
    translations: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    for item in items:
        if item.get("section") not in BLOOMBERG_SECTIONS:
            continue
        summary = str(item.get("summary", "")).strip()
        if not summary or contains_cjk(summary):
            continue
        translated_summary = translated_summary_for(item, translations)
        if not translated_summary:
            warnings.append(
                {
                    "section": item["section"],
                    "command_str": "",
                    "error": "Bloomberg summary 未翻译，已使用原文摘要：{}".format(
                        item["url"]
                    ),
                }
            )
            continue
        if not contains_cjk(translated_summary):
            warnings.append(
                {
                    "section": item["section"],
                    "command_str": "",
                    "error": "Bloomberg summary 翻译看起来仍非中文，已使用原文摘要：{}".format(
                        item["url"]
                    ),
                }
            )
    return warnings


def final_summary_for(
    item: dict[str, str],
    translations: dict[str, dict[str, str]],
) -> str:
    original_summary = str(item.get("summary", "")).strip()
    translated_summary = translated_summary_for(item, translations)
    if translated_summary and contains_cjk(translated_summary):
        return translated_summary
    return original_summary


def finalize_item(item: dict[str, str], translations: dict[str, dict[str, str]]) -> dict[str, str]:
    translated = translations.get(item["url"], {})
    title = str(translated.get("title", "")).strip() or item["raw_title"]
    summary = final_summary_for(item, translations)
    quoted_text_raw = str(item.get("quoted_text_raw", item.get("quoted_text", ""))).strip()
    quoted_text_direct = str(translated.get("quoted_text", "")).strip()
    quoted_text_zh = str(translated.get("quoted_text_zh", "")).strip()

    if quoted_text_direct:
        quoted_text = quoted_text_direct
    elif quoted_text_zh:
        quoted_text = quoted_text_zh
    else:
        quoted_text = quoted_text_raw

    result = {
        "section": item["section"],
        "title": title,
        "raw_title": item["raw_title"],
        "time": item["time"],
        "url": item["url"],
    }
    if quoted_text:
        result["quoted_text"] = quoted_text
        result["quoted_text_raw"] = quoted_text_raw or quoted_text
    if item.get("author_name"):
        result["author_name"] = str(item.get("author_name", "")).strip()
    if item.get("author_screen_name"):
        result["author_screen_name"] = str(item.get("author_screen_name", "")).strip()
    if summary:
        result["summary"] = summary
    return result


def infer_source_metadata(section: str) -> tuple[str, str]:
    if section in TWITTER_SECTIONS:
        return "twitter", "X"
    if section in BLOOMBERG_SECTIONS:
        return "bloomberg", "Bloomberg"
    if section == "techcrunch":
        return "site", "TechCrunch"
    if section == "arstechnica":
        return "site", "Ars Technica"
    if section in PORTAL_SECTIONS:
        return "reuters", "Reuters"
    return "other", section


def canonicalize_item_url(url: str, source_type: str) -> str:
    if source_type != "twitter":
        return url
    parts = urlsplit(url)
    path_parts = [segment for segment in parts.path.split("/") if segment]
    if len(path_parts) >= 3 and path_parts[1] == "status":
        return urlunsplit(("https", "x.com", f"/{path_parts[0]}/status/{path_parts[2]}", "", ""))
    return urlunsplit(("https", "x.com", parts.path, "", ""))


def sidecar_error_entry(error: dict[str, str]) -> dict[str, str]:
    return {
        "label": error.get("section", ""),
        "time": error.get("generated_at", ""),
        "message": error.get("error", ""),
    }


def build_newsreader_sidecar(
    *,
    schema_version: str,
    date_text: str,
    timezone_name: str,
    generated_at: str,
    source_markdown: Path,
    run_id: str,
    items_raw: list[dict[str, str]],
    items_final: list[dict[str, str]],
    translations: dict[str, dict[str, str]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    final_by_url = {item["url"]: item for item in items_final}
    sidecar_items: list[dict[str, Any]] = []
    for order, raw_item in enumerate(items_raw, start=1):
        final_item = final_by_url.get(raw_item["url"])
        if final_item is None:
            continue
        translated = translations.get(raw_item["url"], {})
        source_type, source_name = infer_source_metadata(raw_item["section"])
        raw_summary = str(raw_item.get("summary", "")).strip()
        quoted_text_raw = str(
            raw_item.get("quoted_text_raw", raw_item.get("quoted_text", ""))
        ).strip()
        sidecar_entry: dict[str, Any] = {
            "item_order": order,
            "section": raw_item["section"],
            "source": SECTION_DISPLAY_NAMES.get(raw_item["section"], raw_item["section"]),
            "source_type": source_type,
            "source_name": source_name,
            "title": final_item["title"],
            "title_raw": raw_item["raw_title"],
            "summary": str(final_item.get("summary", "")).strip(),
            "summary_raw": raw_summary,
            "published_at": raw_item["time"],
            "url": raw_item["url"],
            "canonical_url": canonicalize_item_url(raw_item["url"], source_type),
            "quoted_text": str(final_item.get("quoted_text", "")).strip(),
            "quoted_text_zh": (
                str(translated.get("quoted_text_zh", "")).strip()
                or str(translated.get("quoted_text", "")).strip()
            ),
            "provenance": {
                "translation_policy": normalize_translation_policy(
                    raw_item.get("translation_policy", "auto")
                ),
                "run_id": run_id,
            },
        }
        if quoted_text_raw:
            sidecar_entry["quoted_text_raw"] = quoted_text_raw
        if raw_summary or sidecar_entry["summary"]:
            sidecar_entry["summary_raw"] = raw_summary
        if raw_item.get("author_name"):
            sidecar_entry["author_name"] = str(raw_item.get("author_name", "")).strip()
        if raw_item.get("author_screen_name"):
            sidecar_entry["author_screen_name"] = str(
                raw_item.get("author_screen_name", "")
            ).strip()
        if source_type == "bloomberg":
            sidecar_entry["summary_zh"] = (
                str(translated.get("summary_zh", "")).strip()
                or str(translated.get("summary", "")).strip()
            )
        sidecar_items.append(sidecar_entry)

    return {
        "schema_version": schema_version,
        "date": date_text,
        "timezone": timezone_name,
        "generated_at": generated_at,
        "source_markdown": str(source_markdown),
        "items": sidecar_items,
        "errors": [sidecar_error_entry(error) for error in errors],
    }


def state_path_for_date(state_dir: Path, date_text: str) -> Path:
    return state_dir / f"{date_text}.json"


def prepare_incremental(args: argparse.Namespace) -> int:
    current_json_path = Path(args.current_json).expanduser().resolve()
    out_json_path = Path(args.out_json).expanduser().resolve()
    state_dir = Path(args.state_dir).expanduser().resolve()
    current_run_dir = validate_run_artifact_path(
        current_json_path,
        state_dir,
        label="current-json",
        error_code="PREPARE_BAD_ARTIFACT_PATH",
    )
    incremental_run_dir = validate_run_artifact_path(
        out_json_path,
        state_dir,
        label="out-json",
        error_code="PREPARE_BAD_ARTIFACT_PATH",
    )
    if current_run_dir != incremental_run_dir:
        raise incremental_error(
            "PREPARE_BAD_ARTIFACT_PATH",
            "current-json and out-json must live in the same run artifact directory"
        )
    if current_json_path == out_json_path:
        raise incremental_error(
            "PREPARE_BAD_ARTIFACT_PATH",
            "current-json and out-json must be different files",
        )

    try:
        raw_payload = load_json_file(current_json_path)
    except (FileNotFoundError, ValueError) as exc:
        raise incremental_error("PREPARE_CURRENT_JSON_UNREADABLE", str(exc)) from exc
    if not isinstance(raw_payload, dict):
        raise incremental_error("PREPARE_BAD_CURRENT_JSON", "current-json must contain an object")

    try:
        run_meta = extract_run_metadata(raw_payload, source_label="current-json")
    except ValueError as exc:
        raise incremental_error("PREPARE_BAD_RUN_METADATA", str(exc)) from exc
    current_json_path_text = str(raw_payload.get("current_json_path", "")).strip()
    if current_json_path_text:
        reported_path = Path(current_json_path_text).expanduser().resolve()
        if reported_path != current_json_path:
            raise incremental_error(
                "PREPARE_BAD_ARTIFACT_PATH",
                "current-json payload path does not match the provided current-json argument"
            )

    run_dt = run_meta["generated_at"]
    timezone_name = run_meta["timezone_name"]
    date_text = run_dt.strftime("%Y-%m-%d")
    run_file_timestamp = run_dt.strftime("%Y-%m-%d-%H-%M")
    yesterday_text = (run_dt - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        current_section_order = merge_section_order(
            normalize_section_order(raw_payload.get("section_order", [])),
            section_order_from_items([
                item
                for item in (
                    normalize_item(entry) for entry in raw_payload.get("deduped_items", [])
                )
                if item is not None
            ]),
        )
        current_items = [
            item
            for item in (
                normalize_item(entry) for entry in raw_payload.get("deduped_items", [])
            )
            if item is not None
        ]
        current_errors = [
            err
            for err in (
                normalize_error(entry) for entry in raw_payload.get("errors", [])
            )
            if err is not None
        ]
    except TypeError as exc:
        raise incremental_error("PREPARE_BAD_CURRENT_JSON", str(exc)) from exc
    for error in current_errors:
        error["generated_at"] = run_meta["generated_at_text"]

    today_state_path = state_path_for_date(state_dir, date_text)
    yesterday_state_path = state_path_for_date(state_dir, yesterday_text)
    try:
        today_state = load_state(today_state_path, date_text, timezone_name)
        yesterday_state = load_state(yesterday_state_path, yesterday_text, timezone_name)
        latest_snapshot = latest_run_snapshot(today_state)
    except (FileNotFoundError, TypeError, ValueError) as exc:
        raise incremental_error("PREPARE_BAD_STATE", str(exc)) from exc
    latest_generated_at_text = latest_snapshot["latest_finalized_generated_at"]
    if latest_generated_at_text:
        try:
            latest_generated_at = parse_timestamp(
                latest_generated_at_text,
                field_name="state.latest_finalized_generated_at",
            ).replace(tzinfo=run_meta["timezone"])
        except ValueError as exc:
            raise incremental_error("PREPARE_BAD_STATE", str(exc)) from exc
        if run_meta["generated_at"] <= latest_generated_at:
            raise incremental_error(
                "PREPARE_STALE_CURRENT_JSON",
                "current-json is not newer than the latest finalized run: "
                f"{run_meta['generated_at_text']} <= {latest_generated_at_text}"
            )

    for entry in today_state["runs"]:
        existing_run_id = str(entry.get("run_id", "")).strip()
        existing_generated_at = str(entry.get("generated_at", "")).strip()
        if existing_run_id and existing_run_id == run_meta["run_id"]:
            raise incremental_error(
                "PREPARE_RUN_ID_ALREADY_FINALIZED",
                f"run_id already finalized for today: {run_meta['run_id']}"
            )
        if existing_generated_at == run_meta["generated_at_text"]:
            raise incremental_error(
                "PREPARE_GENERATED_AT_ALREADY_FINALIZED",
                "generated_at already finalized for today: "
                f"{run_meta['generated_at_text']}"
            )

    merged_section_order = merge_section_order(today_state["section_order"], current_section_order)
    today_seen_before = set(today_state["today_seen_urls"])
    yesterday_urls = set(yesterday_state["today_seen_urls"])

    current_run_first_seen_items_raw: list[dict[str, str]] = []
    seen_this_batch: set[str] = set()
    for item in current_items:
        url = item["url"]
        if url in today_seen_before or url in seen_this_batch:
            continue
        seen_this_batch.add(url)
        current_run_first_seen_items_raw.append(item)

    run_fresh_items_raw = [
        item for item in current_run_first_seen_items_raw if item["url"] not in yesterday_urls
    ]

    daily_fresh_urls = {
        item["url"] for item in today_state["today_first_seen_items"] if item["url"] not in yesterday_urls
    }
    daily_fresh_urls.update(item["url"] for item in run_fresh_items_raw)

    daily_errors = today_state["daily_errors"] + current_errors

    payload = {
        "date": date_text,
        "run_id": run_meta["run_id"],
        "started_at": run_meta["started_at_text"],
        "finished_at": run_meta["finished_at_text"],
        "generated_at": run_meta["generated_at_text"],
        "timezone": timezone_name,
        "section_order": merged_section_order,
        "run_file_timestamp": run_file_timestamp,
        "current_run_items_raw": current_items,
        "current_run_first_seen_items_raw": current_run_first_seen_items_raw,
        "run_fresh_items_raw": run_fresh_items_raw,
        "current_run_errors": current_errors,
        "daily_errors": daily_errors,
        "items_to_translate": run_fresh_items_raw,
        "paths": {
            "run_artifact_dir": str(current_run_dir),
            "current_json_path": str(current_json_path),
            "incremental_json_path": str(out_json_path),
        },
        "state_snapshot": {
            **latest_snapshot,
            "prepared_at": format_timestamp(datetime.now(run_meta["timezone"])),
        },
        "state": {
            "today_state_path": str(today_state_path),
            "yesterday_state_path": str(yesterday_state_path),
        },
        "stats": {
            "current_run_count": len(current_items),
            "current_run_first_seen_count": len(current_run_first_seen_items_raw),
            "run_fresh_count": len(run_fresh_items_raw),
            "daily_fresh_count": len(daily_fresh_urls),
            "current_error_count": len(current_errors),
            "daily_error_count": len(daily_errors),
        },
    }
    try:
        write_json_file(out_json_path, payload)
    except Exception as exc:
        raise incremental_error("PREPARE_WRITE_FAILED", str(exc)) from exc
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def finalize_incremental(args: argparse.Namespace) -> int:
    incremental_json_path = Path(args.incremental_json).expanduser().resolve()
    translated_json_path = Path(args.translated_json).expanduser().resolve()
    state_dir = Path(args.state_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    incremental_run_dir = validate_run_artifact_path(
        incremental_json_path,
        state_dir,
        label="incremental-json",
        error_code="FINALIZE_BAD_ARTIFACT_PATH",
    )
    translated_run_dir = validate_run_artifact_path(
        translated_json_path,
        state_dir,
        label="translated-json",
        error_code="FINALIZE_BAD_ARTIFACT_PATH",
    )
    if incremental_run_dir != translated_run_dir:
        raise incremental_error(
            "FINALIZE_BAD_ARTIFACT_PATH",
            "incremental-json and translated-json must live in the same run artifact directory"
        )

    try:
        raw_payload = load_json_file(incremental_json_path)
    except (FileNotFoundError, ValueError) as exc:
        raise incremental_error("FINALIZE_BAD_INCREMENTAL_JSON", str(exc)) from exc
    if not isinstance(raw_payload, dict):
        raise incremental_error(
            "FINALIZE_BAD_INCREMENTAL_JSON",
            "incremental-json must contain an object",
        )

    try:
        run_meta = extract_run_metadata(raw_payload, source_label="incremental-json")
    except ValueError as exc:
        raise incremental_error("FINALIZE_BAD_RUN_METADATA", str(exc)) from exc
    try:
        translations = get_translation_map(translated_json_path)
    except (FileNotFoundError, ValueError) as exc:
        raise incremental_error("FINALIZE_BAD_TRANSLATED_JSON", str(exc)) from exc

    expected_date_text = run_meta["generated_at"].strftime("%Y-%m-%d")
    date_text = str(raw_payload.get("date", expected_date_text)).strip() or expected_date_text
    if date_text != expected_date_text:
        raise incremental_error(
            "FINALIZE_BAD_INCREMENTAL_METADATA",
            f"incremental-json date does not match generated_at: {date_text} != {expected_date_text}"
        )

    timezone_name = run_meta["timezone_name"]
    generated_at = run_meta["generated_at_text"]
    run_id = run_meta["run_id"]
    run_file_timestamp = str(
        raw_payload.get(
            "run_file_timestamp",
            run_meta["generated_at"].strftime("%Y-%m-%d-%H-%M"),
        )
    ).strip()
    if not run_file_timestamp:
        raise incremental_error(
            "FINALIZE_BAD_INCREMENTAL_METADATA",
            "incremental-json is missing run_file_timestamp",
        )
    expected_run_file_timestamp = run_meta["generated_at"].strftime("%Y-%m-%d-%H-%M")
    if run_file_timestamp != expected_run_file_timestamp:
        raise incremental_error(
            "FINALIZE_BAD_INCREMENTAL_METADATA",
            "incremental-json run_file_timestamp does not match generated_at: "
            f"{run_file_timestamp} != {expected_run_file_timestamp}"
        )

    paths_payload = raw_payload.get("paths", {})
    if not isinstance(paths_payload, dict):
        raise incremental_error(
            "FINALIZE_BAD_INCREMENTAL_METADATA",
            "incremental-json paths must contain an object",
        )
    run_artifact_dir_text = str(paths_payload.get("run_artifact_dir", "")).strip()
    current_json_path_text = str(paths_payload.get("current_json_path", "")).strip()
    expected_incremental_json_path_text = str(paths_payload.get("incremental_json_path", "")).strip()
    if not run_artifact_dir_text or not current_json_path_text or not expected_incremental_json_path_text:
        raise incremental_error(
            "FINALIZE_BAD_INCREMENTAL_METADATA",
            "incremental-json is missing required run artifact paths"
        )
    if Path(run_artifact_dir_text).expanduser().resolve() != incremental_run_dir:
        raise incremental_error(
            "FINALIZE_BAD_ARTIFACT_PATH",
            "incremental-json run artifact directory does not match its file location",
        )
    if Path(expected_incremental_json_path_text).expanduser().resolve() != incremental_json_path:
        raise incremental_error(
            "FINALIZE_BAD_ARTIFACT_PATH",
            "incremental-json path does not match the stored prepare output path",
        )

    state_snapshot = raw_payload.get("state_snapshot", {})
    if not isinstance(state_snapshot, dict):
        raise incremental_error(
            "FINALIZE_BAD_INCREMENTAL_METADATA",
            "incremental-json state_snapshot must contain an object",
        )

    today_state_path = state_path_for_date(state_dir, date_text)
    try:
        yesterday_text = (
            datetime.strptime(date_text, "%Y-%m-%d") - timedelta(days=1)
        ).strftime("%Y-%m-%d")
    except ValueError as exc:
        raise incremental_error("FINALIZE_BAD_INCREMENTAL_METADATA", str(exc)) from exc
    yesterday_state_path = state_path_for_date(state_dir, yesterday_text)

    try:
        today_state = load_state(today_state_path, date_text, timezone_name)
        yesterday_state = load_state(yesterday_state_path, yesterday_text, timezone_name)
        latest_snapshot_now = latest_run_snapshot(today_state)
    except (FileNotFoundError, TypeError, ValueError) as exc:
        raise incremental_error("FINALIZE_BAD_STATE", str(exc)) from exc
    if (
        latest_snapshot_now["latest_finalized_generated_at"]
        != str(state_snapshot.get("latest_finalized_generated_at", "")).strip()
        or latest_snapshot_now["latest_finalized_run_id"]
        != str(state_snapshot.get("latest_finalized_run_id", "")).strip()
    ):
        raise incremental_error(
            "FINALIZE_STATE_CHANGED_SINCE_PREPARE",
            "State changed since prepare; rerun prepare with the latest daily state before finalize"
        )

    for entry in today_state["runs"]:
        existing_run_id = str(entry.get("run_id", "")).strip()
        existing_generated_at = str(entry.get("generated_at", "")).strip()
        if existing_run_id and existing_run_id == run_id:
            raise incremental_error(
                "FINALIZE_RUN_ALREADY_FINALIZED",
                f"Run already finalized for run_id: {run_id}",
            )
        if existing_generated_at == generated_at:
            raise incremental_error(
                "FINALIZE_RUN_ALREADY_FINALIZED",
                f"Run already finalized for generated_at: {generated_at}",
            )

    merged_section_order = merge_section_order(
        today_state["section_order"],
        normalize_section_order(raw_payload.get("section_order", [])),
        section_order_from_items(today_state["today_first_seen_items"]),
    )

    current_run_items_raw = [
        item
        for item in (
            normalize_item(entry) for entry in raw_payload.get("current_run_items_raw", [])
        )
        if item is not None
    ]
    current_run_first_seen_items_raw = [
        item
        for item in (
            normalize_item(entry)
            for entry in raw_payload.get("current_run_first_seen_items_raw", [])
        )
        if item is not None
    ]
    run_fresh_items_raw = [
        item
        for item in (
            normalize_item(entry) for entry in raw_payload.get("run_fresh_items_raw", [])
        )
        if item is not None
    ]
    current_run_errors = [
        err
        for err in (
            normalize_error(entry) for entry in raw_payload.get("current_run_errors", [])
        )
        if err is not None
    ]
    daily_errors = [
        err
        for err in (
            normalize_error(entry) for entry in raw_payload.get("daily_errors", [])
        )
        if err is not None
    ]
    bloomberg_warnings = bloomberg_summary_translation_warnings(
        run_fresh_items_raw,
        translations,
    )
    for warning in bloomberg_warnings:
        warning["generated_at"] = generated_at
    current_run_errors.extend(bloomberg_warnings)
    daily_errors.extend(bloomberg_warnings)

    today_seen_urls = list(today_state["today_seen_urls"])
    today_seen_set = set(today_seen_urls)
    for item in current_run_items_raw:
        url = item["url"]
        if url in today_seen_set:
            continue
        today_seen_set.add(url)
        today_seen_urls.append(url)

    today_first_seen_items = list(today_state["today_first_seen_items"])
    today_first_seen_index = {
        item["url"]: index for index, item in enumerate(today_first_seen_items)
    }
    for item in current_run_first_seen_items_raw:
        finalized = finalize_item(item, translations)
        url = item["url"]
        if url in today_first_seen_index:
            today_first_seen_items[today_first_seen_index[url]] = finalized
            continue
        today_first_seen_index[url] = len(today_first_seen_items)
        today_first_seen_items.append(finalized)

    yesterday_urls = set(yesterday_state["today_seen_urls"])
    daily_fresh_items_final = [
        item for item in today_first_seen_items if item["url"] not in yesterday_urls
    ]
    run_fresh_items_final = [finalize_item(item, translations) for item in run_fresh_items_raw]

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise incremental_error("FINALIZE_WRITE_FAILED", str(exc)) from exc
    run_fresh_path = out_dir / f"{run_file_timestamp}_freshNews.md"
    daily_fresh_path = out_dir / f"dailyFreshNews_{date_text}.md"
    run_sidecar_path = out_dir / f"{run_file_timestamp}_freshNews.newsreader.json"
    daily_sidecar_path = out_dir / f"dailyFreshNews_{date_text}.newsreader.json"
    if run_fresh_path.exists():
        raise incremental_error(
            "FINALIZE_OUTPUT_EXISTS",
            f"Refusing to overwrite existing run file: {run_fresh_path}",
        )

    run_fresh_markdown = build_markdown(
        merged_section_order,
        run_fresh_items_final,
        current_run_errors,
    )
    daily_fresh_markdown = build_markdown(
        merged_section_order,
        daily_fresh_items_final,
        daily_errors,
    )
    run_sidecar_payload = build_newsreader_sidecar(
        schema_version="newsreader.daily.v1",
        date_text=date_text,
        timezone_name=timezone_name,
        generated_at=generated_at,
        source_markdown=run_fresh_path,
        run_id=run_id,
        items_raw=run_fresh_items_raw,
        items_final=run_fresh_items_final,
        translations=translations,
        errors=current_run_errors,
    )
    daily_sidecar_payload = build_newsreader_sidecar(
        schema_version="newsreader.daily.v1",
        date_text=date_text,
        timezone_name=timezone_name,
        generated_at=generated_at,
        source_markdown=daily_fresh_path,
        run_id=run_id,
        items_raw=daily_fresh_items_final,
        items_final=daily_fresh_items_final,
        translations=translations,
        errors=daily_errors,
    )
    try:
        write_text_new_file(run_fresh_path, run_fresh_markdown)
        write_text_atomic(daily_fresh_path, daily_fresh_markdown)
        write_text_new_file(
            run_sidecar_path,
            json.dumps(run_sidecar_payload, ensure_ascii=False, indent=2) + "\n",
        )
        write_text_atomic(
            daily_sidecar_path,
            json.dumps(daily_sidecar_payload, ensure_ascii=False, indent=2) + "\n",
        )
    except FileExistsError as exc:
        raise incremental_error("FINALIZE_OUTPUT_EXISTS", str(exc)) from exc
    except Exception as exc:
        raise incremental_error("FINALIZE_WRITE_FAILED", str(exc)) from exc

    runs = list(today_state["runs"])
    finalized_at = format_timestamp(datetime.now(run_meta["timezone"]))
    runs.append(
        {
            "run_id": run_id,
            "started_at": run_meta["started_at_text"],
            "finished_at": run_meta["finished_at_text"],
            "generated_at": generated_at,
            "prepared_at": str(state_snapshot.get("prepared_at", "")).strip(),
            "finalized_at": finalized_at,
            "run_artifact_dir": str(incremental_run_dir),
            "current_json_path": current_json_path_text,
            "incremental_json_path": str(incremental_json_path),
            "translated_json_path": str(translated_json_path),
            "run_fresh_path": str(run_fresh_path),
            "daily_fresh_path": str(daily_fresh_path),
            "run_sidecar_path": str(run_sidecar_path),
            "daily_sidecar_path": str(daily_sidecar_path),
            "run_fresh_count": len(run_fresh_items_final),
            "daily_fresh_count": len(daily_fresh_items_final),
            "error_count": len(current_run_errors),
        }
    )

    state_payload = {
        "date": date_text,
        "timezone": timezone_name,
        "section_order": merged_section_order,
        "today_seen_urls": today_seen_urls,
        "today_first_seen_items": today_first_seen_items,
        "daily_errors": daily_errors,
        "runs": runs,
    }
    try:
        write_json_file(today_state_path, state_payload)
    except Exception as exc:
        raise incremental_error("FINALIZE_WRITE_FAILED", str(exc)) from exc

    result = {
        "date": date_text,
        "run_id": run_id,
        "started_at": run_meta["started_at_text"],
        "finished_at": run_meta["finished_at_text"],
        "generated_at": generated_at,
        "timezone": timezone_name,
        "section_order": merged_section_order,
        "run_artifact_dir": str(incremental_run_dir),
        "run_fresh_path": str(run_fresh_path),
        "daily_fresh_path": str(daily_fresh_path),
        "run_sidecar_path": str(run_sidecar_path),
        "daily_sidecar_path": str(daily_sidecar_path),
        "state_path": str(today_state_path),
        "stats": {
            "run_fresh_count": len(run_fresh_items_final),
            "daily_fresh_count": len(daily_fresh_items_final),
            "today_seen_url_count": len(today_seen_urls),
            "daily_error_count": len(daily_errors),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare and finalize incremental daily news outputs without doing translation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Compute daily and per-run fresh news diffs")
    prepare.add_argument("--current-json", required=True, help="Pipeline JSON from run_news_pipeline.py")
    prepare.add_argument("--state-dir", required=True, help="Directory for per-day state JSON files")
    prepare.add_argument("--out-json", required=True, help="Output incremental JSON path")
    prepare.set_defaults(handler=prepare_incremental)

    validate = subparsers.add_parser(
        "validate-translations",
        help="Validate translated-json coverage and required translated fields",
    )
    validate.add_argument("--incremental-json", required=True, help="Incremental JSON from prepare")
    validate.add_argument("--translated-json", required=True, help="Model-produced translation map JSON")
    validate.set_defaults(handler=validate_translations)

    finalize = subparsers.add_parser("finalize", help="Write markdown outputs and update daily state")
    finalize.add_argument("--incremental-json", required=True, help="Incremental JSON from prepare")
    finalize.add_argument("--translated-json", required=True, help="Model-produced translation map JSON")
    finalize.add_argument("--state-dir", required=True, help="Directory for per-day state JSON files")
    finalize.add_argument("--out-dir", required=True, help="Directory for markdown outputs")
    finalize.set_defaults(handler=finalize_incremental)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.handler(args)
    except Exception as exc:
        print(f"Incremental news error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
