"""プロンプトテンプレート(実験)。組み立ての規則、CRUD と丸ごと置換、Run 作成との結合。

組み立て(`app/domain/prompt_templates.py` の `build`)は DB を触らない純粋関数なので、
乱数を `random.Random(種)` で注入して決定的に確かめる。

本人だけに見えること(他人の id は 404、一覧に出ない)は、全ルートを機械的に確かめる
`tests/test_visibility.py` にも載せている。ここでは認証モードで最小限を確かめる。
"""

from __future__ import annotations

import random
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domain import prompt_templates as pt
from app.domain.models import PromptCombo, PromptSlot, PromptTemplate, PromptVariant, Run
from app.domain.schemas import PromptSelections
from tests.conftest import login_as, wait_for_run_terminal

_BASE = "/api/prompt-templates"
_MODEL = "gpt-image-2.5-sunburst"


# -- 組み立てのロジック(純粋関数) ------------------------------------------


def _ids(count: int) -> list[uuid.UUID]:
    """読みやすい、決まった UUID を作る。"""
    return [uuid.UUID(int=i + 1) for i in range(count)]


def _spec(
    *,
    separator: str = ", ",
    slots: tuple[pt.SlotSpec, ...] = (),
    combos: tuple[pt.ComboSpec, ...] = (),
) -> pt.TemplateSpec:
    return pt.TemplateSpec(
        id=uuid.UUID(int=999), name="T", separator=separator, slots=slots, combos=combos
    )


def _slot(
    slot_id: uuid.UUID,
    name: str,
    variants: tuple[pt.VariantSpec, ...] = (),
    *,
    default_mode: str = pt.MODE_RANDOM,
) -> pt.SlotSpec:
    return pt.SlotSpec(id=slot_id, name=name, default_mode=default_mode, variants=variants)


def test_build_joins_in_position_order() -> None:
    s1, s2, s3, v1, v2, v3 = _ids(6)
    spec = _spec(
        slots=(
            _slot(s1, "キャラ", (pt.VariantSpec(v1, "A", "1girl"),)),
            _slot(s2, "表情", (pt.VariantSpec(v2, None, "smile"),)),
            _slot(s3, "背景", (pt.VariantSpec(v3, None, "beach"),)),
        )
    )
    result = pt.build(spec, rng=random.Random(0))
    # スロットの position 順で1本のプロンプトに連結する。
    assert result.prompt == "1girl, smile, beach"
    assert result.build["template_id"] == str(spec.id)
    assert result.build["separator"] == ", "
    assert [e["name"] for e in result.build["slots"]] == ["キャラ", "表情", "背景"]
    assert result.build["slots"][0] == {
        "slot_id": str(s1),
        "name": "キャラ",
        "mode": "random",
        "variant_id": str(v1),
        "variant_name": "A",
        "text": "1girl",
    }


def test_build_skips_empty_slots_with_their_separator() -> None:
    s1, s2, s3, v1, v3 = _ids(5)
    spec = _spec(
        slots=(
            _slot(s1, "前", (pt.VariantSpec(v1, None, "a"),)),
            # 候補が無いスロットと off のスロットは、区切りごと飛ばす。
            _slot(s2, "空"),
            _slot(s3, "後", (pt.VariantSpec(v3, None, "b"),), default_mode=pt.MODE_OFF),
        )
    )
    result = pt.build(spec, rng=random.Random(0))
    assert result.prompt == "a"
    modes = [e["mode"] for e in result.build["slots"]]
    assert modes == ["random", "random", "off"]
    # 空でも内訳には残す(どのスロットが空だったか分かるように)。
    assert result.build["slots"][1]["text"] == ""


def test_build_text_mode_is_passed_through_unchanged() -> None:
    (s1,) = _ids(1)
    spec = _spec(slots=(_slot(s1, "自由"),))
    selections = pt.Selections(
        slots={s1: pt.SlotSelection(mode=pt.MODE_TEXT, text="{a|b} <lora:x:1> (tag:1.2)")}
    )
    result = pt.build(spec, selections, rng=random.Random(0))
    # `{a|b}` などは解釈も展開もしない。
    assert result.prompt == "{a|b} <lora:x:1> (tag:1.2)"
    assert result.build["slots"][0]["mode"] == "text"


