"""ADR-0038: Stable Diffusion WebUI(A1111 互換の API)と通信するだけの低レベルクライアント。

GAKEI の DB や Run には依存しない。`SdWebuiProvider` と `app/api/sdwebui.py` がこれを使う。

使う API(2026-10-10 に Forge で確認。ADR-0038 Context):

- `GET /sdapi/v1/options`: 到達性の確認と、Forge か A1111 かの判定
  (`forge_additional_modules` のキーがあれば Forge)。中身(パスを含む)は外に出さない。
- `GET /sdapi/v1/sd-models`、`/samplers`、`/schedulers`、VAE の一覧(Forge は
  `/sdapi/v1/sd-modules`、A1111 は `/sdapi/v1/sd-vae`)。応答の `filename`(フルパス)は
  読み捨て、名前だけを持つ。
- `GET /sdapi/v1/scripts`、`/sdapi/v1/script-info`: 拡張機能(`alwayson_scripts`)の名前と引数の
  並び(ADR-0038 7章)。取れなければ拡張機能なしとして扱い、一覧の取得は止めない。
- `POST /sdapi/v1/refresh-checkpoints`: チェックポイントの一覧を WebUI に読み直させる。
- `GET /sdapi/v1/loras`、`POST /sdapi/v1/refresh-loras`: LoRA の一覧と読み直し(ADR-0038 8章)。
  `path` とメタ情報は `loras.py` で読み捨て、name・alias・ベースモデル・トリガーの候補だけを持つ。
- `POST /sdapi/v1/txt2img`: 応答 `{"images": [base64...], "info": "<JSON 文字列>"}`。
- `POST /sdapi/v1/img2img`: 本文に `init_images`(base64 の配列)と `mask`(base64。白が描き直す
  範囲)。応答は txt2img と同じ形。
- `POST /internal/progress`(`force_task_id` を付けた実行だけの進み具合)。使えなければ
  `GET /sdapi/v1/progress` に切り替える。

`--api-auth` の Basic 認証は、`/sdapi/v1/*` にも `/internal/*` にも同じ資格情報を付けて送る。
資格情報はログ・例外のメッセージに含めない。

同期の呼び出し(`check_available`、`fetch_catalog`、`refresh_checkpoints`)は、
capabilities と設定画面の API から使う。実行(`txt2img`、`img2img`、`progress`)は非同期。
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from app.i18n import t
from app.providers.sdwebui.loras import LoraInfo, parse_loras

Flavor = Literal["forge", "a1111"]
Credentials = tuple[str, str]

# 一覧の取得に使う既定のタイムアウト(秒)
CATALOG_TIMEOUT_SECONDS = 5.0
# LoRA の一覧(メタ情報を含むので大きくなりうる)の取得のタイムアウト(秒)
LORA_TIMEOUT_SECONDS = 30.0
# 失敗の要約の長さの上限
_SUMMARY_MAX_LENGTH = 500


class SdWebuiError(Exception):
    """クライアントの失敗。`code` は `run.error_code` にそのまま使う値。

    code: sdwebuiUnavailable / sdwebuiValidation / executionError
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class Availability:
    """接続の確認の結果。`reason` は機械可読のコード、`message` は利用者向けの文言。

    reason: apiNotEnabled(404) / unauthorized(401・403) / unreachable(接続できない) /
            timeout / unexpectedResponse / parseFailed
    """

    available: bool
    reason: str | None = None
    message: str | None = None
    flavor: Flavor | None = None


@dataclass(frozen=True)
class Checkpoint:
    """チェックポイント。`model_name` は拡張子なしの名前で、`info.sd_model_name` と比べる。

    `hash` は `/sdapi/v1/sd-models` の短いハッシュ(10 桁。infotext の `Model hash` と同じ)。
    WebUI がまだ計算していなければ `sha256` の先頭 10 桁、それも無ければ None。画像の生成情報を
    フォームに読み込むとき(ADR-0038 9章)の照合にだけ使い、応答には出さない。
    """

    title: str
    model_name: str
    hash: str | None = None


