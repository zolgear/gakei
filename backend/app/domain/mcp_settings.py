"""MCP サーバーの管理者設定(ADR-0023 1章・5章)。

- `mcp.enabled`: `/mcp` を応答させるか。既定は無効(false)。
- `mcp.hourly_run_limit`: MCP 経由で作る Run の上限(直近1時間の件数)。既定 20、0 で生成を止める。

どちらも `app_setting` に保存する(画面で保存した値だけ。環境変数の既定値は持たない)。値の
読み書きは `general_settings` と同じ形式(`{"value": ...}`)。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.general_settings import GeneralSettingsValidationError, _get_raw_value, _save
from app.domain.models import Run
from app.i18n import t

ENABLED_KEY = "mcp.enabled"
HOURLY_RUN_LIMIT_KEY = "mcp.hourly_run_limit"

DEFAULT_ENABLED = False
DEFAULT_HOURLY_RUN_LIMIT = 20
HOURLY_RUN_LIMIT_MIN = 0
HOURLY_RUN_LIMIT_MAX = 1000

# `run.origin` に書く値。
ORIGIN_MCP = "mcp"


class McpSettingsValidationError(GeneralSettingsValidationError):
    """保存しようとした値が不正(API 層で 422 にする)。"""


def is_enabled(db: Session) -> bool:
    value = _get_raw_value(db, ENABLED_KEY)
    return value if isinstance(value, bool) else DEFAULT_ENABLED


def hourly_run_limit(db: Session) -> int:
    """保存された上限。無い、または壊れている(整数でない・範囲外)なら既定値。"""
    value = _get_raw_value(db, HOURLY_RUN_LIMIT_KEY)
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_HOURLY_RUN_LIMIT
    if not (HOURLY_RUN_LIMIT_MIN <= value <= HOURLY_RUN_LIMIT_MAX):
        return DEFAULT_HOURLY_RUN_LIMIT
    return value


def validate_enabled(value: object) -> None:
    if not isinstance(value, bool):
        raise McpSettingsValidationError(t("settings.mcp.invalidEnabled"))


def validate_hourly_run_limit(value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not (HOURLY_RUN_LIMIT_MIN <= value <= HOURLY_RUN_LIMIT_MAX)
    ):
        raise McpSettingsValidationError(
            t(
                "settings.mcp.invalidHourlyRunLimit",
                min=HOURLY_RUN_LIMIT_MIN,
                max=HOURLY_RUN_LIMIT_MAX,
            )
        )


def save_enabled(db: Session, value: bool) -> None:
    validate_enabled(value)
    _save(db, ENABLED_KEY, value)


def save_hourly_run_limit(db: Session, value: int) -> None:
    validate_hourly_run_limit(value)
    _save(db, HOURLY_RUN_LIMIT_KEY, value)


def count_recent_mcp_runs(db: Session, now: datetime | None = None) -> int:
    """直近1時間に MCP から作った Run の件数(削除済みも数える。上限は課金を抑えるためのもの)。"""
    now = now or datetime.now(UTC)
    since = now - timedelta(hours=1)
    return db.execute(
        select(func.count())
        .select_from(Run)
        .where(Run.origin == ORIGIN_MCP, Run.queued_at >= since)
    ).scalar_one()


MCP_PATH = "/mcp"


def resolve_public_base(public_base_url: str | None, request_base_url: str) -> str:
    """利用者・エージェントに見せる URL の基点。`PUBLIC_BASE_URL` があればそれ、無ければ
    リクエストの base URL(いずれも末尾の `/` を除く)。"""
    return (public_base_url or request_base_url).rstrip("/")