def test_build_fixed_requires_a_variant_of_that_slot() -> None:
    s1, s2, v1, v2 = _ids(4)
    spec = _spec(
        slots=(
            _slot(s1, "キャラ", (pt.VariantSpec(v1, None, "a"),)),
            _slot(s2, "衣装", (pt.VariantSpec(v2, None, "b"),)),
        )
    )
    ok = pt.build(spec, pt.Selections(slots={s1: pt.SlotSelection(pt.MODE_FIXED, v1)}))
    assert ok.prompt.startswith("a")

    with pytest.raises(pt.PromptBuildError):
        pt.build(spec, pt.Selections(slots={s1: pt.SlotSelection(pt.MODE_FIXED, v2)}))
    with pytest.raises(pt.PromptBuildError):
        pt.build(spec, pt.Selections(slots={s1: pt.SlotSelection(pt.MODE_FIXED)}))


def test_build_random_follows_weight_and_is_reproducible() -> None:
    s1, v1, v2 = _ids(3)
    spec = _spec(
        slots=(
            _slot(
                s1,
                "キャラ",
                (pt.VariantSpec(v1, None, "a", 1.0), pt.VariantSpec(v2, None, "b", 3.0)),
            ),
        )
    )

    def _draw(seed: int) -> list[str]:
        rng = random.Random(seed)
        return [pt.build(spec, rng=rng).prompt for _ in range(400)]

    first = _draw(1234)
    # 同じ種なら同じ並び(決定的)。
    assert _draw(1234) == first
    # 重み 1:3 なので b が多い(偏りの確認。境界は緩めに取る)。
    share_b = first.count("b") / len(first)
    assert 0.6 < share_b < 0.9


def test_build_weight_zero_is_never_chosen() -> None:
    s1, v1, v2 = _ids(3)
    spec = _spec(
        slots=(
            _slot(
                s1,
                "キャラ",
                (pt.VariantSpec(v1, None, "a", 0.0), pt.VariantSpec(v2, None, "b", 2.0)),
            ),
        )
    )
    rng = random.Random(7)
    assert {pt.build(spec, rng=rng).prompt for _ in range(200)} == {"b"}


def test_build_all_weights_zero_falls_back_to_uniform() -> None:
    """重みの指定が無いのと同じと見て一様に選ぶ(候補があるのに空になるのを避ける)。"""
    s1, v1, v2 = _ids(3)
    spec = _spec(
        slots=(
            _slot(
                s1,
                "キャラ",
                (pt.VariantSpec(v1, None, "a", 0.0), pt.VariantSpec(v2, None, "b", 0.0)),
            ),
        )
    )
    rng = random.Random(3)
    assert {pt.build(spec, rng=rng).prompt for _ in range(200)} == {"a", "b"}


def _combo_spec() -> tuple[pt.TemplateSpec, list[uuid.UUID]]:
    s1, s2, s3, v1, v2, v3, v4, c1, c2 = _ids(9)
    spec = _spec(
        slots=(
            _slot(
                s1, "キャラ", (pt.VariantSpec(v1, "A", "1girl"), pt.VariantSpec(v2, "B", "1boy"))
            ),
            _slot(s2, "衣装", (pt.VariantSpec(v3, "ビキニ", "bikini"),)),
            _slot(s3, "背景", (pt.VariantSpec(v4, "雪山", "snow mountain"),)),
        ),
        combos=(
            pt.ComboSpec(c1, "夏のビーチ", 1.0, (v1, v3)),
            pt.ComboSpec(c2, "冬の山", 1.0, (v2, v4)),
        ),
    )
    return spec, [s1, s2, s3, v1, v2, v3, v4, c1, c2]