@dataclass(frozen=True)
class ScriptArg:
    """`/sdapi/v1/script-info` の引数の1つ。`value` は WebUI の画面の既定値。"""

    label: str | None
    value: Any


@dataclass(frozen=True)
class ScriptInfo:
    """拡張機能などのスクリプト1つ(ADR-0038 7章)。`name` は WebUI の表記(小文字)のまま。

    `/sdapi/v1/scripts` の一覧にあり、`/sdapi/v1/script-info` に引数の並びがあるものだけを持つ。
    """

    name: str
    is_img2img: bool
    is_alwayson: bool
    args: tuple[ScriptArg, ...]


@dataclass(frozen=True)
class Catalog:
    """接続先から取った一覧。パスは持たない。"""

    flavor: Flavor
    checkpoints: list[Checkpoint]
    samplers: list[str] = field(default_factory=list)
    schedulers: list[str] = field(default_factory=list)
    # スケジューラーの名前 → 表示名(infotext の `Schedule type` は表示名。ADR-0038 9章)
    scheduler_labels: dict[str, str] = field(default_factory=dict)
    vaes: list[str] = field(default_factory=list)
    # 拡張機能のスクリプト(取れなければ空。ADR-0038 7章)
    scripts: list[ScriptInfo] = field(default_factory=list)


@dataclass(frozen=True)
class ProgressSnapshot:
    """1回の進捗の問い合わせの結果。`progress` は 0〜1。"""

    progress: float | None
    active: bool = False
    queued: bool = False
    completed: bool = False
    live_preview: bytes | None = None
    id_live_preview: int | None = None
    step: int | None = None
    steps: int | None = None


def _join(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def _auth(credentials: Credentials | None) -> httpx.BasicAuth | None:
    if credentials is None:
        return None
    return httpx.BasicAuth(credentials[0], credentials[1])


def _status_failure(status_code: int) -> Availability:
    if status_code == 404:
        return Availability(False, "apiNotEnabled", t("sdwebui.client.apiNotEnabled"))
    if status_code in (401, 403):
        return Availability(False, "unauthorized", t("sdwebui.client.unauthorized"))
    return Availability(
        False, "unexpectedResponse", t("sdwebui.client.unexpectedResponse", status=status_code)
    )


def _truncate(text: str) -> str:
    text = text.replace("\x00", "")
    if len(text) > _SUMMARY_MAX_LENGTH:
        return text[: _SUMMARY_MAX_LENGTH - 1] + "…"
    return text


def summarize_validation_error(body: Any) -> str:
    """WebUI の 422(FastAPI の検証エラー)を短い要約にする。

    `detail` の各要素から `loc` と `msg` だけを使う(`input` には送った本文、つまり base64 が
    入りうるので使わない)。
    """
    parts: list[str] = []
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, list):
        for item in detail:
            if not isinstance(item, dict):
                continue
            loc = item.get("loc")
            msg = item.get("msg")
            loc_text = ".".join(str(x) for x in loc) if isinstance(loc, list) else ""
            if isinstance(msg, str) and msg:
                parts.append(f"{loc_text}: {msg}" if loc_text else msg)
    elif isinstance(detail, str) and detail:
        parts.append(detail)
    if not parts:
        return t("sdwebui.client.rejectedNoDetails")
    return _truncate(" / ".join(parts))


def summarize_execution_error(body: Any) -> str:
    """WebUI の 500 を短い要約にする(`error`、`errors`、`detail` の文字列だけを使う)。"""
    parts: list[str] = []
    if isinstance(body, dict):
        for key in ("error", "errors", "detail"):
            value = body.get(key)
            if isinstance(value, str) and value.strip() and value not in parts:
                parts.append(value.strip())
    if not parts:
        return t("sdwebui.client.executionFailedNoDetails")
    return _truncate(": ".join(parts))


