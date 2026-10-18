#!/usr/bin/env python3
import argparse
import json
import math
import random
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

_previous_dont_write_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
from newsflow_common import (  # noqa: E402
    VALID_TRANSLATION_POLICIES,
    format_timestamp,
    normalize_time,
    write_json_file,
)
sys.dont_write_bytecode = _previous_dont_write_bytecode


NOISE_PREFIXES = (
    "(node:",
    "Reparsing as ES module",
    "To eliminate this warning",
    "(Use `node --trace-warnings",
)
DISPLAY_TIME_FORMAT = "%Y-%m-%d %H:%M"
RETRY_DELAY_SECONDS = 5.0
RETRY_JITTER_SECONDS = 2.0
MAX_RETRY_WAIT_SECONDS = 30.0
TRANSIENT_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
TERMINAL_ERROR_CODES = {
    "AUTH_REQUIRED", "ARGUMENT", "CONFIG", "NOT_FOUND", "INVALID_FEED", "EMPTY_RESULT",
    "INVALID_JSON", "UNSUPPORTED_URL", "CONTENT_TOO_SHORT", "CONTENT_UNAVAILABLE",
    "RETRY_AFTER", "INVALID_RETRY_METADATA",
}


def make_run_id(started_at: datetime) -> str:
    return started_at.strftime("%Y%m%d-%H%M%S-%f")


def normalize_twitter_local_time(raw_time: Any, *, output_timezone: str) -> str:
    text = str(raw_time or "").strip()
    if not text:
        return "页面未显示"

    candidates: list[datetime] = []
    try:
        candidates.append(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        pass
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%d %H:%M"):
        try:
            candidates.append(datetime.strptime(text, fmt))
        except ValueError:
            continue

    for parsed in candidates:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(output_timezone))
        return parsed.astimezone(ZoneInfo(output_timezone)).strftime(DISPLAY_TIME_FORMAT)

    return normalize_time(raw_time)


