"""知覚ハッシュ(ADR-0033 12章): 計算、距離、しきい値、重複の候補での使い方、worker。

しきい値は `tests/perceptual_images.py` の画像の組で決めた。thumb(長辺 512px の WebP)から
作ったハッシュの距離は次のとおり(6 つのテーマ × 種 4 つと、docs/images のスクリーンショット
14 枚で測った値。2026-10-03)。

| 組 | dHash | 4×4 の Lab | 色相のヒストグラム |
|---|---|---|---|
| 劣化させた重複(382 組) | 0〜14 | 0.00〜6.18 | 0.00〜3.68 |
| 色違い(168 組) | 0〜50 | 3.72〜83.74 | 4.82〜84.35 |
| 同じテーマの別の画像(120 組) | 20〜41 | 1.99〜43.83 | 0.31〜19.18 |

- 劣化: 縮小 50%/25%、JPEG q60/q25、その組み合わせ、1〜3% の切り抜き(中央と片寄り)。
- 色違い: 色相の 60/120/180/240 度の回転、チャンネルの入れ替え(BGR、GRB、BRG)。
- 同じテーマの別の画像: 同じ作り方で乱数の種だけを変えた画像と、別の画面のスクリーンショット。
- 参考: 同じ画面の日本語版と英語版のスクリーンショットは dHash 0〜4、Lab 0.13〜0.47 で、
  ハッシュでは区別できない(文字だけが違う画像は、ハッシュでは重複に見える)。

決めた値は dHash ≤ 17、Lab ≤ 9.0、色相 ≤ 4.2(すべて満たせば近い)。このテストでは、テスト
時間を抑えるため種を減らして同じ性質を確かめる。
"""

from __future__ import annotations

import io
import itertools
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select, update

from app.domain import perceptual_hash as ph
from app.domain.models import AssetEmbedding, AssetPerceptualHash
from app.domain.semantic_search import filter_by_hash, leader_groups
from app.embedding.catalog import XENOVA_CLIP_REVISION
from tests import perceptual_images as pi

ACTIVE_KEY = f"fake:onnx:clip-vit-b32-u8@{XENOVA_CLIP_REVISION}"
DOCS_IMAGES = Path(__file__).resolve().parents[2] / "docs" / "images"


def _hash(image: Image.Image) -> ph.PerceptualHash:
    return ph.compute(pi.thumb(image))


def _distances(a: ph.PerceptualHash, b: ph.PerceptualHash) -> tuple[int, float, float]:
    return (
        ph.dhash_distance(a.dhash, b.dhash),
        ph.color_distance(a.color, b.color),
        ph.hue_distance(a.color, b.color),
    )


# -- 計算と距離 ---------------------------------------------------------------------


def test_compute_is_deterministic_and_compact() -> None:
    image = pi.scene("abstract", 1)
    a, b = ph.compute(image), ph.compute(image.copy())
    assert a == b
    assert len(a.dhash) == ph.DHASH_BYTES == 8
    assert len(a.color) == ph.COLOR_BYTES == 64
    assert a.version == ph.ALGORITHM_VERSION
    assert _distances(a, b) == (0, 0.0, 0.0)
    assert ph.is_close(a, b)


def test_dhash_bits() -> None:
    # 単色は隣との差が無いので全部 0。
    flat = ph.dhash_bytes(Image.new("RGB", (64, 64), (120, 80, 40)))
    assert flat == bytes(8)
    # 左から右へ暗くなる画像は全部 1。
    ramp = np.tile(np.linspace(255, 0, 90).astype(np.uint8), (80, 1))
    assert ph.dhash_bytes(Image.fromarray(ramp, "L").convert("RGB")) == b"\xff" * 8
    assert ph.dhash_distance(flat, b"\xff" * 8) == 64
    assert ph.dhash_distance(b"\x80" + bytes(7), bytes(8)) == 1


