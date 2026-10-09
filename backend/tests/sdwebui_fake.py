"""偽の SD WebUI(A1111 互換の API。ADR-0038)。テスト専用。

`httpx.MockTransport` で、状態を持つ `FakeSdWebui` が応答を作る。実物の WebUI には接続しない。
モデル名などはすべて架空のもの。

使い方::

    fake = FakeSdWebui(flavor="forge")
    client = SdWebuiClient("http://127.0.0.1:7860", transport=fake.transport())

- `flavor`: "forge"(`/options` に `forge_additional_modules`、VAE は `/sd-modules`)か
  "a1111"(`sd_vae`、VAE は `/sd-vae`。`/sd-modules` は 404)。
- `api_enabled=False`: `/sdapi/v1/*` が 404(`--api` なしで起動したとき)。
- `credentials=("user", "pass")`: `/sdapi/v1/*` に Basic 認証を掛ける(無い・違えば 401)。
- 一覧に無いチェックポイントを `override_settings` で指定されたら、今読み込んでいる
  `current_model` で描いたことにする(実物と同じ)。
- `internal_progress=False`: `/internal/progress` が 404。
- `txt2img_delay`: 応答までの秒数(非同期のときだけ。進捗の問い合わせを試すため)。img2img にも効く。
- `POST /sdapi/v1/img2img`: 受け取った本文を `img2img_bodies` に、base64 を読んだ入力画像と
  マスクを `img2img_init_images` / `img2img_masks` に残す(テストから検査するため)。応答は
  txt2img と同じ形(Dynamic Prompts は img2img 用の引数で展開する)。
- `enable_dynamic_prompts()`: 拡張機能 Dynamic Prompts(架空の版 `dynamic prompts v9.9.9`)を
  `/sdapi/v1/scripts` と `/sdapi/v1/script-info` に足す。`{a|b}` を1枚ずつ展開し、
  `info.all_prompts` に入れる(組み合わせ生成では組み合わせの数だけ描く)。既定は拡張機能なし。
- `scripts_status`: `/sdapi/v1/scripts` と `/script-info` の応答の状態(500 などで失敗を試す)。
- `return_grid=True`: 実物の `return_grid` と同じく、先頭にグリッドを足し
  `index_of_first_image = 1` を返す(2枚以上のとき)。
"""

from __future__ import annotations

import asyncio
import base64
import io
import itertools
import json
import re
from typing import Any

import httpx
from PIL import Image

FAKE_PATH_ROOT = "/opt/fake-webui/models"

DYNAMIC_PROMPTS_NAME = "dynamic prompts v9.9.9"
MAX_GENERATIONS_LABEL = "Max generations (0 = all combinations - the batch count value is ignored)"

# Dynamic Prompts の txt2img の引数(label と既定値)。並びは実物のある版に合わせた架空のもの。
DYNAMIC_PROMPTS_ARGS: list[tuple[str, Any]] = [
    ("Dynamic Prompts enabled", True),
    ("Combinatorial generation", False),
    ("Combinatorial batches", 1),
    ("Magic prompt", False),
    ("I'm feeling lucky", False),
    ("Attention grabber", False),
    ("Minimum attention", 1.1),
    ("Maximum attention", 1.5),
    ("Max magic prompt length", 100),
    ("Magic prompt creativity", 0.7),
    ("Fixed seed", False),
    ("Unlink seed from prompt", False),
    ("Don't apply to negative prompts", True),
    ("Enable Jinja2 templates", False),
    ("Don't generate images", False),
    (MAX_GENERATIONS_LABEL, 0),
    ("Magic prompt model", "model-x"),
    ("Magic prompt blocklist regex", ""),
]

_VARIANT_GROUP = re.compile(r"\{([^{}]*)\}")


def prompt_variants(prompt: str) -> list[str]:
    """`{a|b}` の組み合わせをすべて並べる(入れ子・重み・ワイルドカードは扱わない簡易版)。"""
    groups = [m.group(1).split("|") for m in _VARIANT_GROUP.finditer(prompt)]
    if not groups:
        return [prompt]
    result: list[str] = []
    for combo in itertools.product(*groups):
        parts = iter(combo)
        result.append(_VARIANT_GROUP.sub(lambda _m, parts=parts: next(parts), prompt))
    return result


def _script_info_entry(name: str, args: list[tuple[str, Any]], *, is_img2img: bool) -> dict:
    return {
        "name": name,
        "is_alwayson": True,
        "is_img2img": is_img2img,
        "args": [
            {
                "label": label,
                "value": value,
                "minimum": None,
                "maximum": None,
                "step": None,
                "choices": ["model-x"] if label == "Magic prompt model" else None,
            }
            for label, value in args
        ],
    }


