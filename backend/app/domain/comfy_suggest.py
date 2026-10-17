"""ComfyUI のワークフローの、アシスタント(LLM)での推定(ADR-0052 6章)。保存はしない。

ここは DB にも HTTP にも依存しない純粋な関数群。LLM の呼び出しと `ai.before_send` は API 層
(`app/api/comfyui.py`)が行う。

- **送る要約(`summarize_template`):** 1行が1ノードの JSON(id、`class_type`、独自のタイトル、
  入力、出力の行き先)。テンプレートの JSON そのものは送らない。生の JSON を送ると、テンプレートに
  残った文に対して安全方針による拒否が出たため(ADR-0052 Context)。
  - 配線は `@ノード[出力]`。出力の行き先は `ノード.入力<-[出力]`。
  - 文字列の直値は先頭 `STRING_PREVIEW` 文字と全体の文字数だけにする。
  - 秘密に見える値(ADR-0013 8章の判定)は `***` にする。
  - 入れ子の入力(dict)は `a.b` の名前で平らにする。
- **指示と応答の形(`INSTRUCTIONS`、`RESPONSE_SCHEMA`):** Structured Outputs(strict)で、
  差し込み先の 10 項目と操作、項目ごとの理由の文を受ける。確信度は求めない(自己申告の確信度は
  外れた項目でも高く、当てにならなかった)。公開パラメーターは提案させない。
- **応答の解釈(`interpret`):** 提案を `SuggestedBindings` に直し、保存時と同じ検証
  (`check_workflow`。ADR-0052 3章)に掛ける。エラーになった項目は捨てて、理由を返す。
  `image_alpha` のマスクは、2章の規則で部分編集に使われていなければ捨てる。ほかの警告は項目に
  添える。規則の読み取り(analyze)と同じ値かどうかも項目ごとに返す。
- **ダミー(`fake_response`):** `FAKE_PROVIDER=1` とテスト用。規則の読み取りを基にした決まった
  応答(`ask` の項目は第1候補)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from app.domain.comfy_analyze import AnalyzeResult, BindingField, BindingReading
from app.domain.comfy_graph import is_wire
from app.domain.comfy_workflow import (
    InputRef,
    MaskBinding,
    SuggestedBindings,
    ValidationIssue,
    check_workflow,
)
from app.domain.secret_values import REDACTED, is_secret_input
from app.i18n import t

# 文字列の直値を送る長さ(先頭から)。検証では 60 文字で全問正解だった。
STRING_PREVIEW = 60

SCHEMA_NAME = "gakei_comfy_bindings"

FIELDS: tuple[BindingField, ...] = (
    "prompt",
    "negative_prompt",
    "seed",
    "width",
    "height",
    "batch_size",
    "images",
    "mask",
    "outputs",
    "final_prompt",
)
# 理由の文を受ける項目(差し込み先と操作)。
REASON_FIELDS: tuple[str, ...] = ("operation", *FIELDS)

Operation = Literal["generate", "edit"]


# -- 送る要約 -------------------------------------------------------------------------


def _preview(value: str) -> str:
    if len(value) <= STRING_PREVIEW:
        return value
    return value[:STRING_PREVIEW] + f"…(全{len(value)}文字)"


def _flat_inputs(inputs: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    for name, value in inputs.items():
        path = f"{prefix}{name}"
        if isinstance(value, dict):
            items.extend(_flat_inputs(value, f"{path}."))
        else:
            items.append((path, value))
    return items


def _summary_value(name: str, value: Any) -> Any:
    if is_wire(value):
        return f"@{value[0]}[{value[1]}]"
    if is_secret_input(name, value):
        return REDACTED
    if isinstance(value, str):
        return _preview(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    # 配線でない配列など。中身は文字列にして同じく先頭だけ。
    text = json.dumps(value, ensure_ascii=False, default=str)
    return _preview(text)


def summarize_template(template: dict[str, Any]) -> str:
    """LLM に送るノードの要約(1行が1ノード)。テンプレートの並びのまま。"""
    nodes = {nid: node for nid, node in template.items() if isinstance(node, dict)}
    consumers: dict[str, list[str]] = {}
    for nid, node in nodes.items():
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for name, value in _flat_inputs(inputs):
            if is_wire(value):
                consumers.setdefault(value[0], []).append(f"{nid}.{name}<-[{value[1]}]")
    lines: list[str] = []
    for nid, node in nodes.items():
        class_type = node.get("class_type")
        item: dict[str, Any] = {"id": nid, "class": class_type}
        meta = node.get("_meta")
        title = meta.get("title") if isinstance(meta, dict) else None
        if isinstance(title, str) and title and title != class_type:
            item["title"] = _preview(title)
        inputs = node.get("inputs")
        item["in"] = {
            name: _summary_value(name, value)
            for name, value in (_flat_inputs(inputs) if isinstance(inputs, dict) else [])
        }
        if nid in consumers:
            item["to"] = consumers[nid]
        lines.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines)


INPUT_LEAD = (
    '以下はワークフローの要約。1行が1ノード。in の "@ID[n]" はノード ID の出力 n からの配線、'
    'to はこのノードの出力がつながる先("ノード.入力<-[出力 index]")。長い文字列は途中で切って'
    "ある。秘密に見える値は *** に伏せてある。"
)


def build_input(summary: str) -> str:
    return INPUT_LEAD + "\n\n" + summary


# -- 指示と応答の形 -----------------------------------------------------------------------

_INSTRUCTIONS = """\
あなたは ComfyUI の API 形式ワークフロー(ノード ID → {class_type, inputs})を読み、\
画像生成ツール GAKEI の「差し込み先」を決める。GAKEI は実行のたびに、\
利用者の値をテンプレートの指定された (ノード ID, 入力名) に書き込んでから ComfyUI に送る。\
グラフの構造(ノード・配線)は変えない。\
差し込み先は必ず「配線ではない定数の入力」を指す(配線の入力に書き込むと配線が切れる)。

