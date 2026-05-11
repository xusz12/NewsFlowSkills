#!/usr/bin/env bash
set -euo pipefail

# Sync canonical newsflow skill into Codex and Claude install targets.
# Strategy (Phase 4 v1): replace managed dirs (scripts/references/tests) by copy, keep tool-specific files (for example agents/openai.yaml).

CANONICAL_SRC="/Users/x/.skills/newsflow"
CODEX_DST="/Users/x/.codex/skills/newsflow"
CLAUDE_DST="/Users/x/.claude/skills/newsflow"

usage() {
  cat <<'EOF'
Usage:
  sync_install.sh [--dry-run] [--codex-only|--claude-only]

Options:
  --dry-run      Print planned copy operations without writing files.
  --codex-only   Sync only Codex target.
  --claude-only  Sync only Claude target.
EOF
}

DRY_RUN=0
SYNC_CODEX=1
SYNC_CLAUDE=1

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --codex-only) SYNC_CLAUDE=0 ;;
    --claude-only) SYNC_CODEX=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; usage; exit 2 ;;
  esac
done

run_cp() {
  local src="$1"
  local dst="$2"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "[dry-run] cp -Rf $src $dst"
  else
    cp -Rf "$src" "$dst"
  fi
}

sync_target() {
  local dst="$1"
  local adapter_file="$2"

  if [[ $DRY_RUN -eq 1 ]]; then
    echo "[dry-run] mkdir -p $dst"
  else
    mkdir -p "$dst"
  fi

  run_cp "$CANONICAL_SRC/SKILL.md" "$dst/SKILL.md"

  if [[ $DRY_RUN -eq 1 ]]; then
    echo "[dry-run] rm -rf $dst/scripts $dst/references $dst/tests"
  else
    rm -rf "$dst/scripts" "$dst/references" "$dst/tests"
  fi

  run_cp "$CANONICAL_SRC/scripts" "$dst/scripts"
  run_cp "$CANONICAL_SRC/references" "$dst/references"
  if [[ -d "$CANONICAL_SRC/tests" ]]; then
    run_cp "$CANONICAL_SRC/tests" "$dst/tests"
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    echo "[dry-run] mkdir -p $dst/adapters"
  else
    mkdir -p "$dst/adapters"
  fi
  run_cp "$CANONICAL_SRC/adapters/$adapter_file" "$dst/adapters/$adapter_file"
}

if [[ $SYNC_CODEX -eq 1 ]]; then
  sync_target "$CODEX_DST" "codex.md"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "[dry-run] keep existing Codex-only file: $CODEX_DST/agents/openai.yaml"
  else
    mkdir -p "$CODEX_DST/agents"
    src_openai_yaml="/Users/x/.codex/skills/newsflow/agents/openai.yaml"
    dst_openai_yaml="$CODEX_DST/agents/openai.yaml"
    if [[ -f "$src_openai_yaml" ]]; then
      if [[ "$src_openai_yaml" != "$dst_openai_yaml" ]]; then
        cp -f "$src_openai_yaml" "$dst_openai_yaml"
      fi
    fi
  fi
fi

if [[ $SYNC_CLAUDE -eq 1 ]]; then
  sync_target "$CLAUDE_DST" "claude.md"
fi

echo "Sync completed (dry-run=$DRY_RUN)."