def test_build_combo_fills_several_slots_at_once() -> None:
    spec, (s1, s2, s3, v1, _v2, v3, _v4, c1, _c2) = _combo_spec()
    result = pt.build(spec, pt.Selections(combos=(pt.ComboSelection(id=c1),)), rng=random.Random(0))
    assert result.prompt.startswith("1girl, bikini, ")
    assert result.build["combos"] == [{"id": str(c1), "name": "夏のビーチ"}]
    by_slot = {e["slot_id"]: e for e in result.build["slots"]}
    assert by_slot[str(s1)]["mode"] == "combo"
    assert by_slot[str(s1)]["variant_id"] == str(v1)
    assert by_slot[str(s2)]["variant_id"] == str(v3)
    # 組み合わせが覆わなかったスロットは既定(random)で埋まる。
    assert by_slot[str(s3)]["mode"] == "random"


def test_build_overlapping_combos_are_rejected() -> None:
    spec, (*_rest, c1, c2) = _combo_spec()
    # どちらも「キャラ」を埋めるので重なる。
    with pytest.raises(pt.PromptBuildError):
        pt.build(spec, pt.Selections(combos=(pt.ComboSelection(id=c1), pt.ComboSelection(id=c2))))


def test_build_slot_selection_for_a_combo_locked_slot_is_rejected() -> None:
    spec, (s1, _s2, _s3, _v1, v2, _v3, _v4, c1, _c2) = _combo_spec()
    with pytest.raises(pt.PromptBuildError):
        pt.build(
            spec,
            pt.Selections(
                combos=(pt.ComboSelection(id=c1),),
                slots={s1: pt.SlotSelection(pt.MODE_FIXED, v2)},
            ),
        )


def test_build_unknown_combo_and_slot_are_rejected() -> None:
    spec, _ = _combo_spec()
    with pytest.raises(pt.PromptBuildError):
        pt.build(spec, pt.Selections(combos=(pt.ComboSelection(id=uuid.uuid4()),)))
    with pytest.raises(pt.PromptBuildError):
        pt.build(spec, pt.Selections(slots={uuid.uuid4(): pt.SlotSelection(pt.MODE_OFF)}))


def test_build_random_combo_only_picks_one_that_covers_free_slots() -> None:
    spec, (s1, _s2, _s3, _v1, v2, _v3, _v4, c1, c2) = _combo_spec()
    # c1 を固定したあとの {"random": true} は、c1 と重なる c2 を選べないので何もしない。
    result = pt.build(
        spec,
        pt.Selections(combos=(pt.ComboSelection(id=c1), pt.ComboSelection(random=True))),
        rng=random.Random(0),
    )
    assert [c["id"] for c in result.build["combos"]] == [str(c1)]

    # 何も固定していなければ、どちらかが選ばれる。
    rng = random.Random(5)
    picked = {
        pt.build(spec, pt.Selections(combos=(pt.ComboSelection(random=True),)), rng=rng).build[
            "combos"
        ][0]["id"]
        for _ in range(60)
    }
    assert picked == {str(c1), str(c2)}


def test_build_random_combo_without_candidates_is_not_an_error() -> None:
    (s1,) = _ids(1)
    spec = _spec(slots=(_slot(s1, "キャラ"),))
    result = pt.build(spec, pt.Selections(combos=(pt.ComboSelection(random=True),)))
    assert result.build["combos"] == []
    assert result.prompt == ""


# -- API: CRUD -----------------------------------------------------------------


def _payload(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "テンプレA",
        "separator": ", ",
        "slots": [
            {
                "name": "キャラ",
                "variants": [
                    {"ref": "girl", "name": "A", "text": "1girl"},
                    {"ref": "boy", "text": "1boy"},
                ],
            },
            {"name": "衣装", "variants": [{"ref": "bikini", "text": "bikini"}]},
            {"name": "背景", "variants": [{"ref": "beach", "text": "beach"}]},
        ],
        "combos": [
            {
                "name": "夏のビーチ",
                "items": [{"variant_ref": "girl"}, {"variant_ref": "bikini"}],
            }
        ],
    }
    body.update(over)
    return body


def _create(client: TestClient, **over: Any) -> dict[str, Any]:
    response = client.post(_BASE, json=_payload(**over))
    assert response.status_code == 201, response.text
    return response.json()


