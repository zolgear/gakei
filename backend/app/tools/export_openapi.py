"""サーバーを立てずに OpenAPI スキーマを書き出す。

使い方: uv run python -m app.tools.export_openapi <出力パス>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.main import app


def main() -> None:
    if len(sys.argv) != 2:
        print("使い方: uv run python -m app.tools.export_openapi <出力パス>", file=sys.stderr)
        raise SystemExit(1)

    output_path = Path(sys.argv[1])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"OpenAPI を書き出しました: {output_path}")


if __name__ == "__main__":
    main()
