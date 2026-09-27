"""OIDC クライアント(ADR-0019)。

BFF 方式(サーバー側で Authorization Code + PKCE を行う)を Authlib
(`authlib.integrations.starlette_client`)で実装する。`<img src>` と SSE(EventSource)は
ヘッダーを付けられないため、SPA には Cookie(`gakei_session`)しか渡さない。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin

from authlib.integrations.starlette_client import OAuth
from authlib.integrations.starlette_client.apps import StarletteOAuth2App
from fastapi import HTTPException, Request
from starlette.responses import Response

from app.config import Settings
from app.i18n import t

logger = logging.getLogger(__name__)

_CLIENT_NAME = "idp"


@dataclass(frozen=True)
class OidcIdentity:
    """ID トークン(必要なら userinfo で補った)のクレームから取り出した最小限の情報。

    `email_verified` はクレームの生値(`True`/`False`/クレーム自体が無ければ `None`)。
    `email_verified is False` のときは `email` 自体を `None` にしてある(M-1、
    2026-09-27 追記。未検証のメールアドレスで管理者判定・許可ドメイン判定・アカウント
    作成をしないため)。callback 側(`api/auth.py`)は、`email` が空になった理由を
    `email_verified` で見分けて、案内文言(`email_missing` / `email_unverified`)を
    出し分ける。
    """

    issuer: str
    subject: str
    email: str | None
    name: str | None
    # 末尾かつ既定値ありにしてある: 既存の呼び出し側(テストの `OidcIdentity(...)`)が
    # キーワード引数だけを渡していても壊れないようにするため。既定 `None` は「クレーム自体が
    # 無い」を表し、これまでどおりログインを通す。
    email_verified: bool | None = None


class OidcClient(Protocol):
    """テストでは `tests/oidc_fake.py::FakeOidcClient` に差し替える
    (`auth/deps.py::get_oidc_client` の Depends を上書きする。`api/settings.py` の
    `get_key_validator` と同じ流儀)。
    """

    async def authorize_redirect(self, request: Request, redirect_uri: str) -> Response: ...

    async def complete_login(self, request: Request) -> OidcIdentity: ...

    async def end_session_endpoint(self) -> str | None: ...


class AuthlibOidcClient:
    """Authlib による実装。oidc モードのときだけ lifespan が `app.state.oidc_client` に置く。"""

    def __init__(self, settings: Settings) -> None:
        if not settings.oidc_issuer or not settings.oidc_client_id:
            raise ValueError("oidc_issuer と oidc_client_id が必要です")

        self._issuer = settings.oidc_issuer
        self._client_id = settings.oidc_client_id
        # I-5: discovery の issuer と OIDC_ISSUER の食い違いは起動ごとに一度だけ警告する
        # (毎リクエストのログを埋めないため)。
        self._issuer_mismatch_warned = False
        self._oauth = OAuth()

        client_kwargs: dict[str, object] = {
            "scope": settings.oidc_scopes,
            "code_challenge_method": "S256",
        }
        # public client(secret 無し)は Authlib の既定(client_secret_basic)ではコード交換に
        # 失敗するため、明示的に none を指定する。
        if not settings.oidc_client_secret:
            client_kwargs["token_endpoint_auth_method"] = "none"

        self._oauth.register(
            name=_CLIENT_NAME,
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret or None,
            server_metadata_url=urljoin(
                self._issuer.rstrip("/") + "/", ".well-known/openid-configuration"
            ),
            client_kwargs=client_kwargs,
        )

    @property
    def _client(self) -> StarletteOAuth2App:
        return self._oauth.create_client(_CLIENT_NAME)  # type: ignore[return-value]

    async def authorize_redirect(self, request: Request, redirect_uri: str) -> Response:
        # discovery(初回ログイン時)や認可 URL の組み立てに失敗した場合、IdP に届かない・
        # 応答が JSON でない等はすべて「IdP に接続できない」として 502 で返す。例外の型は
        # Authlib が使う HTTP クライアント(httpx2)や JSON の解析など層ごとに違うので、
        # ここでは種類を絞らず、原因はログに残す。
        try:
            return await self._client.authorize_redirect(request, redirect_uri)
        except Exception as exc:
            logger.exception("OIDC の IdP に接続できません(OIDC_ISSUER=%s)", self._issuer)
            raise HTTPException(status_code=502, detail=t("auth.idpUnavailable")) from exc

    def _warn_if_issuer_mismatch(self, discovery_issuer: str) -> None:
        """I-5: discovery の `issuer` と `OIDC_ISSUER` の設定が違えば警告する(一度だけ)。"""
        if self._issuer_mismatch_warned:
            return
        if discovery_issuer.rstrip("/") != self._issuer.rstrip("/"):
            logger.warning(
                "discovery 文書の issuer(%s)が OIDC_ISSUER の設定(%s)と一致しません。",
                discovery_issuer,
                self._issuer,
            )
            self._issuer_mismatch_warned = True

    async def complete_login(self, request: Request) -> OidcIdentity:
        # L-1: OAuthError 以外(JWT のクレーム検証エラー、discovery/JWKS 取得の失敗など)も
        # ここで拾い、トークン・code・secret を含めずに 400 へ丸める(500 のスタックトレースを
        # ブラウザに返さない)。
        try:
            metadata = await self._client.load_server_metadata()
            discovery_issuer = metadata.get("issuer") or self._issuer
            self._warn_if_issuer_mismatch(discovery_issuer)
            # L-2: `aud`(このクライアント宛てであること)と `iss`(discovery の issuer)を
            # 明示的に検証する。Authlib は claims_options を渡すと iss を自動では足さない
            # ため、両方をここで指定する。
            token = await self._client.authorize_access_token(
                request,
                claims_options={
                    "aud": {"essential": True, "values": [self._client_id]},
                    "iss": {"essential": True, "values": [discovery_issuer]},
                },
            )
        except Exception as exc:
            logger.warning("OIDC callback failed: %s", type(exc).__name__)
            raise HTTPException(status_code=400, detail=t("auth.callbackFailed")) from exc

        id_token_claims = token.get("userinfo") or {}
        id_token_subject = id_token_claims.get("sub")

        userinfo = id_token_claims
        email = userinfo.get("email")
        if not email:
            try:
                fetched = await self._client.userinfo(token=token)
            except Exception as exc:
                logger.warning("OIDC callback failed: %s", type(exc).__name__)
                raise HTTPException(status_code=400, detail=t("auth.callbackFailed")) from exc
            if fetched:
                userinfo = fetched
                email = userinfo.get("email")

        subject = userinfo.get("sub")
        if not subject:
            raise HTTPException(status_code=400, detail=t("auth.callbackFailed"))
        # I-4: userinfo で補ったとき、その sub が ID トークンの sub と食い違っていないか確認する
        # (別人の userinfo を取り違えて紐付けることを防ぐ)。
        if id_token_subject and subject != id_token_subject:
            logger.warning("OIDC callback failed: userinfo sub does not match id_token sub")
            raise HTTPException(status_code=400, detail=t("auth.callbackFailed"))

        # M-1: email_verified が明示的に False なクレームは、メールアドレスを持ち帰らない
        # (クレーム自体が無い IdP はこれまでどおり通す)。
        email_verified = userinfo.get("email_verified")
        if email_verified is False:
            email = None

        name = userinfo.get("name") or userinfo.get("preferred_username")
        return OidcIdentity(
            issuer=self._issuer,
            subject=subject,
            email=email,
            email_verified=email_verified,
            name=name,
        )

    async def end_session_endpoint(self) -> str | None:
        """discovery 文書の `end_session_endpoint` を返す(無い IdP なら None)。

        セッションは DB に残るので、サーバーを再起動した直後(このプロセスではまだ誰も
        ログインしていない)にログアウトされることは普通にある。そのため、キャッシュを
        当てにせず、ここでも `load_server_metadata()` を呼ぶ(取得済みなら Authlib が
        キャッシュを返す)。IdP に届かないときは None にして、ログアウト自体は成立させる。
        """
        try:
            metadata = await self._client.load_server_metadata()
        except Exception:  # IdP 側の障害でログアウトを失敗させない
            logger.warning("OIDC の discovery 文書を取得できず、IdP 側のログアウトを省きます")
            return None
        value = metadata.get("end_session_endpoint")
        return value if isinstance(value, str) else None