def test_create_get_and_list(client: TestClient) -> None:
    created = _create(client, name="  テンプレA  ")
    assert created["name"] == "テンプレA"  # 前後の空白は落とす
    assert [s["name"] for s in created["slots"]] == ["キャラ", "衣装", "背景"]
    assert [s["position"] for s in created["slots"]] == [0, 1, 2]
    assert created["slots"][0]["default_mode"] == "random"
    assert [v["text"] for v in created["slots"][0]["variants"]] == ["1girl", "1boy"]
    assert created["slots"][0]["variants"][0]["weight"] == 1.0
    # 組み合わせは、同じリクエストで作った候補を `variant_ref` で指せる。
    girl = created["slots"][0]["variants"][0]["id"]
    bikini = created["slots"][1]["variants"][0]["id"]
    assert created["combos"][0]["variant_ids"] == [girl, bikini]

    fetched = client.get(f"{_BASE}/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created

    listed = client.get(_BASE).json()["items"]
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]
    assert (listed[0]["slot_count"], listed[0]["variant_count"], listed[0]["combo_count"]) == (
        3,
        4,
        1,
    )


def test_list_is_ordered_by_updated_at_desc(client: TestClient) -> None:
    first = _create(client, name="先")
    second = _create(client, name="後")
    ids = [t["id"] for t in client.get(_BASE).json()["items"]]
    assert ids.index(second["id"]) < ids.index(first["id"])

    client.put(f"{_BASE}/{first['id']}", json=_payload(name="更新"))
    ids = [t["id"] for t in client.get(_BASE).json()["items"]]
    assert ids.index(first["id"]) < ids.index(second["id"])


def test_create_rejects_child_ids(client: TestClient) -> None:
    """新規作成で既存の子の id を渡しても、他のテンプレートの子は取り込めない。"""
    body = _payload()
    body["slots"][0]["id"] = str(uuid.uuid4())
    assert client.post(_BASE, json=body).status_code == 422


def test_combo_cannot_point_at_two_variants_of_the_same_slot(client: TestClient) -> None:
    body = _payload()
    body["combos"] = [
        {"name": "ありえない組", "items": [{"variant_ref": "girl"}, {"variant_ref": "boy"}]}
    ]
    response = client.post(_BASE, json=body)
    assert response.status_code == 422, response.text


def test_combo_needs_a_known_reference(client: TestClient) -> None:
    body = _payload()
    body["combos"] = [{"name": "謎", "items": [{"variant_ref": "missing"}]}]
    assert client.post(_BASE, json=body).status_code == 422

    body = _payload()
    body["combos"] = [{"name": "謎", "items": [{"variant_id": str(uuid.uuid4())}]}]
    assert client.post(_BASE, json=body).status_code == 422

    # variant_id と variant_ref はどちらか一方。
    body = _payload()
    body["combos"] = [{"name": "謎", "items": [{}]}]
    assert client.post(_BASE, json=body).status_code == 422


