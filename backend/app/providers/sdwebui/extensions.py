"""ADR-0038 7章: SD WebUI の拡張機能(`alwayson_scripts`)のアダプター。

対応する拡張機能は GAKEI が1つずつ決める。アダプターは次のものを持つ。

- スクリプト名の判定(例: 小文字で `dynamic prompts` で始まる。版の表記は名前に含まれる)
- GAKEI のパラメーターの定義(フォームに出す項目)
- 引数の差し替えの規則(`/sdapi/v1/script-info` の `label` → 値)と、常に固定する値

引数の並びや数は拡張機能の版で変わるので、位置で決め打ちせず `label` で探す。差し替える
`label` が1つでも見つからなければ、その拡張機能は「対応外の版」として扱わない(項目を出さず、
`alwayson_scripts` も送らない)。差し替えない引数は、script-info の既定値のまま送る。
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.i18n import t
from app.providers.base import ParamDef
from app.providers.sdwebui.client import ScriptInfo

# 組み合わせ生成で作る枚数の上限(ADR-0038 7章)
COMBINATORIAL_MAX_OUTPUTS = 32


@dataclass(frozen=True)
class LabelRule:
    """引数の `label` の探し方。`prefix` は、版によって後ろの説明が変わる label のため。"""

    label: str
    prefix: bool = False

    def matches(self, label: str | None) -> bool:
        if label is None:
            return False
        return label.startswith(self.label) if self.prefix else label == self.label


class ExtensionAdapter(ABC):
    """拡張機能1つ分の対応。"""

    #: 識別子(ログやテスト用。パラメーター名ではない)
    key: str

    @abstractmethod
    def matches_script(self, name: str, *, is_img2img: bool) -> bool:
        """接続先のスクリプト名(と txt2img / img2img の別)がこの拡張機能か。"""

    @abstractmethod
    def rules(self) -> tuple[LabelRule, ...]:
        """差し替える引数の label(すべてそろっているときだけ対応する)。"""

    @abstractmethod
    def param_defs(self) -> list[ParamDef]:
        """フォームに出すパラメーター。"""

    @abstractmethod
    def arg_values(self, params: dict[str, Any]) -> dict[LabelRule, Any]:
        """利用者の値(無ければ既定)と固定の値から、差し替える引数の値を決める。"""

    def output_limit(self, params: dict[str, Any]) -> int | None:
        """出力の枚数が `batch_size` ではなくなるときの上限。普段は None。"""
        return None

    def param_names(self) -> set[str]:
        return {p.name for p in self.param_defs()}


@dataclass(frozen=True)
class ResolvedExtension:
    """接続先で見つかった、対応する拡張機能。`indices` は label の規則 → 引数の位置。"""

    adapter: ExtensionAdapter
    script: ScriptInfo
    indices: dict[LabelRule, int]

    def build_args(self, params: dict[str, Any]) -> list[Any]:
        """引数の全体(script-info の既定値に、規則の値を差し替えたもの)。"""
        args = [copy.deepcopy(arg.value) for arg in self.script.args]
        for rule, value in self.adapter.arg_values(params).items():
            args[self.indices[rule]] = value
        return args


# -- Dynamic Prompts(sd-dynamic-prompts) --------------------------------------------

_DP_ENABLED = LabelRule("Dynamic Prompts enabled")
_DP_COMBINATORIAL = LabelRule("Combinatorial generation")
_DP_MAGIC_PROMPT = LabelRule("Magic prompt")
_DP_FEELING_LUCKY = LabelRule("I'm feeling lucky")
_DP_ATTENTION_GRABBER = LabelRule("Attention grabber")
_DP_JINJA = LabelRule("Enable Jinja2 templates")
_DP_NO_IMAGES = LabelRule("Don't generate images")
# 実物の label は "Max generations (0 = all combinations - the batch count value is ignored)"
_DP_MAX_GENERATIONS = LabelRule("Max generations", prefix=True)

PARAM_DYNAMIC_PROMPTS = "dynamic_prompts"
PARAM_DYNAMIC_PROMPTS_COMBINATORIAL = "dynamic_prompts_combinatorial"


class DynamicPromptsAdapter(ExtensionAdapter):
    """Dynamic Prompts(ADR-0038 7章)。txt2img と img2img の両方。

    script-info には同じ名前で txt2img 用と img2img 用が別にある(`is_img2img`)。引数は
    それぞれの並びから label で探すので、どちらも同じ規則で組み立てられる。
    """

    key = "dynamic_prompts"

    def matches_script(self, name: str, *, is_img2img: bool) -> bool:
        return name.lower().startswith("dynamic prompts")

    def rules(self) -> tuple[LabelRule, ...]:
        return (
            _DP_ENABLED,
            _DP_COMBINATORIAL,
            _DP_MAGIC_PROMPT,
            _DP_FEELING_LUCKY,
            _DP_ATTENTION_GRABBER,
            _DP_JINJA,
            _DP_NO_IMAGES,
            _DP_MAX_GENERATIONS,
        )

    def param_defs(self) -> list[ParamDef]:
        return [
            ParamDef(
                name=PARAM_DYNAMIC_PROMPTS,
                type="bool",
                label=t("sdwebui.params.dynamicPrompts"),
                # WebUI の既定(有効)に合わせる
                default=True,
                form_default=True,
                description=t("sdwebui.params.dynamicPromptsDescription"),
            ),
            ParamDef(
                name=PARAM_DYNAMIC_PROMPTS_COMBINATORIAL,
                type="bool",
                label=t("sdwebui.params.dynamicPromptsCombinatorial"),
                default=False,
                form_default=False,
                description=t(
                    "sdwebui.params.dynamicPromptsCombinatorialDescription",
                    limit=COMBINATORIAL_MAX_OUTPUTS,
                ),
            ),
        ]

    @staticmethod
    def _flags(params: dict[str, Any]) -> tuple[bool, bool]:
        enabled = params.get(PARAM_DYNAMIC_PROMPTS, True) is not False
        combinatorial = params.get(PARAM_DYNAMIC_PROMPTS_COMBINATORIAL, False) is True
        return enabled, combinatorial

    def arg_values(self, params: dict[str, Any]) -> dict[LabelRule, Any]:
        enabled, combinatorial = self._flags(params)
        return {
            _DP_ENABLED: enabled,
            _DP_COMBINATORIAL: combinatorial,
            # 外部のモデルやネットワークを使う、または再現が難しいものは常に無効(ADR-0038 7章)
            _DP_MAGIC_PROMPT: False,
            _DP_FEELING_LUCKY: False,
            _DP_ATTENTION_GRABBER: False,
            _DP_JINJA: False,
            _DP_NO_IMAGES: False,
            # 組み合わせ生成では枚数の上限。そうでなければ既定の 0(すべて。枚数は batch で決まる)
            _DP_MAX_GENERATIONS: COMBINATORIAL_MAX_OUTPUTS if combinatorial else 0,
        }

    def output_limit(self, params: dict[str, Any]) -> int | None:
        enabled, combinatorial = self._flags(params)
        return COMBINATORIAL_MAX_OUTPUTS if enabled and combinatorial else None


ADAPTERS: tuple[ExtensionAdapter, ...] = (DynamicPromptsAdapter(),)


# -- 解決と組み立て ---------------------------------------------------------------------


def _resolve_one(adapter: ExtensionAdapter, script: ScriptInfo) -> ResolvedExtension | None:
    indices: dict[LabelRule, int] = {}
    for rule in adapter.rules():
        index = next((i for i, arg in enumerate(script.args) if rule.matches(arg.label)), None)
        if index is None:
            return None
        indices[rule] = index
    return ResolvedExtension(adapter=adapter, script=script, indices=indices)


def resolve_extensions(
    scripts: Iterable[ScriptInfo],
    *,
    is_img2img: bool = False,
    adapters: Iterable[ExtensionAdapter] = ADAPTERS,
) -> list[ResolvedExtension]:
    """接続先のスクリプトから、使える拡張機能を探す(アダプターごとに最初の1つ)。"""
    scripts = [s for s in scripts if s.is_img2img == is_img2img]
    result: list[ResolvedExtension] = []
    for adapter in adapters:
        for script in scripts:
            if not adapter.matches_script(script.name, is_img2img=is_img2img):
                continue
            resolved = _resolve_one(adapter, script)
            if resolved is not None:
                result.append(resolved)
                break
    return result


def all_param_names(adapters: Iterable[ExtensionAdapter] = ADAPTERS) -> set[str]:
    """どれかのアダプターのパラメーター名(接続先に無い拡張機能の項目を断るため)。"""
    names: set[str] = set()
    for adapter in adapters:
        names |= adapter.param_names()
    return names


def build_alwayson_scripts(
    resolved: Iterable[ResolvedExtension], params: dict[str, Any]
) -> dict[str, Any]:
    """`alwayson_scripts` の本文(`{スクリプト名: {"args": [全引数]}}`)。"""
    return {ext.script.name: {"args": ext.build_args(params)} for ext in resolved}


def output_limit(
    alwayson_scripts: Any,
    params: dict[str, Any],
    *,
    is_img2img: bool = False,
    adapters: Iterable[ExtensionAdapter] = ADAPTERS,
) -> int | None:
    """送った `alwayson_scripts` の中に、出力の枚数を変える拡張機能があれば、その上限。"""
    if not isinstance(alwayson_scripts, dict):
        return None
    limits: list[int] = []
    for name in alwayson_scripts:
        if not isinstance(name, str):
            continue
        for adapter in adapters:
            if adapter.matches_script(name, is_img2img=is_img2img):
                limit = adapter.output_limit(params)
                if limit is not None:
                    limits.append(limit)
    return max(limits) if limits else None
