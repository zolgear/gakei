"""外部からの判定の受付(ADR-0051 8章)。`POST /api/mods/{mod_id}/verdicts`。

非同期の判定を後から返したいシステム(Webhook の Mod)のための受け口。

- 認証はユーザーのセッションでもアクセストークンでもなく、その Webhook の署名の鍵による署名。
  送るときと同じ形式(`X-Gakei-Timestamp` は UNIX 秒、`X-Gakei-Signature: sha256=<HMAC-SHA256(鍵,
  "<timestamp>." + 本文のバイト列) の hex>`)で確かめ、時刻が前後 `MAX_SKEW_SECONDS` を超えて
  ずれていれば断る(古い要求の再送を受けない)。`require_user` は掛けない。
- 本文は応答と同じ判定の配列(`[{target, verdict, ...}]` か `{"verdicts": [...]}`)。
- 送れる対象は、その Mod の呼び出し(`asset.ingested` / `run.finished`)で届いた Asset と Run
  だけ。対象外が1つでもあれば、何も記録せずに 403。
- 受けた判定は配達の応答と同じ扱い(`mod_verdict` に追記し、`deny` なら画像を残さない)。
- 無効にした Webhook と、無い Webhook は 404。
- 同じ要求(Mod の id、`X-Gakei-Timestamp`、本文が同じもの)は1度だけ受け付ける
  (`app/mods/replay.py`)。受け付け済みの要求がもう一度届けば、記録せずに1回目と同じ応答を
  `duplicate: true` を付けて返す(応答を受け取れなかった Mod の送り直しを成功として扱えるように)。
  1回目を処理している間に届けば 409、記録が窓の中の要求で埋まっていれば 429。
- 本文は `_BODY_MAX_BYTES` までを読みながら数え、超えた時点で打ち切って 413(`Content-Length`
  が超えていれば読まずに 413)。全体のミドルウェア(`app/body_guard.py`)にも同じ上限を印で渡す。
"""

from __future__ import annotations

import hmac
import json
import time

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.body_guard import body_policy
from app.domain.schemas import ModVerdictReceiveResponse
from app.i18n import t
from app.mods import api as contract
from app.mods import config as mods_config
from app.mods import webhook as webhook_send
from app.mods.replay import MAX_SKEW_SECONDS, request_key
from app.mods.runtime import ModRuntime, VerdictTargetError

router = APIRouter(prefix="/api/mods", tags=["mods"])

__all__ = ["MAX_SKEW_SECONDS", "router", "verify_signature"]
_BODY_MAX_BYTES = 1024 * 1024


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail=t("mods.verdictUnauthorized"))


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=t("mods.notFound"))


def _too_large() -> HTTPException:
    return HTTPException(status_code=413, detail=t("mods.verdictTooLarge"))


async def _read_body(request: Request) -> bytes:
    """本文を読む。`_BODY_MAX_BYTES` を超えた時点で読むのをやめて 413 にする。"""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > _BODY_MAX_BYTES:
        raise _too_large()
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _BODY_MAX_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def verify_signature(
    secret: str, timestamp: str | None, signature: str | None, body: bytes, *, now: float
) -> bool:
    """署名と時刻を確かめる。"""
    if not timestamp or not signature:
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs(now - sent_at) > MAX_SKEW_SECONDS:
        return False
    expected = webhook_send.sign(secret, timestamp, body)
    return hmac.compare_digest(expected, signature)


@router.post(
    "/{mod_id}/verdicts",
    response_model=ModVerdictReceiveResponse,
    operation_id="receive_mod_verdicts",
    responses={
        401: {"description": "署名が無い・合わない、または時刻が古い"},
        403: {"description": "その Mod に届けていない Asset・Run への判定を含む"},
        404: {"description": "Webhook が無い、または無効"},
        409: {"description": "同じ要求を処理している"},
        422: {"description": "本文が判定の配列の形でない"},
        429: {"description": "受け付けた要求の記録が上限に達している"},
    },
)
# 認証はログインではなく署名なので、ログインの前に本文を読んでよい(ADR-0051 8章)。
@body_policy(max_bytes=_BODY_MAX_BYTES, before_login=True)
async def receive_mod_verdicts(mod_id: str, request: Request) -> ModVerdictReceiveResponse:
    runtime: ModRuntime = request.app.state.mods
    body = await _read_body(request)

    def _lookup() -> tuple[mods_config.WebhookConfig | None, str | None]:
        with request.app.state.session_factory() as db:
            webhook = mods_config.find_webhook(mods_config.load_webhooks(db), mod_id)
        if webhook is None:
            return None, None
        return webhook, mods_config.read_webhook_secret(runtime.settings.data_dir, webhook.id)

    webhook, secret = await run_in_threadpool(_lookup)
    if webhook is None or not webhook.common.enabled:
        raise _not_found()
    timestamp = request.headers.get("x-gakei-timestamp")
    if not secret or not verify_signature(
        secret, timestamp, request.headers.get("x-gakei-signature"), body, now=time.time()
    ):
        raise _unauthorized()

    # 署名が合った要求だけを記録する(鍵を持たない人は記録を埋められない)。
    guard = runtime.verdict_replay
    reservation = guard.reserve(request_key(webhook.id, timestamp or "", body))
    if reservation.outcome == "done":
        return ModVerdictReceiveResponse(accepted=reservation.accepted or 0, duplicate=True)
    if reservation.outcome == "in_progress":
        raise HTTPException(status_code=409, detail=t("mods.verdictInProgress"))
    if reservation.outcome == "full":
        raise HTTPException(status_code=429, detail=t("mods.verdictTooMany"))

    try:
        try:
            raw = json.loads(body) if body.strip() else None
            verdicts = contract.verdicts_from_json(raw)
        except (ValueError, contract.ContractError) as e:
            raise HTTPException(
                status_code=422, detail=t("mods.verdictInvalid", error=str(e))
            ) from e

        try:
            accepted = await run_in_threadpool(
                runtime.receive_verdicts,
                mods_config.KIND_WEBHOOK,
                webhook.id,
                webhook.name,
                verdicts,
            )
        except VerdictTargetError as e:
            raise HTTPException(
                status_code=403,
                detail=t("mods.verdictTargetNotAllowed", target=f"{e.target_type} {e.target_id}"),
            ) from e
    except BaseException:
        # 記録しなかった要求は覚えない(送り直せば同じ確認をもう一度する)。
        guard.release(reservation.key)
        raise
    guard.complete(reservation.key, accepted)
    return ModVerdictReceiveResponse(accepted=accepted)
