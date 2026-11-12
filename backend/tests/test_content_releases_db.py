"""画像の配信が、本体を作る・送る間に DB の接続を握らないこと。

依存のセッションの後始末は応答を送り終えた後なので、閉じておかないと、遅い回線への送信や
高負荷のときに接続プールが埋まり、ほかの要求が待ち時間の後に 500 になる。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.assets import asset_content_response
from app.auth.identity import LOCAL_ADMIN
from app.domain.models import Asset
from tests.conftest import make_png_bytes


def _upload(client: TestClient) -> uuid.UUID:
    response = client.post(
        "/api/assets",
        files={"file": ("base.png", make_png_bytes(width=64, height=64), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


@pytest.mark.parametrize(
    ("variant", "download"),
    [("thumb", False), ("original", False), ("original", True)],
)
def test_content_response_closes_the_session_before_sending(
    client: TestClient, variant: str, download: bool
) -> None:
    asset_id = _upload(client)
    with client.app.state.session_factory() as session:
        asset = session.get(Asset, asset_id)
        assert asset is not None
        assert session.in_transaction()
        response = asset_content_response(
            session,
            client.app.state.store,
            asset,
            viewer=LOCAL_ADMIN,
            variant=variant,  # type: ignore[arg-type]
            download=download,
        )
        assert response.status_code == 200
        assert not session.in_transaction()


def test_not_modified_response_also_closes_the_session(client: TestClient) -> None:
    asset_id = _upload(client)
    etag = client.get(f"/api/assets/{asset_id}/content?variant=thumb").headers["etag"]
    with client.app.state.session_factory() as session:
        asset = session.get(Asset, asset_id)
        assert asset is not None
        response = asset_content_response(
            session,
            client.app.state.store,
            asset,
            viewer=LOCAL_ADMIN,
            variant="thumb",
            download=False,
            if_none_match=etag,
        )
        assert response.status_code == 304
        assert not session.in_transaction()
