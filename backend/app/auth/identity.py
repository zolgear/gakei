"""ログインユーザーの表現(ADR-0019)。Authlib・DB(`AppUser`)の詳細から切り離した、
API 層(`api/*.py`)が扱う値。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

Role = Literal["user", "admin"]


@dataclass(frozen=True)
class CurrentUser:
    """`none`(個人)モードでは常に `LOCAL_ADMIN`(id=None)。`oidc` モードはログインした
    `AppUser` から作る(id は `app_user.id`)。
    """

    id: uuid.UUID | None
    name: str | None
    email: str | None
    role: Role
    # アバターのハッシュ(ADR-0020)。`none` モードでは常に None(アバター機能自体が無い)。
    avatar_sha256: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# `none` モードで使う固定ユーザー。実行者を記録しない(created_by_user_id は常に null)ため
# id を持たない。
LOCAL_ADMIN = CurrentUser(id=None, name=None, email=None, role="admin")
