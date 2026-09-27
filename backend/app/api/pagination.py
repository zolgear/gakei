"""一覧APIの簡単なカーソルページング。`(created_at, id)` の組を base64 に詰めるだけ。"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime

from app.i18n import t


class InvalidCursorError(ValueError):
    pass


def encode_cursor(moment: datetime, id_: uuid.UUID) -> str:
    raw = f"{moment.isoformat()}|{id_}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        moment_str, id_str = raw.split("|", 1)
        return datetime.fromisoformat(moment_str), uuid.UUID(id_str)
    except (ValueError, UnicodeDecodeError) as e:
        raise InvalidCursorError(t("pagination.invalidCursor")) from e