def _names(body: Any, key: str) -> list[str]:
    """`[{key: ...}, ...]` から文字列の値を順に取り出す(重複は除く)。"""
    result: list[str] = []
    if not isinstance(body, list):
        return result
    for item in body:
        if isinstance(item, dict):
            value = item.get(key)
            if isinstance(value, str) and value and value not in result:
                result.append(value)
    return result


def _parse_checkpoints(body: Any) -> list[Checkpoint]:
    result: list[Checkpoint] = []
    seen: set[str] = set()
    if not isinstance(body, list):
        return result
    for item in body:
        if not isinstance(item, dict):
            continue
        model_name = item.get("model_name")
        title = item.get("title")
        if not isinstance(model_name, str) or not model_name or model_name in seen:
            continue
        seen.add(model_name)
        result.append(
            Checkpoint(
                title=title if isinstance(title, str) and title else model_name,
                model_name=model_name,
                hash=_checkpoint_hash(item),
            )
        )
    return result


def _checkpoint_hash(item: dict[str, Any]) -> str | None:
    """短いハッシュ(小文字)。`hash` が無ければ `sha256` の先頭 10 桁(WebUI の短いハッシュと
    同じ)。"""
    value = item.get("hash")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    sha256 = item.get("sha256")
    if isinstance(sha256, str) and len(sha256.strip()) >= 10:
        return sha256.strip().lower()[:10]
    return None


def _labels(body: Any) -> dict[str, str]:
    """`[{name, label}, ...]` から名前 → 表示名を取り出す(表示名の無いものは除く)。"""
    result: dict[str, str] = {}
    if not isinstance(body, list):
        return result
    for item in body:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        label = item.get("label")
        if isinstance(name, str) and name and isinstance(label, str) and label:
            result.setdefault(name, label)
    return result


def _is_vae_path(filename: Any) -> bool:
    """Forge の `sd-modules` の `filename` が、VAE のフォルダーの下にあるか。パスは判定にだけ
    使い、どこにも残さない。"""
    if not isinstance(filename, str):
        return False
    segments = filename.replace("\\", "/").split("/")[:-1]
    return any(segment.lower() == "vae" for segment in segments)


def _parse_forge_modules(body: Any) -> list[str]:
    """Forge の `sd-modules` には VAE とテキストエンコーダーが混ざる。VAE のフォルダーに
    あるものが1つでもあればそれだけに絞り、判別できなければ全部を候補にする。"""
    if not isinstance(body, list):
        return []
    entries = [item for item in body if isinstance(item, dict)]
    vae_entries = [item for item in entries if _is_vae_path(item.get("filename"))]
    return _names(vae_entries or entries, "model_name")


def _parse_script_args(raw: Any) -> tuple[ScriptArg, ...] | None:
    if not isinstance(raw, list):
        return None
    args: list[ScriptArg] = []
    for item in raw:
        if not isinstance(item, dict):
            # 位置をずらさないため、形の崩れた引数も「label なし・値 None」として残す。
            args.append(ScriptArg(label=None, value=None))
            continue
        label = item.get("label")
        args.append(
            ScriptArg(label=label if isinstance(label, str) else None, value=item.get("value"))
        )
    return tuple(args)


def parse_scripts(scripts_body: Any, script_info_body: Any) -> list[ScriptInfo]:
    """`/sdapi/v1/scripts` と `/sdapi/v1/script-info` の応答を合わせる。

    `scripts` は `{"txt2img": [名前...], "img2img": [名前...]}`。`script-info` は
    `[{"name", "is_alwayson", "is_img2img", "args": [{"label", "value", ...}]}]` で、同じ名前の
    txt2img 用と img2img 用が別の要素になる。両方にあるものだけを返す。
    """
    if not isinstance(scripts_body, dict) or not isinstance(script_info_body, list):
        return []
    listed: dict[bool, set[str]] = {}
    for key, is_img2img in (("txt2img", False), ("img2img", True)):
        names = scripts_body.get(key)
        listed[is_img2img] = (
            {n for n in names if isinstance(n, str)} if isinstance(names, list) else set()
        )
    result: list[ScriptInfo] = []
    seen: set[tuple[str, bool]] = set()
    for item in script_info_body:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        is_img2img = bool(item.get("is_img2img"))
        if name not in listed[is_img2img] or (name, is_img2img) in seen:
            continue
        args = _parse_script_args(item.get("args"))
        if args is None:
            continue
        seen.add((name, is_img2img))
        result.append(
            ScriptInfo(
                name=name,
                is_img2img=is_img2img,
                is_alwayson=bool(item.get("is_alwayson")),
                args=args,
            )
        )
    return result