def canonicalize_twitter_url(*, tweet_id: str, screen_name: str, fallback_url: Any) -> str:
    cleaned_id = str(tweet_id or "").strip()
    cleaned_screen_name = str(screen_name or "").strip().lstrip("@")
    if cleaned_id and cleaned_screen_name:
        return f"https://x.com/{cleaned_screen_name}/status/{cleaned_id}?s=20"

    raw_url = str(fallback_url or "").strip()
    if not raw_url:
        return ""

    parsed = urlparse(raw_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 3 and path_parts[1] == "status":
        return f"https://x.com/{path_parts[0]}/status/{path_parts[2]}?s=20"
    return raw_url


def summarize_error(stdout_text: str, stderr_text: str, returncode: int) -> str:
    lines: list[str] = []
    for line in (stderr_text + "\n" + stdout_text).splitlines():
        clean = line.strip()
        if not clean:
            continue
        if "MODULE_TYPELESS_PACKAGE_JSON" in clean:
            continue
        if clean.startswith(NOISE_PREFIXES):
            continue
        lines.append(clean)

    if lines:
        preferred = [
            line
            for line in lines
            if ("error" in line.lower()) or ("unknown" in line.lower())
        ]
        if preferred:
            return preferred[-1]
        return lines[-1]
    return f"exit code {returncode}"


def parse_json_array(stdout_text: str) -> list[dict[str, Any]]:
    text = stdout_text.strip()
    if not text:
        raise ValueError("empty stdout")

    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
    except Exception:
        pass

    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if not line.lstrip().startswith("["):
            continue
        candidate = "\n".join(lines[idx:]).strip()
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return [row for row in data if isinstance(row, dict)]
        except Exception:
            continue

    raise ValueError("stdout does not contain a valid JSON array")


def parse_json_items(stdout_text: str) -> list[dict[str, Any]]:
    text = stdout_text.strip()
    if not text:
        raise ValueError("empty stdout")

    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            rows = data.get("data")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
            raise ValueError("JSON object is missing list field 'data'")
    except json.JSONDecodeError:
        pass

    return parse_json_array(stdout_text)


def parse_command(raw_command: Any) -> list[str]:
    if isinstance(raw_command, list):
        parts = [str(part).strip() for part in raw_command]
        parts = [part for part in parts if part]
        if not parts:
            raise ValueError("command list is empty")
        return parts

    if isinstance(raw_command, str):
        parts = shlex.split(raw_command)
        if not parts:
            raise ValueError("command string is empty")
        return parts

    raise ValueError("command must be list[str] or string")


def compact_text(raw: Any) -> str:
    text = str(raw or "").strip()
    return " ".join(text.split())


def source_defaults(
    section: str,
    command: list[str],
    source_type: str,
    source_name: str,
    display_name: str,
) -> tuple[str, str, str]:
    """Fill legacy config metadata without coupling incremental logic to section lists."""
    resolved_type = source_type
    if not resolved_type:
        if command[:3] == ["opencli", "twitter", "tweets"]:
            resolved_type = "twitter"
        elif len(command) > 1 and command[1] == "ReutersBrowser":
            resolved_type = "reuters"
        elif len(command) > 1 and command[1] == "BloombergUser":
            resolved_type = "bloomberg"
        else:
            resolved_type = "site"

    default_names = {
        "reuters": "Reuters",
        "bloomberg": "Bloomberg",
        "twitter": section,
    }
    resolved_name = source_name or default_names.get(resolved_type, section)
    resolved_display = display_name or resolved_name
    return resolved_type, resolved_name, resolved_display


def normalize_row(
    section: str,
    row: dict[str, Any],
    *,
    translation_policy: str,
    output_timezone: str,
    source_type: str = "",
    source_handle: str = "",
    source_name: str = "",
) -> dict[str, str] | None:
    title = str(row.get("title", "")).strip()
    url = str(row.get("url") or row.get("link") or "").strip()
    summary = compact_text(row.get("summary") or row.get("description") or "")
    raw_time = row.get("time")
    if raw_time is None or not str(raw_time).strip():
        for fallback_key in (
            "publishedAt",
            "pubDate",
            "date",
            "published_at",
            "createdAt",
            "created_at",
        ):
            candidate = row.get(fallback_key)
            if candidate is None:
                continue
            if not str(candidate).strip():
                continue
            raw_time = candidate
            break
    if title and url:
        item = {
            "section": section,
            "title": title,
            "time": normalize_time(raw_time),
            "url": url,
            "translation_policy": translation_policy,
        }
        if summary:
            item["summary"] = summary
        if source_type:
            item["source_type"] = source_type
        if source_handle:
            item["source_handle"] = source_handle
        if source_name:
            item["source_name"] = source_name
        return item

    tweet_id = str(row.get("id", "")).strip()
    tweet_text = compact_text(row.get("text", ""))
    author = row.get("author")
    if isinstance(author, dict):
        screen_name = str(author.get("screenName", "")).strip()
        author_name = str(author.get("name", "")).strip()
    else:
        screen_name = str(author or "").strip().lstrip("@")
        author_name = str(row.get("name", "")).strip()

    if not tweet_id or not tweet_text:
        return None

    url = canonicalize_twitter_url(
        tweet_id=tweet_id,
        screen_name=screen_name,
        fallback_url=row.get("url"),
    )
    if not url:
        return None

    quote_text = ""
    quoted = row.get("quotedTweet")
    if not isinstance(quoted, dict):
        quoted = row.get("quoted_tweet")
    if isinstance(quoted, dict):
        quote_text = compact_text(quoted.get("text", ""))

    item = {
        "section": section,
        "title": tweet_text,
        "time": normalize_twitter_local_time(
            row.get("createdAtLocal") or row.get("created_at"),
            output_timezone=output_timezone,
        ),
        "url": url,
        "translation_policy": translation_policy,
        "author_name": author_name,
        "author_screen_name": screen_name,
        "quoted_text_raw": quote_text,
    }
    if source_type:
        item["source_type"] = source_type
    if source_handle:
        item["source_handle"] = source_handle
    if source_name:
        item["source_name"] = source_name
    return item


def parse_positive_int(raw: Any, default: int) -> int:
    try:
        value = int(raw)
        if value > 0:
            return value
    except Exception:
        pass
    return default


def structured_command_error(stdout_text: str) -> dict[str, Any] | None:
    """Read opencli 2.x errors even when a command mistakenly exits with zero."""
    try:
        payload = json.loads(stdout_text)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("ok") is not False:
        return None
    error = payload.get("error")
    return error if isinstance(error, dict) else {"code": "OPENCLI_ERROR", "message": str(error or "Command failed")}


def parse_retry_at(value: Any) -> float | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("retry_at must be an ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("retry_at must include a timezone")
    timestamp = parsed.timestamp()
    if not math.isfinite(timestamp):
        raise ValueError("retry_at must be finite")
    return timestamp


def classify_command_error(error: dict[str, Any] | None, message: str) -> dict[str, Any]:
    error = error or {}
    code = str(error.get("code") or "UNKNOWN").upper()
    status = None
    retry_at = None
    metadata = None
    try:
        candidate = json.loads(error.get("cause", ""))
        if isinstance(candidate, dict) and candidate.get("schema") == "newsflow.rss-error/v1":
            metadata = candidate
    except (ValueError, TypeError):
        pass
    if metadata is not None:
        try:
            status = metadata.get("http_status")
            if status is not None and (type(status) is not int or not 100 <= status <= 599):
                raise ValueError("invalid HTTP status")
            if type(metadata.get("retryable")) is not bool:
                raise ValueError("invalid retryable flag")
            retry_at = metadata.get("retry_at")
            parse_retry_at(retry_at)
        except (ValueError, OverflowError, OSError):
            return {"error_code": "INVALID_RETRY_METADATA", "retryable": False, "http_status": status, "retry_at": None}
        retryable = metadata["retryable"]
        if status is not None:
            retryable = retryable and status in TRANSIENT_HTTP_STATUSES
    else:
        match = re.search(r"\bHTTP(?:\s+(?:status|error))?\s*[:=]?\s*(\d{3})\b", message, re.I)
        status = int(match[1]) if match else None
        retryable = code in {"TIMEOUT", "NETWORK_ERROR", "BROWSER_CONNECT", "ECONNRESET", "ETIMEDOUT"}
        if status is not None:
            retryable = status in TRANSIENT_HTTP_STATUSES
        if status is None and code in {"UNKNOWN", "COMMAND_EXEC"} and re.search(
            r"navigation rejected|browser connection dropped|connection (?:reset|closed)|timed? out|\bECONNRESET\b|\bETIMEDOUT\b", message, re.I
        ):
            retryable = True
    if code in TERMINAL_ERROR_CODES or re.search(r"captcha|human verification|bot.protection|paywall|subscription required", message, re.I):
        retryable = False
    return {"error_code": code, "retryable": retryable, "http_status": status, "retry_at": retry_at}


def retry_delay(attempt: dict[str, Any]) -> float | None:
    if not attempt.get("retryable", False):
        return None
    delay = RETRY_DELAY_SECONDS + random.uniform(0, RETRY_JITTER_SECONDS)
    until = parse_retry_at(attempt.get("retry_at"))
    if until is not None:
        delay = max(delay, until - time.time())
    # Never shorten a server cooldown just to fit the local waiting budget.
    return delay if delay <= MAX_RETRY_WAIT_SECONDS else None


def wait_for_retry(delay: float, retry_at: str | None) -> tuple[float, bool]:
    started = time.monotonic()
    ready = started + delay
    deadline = started + MAX_RETRY_WAIT_SECONDS
    until = parse_retry_at(retry_at)
    while True:
        now = time.monotonic()
        remaining = max(ready - now, (until - time.time()) if until is not None else 0.0)
        if remaining <= 0:
            return now - started, True
        if now + remaining > deadline:
            return now - started, False
        time.sleep(remaining)


def execute_command_once(
    *,
    section: str,
    command: list[str],
    timeout_seconds: int,
    min_valid_items: int,
    treat_empty_as_failure: bool,
    translation_policy: str,
    output_timezone: str,
    source_type: str,
    source_handle: str,
    source_name: str,
) -> dict[str, Any]:
    command_str = " ".join(shlex.quote(part) for part in command)

    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "command": command,
            "command_str": command_str,
            "error": f"timeout after {timeout_seconds}s",
            "failed_reason": "timeout",
            "error_code": "TIMEOUT", "retryable": True, "retry_at": None,
            "items": [],
            "raw_count": 0,
            "valid_count": 0,
        }

    except OSError as exc:
        return {
            "ok": False, "command": command, "command_str": command_str,
            "error": str(exc), "failed_reason": "command_unavailable",
            "error_code": "COMMAND_UNAVAILABLE", "retryable": False,
            "items": [], "raw_count": 0, "valid_count": 0,
        }

    structured = structured_command_error(proc.stdout)
    if proc.returncode != 0 or structured is not None:
        message = (str(structured.get("message") or structured.get("code") or "Command failed")
                   if structured is not None else summarize_error(proc.stdout, proc.stderr, proc.returncode))
        if structured and structured.get("hint"):
            message += " | " + str(structured["hint"])
        return {
            "ok": False,
            "command": command,
            "command_str": command_str,
            "error": message,
            "failed_reason": "non_zero_exit" if proc.returncode else "structured_error",
            "items": [],
            "raw_count": 0,
            "valid_count": 0,
            **classify_command_error(structured, message),
        }

    try:
        rows = parse_json_items(proc.stdout)
    except Exception as exc:
        return {
            "ok": False,
            "command": command,
            "command_str": command_str,
            "error": f"JSON parse error: {exc}",
            "failed_reason": "json_parse_error",
            "error_code": "INVALID_JSON", "retryable": False,
            "items": [],
            "raw_count": 0,
            "valid_count": 0,
        }

    items: list[dict[str, str]] = []
    for row in rows:
        normalized = normalize_row(
            section,
            row,
            translation_policy=translation_policy,
            output_timezone=output_timezone,
            source_type=source_type,
            source_handle=source_handle,
            source_name=source_name,
        )
        if normalized is None:
            continue
        items.append(normalized)

    valid_count = len(items)
    if treat_empty_as_failure and valid_count < min_valid_items:
        return {
            "ok": False,
            "command": command,
            "command_str": command_str,
            "error": (
                f"normalized valid items below threshold: "
                f"{valid_count} < {min_valid_items}"
            ),
            "failed_reason": "below_min_valid_items",
            "error_code": "EMPTY_RESULT", "retryable": False,
            "items": items,
            "raw_count": len(rows),
            "valid_count": valid_count,
        }

    return {
        "ok": True,
        "command": command,
        "command_str": command_str,
        "error": "",
        "failed_reason": "",
        "items": items,
        "raw_count": len(rows),
        "valid_count": valid_count,
    }


def build_recovered_error(
    *,
    section: str,
    primary_command: list[str],
    primary_attempts: list[dict[str, Any]],
    success_attempt: dict[str, Any],
    used_fallback: bool,
) -> dict[str, Any]:
    primary_command_str = " ".join(shlex.quote(part) for part in primary_command)
    failed_reasons = [
        attempt["failed_reason"] or "unknown"
        for attempt in primary_attempts
        if not attempt["ok"]
    ]
    failed_reason_text = "; ".join(failed_reasons) if failed_reasons else "unknown"

    if used_fallback:
        recovered_error = (
            f"已恢复：主命令失败（尝试 {len(primary_attempts)} 次，原因：{failed_reason_text}）；"
            f"fallback 成功：{success_attempt['command_str']}"
        )
    else:
        success_index = next(
            (
                index
                for index, attempt in enumerate(primary_attempts, start=1)
                if attempt["ok"]
            ),
            len(primary_attempts),
        )
        recovered_error = (
            f"已恢复：主命令失败（原因：{failed_reason_text}），"
            f"第 {success_index} 次重试成功"
        )

    return {
        "section": section,
        "command": primary_command,
        "command_str": primary_command_str,
        "error": recovered_error,
        "recovered": True,
    }


def load_config(config_path: Path) -> list[dict[str, Any]]:
    if not config_path.exists():
        raise FileNotFoundError(f"config not found: {config_path}")

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("config root must be an array")

    parsed: list[dict[str, Any]] = []
    seen_sections: set[str] = set()
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"config item #{i} must be object")

        section = str(item.get("section", "")).strip()
        if not section:
            raise ValueError(f"config item #{i} missing section")
        if section in seen_sections:
            raise ValueError(f"config item #{i} duplicates section: {section}")
        seen_sections.add(section)

        command = parse_command(item.get("command"))

        fallback_raw = item.get("fallback_command")
        fallback_command = (
            parse_command(fallback_raw) if fallback_raw is not None else None
        )

        command_timeout = item.get("command_timeout_seconds")
        if command_timeout is not None and (type(command_timeout) is not int or command_timeout <= 0):
            raise ValueError(f"config item #{i} command_timeout_seconds must be a positive integer")
        retry_once = bool(item.get("retry_once", False))
        treat_empty_as_failure = bool(item.get("treat_empty_as_failure", False))
        min_valid_items = parse_positive_int(item.get("min_valid_items", 1), default=1)
        translation_policy = str(item.get("translation_policy", "auto")).strip().lower() or "auto"
        if translation_policy not in VALID_TRANSLATION_POLICIES:
            raise ValueError(
                f"config item #{i} has invalid translation_policy: {translation_policy}"
            )
        source_type = str(item.get("source_type", "")).strip().lower()
        source_handle = str(item.get("source_handle", "")).strip().lstrip("@")
        source_name = str(item.get("source_name", "")).strip()
        display_name = str(item.get("display_name", "")).strip()
        command_is_twitter = command[:3] == ["opencli", "twitter", "tweets"]
        fallback_is_twitter = (
            fallback_command is not None
            and fallback_command[:2] == ["twitter", "user-posts"]
        )
        if (command_is_twitter or fallback_is_twitter) and source_type != "twitter":
            raise ValueError(
                f"config item #{i} Twitter command requires source_type=twitter"
            )
        source_type, source_name, display_name = source_defaults(
            section, command, source_type, source_name, display_name
        )
        if source_type == "twitter" and (not source_handle or not source_name):
            raise ValueError(
                f"config item #{i} with source_type=twitter requires source_handle and source_name"
            )
        if command_is_twitter and len(command) > 3 and command[3] != source_handle:
            raise ValueError(
                f"config item #{i} source_handle must match Twitter command handle"
            )
        if (
            fallback_is_twitter
            and fallback_command is not None
            and len(fallback_command) > 2
            and fallback_command[2] != source_handle
        ):
            raise ValueError(
                f"config item #{i} source_handle must match Twitter fallback handle"
            )

        parsed.append(
            {
                "section": section,
                "command": command,
                "fallback_command": fallback_command,
                "retry_once": retry_once,
                "command_timeout_seconds": command_timeout,
                "treat_empty_as_failure": treat_empty_as_failure,
                "min_valid_items": min_valid_items,
                "translation_policy": translation_policy,
                "source_type": source_type,
                "source_handle": source_handle,
                "source_name": source_name,
                "display_name": display_name,
            }
        )

    return parsed


