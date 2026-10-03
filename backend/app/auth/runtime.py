"""実効の認証の設定と、作り終えた OIDC クライアントをプロセス内に置く(ADR-0034 4章)。

起動時(lifespan)に DB から読み、設定 API の保存が成功したら `reload` で作り直す。api は
1プロセスで、worker もその中で動くので、プロセス内に置くだけで全体に効く。リクエストの処理は
モードや管理者のメールを `Settings` からではなく、ここから読む。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.orm import Session

from app.auth.oidc import AuthlibOidcClient, OidcClient
from app.config import Settings
from app.domain.auth_settings import EffectiveAuthConfig, OidcConnection, resolve_auth_config
from app.domain.mcp_settings import resolve_public_base

OidcClientFactory = Callable[[OidcConnection], OidcClient]


def build_authlib_client(connection: OidcConnection) -> OidcClient:
    return AuthlibOidcClient(
        issuer=connection.issuer,
        client_id=connection.client_id,
        client_secret=connection.client_secret,
        scopes=connection.scopes,
    )


@dataclass(frozen=True)
class _Snapshot:
    config: EffectiveAuthConfig
    oidc_client: OidcClient | None
    client_fingerprint: str | None


class AuthRuntime:
    """`app.state.auth_runtime`。`reload` は値を1つのスナップショットに作ってから差し替える
    (読み手が途中の状態を見ないように)。"""

    def __init__(
        self, settings: Settings, client_factory: OidcClientFactory = build_authlib_client
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._snapshot = _Snapshot(
            config=resolve_auth_config(None, settings), oidc_client=None, client_fingerprint=None
        )

    def reload(self, db: Session) -> None:
        config = resolve_auth_config(db, self._settings)
        previous = self._snapshot
        client: OidcClient | None = None
        fingerprint: str | None = None
        connection = config.connection
        if config.mode == "oidc" and connection is not None:
            fingerprint = connection.fingerprint()
            # 接続が変わったときだけ作り直す(discovery 文書のキャッシュを捨てないため)。
            if previous.oidc_client is not None and previous.client_fingerprint == fingerprint:
                client = previous.oidc_client
            else:
                client = self._client_factory(connection)
        self._snapshot = _Snapshot(
            config=config, oidc_client=client, client_fingerprint=fingerprint
        )

    @property
    def config(self) -> EffectiveAuthConfig:
        return self._snapshot.config

    @property
    def oidc_client(self) -> OidcClient | None:
        """oidc モードで本登録の接続があるときだけ。none モードでは None。"""
        return self._snapshot.oidc_client

    @property
    def mode(self) -> str:
        return self._snapshot.config.mode

    @property
    def is_oidc(self) -> bool:
        return self._snapshot.config.mode == "oidc"

    def admin_email_set(self) -> set[str]:
        return self._snapshot.config.admin_email_set()

    def allowed_email_domain_set(self) -> set[str]:
        return self._snapshot.config.allowed_email_domain_set()

    @property
    def session_hours(self) -> int:
        return self._snapshot.config.session_hours.value

    @property
    def public_base_url(self) -> str | None:
        return self._snapshot.config.public_base_url.value

    @property
    def public_base_is_https(self) -> bool:
        return self._snapshot.config.public_base_is_https

    @property
    def client_id(self) -> str | None:
        return self._snapshot.config.client_id.value


def get_auth_runtime(request: Request) -> AuthRuntime:
    return request.app.state.auth_runtime


def reload_auth_runtime(request: Request, db: Session) -> None:
    get_auth_runtime(request).reload(db)


def public_base_for(request: Request) -> str:
    """利用者・エージェントに見せる URL の基点(実効の `PUBLIC_BASE_URL`、無ければリクエストの
    base URL)。"""
    return resolve_public_base(get_auth_runtime(request).public_base_url, str(request.base_url))