def decode_base64_image(value: Any) -> bytes | None:
    """base64(`data:image/...;base64,` の接頭辞があれば除く)をバイト列にする。"""
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("data:"):
        _, _, value = value.partition(",")
    try:
        return base64.b64decode(value, validate=False)
    except (binascii.Error, ValueError):
        return None


class SdWebuiClient:
    """A1111 互換の API を薄く包んだクライアント。

    `transport` はテストで偽の WebUI(`httpx.MockTransport`)を注入するため。同期と非同期の
    どちらの `httpx` クライアントにも同じものを使う。
    """

    def __init__(
        self,
        base_url: str,
        *,
        credentials: Credentials | None = None,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._credentials = credentials
        self._transport = transport

    # -- 同期 ----------------------------------------------------------------------

    def _sync_client(self, timeout: float) -> httpx.Client:
        kwargs: dict[str, Any] = {"timeout": timeout, "auth": _auth(self._credentials)}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def check_available(self, timeout: float = 1.0) -> Availability:
        """`GET /sdapi/v1/options` で到達性を確かめる。"""
        try:
            with self._sync_client(timeout) as http:
                response = http.get(_join(self.base_url, "/sdapi/v1/options"))
        except httpx.TimeoutException:
            return Availability(False, "timeout", t("sdwebui.client.connectTimeout"))
        except httpx.HTTPError:
            # 例外の文字列は URL を含みうるが資格情報は含まない。それでも文言には出さない。
            return Availability(False, "unreachable", t("sdwebui.client.connectFailed"))
        if response.status_code != 200:
            return _status_failure(response.status_code)
        try:
            body = response.json()
        except ValueError:
            return Availability(False, "parseFailed", t("sdwebui.client.parseFailed"))
        if not isinstance(body, dict):
            return Availability(False, "parseFailed", t("sdwebui.client.parseFailed"))
        flavor: Flavor = "forge" if "forge_additional_modules" in body else "a1111"
        return Availability(True, flavor=flavor)

    def fetch_catalog(self, timeout: float = CATALOG_TIMEOUT_SECONDS) -> Catalog:
        """チェックポイント・サンプラー・スケジューラー・VAE の一覧を取る。

        チェックポイントの一覧が取れなければ `SdWebuiError`。サンプラーなど補助の一覧は、
        取れなければ空にする(項目を出さない。ADR-0038 2章)。
        """
        try:
            with self._sync_client(timeout) as http:
                options = self._get_required(http, "/sdapi/v1/options")
                flavor: Flavor = (
                    "forge"
                    if isinstance(options, dict) and "forge_additional_modules" in options
                    else "a1111"
                )
                checkpoints = _parse_checkpoints(self._get_required(http, "/sdapi/v1/sd-models"))
                samplers = _names(self._get_optional(http, "/sdapi/v1/samplers"), "name")
                schedulers_body = self._get_optional(http, "/sdapi/v1/schedulers")
                schedulers = _names(schedulers_body, "name")
                scheduler_labels = _labels(schedulers_body)
                if flavor == "forge":
                    vaes = _parse_forge_modules(self._get_optional(http, "/sdapi/v1/sd-modules"))
                else:
                    vaes = _names(self._get_optional(http, "/sdapi/v1/sd-vae"), "model_name")
                scripts = self._fetch_scripts(http)
        except httpx.TimeoutException as exc:
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.connectTimeout")) from exc
        except httpx.HTTPError as exc:
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.connectFailed")) from exc
        return Catalog(
            flavor=flavor,
            checkpoints=checkpoints,
            samplers=samplers,
            schedulers=schedulers,
            scheduler_labels=scheduler_labels,
            vaes=vaes,
            scripts=scripts,
        )

    def _fetch_scripts(self, http: httpx.Client) -> list[ScriptInfo]:
        """拡張機能のスクリプトの一覧(ADR-0038 7章)。取れなければ(接続の失敗も含めて)空にし、
        本体の生成は止めない。"""
        try:
            scripts_body = self._get_optional(http, "/sdapi/v1/scripts")
            if scripts_body is None:
                return []
            info_body = self._get_optional(http, "/sdapi/v1/script-info")
        except httpx.HTTPError:
            return []
        return parse_scripts(scripts_body, info_body)

    def refresh_checkpoints(self, timeout: float = 30.0) -> None:
        try:
            with self._sync_client(timeout) as http:
                response = http.post(_join(self.base_url, "/sdapi/v1/refresh-checkpoints"))
        except httpx.TimeoutException as exc:
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.connectTimeout")) from exc
        except httpx.HTTPError as exc:
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.connectFailed")) from exc
        if response.status_code != 200:
            failure = _status_failure(response.status_code)
            raise SdWebuiError("sdwebuiUnavailable", failure.message or "")

    def fetch_loras(self, timeout: float = LORA_TIMEOUT_SECONDS) -> list[LoraInfo]:
        """`GET /sdapi/v1/loras`(ADR-0038 8章)。`path` とメタ情報は `parse_loras` で読み捨てる。

        LoRA の機能が無い(404)ときは空の一覧。接続できなければ `SdWebuiError`。
        """
        try:
            with self._sync_client(timeout) as http:
                response = http.get(_join(self.base_url, "/sdapi/v1/loras"))
        except httpx.TimeoutException as exc:
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.connectTimeout")) from exc
        except httpx.HTTPError as exc:
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.connectFailed")) from exc
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            failure = _status_failure(response.status_code)
            raise SdWebuiError("sdwebuiUnavailable", failure.message or "")
        body = _safe_json(response)
        if not isinstance(body, list):
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.parseFailed"))
        return parse_loras(body)

    def refresh_loras(self, timeout: float = 30.0) -> None:
        """`POST /sdapi/v1/refresh-loras`。LoRA の機能が無い(404)ときは何もしない。"""
        try:
            with self._sync_client(timeout) as http:
                response = http.post(_join(self.base_url, "/sdapi/v1/refresh-loras"))
        except httpx.TimeoutException as exc:
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.connectTimeout")) from exc
        except httpx.HTTPError as exc:
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.connectFailed")) from exc
        if response.status_code not in (200, 404):
            failure = _status_failure(response.status_code)
            raise SdWebuiError("sdwebuiUnavailable", failure.message or "")

    def _get_required(self, http: httpx.Client, path: str) -> Any:
        response = http.get(_join(self.base_url, path))
        if response.status_code != 200:
            failure = _status_failure(response.status_code)
            raise SdWebuiError("sdwebuiUnavailable", failure.message or "")
        try:
            return response.json()
        except ValueError as exc:
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.parseFailed")) from exc

    def _get_optional(self, http: httpx.Client, path: str) -> Any:
        """補助の一覧。404 などは None(その項目を出さない)。接続の失敗は呼び出し側へ。"""
        response = http.get(_join(self.base_url, path))
        if response.status_code != 200:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    # -- 非同期(実行) ------------------------------------------------------------

    def async_client(self, timeout: float | None = 30.0) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": timeout, "auth": _auth(self._credentials)}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def txt2img(
        self, http: httpx.AsyncClient, body: dict[str, Any], *, timeout: float | None
    ) -> dict[str, Any]:
        """`POST /sdapi/v1/txt2img`。応答の JSON(`images`、`info` など)を返す。"""
        return await self._post_generation(http, "/sdapi/v1/txt2img", body, timeout=timeout)

    async def img2img(
        self, http: httpx.AsyncClient, body: dict[str, Any], *, timeout: float | None
    ) -> dict[str, Any]:
        """`POST /sdapi/v1/img2img`(`init_images` と `mask` は base64)。応答は txt2img と同じ形。"""
        return await self._post_generation(http, "/sdapi/v1/img2img", body, timeout=timeout)

    async def _post_generation(
        self, http: httpx.AsyncClient, path: str, body: dict[str, Any], *, timeout: float | None
    ) -> dict[str, Any]:
        try:
            response = await http.post(_join(self.base_url, path), json=body, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.requestTimeout")) from exc
        except httpx.HTTPError as exc:
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.connectFailed")) from exc

        if response.status_code == 422:
            raise SdWebuiError(
                "sdwebuiValidation", summarize_validation_error(_safe_json(response))
            )
        if response.status_code >= 500:
            raise SdWebuiError("executionError", summarize_execution_error(_safe_json(response)))
        if response.status_code != 200:
            failure = _status_failure(response.status_code)
            raise SdWebuiError("sdwebuiUnavailable", failure.message or "")
        body_json = _safe_json(response)
        if not isinstance(body_json, dict):
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.parseFailed"))
        return body_json

    async def internal_progress(
        self, http: httpx.AsyncClient, task_id: str, id_live_preview: int
    ) -> ProgressSnapshot | None:
        """`POST /internal/progress`。この API が無い(404・405)ときは None。

        接続の失敗は `SdWebuiError`(呼び出し側は進捗を諦めるだけで、実行は続ける)。
        """
        try:
            response = await http.post(
                _join(self.base_url, "/internal/progress"),
                json={"id_task": task_id, "id_live_preview": id_live_preview, "live_preview": True},
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.connectFailed")) from exc
        if response.status_code in (404, 405):
            return None
        if response.status_code != 200:
            raise SdWebuiError(
                "sdwebuiUnavailable",
                t("sdwebui.client.unexpectedResponse", status=response.status_code),
            )
        body = _safe_json(response)
        if not isinstance(body, dict):
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.parseFailed"))
        progress = body.get("progress")
        id_preview = body.get("id_live_preview")
        return ProgressSnapshot(
            progress=float(progress) if isinstance(progress, int | float) else None,
            active=bool(body.get("active")),
            queued=bool(body.get("queued")),
            completed=bool(body.get("completed")),
            live_preview=decode_base64_image(body.get("live_preview")),
            id_live_preview=id_preview if isinstance(id_preview, int) else None,
        )

    async def public_progress(self, http: httpx.AsyncClient) -> ProgressSnapshot:
        """`GET /sdapi/v1/progress`。WebUI で今動いているもの(誰が始めたかを問わない)の
        進み具合なので、目安として使う(ADR-0038 4章)。"""
        try:
            response = await http.get(
                _join(self.base_url, "/sdapi/v1/progress"),
                params={"skip_current_image": "false"},
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.connectFailed")) from exc
        if response.status_code != 200:
            raise SdWebuiError(
                "sdwebuiUnavailable",
                t("sdwebui.client.unexpectedResponse", status=response.status_code),
            )
        body = _safe_json(response)
        if not isinstance(body, dict):
            raise SdWebuiError("sdwebuiUnavailable", t("sdwebui.client.parseFailed"))
        progress = body.get("progress")
        state = body.get("state") if isinstance(body.get("state"), dict) else {}
        step = state.get("sampling_step")
        steps = state.get("sampling_steps")
        return ProgressSnapshot(
            progress=float(progress) if isinstance(progress, int | float) else None,
            active=True,
            live_preview=decode_base64_image(body.get("current_image")),
            step=step if isinstance(step, int) else None,
            steps=steps if isinstance(steps, int) else None,
        )


def parse_info(value: Any) -> dict[str, Any]:
    """応答の `info`(JSON 文字列)を dict にする。読めなければ空の dict。"""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None