def run_pipeline(
    entries: list[dict[str, Any]],
    timeout_seconds: int,
    output_timezone: str,
) -> dict[str, Any]:
    section_order: list[str] = []
    section_items: dict[str, list[dict[str, str]]] = {}
    errors: list[dict[str, Any]] = []
    recovered_attempts: list[dict[str, Any]] = []
    timing_rows: list[dict[str, Any]] = []
    pipeline_started_monotonic = time.monotonic()
    section_metadata = {
        entry["section"]: {
            "display_name": entry["display_name"],
            "source_type": entry["source_type"],
            "source_name": entry["source_name"],
            "source_handle": entry["source_handle"],
            "translation_policy": entry["translation_policy"],
        }
        for entry in entries
    }

    for entry in entries:
        section_started_monotonic = time.monotonic()
        section = entry["section"]
        command = entry["command"]
        fallback_command = entry.get("fallback_command")
        retry_once = bool(entry.get("retry_once", False))
        treat_empty_as_failure = bool(entry.get("treat_empty_as_failure", False))
        min_valid_items = parse_positive_int(entry.get("min_valid_items", 1), default=1)
        translation_policy = str(entry.get("translation_policy", "auto")).strip().lower() or "auto"
        source_type = str(entry.get("source_type", "")).strip().lower()
        source_handle = str(entry.get("source_handle", "")).strip().lstrip("@")
        source_name = str(entry.get("source_name", "")).strip()

        if section not in section_order:
            section_order.append(section)
            section_items[section] = []

        source_timeout = min(timeout_seconds, entry.get("command_timeout_seconds") or timeout_seconds)
        primary_attempts: list[dict[str, Any]] = []
        retry_wait_seconds = 0.0
        retry_skip_reason = ""
        first = execute_command_once(
            section=section,
            command=command,
            timeout_seconds=source_timeout,
            min_valid_items=min_valid_items,
            treat_empty_as_failure=treat_empty_as_failure,
            translation_policy=translation_policy,
            output_timezone=output_timezone,
            source_type=source_type,
            source_handle=source_handle,
            source_name=source_name,
        )
        primary_attempts.append(first)

        delay = retry_delay(first) if (not first["ok"]) and retry_once else None
        if (not first["ok"]) and retry_once and delay is None:
            retry_skip_reason = "server_cooldown_exceeds_budget" if first.get("retryable") else "non_retryable_error"
        if delay is not None:
            print(f"[retry] section={section} wait_seconds={delay:.3f} code={first.get('error_code', 'UNKNOWN')}", file=sys.stderr)
            retry_wait_seconds, ready = wait_for_retry(delay, first.get("retry_at"))
            if not ready:
                retry_skip_reason = "server_cooldown_exceeds_budget"
            else:
                second = execute_command_once(
                    section=section,
                    command=command,
                    timeout_seconds=source_timeout,
                    min_valid_items=min_valid_items,
                    treat_empty_as_failure=treat_empty_as_failure,
                    translation_policy=translation_policy,
                    output_timezone=output_timezone,
                    source_type=source_type,
                    source_handle=source_handle,
                    source_name=source_name,
                )
                primary_attempts.append(second)

        success_attempt = next((a for a in primary_attempts if a["ok"]), None)
        used_fallback = False
        fallback_attempt: dict[str, Any] | None = None

        cooldown_until = parse_retry_at(primary_attempts[-1].get("retry_at"))
        if success_attempt is None and fallback_command and (cooldown_until is None or cooldown_until <= time.time()):
            fallback_attempt = execute_command_once(
                section=section,
                command=fallback_command,
                timeout_seconds=timeout_seconds,
                min_valid_items=min_valid_items,
                treat_empty_as_failure=treat_empty_as_failure,
                translation_policy=translation_policy,
                output_timezone=output_timezone,
                source_type=source_type,
                source_handle=source_handle,
                source_name=source_name,
            )
            if fallback_attempt["ok"]:
                success_attempt = fallback_attempt
                used_fallback = True

        section_duration_ms = int((time.monotonic() - section_started_monotonic) * 1000)
        attempts_count = len(primary_attempts) + (1 if fallback_attempt is not None else 0)

        if success_attempt is not None:
            section_items[section].extend(success_attempt["items"])
            if used_fallback:
                status = "recovered_fallback"
            elif len(primary_attempts) > 1:
                status = "recovered_retry"
            else:
                status = "success"

            print(
                (
                    f"[timing] section={section} status={status} "
                    f"duration_ms={section_duration_ms} attempts={attempts_count} "
                    f"items={len(success_attempt['items'])}"
                ),
                file=sys.stderr,
            )
            timing_rows.append(
                {
                    "section": section,
                    "status": status,
                    "duration_ms": section_duration_ms,
                    "attempts": attempts_count,
                    "items": len(success_attempt["items"]),
                    "retry_wait_seconds": round(retry_wait_seconds, 3),
                }
            )
            if (len(primary_attempts) > 1) or used_fallback:
                recovered_attempts.append(
                    {
                        "section": section,
                        "primary_command": command,
                        "primary_attempts": len(primary_attempts),
                        "primary_fail_reasons": [
                            a["failed_reason"] for a in primary_attempts if not a["ok"]
                        ],
                        "fallback_command": fallback_command,
                        "used_fallback": used_fallback,
                        "used_command": success_attempt["command"],
                    }
                )
                errors.append(
                    build_recovered_error(
                        section=section,
                        primary_command=command,
                        primary_attempts=primary_attempts,
                        success_attempt=success_attempt,
                        used_fallback=used_fallback,
                    )
                )
            continue

        final_error = ""
        final_command = command
        final_command_str = " ".join(shlex.quote(part) for part in command)
        if fallback_attempt is not None:
            final_error = (
                f"primary failed ({'; '.join(a['failed_reason'] or 'unknown' for a in primary_attempts)}); "
                f"fallback failed: {fallback_attempt['error']}"
            )
            final_command = fallback_command
            final_command_str = " ".join(shlex.quote(part) for part in fallback_command)
        else:
            last_primary = primary_attempts[-1]
            final_error = last_primary["error"]
            final_command = last_primary["command"]
            final_command_str = last_primary["command_str"]

        final_attempt = fallback_attempt or primary_attempts[-1]
        if final_attempt.get("retry_at"):
            final_error += f"; server cooldown until {final_attempt['retry_at']}"
        if retry_skip_reason == "server_cooldown_exceeds_budget":
            final_error += f"; automatic retry skipped (maximum wait {MAX_RETRY_WAIT_SECONDS:g}s)"

        print(
            (
                f"[timing] section={section} status=failed "
                f"duration_ms={section_duration_ms} attempts={attempts_count} "
                f"items=0 error={json.dumps(final_error, ensure_ascii=False)}"
            ),
            file=sys.stderr,
        )
        timing_rows.append(
            {
                "section": section,
                "status": "failed",
                "duration_ms": section_duration_ms,
                "attempts": attempts_count,
                "items": 0,
                "retry_wait_seconds": round(retry_wait_seconds, 3),
                "retry_skip_reason": retry_skip_reason,
            }
        )

        errors.append(
            {
                "section": section,
                "command": final_command,
                "command_str": final_command_str,
                "error": final_error,
                "error_code": final_attempt.get("error_code", "UNKNOWN"),
                "retry_at": final_attempt.get("retry_at"),
                "retry_skip_reason": retry_skip_reason,
            }
        )

    total_pipeline_duration_ms = int((time.monotonic() - pipeline_started_monotonic) * 1000)
    top_slowest = sorted(timing_rows, key=lambda row: row["duration_ms"], reverse=True)[:3]
    print(
        f"[timing-summary] total_ms={total_pipeline_duration_ms} sections={len(timing_rows)}",
        file=sys.stderr,
    )
    for index, row in enumerate(top_slowest, start=1):
        print(
            (
                f"[timing-summary] top{index} section={row['section']} status={row['status']} "
                f"duration_ms={row['duration_ms']} attempts={row['attempts']} items={row['items']}"
            ),
            file=sys.stderr,
        )

    seen_urls: set[str] = set()
    deduped_items: list[dict[str, str]] = []
    for section in section_order:
        for item in section_items[section]:
            url = item["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            deduped_items.append(item)

    grouped: dict[str, list[dict[str, str]]] = {section: [] for section in section_order}
    for item in deduped_items:
        grouped[item["section"]].append(item)

    return {
        "section_order": section_order,
        "section_metadata": section_metadata,
        "grouped_items": grouped,
        "deduped_items": deduped_items,
        "errors": errors,
        "recovered_attempts": recovered_attempts,
        "timings": timing_rows,
        "stats": {
            "command_count": len(entries),
            "section_count": len(section_order),
            "collected_before_dedup": sum(len(items) for items in section_items.values()),
            "after_dedup": len(deduped_items),
            "error_count": len(errors),
            "recovered_count": len(recovered_attempts),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run configurable opencli news commands sequentially and dedupe by URL."
    )
    default_config = Path(__file__).resolve().parents[1] / "references" / "commands.json"

    parser.add_argument("--config", default=str(default_config), help="Path to commands.json")
    parser.add_argument("--out-json", default="", help="Optional output JSON path")
    parser.add_argument("--timeout", type=int, default=300, help="Per-command timeout seconds")
    parser.add_argument("--timezone", default="Asia/Shanghai", help="Timezone for generated_at")

    args = parser.parse_args()

    try:
        tz = ZoneInfo(args.timezone)
    except Exception as exc:
        print(f"Invalid timezone '{args.timezone}': {exc}", file=sys.stderr)
        return 2

    started_at = datetime.now(tz)
    run_id = make_run_id(started_at)

    try:
        entries = load_config(Path(args.config).expanduser().resolve())
        result = run_pipeline(
            entries,
            timeout_seconds=args.timeout,
            output_timezone=args.timezone,
        )
    except Exception as exc:
        print(f"Pipeline error: {exc}", file=sys.stderr)
        return 2

    finished_at = datetime.now(tz)

    payload = {
        "run_id": run_id,
        "started_at": format_timestamp(started_at),
        "finished_at": format_timestamp(finished_at),
        "generated_at": format_timestamp(finished_at),
        "timezone": args.timezone,
        **result,
    }

    if args.out_json:
        out_path = Path(args.out_json).expanduser().resolve()
        payload["current_json_path"] = str(out_path)
        write_json_file(out_path, payload)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