def test_transparent_pixels_are_white() -> None:
    """埋め込みの前処理と同じく、透明な部分は白で合成する。"""
    transparent = Image.new("RGBA", (64, 64), (255, 0, 0, 0))
    white = Image.new("RGB", (64, 64), (255, 255, 255))
    assert ph.compute(transparent) == ph.compute(white)
    # 白の Lab は L*=100、a*=b*=0、彩度が無いので色相のヒストグラムは空。
    color = ph.color_bytes(white)
    assert color[:3] == bytes([255, 128, 128])
    assert color[48:] == bytes(16)


def test_color_and_hue_distance() -> None:
    red = ph.color_bytes(Image.new("RGB", (64, 64), (220, 30, 30)))
    green = ph.color_bytes(Image.new("RGB", (64, 64), (30, 200, 30)))
    assert ph.color_distance(red, red) == 0.0
    assert ph.hue_distance(red, red) == 0.0
    assert ph.color_distance(red, green) > 50
    assert ph.hue_distance(red, green) > 50


def test_from_stored_rejects_old_versions_and_bad_shapes() -> None:
    value = ph.compute(pi.scene("poster", 1))
    assert ph.from_stored(value.dhash, value.color, value.version) == value
    assert ph.from_stored(value.dhash, value.color, ph.ALGORITHM_VERSION + 1) is None
    assert ph.from_stored(value.dhash, value.color, None) is None
    assert ph.from_stored(value.dhash[:7], value.color, value.version) is None
    assert ph.from_stored(value.dhash, value.color[:48], value.version) is None
    assert ph.from_stored(None, value.color, value.version) is None


# -- しきい値(画像の組) -------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(pi.SCENES))
def test_thresholds_on_synthetic_scenes(name: str) -> None:
    """劣化させた重複はすべて近く、色違いと同じテーマの別の画像はどれも近くない。"""
    bases = {seed: pi.scene(name, seed) for seed in (1, 2, 3)}
    hashes = {seed: _hash(image) for seed, image in bases.items()}

    for seed in (1, 2):
        for label, degrade in pi.DEGRADATIONS.items():
            other = _hash(degrade(bases[seed]))
            assert ph.is_close(hashes[seed], other), (
                name,
                seed,
                label,
                _distances(hashes[seed], other),
            )
        for label, recolor in pi.COLOR_VARIANTS.items():
            other = _hash(recolor(bases[seed]))
            assert not ph.is_close(hashes[seed], other), (
                name,
                seed,
                label,
                _distances(hashes[seed], other),
            )
    for a, b in itertools.combinations(hashes, 2):
        assert not ph.is_close(hashes[a], hashes[b]), (name, a, b, _distances(hashes[a], hashes[b]))


def _screenshots() -> list[Path]:
    return sorted(DOCS_IMAGES.glob("*-ja.webp"))


@pytest.mark.skipif(not DOCS_IMAGES.is_dir(), reason="docs/images が無い")
@pytest.mark.parametrize("path", _screenshots(), ids=lambda p: p.stem)
def test_thresholds_on_degraded_screenshots(path: Path) -> None:
    """実際の画面のスクリーンショット(細かい文字と小さな色の部品が多い)を劣化させても近い。"""
    image = Image.open(path).convert("RGB")
    base = _hash(image)
    for label in ("s25_q25", "crop3_corner", "crop3_s25_q25", "crop3c_s50_q60"):
        other = _hash(pi.DEGRADATIONS[label](image))
        assert ph.is_close(base, other), (path.name, label, _distances(base, other))


@pytest.mark.skipif(not DOCS_IMAGES.is_dir(), reason="docs/images が無い")
def test_different_screenshots_are_not_close() -> None:
    hashes = {p.stem: _hash(Image.open(p).convert("RGB")) for p in _screenshots()}
    for a, b in itertools.combinations(hashes, 2):
        assert not ph.is_close(hashes[a], hashes[b]), (a, b, _distances(hashes[a], hashes[b]))


