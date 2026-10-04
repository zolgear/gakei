"""GET /api/health。コンテナのヘルスチェックと CI の起動待ち用の生存確認(Issue #43)。

ADR-0019 の例外として、ログインなしで呼べる(`app/main.py` で `require_user` を付けずに
include する)。そのため応答は `{"status": "ok"}` だけにし、バージョンや設定は出さない。
DB にも触れない(プロセスが HTTP を返せることだけを確かめる)。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.domain.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/api/health", response_model=HealthResponse, operation_id="get_health")
def get_health() -> HealthResponse:
    return HealthResponse()
