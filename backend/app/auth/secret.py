"""`gakei_oidc` Cookie(state/nonce/PKCE の一時保存、Starlette `SessionMiddleware`)の署名鍵
(ADR-0019)。

環境変数 `AUTH_SECRET` を優先する。無ければ `DATA_DIR/secrets.json` の `auth_secret` を読み、
それも無ければ生成してそこに保存する(次回起動時も同じ鍵を使い、再起動のたびに進行中の
ログインが失敗するのを防ぐ)。
"""

from __future__ import annotations

import secrets

from app.config import Settings
from app.domain.api_key import read_secret_field, write_secret_field

_SECRET_FIELD = "auth_secret"


def load_or_create_auth_secret(settings: Settings) -> str:
    if settings.auth_secret:
        return settings.auth_secret

    existing = read_secret_field(settings.data_dir, _SECRET_FIELD)
    if existing:
        return existing

    generated = secrets.token_urlsafe(32)
    write_secret_field(settings.data_dir, _SECRET_FIELD, generated)
    return generated