def test_close_pairs_matches_is_close() -> None:
    images = [pi.scene("landscape", 1), pi.scene("landscape", 2)]
    images += [pi.DEGRADATIONS["s50_q25"](images[0]), pi.COLOR_VARIANTS["hue60"](images[0])]
    hashes: list[ph.PerceptualHash | None] = [_hash(image) for image in images]
    hashes.append(None)
    table = ph.decode_many(hashes)
    assert table.present.tolist() == [True, True, True, True, False]
    pairs = [(i, j) for i, j in itertools.combinations(range(4), 2)]
    got = ph.close_pairs(table, np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs]))
    expected = [ph.is_close(hashes[i], hashes[j]) for i, j in pairs]  # type: ignore[arg-type]
    assert got.tolist() == expected
    assert expected == [False, True, False, False, False, False]


# -- 重複の候補での使い方 -------------------------------------------------------------


def test_filter_and_group_drop_color_variants_and_do_not_chain() -> None:
    """CLIP がすべて重複と言っても、色違いはハッシュで外れ、連鎖でまとまらない。"""
    base = pi.scene("portrait", 1)
    # 位置は新しい順(0 がいちばん新しい)。3 が元の画像。
    images = [
        pi.COLOR_VARIANTS["bgr"](base),  # 0: 色違い
        pi.DEGRADATIONS["crop3_s25_q25"](base),  # 1: 劣化させた重複
        pi.scene("portrait", 2),  # 2: 同じテーマの別の画像
        base,  # 3
    ]
    hashes: list[ph.PerceptualHash | None] = [_hash(image) for image in images]
    pairs = [(i, j, 0.95) for i, j in itertools.combinations(range(4), 2)]
    kept = filter_by_hash(pairs, hashes)
    assert [(i, j) for i, j, _ in kept] == [(1, 3)]
    assert leader_groups(4, kept) == [(3, [(1, 0.95)])]

    # 2 にハッシュが無ければ、2 を含む組は CLIP だけで判定する(残る)。代表 3 が 1 と 2 を
    # 取り、2 とだけ組の 0 は入らない。
    hashes[2] = None
    kept = filter_by_hash(pairs, hashes)
    assert [(i, j) for i, j, _ in kept] == [(0, 2), (1, 2), (1, 3), (2, 3)]
    assert leader_groups(4, kept) == [(3, [(1, 0.95), (2, 0.95)])]
    assert filter_by_hash([], hashes) == []


# -- worker -------------------------------------------------------------------------