def test_put_replaces_wholesale(client: TestClient) -> None:
    created = _create(client)
    chara = created["slots"][0]
    girl = chara["variants"][0]
    boy = chara["variants"][1]
    costume = created["slots"][1]
    scenery = created["slots"][2]
    combo = created["combos"][0]

    replaced = client.put(
        f"{_BASE}/{created['id']}",
        json={
            "name": "テンプレB",
            "separator": " ",
            "slots": [
                # id を渡した子は残る(id が変わらない)。順番も本文のとおりに振り直す。
                {
                    "id": costume["id"],
                    "name": "衣装(改)",
                    "variants": [{"id": costume["variants"][0]["id"], "text": "dress"}],
                },
                {
                    "id": chara["id"],
                    "name": "キャラ",
                    "default_mode": "off",
                    # boy は本文に無いので論理削除される。新しい候補も足す。
                    "variants": [
                        {"id": girl["id"], "text": "1girl, smile", "weight": 2.5},
                        {"ref": "cat", "text": "cat girl"},
                    ],
                },
            ],
            "combos": [
                {
                    "id": combo["id"],
                    "name": "夏のビーチ(改)",
                    "items": [{"variant_ref": "cat"}, {"variant_id": costume["variants"][0]["id"]}],
                }
            ],
        },
    )
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()
    assert body["name"] == "テンプレB"
    assert body["separator"] == " "
    assert [s["id"] for s in body["slots"]] == [costume["id"], chara["id"]]
    assert [s["position"] for s in body["slots"]] == [0, 1]
    assert body["slots"][1]["default_mode"] == "off"
    # 本文に無かった背景スロットは消える。
    assert scenery["id"] not in [s["id"] for s in body["slots"]]
    kept = body["slots"][1]["variants"]
    assert [v["id"] for v in kept][0] == girl["id"]
    assert kept[0]["text"] == "1girl, smile"
    assert kept[0]["weight"] == 2.5
    assert boy["id"] not in [v["id"] for v in kept]
    cat_id = kept[1]["id"]
    assert body["combos"][0]["id"] == combo["id"]
    # 中身はスロットの順・候補の順に並ぶ(置換後は衣装が先)。
    assert body["combos"][0]["variant_ids"] == [costume["variants"][0]["id"], cat_id]

    # 行は残り、deleted_at が入る(論理削除)。
    with client.app.state.session_factory() as db:
        assert db.get(PromptVariant, uuid.UUID(boy["id"])).deleted_at is not None
        assert db.get(PromptSlot, uuid.UUID(scenery["id"])).deleted_at is not None
        assert db.get(PromptVariant, uuid.UUID(scenery["variants"][0]["id"])).deleted_at is not None

    # 取り直しても同じ。
    assert client.get(f"{_BASE}/{created['id']}").json() == body


def test_put_rejects_children_of_other_templates(client: TestClient) -> None:
    mine = _create(client, name="自分")
    other = _create(client, name="ほか")
    body = _payload()
    body["slots"][0]["id"] = other["slots"][0]["id"]
    assert client.put(f"{_BASE}/{mine['id']}", json=body).status_code == 422

    # 消えた候補を組み合わせから指すのも 422(置換で落ちる候補は指せない)。
    body = _payload()
    body["combos"] = [
        {"name": "謎", "items": [{"variant_id": other["slots"][0]["variants"][0]["id"]}]}
    ]
    assert client.put(f"{_BASE}/{mine['id']}", json=body).status_code == 422


def test_put_drops_deleted_variants_from_combos(client: TestClient) -> None:
    created = _create(client)
    chara = created["slots"][0]
    # 「衣装」スロットを丸ごと落とす。組み合わせはそのまま置くが、指す候補が消える。
    body = _payload()
    body = {
        "name": created["name"],
        "slots": [
            {
                "id": chara["id"],
                "name": "キャラ",
                "variants": [{"id": chara["variants"][0]["id"], "text": "1girl"}],
            }
        ],
        "combos": [
            {
                "id": created["combos"][0]["id"],
                "name": "夏のビーチ",
                "items": [{"variant_id": chara["variants"][0]["id"]}],
            }
        ],
    }
    replaced = client.put(f"{_BASE}/{created['id']}", json=body)
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["combos"][0]["variant_ids"] == [chara["variants"][0]["id"]]


def test_delete_is_logical(client: TestClient) -> None:
    created = _create(client)
    assert client.delete(f"{_BASE}/{created['id']}").status_code == 204
    assert client.get(_BASE).json()["items"] == []
    assert client.get(f"{_BASE}/{created['id']}").status_code == 404
    assert client.put(f"{_BASE}/{created['id']}", json=_payload()).status_code == 404
    assert client.delete(f"{_BASE}/{created['id']}").status_code == 404
    assert client.post(f"{_BASE}/{created['id']}/build", json={}).status_code == 404

    with client.app.state.session_factory() as db:
        row = db.get(PromptTemplate, uuid.UUID(created["id"]))
        assert row is not None and row.deleted_at is not None
        # 子は残す(テンプレートを戻せるようにはしないが、履歴の読み取りの邪魔もしない)。
        assert db.get(PromptCombo, uuid.UUID(created["combos"][0]["id"])).deleted_at is None


