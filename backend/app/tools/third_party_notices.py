"""サーバーを立てずに第三者ライセンス表記を書き出す(ADR-0021 4章)。

使い方: uv run python -m app.tools.third_party_notices <出力パス|->
`-` を指定すると標準出力に書く(リリースワークフローが GHCR イメージの中でこれを実行し、
`THIRD_PARTY_NOTICES.txt` を取り出して GitHub Release に添付する)。
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.domain.third_party import render_notices
from app.main import _FRONTEND_DIST


def main() -> None:
    if len(sys.argv) != 2:
        print(
            "使い方: uv run python -m app.tools.third_party_notices <出力パス|->",
            file=sys.stderr,
        )
        raise SystemExit(1)

    notices = render_notices(_FRONTEND_DIST.resolve())

    destination = sys.argv[1]
    if destination == "-":
        sys.stdout.write(notices)
        return

    output_path = Path(destination)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(notices, encoding="utf-8")
    print(f"第三者ライセンス表記を書き出しました: {output_path}")


if __name__ == "__main__":
    main()
