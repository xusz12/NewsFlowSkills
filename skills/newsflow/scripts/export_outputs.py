#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path


LEGACY_DEFAULT_TARGET_ROOT = Path(
    "/Users/x/Library/Mobile Documents/iCloud~md~obsidian/Documents/DailyNews"
)
SIDECAR_SUFFIX = ".newsreader.json"
FRESH_FILENAME_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-\d{2}-\d{2}-\d{2}"
    r"(?:-\d{2}-[0-9a-f]{12})?_freshNews\.md$"
)


def parse_daily_date(path: Path) -> tuple[int, int]:
    # dailyFreshNews_YYYY-MM-DD.md
    name = path.name
    prefix = "dailyFreshNews_"
    suffix = ".md"
    if not (name.startswith(prefix) and name.endswith(suffix)):
        raise ValueError("invalid_daily_filename")
    date_text = name[len(prefix) : -len(suffix)]
    parts = date_text.split("-")
    if len(parts) != 3:
        raise ValueError("invalid_daily_filename")
    year, month, _day = parts
    if len(year) != 4 or len(month) != 2:
        raise ValueError("invalid_daily_filename")
    return int(year), int(month)


def parse_fresh_date(path: Path) -> tuple[int, int]:
    # Legacy: YYYY-MM-DD-HH-mm_freshNews.md
    # Current: YYYY-MM-DD-HH-mm-ss-<12 hex>_freshNews.md
    match = FRESH_FILENAME_RE.fullmatch(path.name)
    if match is None:
        raise ValueError("invalid_fresh_filename")
    return int(match.group("year")), int(match.group("month"))


def resolve_target_root(cli_target_root: str | None) -> tuple[Path, bool]:
    if cli_target_root:
        return Path(cli_target_root).expanduser().resolve(), False
    env_target_root = os.environ.get("NEWSFLOW_EXPORT_ROOT", "").strip()
    if env_target_root:
        return Path(env_target_root).expanduser().resolve(), False
    return LEGACY_DEFAULT_TARGET_ROOT.expanduser().resolve(), True


def check_root_exists(target_root: Path) -> None:
    if not target_root.exists():
        raise FileNotFoundError("target_root_not_found")
    if not target_root.is_dir():
        raise NotADirectoryError("target_root_not_found")


def ensure_writable_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path,
            prefix=".export_probe.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write("ok")
            temp_path = Path(handle.name)
        os.replace(temp_path, temp_path)
        temp_path.unlink(missing_ok=True)
    except Exception as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise PermissionError(f"target_dir_not_writable: {exc}") from exc


def copy_overwrite(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def sidecar_path_for(markdown_path: Path) -> Path:
    if markdown_path.suffix != ".md":
        raise ValueError("invalid_markdown_filename")
    return markdown_path.with_suffix(SIDECAR_SUFFIX)


def fail_payload(reason: str, target_root: Path, extra: dict | None = None) -> dict:
    payload = {
        "ok": False,
        "error": reason,
        "target_dir": str(target_root),
        "exported": [],
        "errors": [],
    }
    if extra:
        payload.update(extra)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export newsflow daily/fresh outputs into month-based DailyNews folders."
    )
    parser.add_argument("--daily", required=True, help="Path to dailyFreshNews_YYYY-MM-DD.md")
    parser.add_argument(
        "--fresh",
        required=True,
        help="Path to a legacy or current per-run *_freshNews.md",
    )
    parser.add_argument(
        "--target-root",
        help="Export root (overrides NEWSFLOW_EXPORT_ROOT and the legacy default)",
    )
    args = parser.parse_args()

    daily_path = Path(args.daily).expanduser().resolve()
    fresh_path = Path(args.fresh).expanduser().resolve()
    target_root, used_legacy_default = resolve_target_root(args.target_root)
    if used_legacy_default:
        print(
            "warning: using the legacy personal export root; pass --target-root "
            "or set NEWSFLOW_EXPORT_ROOT",
            file=sys.stderr,
        )

    if not daily_path.exists():
        print(
            json.dumps(
                fail_payload(
                    "source_file_not_found",
                    target_root,
                    {"missing_path": str(daily_path)},
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    if not fresh_path.exists():
        print(
            json.dumps(
                fail_payload(
                    "source_file_not_found",
                    target_root,
                    {"missing_path": str(fresh_path)},
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    daily_sidecar_path = sidecar_path_for(daily_path)
    if not daily_sidecar_path.exists():
        print(
            json.dumps(
                fail_payload(
                    "source_sidecar_not_found",
                    target_root,
                    {"missing_path": str(daily_sidecar_path)},
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    try:
        check_root_exists(target_root)
    except Exception:
        print(json.dumps(fail_payload("target_root_not_found", target_root), ensure_ascii=False, indent=2))
        return 2

    try:
        daily_year, daily_month = parse_daily_date(daily_path)
    except ValueError as exc:
        print(json.dumps(fail_payload(str(exc), target_root), ensure_ascii=False, indent=2))
        return 2
    try:
        fresh_year, fresh_month = parse_fresh_date(fresh_path)
    except ValueError as exc:
        print(json.dumps(fail_payload(str(exc), target_root), ensure_ascii=False, indent=2))
        return 2

    if (daily_year, daily_month) != (fresh_year, fresh_month):
        print(
            json.dumps(
                fail_payload(
                    "date_mismatch",
                    target_root,
                    {
                        "daily": daily_path.name,
                        "fresh": fresh_path.name,
                    },
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    month_dir = target_root / f"{daily_year}年{daily_month}月"
    try:
        ensure_writable_dir(month_dir)
        daily_target = month_dir / daily_path.name
        fresh_target = month_dir / fresh_path.name
        daily_sidecar_target = month_dir / daily_sidecar_path.name
        copy_overwrite(daily_path, daily_target)
        copy_overwrite(fresh_path, fresh_target)
        copy_overwrite(daily_sidecar_path, daily_sidecar_target)
    except PermissionError as exc:
        print(
            json.dumps(
                fail_payload("target_dir_not_writable", target_root, {"detail": str(exc)}),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    except Exception as exc:
        print(
            json.dumps(
                fail_payload("copy_failed", target_root, {"detail": str(exc)}),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    payload = {
        "ok": True,
        "target_dir": str(month_dir),
        "exported": [
            {"source": str(daily_path), "target": str(daily_target)},
            {"source": str(fresh_path), "target": str(fresh_target)},
            {"source": str(daily_sidecar_path), "target": str(daily_sidecar_target)},
        ],
        "errors": [],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