共通の考え方: 値が配線で届いている入力は、配線を上流へ辿る。\
辿った先が Primitive 系のノード(PrimitiveInt、PrimitiveStringMultiline など、\
値を1つ持つだけのノード)ならその value を選ぶ。\
辿った先が値を計算・加工するノード(解像度の選択、画像からの寸法、文字列の生成・置換など)なら、\
その計算を壊さないよう差し込み先にしない。

各差し込み先:
- operation: 実行のたびに利用者が画像を渡すワークフロー(LoadImage がある。\
用途は編集・インペイント・参照・ControlNet の手本など何でもよい)なら "edit"、\
無ければ "generate"。generate では images を空、mask を null にする。
- prompt(必須): 利用者が書く主たるプロンプトの文字列。エンコーダーに直接の定数があればそれ。\
配線なら上流へ辿り、利用者の文が入る定数の文字列を選ぶ。\
固定の前置き・後置き・システムプロンプト・様式の指定と連結される構成では、\
それら固定文ではなく、利用者が毎回書く文が入る1つを選ぶ(タイトル、長さ、内容から判断する)。
- negative_prompt: ネガティブプロンプトの定数の文字列(上と同じく辿る)。値が空文字でも、\
ネガティブ用の文字列の入力があればそれを選ぶ(1つのエンコーダーが prompt と negative_prompt の両\
方の入力を持ち、positive と negative の両方を出力することもある)。\
ネガティブ側がプロンプトを ConditioningZeroOut したものなど、\
ネガティブ用の文字列の入力がそもそも無いときだけ null。
- seed: 毎回変える乱数のシードの整数入力(複数可)。サンプラーやノイズのノードが複数あり、\
それぞれ別の定数のシードを持つなら、すべて入れる。1つの Primitive が複数に配られているなら、\
その value 1つ。固定値のまま使う補助処理(プロンプト強化の生成など)のシードは入れなくてよい。
- width / height / batch_size: 出力の幅・高さ・枚数(Empty*Latent* ノードなど)。\
共通の考え方のとおり、定数ならその入力、Primitive からの配線なら Primitive の value、\
計算するノードからの配線なら null。\
潜在画像を入力画像から作る(VAEEncode やエンコーダーの出力を使う)なら null。
- images: 実行のたびに利用者が渡す画像(LoadImage の image)の順序付きリスト。1番目が主たる入力。\
エンコーダーの番号付きの入力(image1, image2… や images.image_1…)があれば、\
その番号の順に並べる(ノード ID の順ではない)。間に拡大縮小などのノードを挟んでいても、\
どの番号につながるかで決める。
- mask: インペイントのマスクの渡し方を、MASK が何に使われているかで決める。
  - LoadImageMask があり、\
