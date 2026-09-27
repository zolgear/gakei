"""バージョン情報(ADR-0021 3章)。バージョンの正は `backend/pyproject.toml` の1か所だけ。

`frontend/package.json` の `version` は使わない(`"private": true` で公開しないパッケージ
なので `0.0.0` のまま)。フロントは `GET /api/about` から受け取る。
"""

from __future__ import annotations

import logging
import tomllib
from functools import cache
from pathlib import Path

from app.config import Settings

logger = logging.getLogger(__name__)

# backend/app/version.py の2つ上(backend/)。
_PYPROJECT_PATH = Path(__file__).resolve().parents[1] / "pyproject.toml"


@cache
def get_version() -> str:
    """`pyproject.toml` の `project.version` を返す。読めない/壊れている場合は起動を止めず
    `"0.0.0"` を返して警告だけログに出す。
    """
    try:
        data = tomllib.loads(_PYPROJECT_PATH.read_text(encoding="utf-8"))
        version = data["project"]["version"]
        if not isinstance(version, str):
            raise TypeError(f"project.version が文字列ではありません: {version!r}")
        return version
    except Exception:
        logger.warning(
            "pyproject.toml からバージョンを読めませんでした(%s)。既定値 0.0.0 を使います。",
            _PYPROJECT_PATH,
            exc_info=True,
        )
        return "0.0.0"


def get_commit(settings: Settings) -> str | None:
    """`GAKEI_COMMIT`(リリースワークフローがビルド時に埋め込む git のコミット SHA)。

    未設定、または空白のみの値は `None` として扱う。起動スクリプトで動かす場合は
    通常 `None`(git を呼んで補うことはしない。ADR-0021 3章)。
    """
    if settings.gakei_commit is None:
        return None
    stripped = settings.gakei_commit.strip()
    return stripped or None