def test_unknown_id_is_404(client: TestClient) -> None:
    missing = uuid.uuid4()
    assert client.get(f"{_BASE}/{missing}").status_code == 404
    assert client.put(f"{_BASE}/{missing}", json=_payload()).status_code == 404
    assert client.delete(f"{_BASE}/{missing}").status_code == 404
    assert client.post(f"{_BASE}/{missing}/build", json={}).status_code == 404


# -- API: build ----------------------------------------------------------------


def test_build_endpoint_returns_prompt_and_build(client: TestClient) -> None:
    created = _create(client)
    chara, costume, scenery = created["slots"]
    response = client.post(
        f"{_BASE}/{created['id']}/build",
        json={
            "selections": {
                "slots": {
                    chara["id"]: {"mode": "fixed", "variant_id": chara["variants"][1]["id"]},
                    costume["id"]: {"mode": "text", "text": "school uniform"},
                }
            }
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["prompt"] == "1boy, school uniform, beach"
    assert body["build"]["template_name"] == "テンプレA"
    assert [e["mode"] for e in body["build"]["slots"]] == ["fixed", "text", "random"]
    assert scenery["id"] == body["build"]["slots"][2]["slot_id"]


def test_build_endpoint_seed_makes_it_reproducible(client: TestClient) -> None:
    created = _create(client)
    first = client.post(f"{_BASE}/{created['id']}/build", json={"seed": 42}).json()
    second = client.post(f"{_BASE}/{created['id']}/build", json={"seed": 42}).json()
    assert first == second


def test_build_endpoint_rejects_overlapping_combo(client: TestClient) -> None:
    body = _payload()
    body["combos"] = [
        {"name": "夏", "items": [{"variant_ref": "girl"}, {"variant_ref": "bikini"}]},
        {"name": "冬", "items": [{"variant_ref": "boy"}]},
    ]
    created = client.post(_BASE, json=body).json()
    c1, c2 = (c["id"] for c in created["combos"])
    response = client.post(
        f"{_BASE}/{created['id']}/build",
        json={"selections": {"combos": [{"id": c1}, {"id": c2}]}},
    )
    assert response.status_code == 422, response.text

    # 組み合わせが覆ったスロットは、個別に指定できない。
    response = client.post(
        f"{_BASE}/{created['id']}/build",
        json={
            "selections": {
                "combos": [{"id": c1}],
                "slots": {created["slots"][0]["id"]: {"mode": "off"}},
            }
        },
    )
    assert response.status_code == 422, response.text


# -- Run の作成との結合 ---------------------------------------------------------


def _post_run(client: TestClient, **over: Any) -> Any:
    body: dict[str, Any] = {"operation": "generate", "model": _MODEL, "params": {"n": 1}}
    body.update(over)
    return client.post("/api/runs", json=body)


def test_run_uses_built_prompt_and_records_prompt_build(client: TestClient) -> None:
    created = _create(client)
    chara, costume, _scenery = created["slots"]
    response = _post_run(
        client,
        prompt_template={
            "template_id": created["id"],
            "selections": {
                "slots": {
                    chara["id"]: {"mode": "fixed", "variant_id": chara["variants"][0]["id"]},
                    costume["id"]: {"mode": "fixed", "variant_id": costume["variants"][0]["id"]},
                }
            },
        },
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    # 組み立てた後の文字列が run.prompt に入る(来歴の規則は変えない)。
    assert detail["prompt"] == "1girl, bikini, beach"
    build = detail["prompt_build"]
    assert build["template_id"] == created["id"]
    assert build["template_name"] == "テンプレA"
    assert [e["text"] for e in build["slots"]] == ["1girl", "bikini", "beach"]
    # params には入れない(API に送った値ではない)。
    assert "prompt_build" not in detail["params"]


def test_run_rebuilds_for_every_repeat(client: TestClient) -> None:
    """繰り返し回数と併用したときは Run ごとに引き直す(意味の通ったガチャ)。"""
    body = _payload()
    # 候補を増やして、20 回引けばほぼ確実に 2 種類以上が出るようにする。
    body["slots"] = [
        {
            "name": "キャラ",
            "variants": [{"text": f"c{i}"} for i in range(8)],
        }
    ]
    body["combos"] = []
    created = client.post(_BASE, json=body).json()

    response = _post_run(
        client,
        repeat=20,
        prompt_template={"template_id": created["id"], "selections": {}},
    )
    assert response.status_code == 202, response.text
    run_ids = [r["id"] for r in response.json()["runs"]]
    assert len(run_ids) == 20
    with client.app.state.session_factory() as db:
        rows = [db.get(Run, uuid.UUID(run_id)) for run_id in run_ids]
        prompts = {row.prompt for row in rows}
        assert len(prompts) > 1, prompts
        # 内訳は Run ごとに、その Run で選ばれた候補を指す。
        for row in rows:
            assert row.prompt_build["slots"][0]["text"] == row.prompt


def test_run_requires_exactly_one_of_prompt_and_template(client: TestClient) -> None:
    created = _create(client)
    assert _post_run(client).status_code == 422
    assert (
        _post_run(
            client,
            prompt="a cat",
            prompt_template={"template_id": created["id"], "selections": {}},
        ).status_code
        == 422
    )
    # どちらか一方なら通る。
    assert _post_run(client, prompt="a cat").status_code == 202


def test_run_with_unknown_template_is_404(client: TestClient) -> None:
    response = _post_run(
        client, prompt_template={"template_id": str(uuid.uuid4()), "selections": {}}
    )
    assert response.status_code == 404, response.text


def test_run_with_broken_selection_is_422(client: TestClient) -> None:
    created = _create(client)
    response = _post_run(
        client,
        prompt_template={
            "template_id": created["id"],
            "selections": {
                "slots": {
                    created["slots"][0]["id"]: {"mode": "fixed", "variant_id": str(uuid.uuid4())}
                }
            },
        },
    )
    assert response.status_code == 422, response.text


def test_run_with_empty_built_prompt_is_422(client: TestClient) -> None:
    """全スロットが空ならプロンプトが空になるので、Run を作らずに断る。"""
    created = client.post(
        _BASE,
        json={"name": "空", "slots": [{"name": "キャラ", "default_mode": "off"}], "combos": []},
    ).json()
    response = _post_run(client, prompt_template={"template_id": created["id"], "selections": {}})
    assert response.status_code == 422, response.text


# -- 可視性 ---------------------------------------------------------------------


def test_other_users_templates_are_invisible(client_oidc: TestClient) -> None:
    login_as(client_oidc, "alice@example.com", "Alice")
    mine = _create(client_oidc)
    alice_cookie = client_oidc.cookies.get("gakei_session")

    client_oidc.cookies.clear()
    login_as(client_oidc, "bob@example.com", "Bob")
    assert client_oidc.get(_BASE).json()["items"] == []
    assert client_oidc.get(f"{_BASE}/{mine['id']}").status_code == 404
    assert client_oidc.put(f"{_BASE}/{mine['id']}", json=_payload()).status_code == 404
    assert client_oidc.delete(f"{_BASE}/{mine['id']}").status_code == 404
    assert client_oidc.post(f"{_BASE}/{mine['id']}/build", json={}).status_code == 404
    # Run の作成でも、他人のテンプレートは存在しないのと同じ。
    assert (
        _post_run(
            client_oidc, prompt_template={"template_id": mine["id"], "selections": {}}
        ).status_code
        == 404
    )

    client_oidc.cookies.clear()
    client_oidc.cookies.set("gakei_session", alice_cookie)
    assert [t["id"] for t in client_oidc.get(_BASE).json()["items"]] == [mine["id"]]


# -- スキーマの変換 -------------------------------------------------------------


def test_selections_to_domain() -> None:
    slot_id, variant_id, combo_id = _ids(3)
    selections = PromptSelections.model_validate(
        {
            "combos": [{"id": str(combo_id)}, {"random": True}],
            "slots": {str(slot_id): {"mode": "fixed", "variant_id": str(variant_id)}},
        }
    ).to_domain()
    assert selections.combos == (
        pt.ComboSelection(id=combo_id, random=False),
        pt.ComboSelection(id=None, random=True),
    )
    assert selections.slots == {slot_id: pt.SlotSelection("fixed", variant_id, None)}
