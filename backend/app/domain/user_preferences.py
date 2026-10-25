"""ユーザーごとの設定(ADR-0049 6章)。

`user_preference` に (`owner`, `key`) ごとの JSON の値として持つ。`owner` は認証モードでは
`app_user.id` の文字列、個人モードでは `local`。知っているキーと値の形はここで決める
(API の入力の検証は `UserPreferencesUpdateRequest` が行う)。保存された値が壊れていれば
既定値として扱う。

今のキー:

- `content_rating.mask_levels`: マスクするレーティングの段階の配列(既定は R 以上)。
- `content_rating.mask_unrated`: 未判定の画像もマスクするか(既定 false)。
- `assistant.image_send`: アシスタントが道具で画像を LLM に送る前に確かめるか(`ask`、既定)、
  確かめずに送るか(`always`)。ADR-0048 14.1。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain.content_rating import LEVELS
from app.domain.models import UserPreference

LOCAL_OWNER = "local"

KEY_MASK_LEVELS = "content_rating.mask_levels"
KEY_MASK_UNRATED = "content_rating.mask_unrated"
KEY_ASSISTANT_IMAGE_SEND = "assistant.image_send"
IMAGE_SEND_ASK = "ask"
IMAGE_SEND_ALWAYS = "always"


def _normalize_levels(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(KEY_MASK_LEVELS)
    if any(v not in LEVELS for v in value):
        raise ValueError(KEY_MASK_LEVELS)
    chosen = set(value)
    # 弱い順に並べ、重複を除く。
    return [level for level in LEVELS if level in chosen]


def _normalize_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(KEY_MASK_UNRATED)
    return value


def _normalize_image_send(value: Any) -> str:
    if value not in (IMAGE_SEND_ASK, IMAGE_SEND_ALWAYS):
        raise ValueError(KEY_ASSISTANT_IMAGE_SEND)
    return value


# キー → (既定値, 検証して保存する形に整える関数)
_SPECS: dict[str, tuple[Any, Callable[[Any], Any]]] = {
    KEY_MASK_LEVELS: (["r", "x", "xx", "xxx"], _normalize_levels),
    KEY_MASK_UNRATED: (False, _normalize_bool),
    KEY_ASSISTANT_IMAGE_SEND: (IMAGE_SEND_ASK, _normalize_image_send),
}
KEYS: tuple[str, ...] = tuple(_SPECS)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def owner_for(user: CurrentUser) -> str:
    """設定の持ち主。個人モード(id なし)は `local`。"""
    return str(user.id) if user.id is not None else LOCAL_OWNER


def normalize(key: str, value: Any) -> Any:
    """1つのキーの値を検証して整える。知らないキーは KeyError、形が合わなければ ValueError。"""
    _default, normalizer = _SPECS[key]
    return normalizer(value)


def load(db: Session, user: CurrentUser) -> dict[str, Any]:
    """すべてのキーの値(保存していない・壊れているキーは既定値)。"""
    values: dict[str, Any] = {key: _default_of(key) for key in KEYS}
    rows = db.execute(
        select(UserPreference.key, UserPreference.value).where(
            UserPreference.owner == owner_for(user), UserPreference.key.in_(KEYS)
        )
    ).all()
    for key, raw in rows:
        try:
            values[key] = normalize(key, raw)
        except (KeyError, ValueError):
            continue
    return values


def _default_of(key: str) -> Any:
    default, _ = _SPECS[key]
    return list(default) if isinstance(default, list) else default


def save(db: Session, user: CurrentUser, updates: dict[str, Any]) -> None:
    """値をまとめて検証してから保存する(一部だけ保存される事態を避ける)。commit はしない。"""
    normalized = {key: normalize(key, value) for key, value in updates.items()}
    owner = owner_for(user)
    now = _utcnow()
    for key, value in normalized.items():
        row = db.get(UserPreference, (owner, key))
        if row is None:
            db.add(UserPreference(owner=owner, key=key, value=value, updated_at=now))
        else:
            row.value = value
            row.updated_at = now
    db.flush()
