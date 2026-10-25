"""ユーザーごとの設定(ADR-0049 6章): `GET`/`PATCH /api/users/me/preferences`。

個人モードでは持ち主が `local`、認証モードでは `app_user.id` ごとに分かれる。知らないキーと
形の合わない値は 422。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import UserPreference
from tests.conftest import login_as

PREFS = "/api/users/me/preferences"
DEFAULTS = {
    "content_rating.mask_levels": ["r", "x", "xx", "xxx"],
    "content_rating.mask_unrated": False,
    "assistant.image_send": "ask",
}


def test_defaults_and_update_in_none_mode(client: TestClient) -> None:
    response = client.get(PREFS)
    assert response.status_code == 200, response.text
    assert response.json() == DEFAULTS

    response = client.patch(PREFS, json={"content_rating.mask_levels": ["xxx", "r", "r"]})
    assert response.status_code == 200, response.text
    # 弱い順に並べ、重複は除く。書かなかったキーは既定値のまま。
    assert response.json() == {
        "content_rating.mask_levels": ["r", "xxx"],
        "content_rating.mask_unrated": False,
        "assistant.image_send": "ask",
    }
    response = client.patch(PREFS, json={"content_rating.mask_unrated": True})
    assert response.json() == {
        "content_rating.mask_levels": ["r", "xxx"],
        "content_rating.mask_unrated": True,
        "assistant.image_send": "ask",
    }
    # 空の配列(何もマスクしない)も選べる。
    assert (
        client.patch(PREFS, json={"content_rating.mask_levels": []}).json()[
            "content_rating.mask_levels"
        ]
        == []
    )
    assert client.get(PREFS).json() == {
        "content_rating.mask_levels": [],
        "content_rating.mask_unrated": True,
        "assistant.image_send": "ask",
    }
    with client.app.state.session_factory() as db:
        owners = set(db.execute(select(UserPreference.owner)).scalars().all())
    assert owners == {"local"}


@pytest.mark.parametrize(
    "body",
    [
        {"content_rating.unknown": 1},
        {"theme": "dark"},
        {"content_rating.mask_levels": ["nc17"]},
        {"content_rating.mask_levels": "r"},
        {"content_rating.mask_levels": None},
        {"content_rating.mask_unrated": None},
        {"content_rating.mask_unrated": "yes"},
        {"content_rating.mask_unrated": 1},
        {"assistant.image_send": "never"},
        {"assistant.image_send": None},
    ],
)
def test_invalid_updates_are_rejected(client: TestClient, body: dict) -> None:
    response = client.patch(PREFS, json=body)
    assert response.status_code == 422, response.text
    assert client.get(PREFS).json() == DEFAULTS


def test_broken_stored_value_falls_back_to_default(client: TestClient) -> None:
    with client.app.state.session_factory() as db:
        db.add(UserPreference(owner="local", key="content_rating.mask_levels", value=["bad"]))
        db.commit()
    assert client.get(PREFS).json() == DEFAULTS


def test_preferences_are_per_user_in_oidc_mode(client_oidc: TestClient) -> None:
    client = client_oidc
    assert client.get(PREFS).status_code == 401

    login_as(client, "alice@example.com", "Alice")
    response = client.patch(PREFS, json={"content_rating.mask_levels": ["xxx"]})
    assert response.status_code == 200, response.text
    assert client.get(PREFS).json()["content_rating.mask_levels"] == ["xxx"]

    login_as(client, "bob@example.com", "Bob")
    assert client.get(PREFS).json() == DEFAULTS
    client.patch(PREFS, json={"content_rating.mask_unrated": True})

    login_as(client, "alice@example.com", "Alice")
    assert client.get(PREFS).json() == {
        "content_rating.mask_levels": ["xxx"],
        "content_rating.mask_unrated": False,
        "assistant.image_send": "ask",
    }
    with client.app.state.session_factory() as db:
        owners = set(db.execute(select(UserPreference.owner)).scalars().all())
    assert len(owners) == 2 and "local" not in owners


def test_assistant_image_send(client: TestClient) -> None:
    """アシスタントが画像を LLM に送る前に確かめるか(ADR-0048 14.1)。既定は ask。"""
    response = client.patch(PREFS, json={"assistant.image_send": "always"})
    assert response.status_code == 200, response.text
    assert response.json()["assistant.image_send"] == "always"
    assert (
        client.patch(PREFS, json={"assistant.image_send": "ask"}).json()["assistant.image_send"]
        == "ask"
    )
