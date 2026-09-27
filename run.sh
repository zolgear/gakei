#!/usr/bin/env bash
# Start GAKEI locally on Linux / macOS (ADR-0012 Decision 1, 2).
# Usage: ./run.sh [--host H] [--port P] [--data-dir DIR] [--no-browser]
#
# Comments in run.sh and run.bat are kept in English so the two launchers can be
# maintained side by side (run.bat must stay ASCII only; see the note there).
# The messages this script prints are bilingual and chosen by the OS locale below.
set -euo pipefail

# Resolve paths from the directory of this script (= the repository root), so the
# script works from any current directory and with spaces in the path.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ADR-0015: pick the language of the few messages this script prints itself (the uv
# install prompt) from the OS locale. Everything after python -m app.launch is
# printed by the Python side (console_t in app/i18n.py), which uses the same rule:
# whether LC_ALL / LC_MESSAGES / LANG starts with "ja".
_locale_value="${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}"
case "$_locale_value" in
    ja*) _is_japanese=1 ;;
    *) _is_japanese=0 ;;
esac

# Usage: msg "Japanese text" "English text"
msg() {
    if [ "$_is_japanese" = "1" ]; then
        printf '%s' "$1"
    else
        printf '%s' "$2"
    fi
}

# Add the default install locations of uv to PATH (no-op if already present).
for candidate_dir in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    case ":$PATH:" in
        *":$candidate_dir:"*) ;;
        *)
            if [ -d "$candidate_dir" ]; then
                export PATH="$PATH:$candidate_dir"
            fi
            ;;
    esac
done

if ! command -v uv >/dev/null 2>&1; then
    if [ -t 0 ]; then
        read -r -p "$(msg 'uv が見つかりません。公式のインストーラーで uv をインストールしますか? [Y/n] ' 'uv was not found. Install it with the official installer? [Y/n] ')" answer
        answer="${answer:-Y}"
        case "$answer" in
            [Yy]*) ;;
            *)
                echo "$(msg 'インストールを中止しました。https://docs.astral.sh/uv/ を参照して手動でインストールしてください。' 'Canceled. Install uv manually: https://docs.astral.sh/uv/')" >&2
                exit 1
                ;;
        esac
    else
        echo "$(msg 'uv が見つかりません。https://docs.astral.sh/uv/ の手順でインストールしてから、もう一度実行してください。' 'uv was not found. Install it following https://docs.astral.sh/uv/ and run this script again.')" >&2
        exit 1
    fi

    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        echo "$(msg 'curl も wget も見つかりません。uv を手動でインストールしてください: https://docs.astral.sh/uv/' 'Neither curl nor wget was found. Install uv manually: https://docs.astral.sh/uv/')" >&2
        exit 1
    fi

    export PATH="$HOME/.local/bin:$PATH"

    if ! command -v uv >/dev/null 2>&1; then
        echo "$(msg 'uv のインストールに失敗しました。https://docs.astral.sh/uv/ を参照してください。' 'Failed to install uv. See https://docs.astral.sh/uv/')" >&2
        exit 1
    fi
fi

cd "$REPO_ROOT/backend"
exec uv run --frozen python -m app.launch "$@"