def _upload(client: TestClient, image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", buffer.getvalue(), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _enable(client: TestClient) -> None:
    response = client.patch("/api/settings/embeddings", json={"enabled": True})
    assert response.status_code == 200, response.text


def _wait(predicate: Any, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise TimeoutError("待ちきれませんでした")


def _embedded(client: TestClient, asset_id: str) -> bool:
    with client.app.state.session_factory() as db:
        row = db.get(AssetEmbedding, (uuid.UUID(asset_id), ACTIVE_KEY))
        return row is not None and row.status == "succeeded"


def _stored(client: TestClient, asset_id: str) -> AssetPerceptualHash | None:
    with client.app.state.session_factory() as db:
        return db.get(AssetPerceptualHash, uuid.UUID(asset_id))


def _thumb_hash(client: TestClient, asset_id: str) -> ph.PerceptualHash:
    response = client.get(f"/api/assets/{asset_id}/content", params={"variant": "thumb"})
    assert response.status_code == 200
    return ph.compute(Image.open(io.BytesIO(response.content)))


def test_worker_stores_hash_with_embedding(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client, pi.scene("city", 1))
    _wait(lambda: _embedded(client, asset_id))
    row = _stored(client, asset_id)
    assert row is not None
    assert row.version == ph.ALGORITHM_VERSION
    assert ph.from_stored(row.dhash, row.color, row.version) == _thumb_hash(client, asset_id)


def test_hash_failure_does_not_fail_embedding(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(image: Image.Image) -> ph.PerceptualHash:
        raise ValueError("壊れた")

    monkeypatch.setattr(ph, "compute", broken)
    _enable(client)
    asset_id = _upload(client, pi.scene("pattern", 1))
    _wait(lambda: _embedded(client, asset_id))
    assert _stored(client, asset_id) is None
    # 失敗した画像は、プロセスが動いている間は埋め戻しでも試さない。
    assert uuid.UUID(asset_id) in client.app.state.embedder._hash_failed


def test_worker_backfills_missing_and_outdated_hashes(client: TestClient) -> None:
    """埋め込みはあるのにハッシュが無い・版が古い画像は、待ち行列が空のときに埋める。"""
    _enable(client)
    first = _upload(client, pi.scene("abstract", 1))
    second = _upload(client, pi.scene("abstract", 2))
    _wait(lambda: _embedded(client, first) and _embedded(client, second))
    with client.app.state.session_factory() as db:
        db.execute(
            AssetPerceptualHash.__table__.delete().where(
                AssetPerceptualHash.asset_id == uuid.UUID(first)
            )
        )
        db.execute(
            update(AssetPerceptualHash)
            .where(AssetPerceptualHash.asset_id == uuid.UUID(second))
            .values(version=0, dhash=bytes(8))
        )
        db.commit()
    client.app.state.embedder.notify()

    def refilled() -> bool:
        rows = [_stored(client, a) for a in (first, second)]
        return all(r is not None and r.version == ph.ALGORITHM_VERSION for r in rows)

    _wait(refilled)
    for asset_id in (first, second):
        row = _stored(client, asset_id)
        assert row is not None
        assert ph.from_stored(row.dhash, row.color, row.version) == _thumb_hash(client, asset_id)
    with client.app.state.session_factory() as db:
        assert len(db.execute(select(AssetPerceptualHash)).all()) == 2


def test_duplicates_api_uses_hashes(client: TestClient) -> None:
    """API: 色違いは CLIP のしきい値を下げても出ない。ハッシュが無い画像には印が付く。"""
    _enable(client)
    base = pi.scene("landscape", 3)
    original = _upload(client, base)
    degraded = _upload(client, pi.DEGRADATIONS["s50_q25"](base))
    variant = _upload(client, pi.COLOR_VARIANTS["hue180"](base))
    ids = (original, degraded, variant)
    _wait(lambda: all(_embedded(client, a) for a in ids))

    body = client.get("/api/embeddings/duplicates", params={"threshold": 0.5}).json()
    groups = [[a["id"] for a in g["assets"]] for g in body["groups"]]
    assert groups == [[original, degraded]]
    group = body["groups"][0]
    assert group["hash_missing"] is False
    assert [a["hash_missing"] for a in group["assets"]] == [False, False]
    # 代表(古いほう)の値はメンバーとの類似度の最大値。メンバーは代表との類似度。
    assert group["assets"][0]["max_score"] == group["assets"][1]["max_score"] == group["max_score"]

    # 劣化させた重複のハッシュを消すと、その画像の組は CLIP だけで判定し、印を付ける。
    # worker が埋め戻さないよう、作れなかった画像として扱わせる。
    client.app.state.embedder._hash_failed.add(uuid.UUID(degraded))
    with client.app.state.session_factory() as db:
        db.execute(
            AssetPerceptualHash.__table__.delete().where(
                AssetPerceptualHash.asset_id == uuid.UUID(degraded)
            )
        )
        db.commit()
    body = client.get("/api/embeddings/duplicates", params={"threshold": 0.5}).json()
    # 代表は元の画像(いちばん古い)。色違いは元の画像とハッシュで外れ、劣化させた重複とは
    # CLIP だけで組になりうるが、劣化させた重複は既に元の画像のグループに入っている。
    groups = [[a["id"] for a in g["assets"]] for g in body["groups"]]
    assert groups == [[original, degraded]]
    group = body["groups"][0]
    assert group["hash_missing"] is True
    assert [a["hash_missing"] for a in group["assets"]] == [False, True]
