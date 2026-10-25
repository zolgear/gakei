#!/usr/bin/env bash
# Claude Code の PostToolUse フック(Edit / Write の後)。編集したファイルだけに整形と lint を回す。
# 失敗したら終了コード 2 で返し、標準エラーの内容を Claude に伝える。
# ツールが入っていない worktree(.venv や node_modules が無い)では何もしない。
set -u

file=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)
[ -n "$file" ] && [ -f "$file" ] || exit 0

root=$(git -C "$(dirname "$file")" rev-parse --show-toplevel 2>/dev/null) || exit 0
rel=${file#"$root"/}

case "$rel" in
  backend/*.py)
    ruff="$root/backend/.venv/bin/ruff"
    [ -x "$ruff" ] || exit 0
    cd "$root/backend" || exit 0
    "$ruff" format -q "$file"
    if ! out=$("$ruff" check "$file" 2>&1); then
      echo "$out" >&2
      exit 2
    fi
    ;;
  frontend/*.ts | frontend/*.tsx)
    oxlint="$root/frontend/node_modules/.bin/oxlint"
    [ -x "$oxlint" ] || exit 0
    cd "$root/frontend" || exit 0
    if ! out=$("$oxlint" "$file" 2>&1); then
      echo "$out" >&2
      exit 2
    fi
    ;;
esac
exit 0