その出力がマスクとして使われる → {"mode":"load_image_mask","node":そのノード,"input":"image"}
  - 1枚目の LoadImage の MASK 出力(出力 index 1)が、\
どこを描き直すか・どこを合成するかを決める処理(VAEEncodeForInpaint、SetLatentNoiseMask、\
InpaintModelConditioning、ImageCompositeMasked の mask など。\
InvertMask や GrowMask を挟んでもよい)\
に使われている → {"mode":"image_alpha","node":null,"input":null}
  - MASK が透過(アルファ)を保つためだけ(JoinImageWithAlpha の alpha など)に使われている、\
または使われていない → null
- outputs: 最終画像を保存するノード ID(SaveImage 系、0個以上)。\
PreviewImage や画像比較など UI 専用のノードは入れない。保存のノードが無ければ空にする。\
途中経過と最終の両方を保存しているなら、最終のものを入れる(途中も入れてよい)。
- final_prompt: プロンプト強化(LLM による書き換え)\
の最終の文字列を表示・出力するノード(PreviewAny など)の ID。\
整形・連結した後の最終の文を受けるものを選ぶ。プロンプト強化が無ければ null。
- 同じ (node, input) を2回使ってはいけない。\
node はテンプレートのキーの文字列そのまま("12:3" のような形も含む)、\
input は inputs のキーそのまま(ドットを含むことがある)。
- reasons: operation と各差し込み先について、その値にした(または null・空にした)\
短い理由を1文ずつ、{language}で書く。ノードは ID とタイトル(無ければ class_type)で示す。
"""

_LANGUAGES = {"ja": "日本語", "en": "英語"}


def instructions(locale: str) -> str:
    """LLM への指示。理由の文の言語だけを画面の言語に合わせる。"""
    return _INSTRUCTIONS.replace("{language}", _LANGUAGES.get(locale, "日本語"))


def _ref_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"node": {"type": "string"}, "input": {"type": "string"}},
        "required": ["node", "input"],
        "additionalProperties": False,
    }


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "operation": {"type": "string", "enum": ["generate", "edit"]},
        "prompt": _nullable(_ref_schema()),
        "negative_prompt": _nullable(_ref_schema()),
        "seed": {"type": "array", "items": _ref_schema()},
        "width": _nullable(_ref_schema()),
        "height": _nullable(_ref_schema()),
        "batch_size": _nullable(_ref_schema()),
        "images": {"type": "array", "items": _ref_schema()},
        "mask": _nullable(
            {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["load_image_mask", "image_alpha"]},
                    "node": {"type": ["string", "null"]},
                    "input": {"type": ["string", "null"]},
                },
                "required": ["mode", "node", "input"],
                "additionalProperties": False,
            }
        ),
        "outputs": {"type": "array", "items": {"type": "string"}},
        "final_prompt": {"type": ["string", "null"]},
        "reasons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "enum": list(REASON_FIELDS)},
                    "reason": {"type": "string"},
                },
                "required": ["field", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["operation", *FIELDS, "reasons"],
    "additionalProperties": False,
}


# -- 応答の解釈 ---------------------------------------------------------------------------

# 理由の文の長さの上限(画面に出す)。
_REASON_MAX = 400


@dataclass
class SuggestItem:
    """提案した項目1つ。値は `SuggestOutcome.bindings` にある。"""

    field: BindingField
    reason: str
    # 規則の読み取り(analyze)と同じ値か。
    matches_reading: bool
    # 保存時の検証の警告(使われない枝など。保存はできる)。
    warnings: list[str] = field(default_factory=list)


@dataclass
class Discarded:
    """捨てた提案。`target` は `seed[1]`、`outputs.9` のように、どの値かを示す。"""

    field: BindingField
    target: str
    message: str


@dataclass
class SuggestOutcome:
    operation: Operation
    operation_reason: str
    bindings: SuggestedBindings
    items: list[SuggestItem]
    discarded: list[Discarded]


def _parse_ref(value: Any) -> InputRef | None | Literal["bad"]:
    if value is None:
        return None
    if (
        isinstance(value, dict)
        and isinstance(value.get("node"), str)
        and isinstance(value.get("input"), str)
        and value["node"]
        and value["input"]
    ):
        return InputRef(node=value["node"], input=value["input"])
    return "bad"


def _ref_key(ref: InputRef | None) -> tuple[str, str] | None:
    return None if ref is None else (ref.node, ref.input)


def field_value_key(bindings: SuggestedBindings, name: BindingField) -> Any:
    """項目の値を比べられる形にする(seed と出力は集合、画像の枠は順のまま)。"""
    if name == "seed":
        return frozenset(_ref_key(r) for r in bindings.seed)
    if name == "images":
        return tuple(_ref_key(r) for r in bindings.images)
    if name == "outputs":
        return frozenset(bindings.outputs)
    if name == "mask":
        mask = bindings.mask
        return None if mask is None else (mask.mode, mask.node, mask.input)
    if name == "final_prompt":
        return bindings.final_prompt
    return _ref_key(getattr(bindings, name))


def _index_of(target: str, name: str) -> int | None:
    prefix = f"{name}["
    if target.startswith(prefix) and target.endswith("]"):
        try:
            return int(target[len(prefix) : -1])
        except ValueError:
            return None
    return None


def interpret(
    template: dict[str, Any],
    data: dict[str, Any],
    reading: SuggestedBindings,
    fallback_operation: Operation,
) -> SuggestOutcome:
    """LLM の応答を、検証を通った提案と、捨てた項目に分ける。`reading` は規則の読み取りの値。"""
    discarded: list[Discarded] = []
    # 提案があった項目(すべて捨てた項目は外す)。
    proposed: set[BindingField] = set()

    def drop(name: BindingField, target: str, message: str) -> None:
        discarded.append(Discarded(field=name, target=target, message=message))

    bad_shape = t("comfyui.suggest.badShape")

    operation_raw = data.get("operation")
    operation: Operation = (
        operation_raw if operation_raw in ("generate", "edit") else fallback_operation
    )

    values: dict[str, Any] = {}
    for name in ("prompt", "negative_prompt", "width", "height", "batch_size"):
        if name not in data:
            continue
        parsed = _parse_ref(data.get(name))
        if parsed == "bad":
            drop(name, name, bad_shape)  # type: ignore[arg-type]
            continue
        values[name] = parsed
        proposed.add(name)  # type: ignore[arg-type]

    seeds: list[InputRef] = []
    if isinstance(data.get("seed"), list):
        proposed.add("seed")
        for index, raw in enumerate(data["seed"]):
            parsed = _parse_ref(raw)
            if isinstance(parsed, InputRef):
                seeds.append(parsed)
            else:
                drop("seed", f"seed[{index}]", bad_shape)
        if not seeds and data["seed"]:
            proposed.discard("seed")
    values["seed"] = seeds

    images: list[InputRef] = []
    if isinstance(data.get("images"), list):
        proposed.add("images")
        for raw in data["images"]:
            parsed = _parse_ref(raw)
            if not isinstance(parsed, InputRef):
                drop("images", "images", bad_shape)
                images = []
                proposed.discard("images")
                break
            images.append(parsed)
    values["images"] = images

    mask: MaskBinding | None = None
    if "mask" in data:
        raw_mask = data.get("mask")
        if raw_mask is None:
            proposed.add("mask")
        elif isinstance(raw_mask, dict) and raw_mask.get("mode") == "image_alpha":
            mask = MaskBinding(mode="image_alpha")
            proposed.add("mask")
        elif (
            isinstance(raw_mask, dict)
            and raw_mask.get("mode") == "load_image_mask"
            and isinstance(raw_mask.get("node"), str)
            and isinstance(raw_mask.get("input"), str)
            and raw_mask["node"]
            and raw_mask["input"]
        ):
            mask = MaskBinding(
                mode="load_image_mask", node=raw_mask["node"], input=raw_mask["input"]
            )
            proposed.add("mask")
        else:
            drop("mask", "mask", bad_shape)
    values["mask"] = mask

    outputs: list[str] = []
    if isinstance(data.get("outputs"), list):
        proposed.add("outputs")
        for raw in data["outputs"]:
            if isinstance(raw, str) and raw and raw not in outputs:
                outputs.append(raw)
            elif not isinstance(raw, str) or not raw:
                drop("outputs", "outputs", bad_shape)
        if not outputs and data["outputs"]:
            proposed.discard("outputs")
    values["outputs"] = outputs

    if "final_prompt" in data:
        raw_final = data.get("final_prompt")
        if raw_final is None or (isinstance(raw_final, str) and raw_final):
            values["final_prompt"] = raw_final
            proposed.add("final_prompt")
        else:
            drop("final_prompt", "final_prompt", bad_shape)

    # generate は画像の枠とマスクを取らない(ADR-0013)。
    if operation == "generate":
        if values["images"]:
            drop("images", "images", t("comfyui.workflow.generateCannotHaveImageOrMask"))
        if values["mask"] is not None:
            drop("mask", "mask", t("comfyui.workflow.generateCannotHaveImageOrMask"))
        values["images"] = []
        values["mask"] = None
        proposed.discard("images")
        proposed.discard("mask")

    # image_alpha は枠1の LoadImage の MASK を使う形なので、枠が無ければ成り立たない。
    if values["mask"] is not None and values["mask"].mode == "image_alpha" and not values["images"]:
        drop("mask", "mask", t("comfyui.suggest.maskNeedsImage"))
        values["mask"] = None
        proposed.discard("mask")

    bindings = SuggestedBindings(**values)

    # 保存時の検証に掛け、エラーになった値を捨てる。捨てると「生きている部分」の判定(出力が錨)
    # や重複が変わりうるので、エラーが無くなるまで繰り返す(値は減る一方なので必ず止まる)。
    issues: list[ValidationIssue] = []
    for _ in range(len(FIELDS) + len(bindings.seed) + len(bindings.outputs) + 2):
        issues = check_workflow(template, operation, bindings, [])
        bindings, changed = _drop_failed(bindings, issues, drop, proposed)
        if not changed:
            break

    warnings_by_field: dict[str, list[str]] = {}
    for issue in issues:
        if issue.level != "warning" or issue.field is None:
            continue
        name = issue.field.split("[", 1)[0]
        warnings_by_field.setdefault(name, []).append(issue.message)

    reasons: dict[str, str] = {}
    for item in data.get("reasons") or []:
        if not isinstance(item, dict):
            continue
        key, text = item.get("field"), item.get("reason")
        if key in REASON_FIELDS and isinstance(text, str) and text.strip() and key not in reasons:
            reasons[key] = text.strip()[:_REASON_MAX]

    items = [
        SuggestItem(
            field=name,
            reason=reasons.get(name, ""),
            matches_reading=field_value_key(bindings, name) == field_value_key(reading, name),
            warnings=warnings_by_field.get(name, []),
        )
        for name in FIELDS
        if name in proposed
    ]
    return SuggestOutcome(
        operation=operation,
        operation_reason=reasons.get("operation", ""),
        bindings=bindings,
        items=items,
        discarded=discarded,
    )


def _drop_failed(
    bindings: SuggestedBindings,
    issues: list[ValidationIssue],
    drop: Any,
    proposed: set[BindingField],
) -> tuple[SuggestedBindings, bool]:
    """検証のエラーになった値を外した提案を返す。`image_alpha` のマスクは、部分編集に使われて
    いない(警告 `mask_not_inpaint`)ときも外す。"""
    values = bindings.model_dump()
    changed = False
    bad_seeds: set[int] = set()
    bad_outputs: set[str] = set()
    for issue in issues:
        target = issue.field or ""
        if issue.code in ("edit_requires_image", "generate_has_image"):
            # 値の誤りではない(操作との組み合わせ。generate の画像は先に外している)。
            continue
        if issue.level == "warning" and issue.code != "mask_not_inpaint":
            continue
        if target in ("prompt", "negative_prompt", "width", "height", "batch_size"):
            if values[target] is not None:
                drop(target, target, issue.message)
                values[target] = None
                proposed.discard(target)  # type: ignore[arg-type]
                changed = True
        elif target.startswith("seed["):
            index = _index_of(target, "seed")
            if index is not None and index < len(bindings.seed) and index not in bad_seeds:
                drop("seed", target, issue.message)
                bad_seeds.add(index)
        elif target.startswith("images"):
            if values["images"]:
                drop("images", target, issue.message)
                values["images"] = []
                proposed.discard("images")
                changed = True
                # image_alpha は枠1の LoadImage の MASK を使う形なので、枠が無ければ成り立たない。
                if values["mask"] is not None and values["mask"]["mode"] == "image_alpha":
                    drop("mask", "mask", t("comfyui.suggest.maskNeedsImage"))
                    values["mask"] = None
                    proposed.discard("mask")
        elif target == "mask":
            if values["mask"] is not None:
                drop("mask", "mask", issue.message)
                values["mask"] = None
                proposed.discard("mask")
                changed = True
        elif target == "outputs":
            node = issue.node
            if node is not None and node in bindings.outputs and node not in bad_outputs:
                drop("outputs", f"outputs.{node}", issue.message)
                bad_outputs.add(node)
        elif target == "final_prompt":
            if values["final_prompt"] is not None:
                drop("final_prompt", "final_prompt", issue.message)
                values["final_prompt"] = None
                proposed.discard("final_prompt")
                changed = True
    if bad_seeds:
        values["seed"] = [r for i, r in enumerate(values["seed"]) if i not in bad_seeds]
        if not values["seed"]:
            proposed.discard("seed")
        changed = True
    if bad_outputs:
        values["outputs"] = [o for o in values["outputs"] if o not in bad_outputs]
        if not values["outputs"]:
            proposed.discard("outputs")
        changed = True
    return SuggestedBindings.model_validate(values), changed


# -- ダミー(FAKE_PROVIDER=1 とテスト) ---------------------------------------------------------


def _first_candidate(reading: BindingReading | None) -> Any:
    if reading is None or reading.confidence != "ask" or not reading.candidates:
        return None
    return reading.candidates[0]


def fake_response(analysis: AnalyzeResult, locale: str = "ja") -> dict[str, Any]:
    """LLM の応答と同じ形の、決まった提案。規則の読み取りの値を使い、`ask` の項目は第1候補を
    選ぶ。理由の文はダミーであることが分かる文にする。"""
    suggested = analysis.suggested_bindings
    readings = {r.field: r for r in analysis.readings}
    data: dict[str, Any] = json.loads(suggested.model_dump_json())
    for name in ("prompt", "negative_prompt", "width", "height", "batch_size"):
        candidate = _first_candidate(readings.get(name))  # type: ignore[arg-type]
        if candidate is not None and candidate.input:
            data[name] = {"node": candidate.node, "input": candidate.input}
    candidate = _first_candidate(readings.get("outputs"))
    if candidate is not None and not data.get("outputs"):
        data["outputs"] = [candidate.node]
    candidate = _first_candidate(readings.get("final_prompt"))
    if candidate is not None and data.get("final_prompt") is None:
        data["final_prompt"] = candidate.node
    data["operation"] = analysis.suggested_operation
    text = (
        "Fake suggestion (FAKE_PROVIDER). Based on the rule-based reading."
        if locale == "en"
        else "ダミーの提案です(FAKE_PROVIDER)。規則の読み取りを基にしています。"
    )
    data["reasons"] = [{"field": name, "reason": text} for name in REASON_FIELDS]
    return {key: data.get(key) for key in ("operation", *FIELDS, "reasons")}