def png_bytes(color: tuple[int, int, int] = (10, 20, 30), size: tuple[int, int] = (8, 8)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_bytes(size: tuple[int, int] = (8, 8)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 100, 50)).save(buffer, format="JPEG")
    return buffer.getvalue()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class _Transport(httpx.MockTransport):
    """txt2img だけ、非同期のときに待ちを入れる。"""

    def __init__(self, fake: FakeSdWebui) -> None:
        super().__init__(fake.handle)
        self._fake = fake

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if (
            request.url.path in ("/sdapi/v1/txt2img", "/sdapi/v1/img2img")
            and self._fake.txt2img_delay
        ):
            self._fake.txt2img_started = True
            await asyncio.sleep(self._fake.txt2img_delay)
        return await super().handle_async_request(request)


class FakeSdWebui:
    def __init__(
        self,
        *,
        flavor: str = "forge",
        api_enabled: bool = True,
        credentials: tuple[str, str] | None = None,
    ) -> None:
        self.flavor = flavor
        self.api_enabled = api_enabled
        self.credentials = credentials
        self.checkpoints: list[dict[str, Any]] = [
            {
                "title": "model-a.safetensors [0123456789]",
                "model_name": "model-a",
                "hash": "01234567",
                "sha256": "0" * 64,
                "filename": f"{FAKE_PATH_ROOT}/Stable-diffusion/model-a.safetensors",
                "config": None,
            },
            {
                "title": "model-b.safetensors",
                "model_name": "model-b",
                "hash": None,
                "sha256": None,
                "filename": f"{FAKE_PATH_ROOT}/Stable-diffusion/model-b.safetensors",
                "config": None,
            },
        ]
        self.current_model = "model-a"
        self.samplers = ["Euler a", "Euler", "DPM++ 2M"]
        self.schedulers: list[str] | None = ["automatic", "karras"]
        self.vaes = ["vae-a.safetensors", "vae-b.safetensors"]
        # Forge の sd-modules に混ざるテキストエンコーダー(VAE の一覧には出さない)
        self.text_encoders = ["te-a.safetensors"]
        self.internal_progress = True
        self.progress_values = [0.25, 0.5, 0.75]
        self.live_preview: bytes | None = None
        self.txt2img_delay = 0.0
        self.txt2img_started = False
        self.txt2img_status = 200
        self.txt2img_error_body: Any = None
        self.extra_images = 0
        self.return_no_images = False
        self.requests: list[httpx.Request] = []
        self.txt2img_bodies: list[dict[str, Any]] = []
        self.img2img_bodies: list[dict[str, Any]] = []
        self.img2img_init_images: list[list[bytes]] = []
        self.img2img_masks: list[bytes | None] = []
        self.progress_bodies: list[dict[str, Any]] = []
        self.refresh_count = 0
        self._progress_index = 0
        # 拡張機能(ADR-0038 7章)。`scripts` は /sdapi/v1/scripts、`script_infos` は script-info。
        self.scripts: dict[str, list[str]] = {"txt2img": ["refiner", "seed"], "img2img": []}
        self.script_infos: list[dict[str, Any]] = []
        self.scripts_status = 200
        self.return_grid = False

    def enable_dynamic_prompts(
        self,
        *,
        name: str = DYNAMIC_PROMPTS_NAME,
        args: list[tuple[str, Any]] | None = None,
        img2img_args: list[tuple[str, Any]] | None = None,
    ) -> None:
        """Dynamic Prompts を足す。`args` で引数の並び(版の違い)を変えられる。
        `img2img_args` は img2img 用の並び(省けば `args` と同じ)。"""
        txt2img_args = list(DYNAMIC_PROMPTS_ARGS if args is None else args)
        i2i_args = txt2img_args if img2img_args is None else list(img2img_args)
        self.scripts["txt2img"].append(name)
        self.scripts["img2img"].append(name)
        # 実物と同じく、同じ名前で txt2img 用と img2img 用が並ぶ
        self.script_infos.append(_script_info_entry(name, txt2img_args, is_img2img=False))
        self.script_infos.append(_script_info_entry(name, i2i_args, is_img2img=True))

    def _dynamic_prompts_args(
        self, body: dict[str, Any], *, is_img2img: bool = False
    ) -> dict[str, Any] | None:
        """この実行での Dynamic Prompts の引数(label → 値)。拡張機能が無ければ None。"""
        entry = next(
            (
                e
                for e in self.script_infos
                if e["is_img2img"] == is_img2img and e["name"].startswith("dynamic prompts")
            ),
            None,
        )
        if entry is None:
            return None
        values = {a["label"]: a["value"] for a in entry["args"]}
        sent = (body.get("alwayson_scripts") or {}).get(entry["name"])
        if isinstance(sent, dict) and isinstance(sent.get("args"), list):
            for arg, value in zip(entry["args"], sent["args"], strict=False):
                values[arg["label"]] = value
        return values

    def transport(self) -> httpx.MockTransport:
        return _Transport(self)

    # -- 応答 -----------------------------------------------------------------------

    def _authorized(self, request: httpx.Request) -> bool:
        if self.credentials is None:
            return True
        header = request.headers.get("authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
        except ValueError:
            return False
        return decoded == f"{self.credentials[0]}:{self.credentials[1]}"

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path

        if path == "/internal/progress":
            if not self.internal_progress:
                return httpx.Response(404, json={"detail": "Not Found"})
            return self._internal_progress(request)

        if not path.startswith("/sdapi/v1/"):
            return httpx.Response(404, json={"detail": "Not Found"})
        if not self.api_enabled:
            return httpx.Response(404, json={"detail": "Not Found"})
        if not self._authorized(request):
            return httpx.Response(401, json={"detail": "Not authenticated"})

        name = path[len("/sdapi/v1/") :]
        if name == "options" and request.method == "GET":
            options: dict[str, Any] = {
                "sd_model_checkpoint": self.current_model,
                "outdir_samples": f"{FAKE_PATH_ROOT}/../outputs",
            }
            if self.flavor == "forge":
                options["forge_additional_modules"] = [f"{FAKE_PATH_ROOT}/VAE/{self.vaes[0]}"]
            else:
                options["sd_vae"] = "Automatic"
            return httpx.Response(200, json=options)
        if name == "sd-models":
            return httpx.Response(200, json=self.checkpoints)
        if name == "samplers":
            return httpx.Response(
                200, json=[{"name": s, "aliases": [], "options": {}} for s in self.samplers]
            )
        if name == "schedulers":
            if self.schedulers is None:
                return httpx.Response(404, json={"detail": "Not Found"})
            return httpx.Response(
                200, json=[{"name": s, "label": s.title()} for s in self.schedulers]
            )
        if name == "sd-modules":
            if self.flavor != "forge":
                return httpx.Response(404, json={"detail": "Not Found"})
            entries = [
                {"model_name": v, "filename": f"{FAKE_PATH_ROOT}/VAE/{v}"} for v in self.vaes
            ] + [
                {"model_name": te, "filename": f"{FAKE_PATH_ROOT}/text_encoder/{te}"}
                for te in self.text_encoders
            ]
            return httpx.Response(200, json=entries)
        if name == "sd-vae":
            if self.flavor == "forge":
                return httpx.Response(404, json={"detail": "Not Found"})
            return httpx.Response(
                200,
                json=[
                    {"model_name": v, "filename": f"{FAKE_PATH_ROOT}/VAE/{v}"} for v in self.vaes
                ],
            )
        if name == "refresh-checkpoints" and request.method == "POST":
            self.refresh_count += 1
            return httpx.Response(200, json=None)
        if name in ("scripts", "script-info"):
            if self.scripts_status != 200:
                return httpx.Response(self.scripts_status, json={"error": "failed"})
            return httpx.Response(
                200, json=self.scripts if name == "scripts" else self.script_infos
            )
        if name == "txt2img" and request.method == "POST":
            return self._txt2img(request)
        if name == "img2img" and request.method == "POST":
            return self._img2img(request)
        if name == "progress":
            return self._public_progress()
        return httpx.Response(404, json={"detail": "Not Found"})

    def _txt2img(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.txt2img_bodies.append(body)
        return self._generate(body, is_img2img=False)

    def _img2img(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.img2img_bodies.append(body)
        self.img2img_init_images.append(
            [base64.b64decode(item) for item in body.get("init_images") or []]
        )
        mask = body.get("mask")
        self.img2img_masks.append(base64.b64decode(mask) if isinstance(mask, str) else None)
        return self._generate(body, is_img2img=True)

    def _generate(self, body: dict[str, Any], *, is_img2img: bool) -> httpx.Response:
        if self.txt2img_status != 200:
            return httpx.Response(self.txt2img_status, json=self.txt2img_error_body)

        override = body.get("override_settings") or {}
        requested = override.get("sd_model_checkpoint")
        names = {c["model_name"] for c in self.checkpoints}
        used_model = requested if requested in names else self.current_model

        batch = int(body.get("batch_size", 1))
        seed = int(body.get("seed", 0))
        prompt = str(body.get("prompt", ""))
        negative = str(body.get("negative_prompt", ""))
        all_prompts = [prompt] * batch
        dp = self._dynamic_prompts_args(body, is_img2img=is_img2img)
        if dp is not None and dp.get("Dynamic Prompts enabled"):
            variants = prompt_variants(prompt)
            if dp.get("Combinatorial generation"):
                # 組み合わせの数だけ描く(batch_size は効かない)。Max generations が上限。
                max_generations = int(dp.get(MAX_GENERATIONS_LABEL) or 0)
                all_prompts = variants[:max_generations] if max_generations else variants
                batch = len(all_prompts)
            else:
                all_prompts = [variants[i % len(variants)] for i in range(batch)]
        all_negative_prompts = [negative] * batch
        all_seeds = [seed + i for i in range(batch)]
        images = (
            []
            if self.return_no_images
            else [b64(png_bytes((i * 20 % 255, 0, 0))) for i in range(batch + self.extra_images)]
        )
        infotexts = [
            f"{p}\nSteps: {body.get('steps')}, Seed: {s}, Model: {used_model}"
            for p, s in zip(all_prompts, all_seeds, strict=True)
        ]
        index_of_first_image = 0
        if self.return_grid and len(images) > 1:
            images.insert(0, b64(png_bytes((0, 255, 0), size=(16, 16))))
            infotexts.insert(0, f"grid\nSeed: {seed}")
            index_of_first_image = 1
        info = {
            "prompt": prompt,
            "all_prompts": all_prompts,
            "negative_prompt": negative,
            "all_negative_prompts": all_negative_prompts,
            "seed": seed,
            "all_seeds": all_seeds,
            "sd_model_name": used_model,
            "infotexts": infotexts,
            "index_of_first_image": index_of_first_image,
        }
        return httpx.Response(
            200, json={"images": images, "parameters": body, "info": json.dumps(info)}
        )

    def _next_progress(self) -> float:
        if not self.progress_values:
            return 0.0
        value = self.progress_values[min(self._progress_index, len(self.progress_values) - 1)]
        self._progress_index += 1
        return value

    def _internal_progress(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.progress_bodies.append(body)
        progress = self._next_progress()
        preview = None
        if self.live_preview is not None:
            preview = "data:image/jpeg;base64," + b64(self.live_preview)
        return httpx.Response(
            200,
            json={
                "active": True,
                "queued": False,
                "completed": False,
                "progress": progress,
                "eta": 1.0,
                "live_preview": preview,
                "id_live_preview": self._progress_index,
                "textinfo": None,
            },
        )

    def _public_progress(self) -> httpx.Response:
        progress = self._next_progress()
        return httpx.Response(
            200,
            json={
                "progress": progress,
                "eta_relative": 1.0,
                "state": {"sampling_step": int(progress * 20), "sampling_steps": 20},
                "current_image": b64(self.live_preview) if self.live_preview else None,
                "textinfo": None,
            },
        )


def unreachable_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.MockTransport(handler)


def install_fake_factories(monkeypatch: Any, fake: FakeSdWebui) -> None:
    """`app/api/sdwebui.py` が作るクライアントとプロバイダーを、偽の WebUI につなぐ。
    以降の `PUT /api/sdwebui/connection` で、偽の WebUI を使うプロバイダーが登録される。"""
    from app.domain.sdwebui_connection import read_credentials
    from app.providers.sdwebui.client import SdWebuiClient
    from app.providers.sdwebui.provider import SdWebuiProvider

    def make_client(url: str, credentials: Any) -> SdWebuiClient:
        return SdWebuiClient(url, credentials=credentials, transport=fake.transport())

    def make_provider(url: str, settings: Any, session_factory: Any) -> SdWebuiProvider:
        data_dir = settings.data_dir
        return SdWebuiProvider(
            url,
            session_factory,
            settings.sdwebui_timeout_seconds,
            credentials_loader=lambda: read_credentials(data_dir),
            transport=fake.transport(),
            progress_interval=0.05,
        )

    monkeypatch.setattr("app.api.sdwebui._make_client", make_client)
    monkeypatch.setattr("app.api.sdwebui._make_provider", make_provider)
