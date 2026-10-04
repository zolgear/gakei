"""ComfyUI のグラフに書かれた「秘密に見える値」の判定(ADR-0013 8章、ADR-0029 3章・4章)。

カスタムノードには、API キーなどを入力に直接書くものがある。判定はここ1か所に置き、
登録時の警告(`comfy_workflow.analyze_workflow`)と、共有のページ(`shares`)の両方から使う。

判定は推測で、誤りも漏れもありうる。次のどちらかに当たる入力を秘密に見えるものとする。

- 入力の名前を語に分けたとき(`_`・`-`・`.`・空白・camelCase の切れ目。大文字小文字は無視)、
  秘密の語に当たる(`api_key`、`token`、`password` など)。値が空(空白だけ)のものは除く。
- 値が、よく知られた API キーの形に当たる(`sk-…`、`hf_…`、`ghp_…`、`AKIA…` など)。

値が文字列でないもの(数値、ノードへのリンク `["6", 0]` など)は対象外。
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

# 伏せた値の置き換え。
REDACTED = "***"

# 単独で秘密の語とみなす語(名前を語に分け、小文字にしたもの)。
_SECRET_WORDS = frozenset(
    {
        "apikey",
        "apikeys",
        "token",
        "secret",
        "secrets",
        "password",
        "passwords",
        "passwd",
        "passphrase",
        "credential",
        "credentials",
        "authorization",
        "auth",
        "bearer",
        "accesskey",
        "privatekey",
        "clientsecret",
    }
)

# 2語の並びで秘密の語とみなすもの(`api_key`、`access_key`、`private_key` など)。
_SECRET_PAIRS = frozenset(
    {
        ("api", "key"),
        ("api", "keys"),
        ("access", "key"),
        ("private", "key"),
        ("secret", "key"),
    }
)

# `token` と並ぶと、秘密ではなく数・設定を表す語(`max_tokens` は `tokens` なので元から当たらない。
# `token_count`、`max_token`、`token_limit` のような名前を外すため)。
_TOKEN_QUANTITY_WORDS = frozenset(
    {
        "count",
        "counts",
        "max",
        "min",
        "num",
        "number",
        "limit",
        "length",
        "len",
        "size",
        "budget",
        "total",
        "per",
        "ratio",
        "merging",
        "weight",
        "weights",
        "id",
        "ids",
        "type",
        "index",
    }
)

# camelCase の切れ目(`apiKey` → api, Key。`APIKey` → API, Key。`HFToken2` → HF, Token, 2)。
_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_SEPARATOR_RE = re.compile(r"[\s_\-.:/]+")


def split_name_words(name: str) -> list[str]:
    """入力の名前を語に分ける(小文字にして返す)。"""
    words: list[str] = []
    for part in _SEPARATOR_RE.split(name):
        words.extend(w.lower() for w in _WORD_RE.findall(part))
    return words


def is_secret_name(name: str) -> bool:
    """入力の名前が秘密の語に当たるか。"""
    words = split_name_words(name)
    for index, word in enumerate(words):
        if word == "token":
            # `token_count` のように数・設定を表す語と並ぶものは外す。
            if any(other in _TOKEN_QUANTITY_WORDS for other in words if other != word):
                continue
            return True
        if word in _SECRET_WORDS:
            return True
        if index + 1 < len(words) and (word, words[index + 1]) in _SECRET_PAIRS:
            return True
    return False


# よく知られた API キーの形。プロンプトの文章の中の短い語で当たらないよう、十分な長さを要求し、
# 前に英数字が続く位置(`task-…` の `sk-` など)では当てない。
_BEFORE = r"(?<![A-Za-z0-9])"
_SECRET_VALUE_RES: tuple[re.Pattern[str], ...] = (
    # OpenAI(`sk-`、`sk-proj-`、`sk-svcacct-`、`sk-admin-`)、Anthropic(`sk-ant-`)
    re.compile(_BEFORE + r"sk-(?:proj-|svcacct-|admin-|ant-)?[A-Za-z0-9_\-]{20,}"),
    # Hugging Face
    re.compile(_BEFORE + r"hf_[A-Za-z0-9]{30,}"),
    # GitHub(`ghp_`、`gho_`、`ghu_`、`ghs_`、`ghr_`、`github_pat_`)
    re.compile(_BEFORE + r"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(_BEFORE + r"github_pat_[A-Za-z0-9_]{40,}"),
    # AWS のアクセスキー ID
    re.compile(_BEFORE + r"(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])"),
    # Google の API キー
    re.compile(_BEFORE + r"AIza[0-9A-Za-z_\-]{35}"),
    # Slack
    re.compile(_BEFORE + r"xox[abprs]-[A-Za-z0-9\-]{10,}"),
    # Replicate
    re.compile(_BEFORE + r"r8_[A-Za-z0-9]{30,}"),
    # `Bearer <長いトークン>`
    re.compile(_BEFORE + r"[Bb]earer\s+[A-Za-z0-9._~+/=\-]{20,}"),
)
_DIGIT_RE = re.compile(r"\d")


def looks_like_secret_value(value: str) -> bool:
    """値が、よく知られた API キーの形を含むか。乱数で作るキーは数字を含むので、数字を含まない
    並び(英単語をハイフンでつないだ文章など)は当てない。"""
    for pattern in _SECRET_VALUE_RES:
        for match in pattern.finditer(value):
            if _DIGIT_RE.search(match.group()):
                return True
    return False


def is_secret_input(name: Any, value: Any) -> bool:
    """1つの入力(名前と値)が秘密に見えるか。値が文字列でないもの・空のものは当てない。"""
    if not isinstance(value, str) or not value.strip():
        return False
    if isinstance(name, str) and is_secret_name(name):
        return True
    return looks_like_secret_value(value)


@dataclass(frozen=True)
class SecretInput:
    """秘密に見える値が書かれた入力(値そのものは持たない)。"""

    node_id: str
    class_type: str | None
    input: str


def find_secret_inputs(graph: Any) -> list[SecretInput]:
    """API 形式のグラフ(`{node_id: {class_type, inputs, _meta}}`)のうち、秘密に見える入力。
    並びはグラフのノードの順、各ノードの入力の順。"""
    found: list[SecretInput] = []
    if not isinstance(graph, dict):
        return found
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        class_type = node.get("class_type")
        for input_path in _secret_paths(inputs):
            found.append(
                SecretInput(
                    node_id=str(node_id),
                    class_type=class_type if isinstance(class_type, str) else None,
                    input=input_path,
                )
            )
    return found


def _secret_paths(inputs: dict[str, Any], prefix: str = "") -> list[str]:
    """入力の dict のうち、秘密に見える値の位置(`images.image_1` のようにドットでつなぐ)。
    入れ子の dict の入力(可変長の入力など)の中も見る。"""
    paths: list[str] = []
    for name, value in inputs.items():
        path = f"{prefix}{name}"
        if isinstance(value, dict):
            paths.extend(_secret_paths(value, f"{path}."))
        elif is_secret_input(name, value):
            paths.append(path)
    return paths


def _redact_inputs(inputs: dict[str, Any]) -> None:
    """`_secret_paths` と同じ判定で、入れ子の dict の中まで値を `***` に置き換える(その場で)。"""
    for name, value in inputs.items():
        if isinstance(value, dict):
            _redact_inputs(value)
        elif is_secret_input(name, value):
            inputs[name] = REDACTED


def redact_graph(graph: Any) -> Any:
    """グラフの写しの、秘密に見える入力の値を `***` に置き換えたもの(キーは残す)。元のグラフは
    変えない。グラフの形でなければ写しをそのまま返す。"""
    redacted = copy.deepcopy(graph)
    if not isinstance(redacted, dict):
        return redacted
    for node in redacted.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        _redact_inputs(inputs)
    return redacted


def _is_public_param_key(key: Any) -> bool:
    # 公開パラメーター(ワークフローの登録時に選んだもの)は、トップレベルの `comfyui_*` 以外。
    return isinstance(key, str) and not key.startswith("comfyui_")


def redact_comfyui_params(params: dict[str, Any]) -> dict[str, Any]:
    """ComfyUI の Run の `params` の写しのうち、送ったグラフ(`comfyui_prompt`)の各ノードの入力と、
    公開パラメーターの秘密に見える値を `***` に置き換えたもの(キーは残す)。元の値は変えない。"""
    result: dict[str, Any] = {}
    for key, value in params.items():
        if key == "comfyui_prompt":
            result[key] = redact_graph(value)
        elif _is_public_param_key(key) and is_secret_input(key, value):
            result[key] = REDACTED
        else:
            result[key] = copy.deepcopy(value)
    return result


def comfyui_params_have_secret(params: Any) -> bool:
    """ComfyUI の Run の `params` に秘密に見える値があるか(送ったグラフと公開パラメーター)。"""
    if not isinstance(params, dict):
        return False
    if find_secret_inputs(params.get("comfyui_prompt")):
        return True
    return any(
        _is_public_param_key(key) and is_secret_input(key, value) for key, value in params.items()
    )
