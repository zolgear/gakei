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
- `txt2img_delay`: 応答までの秒数(非同期のときだけ。進捗の問い合わせを試すため)。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from typing import Any

import httpx
from PIL import Image

FAKE_PATH_ROOT = "/opt/fake-webui/models"


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
        if request.url.path == "/sdapi/v1/txt2img" and self._fake.txt2img_delay:
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
        self.progress_bodies: list[dict[str, Any]] = []
        self.refresh_count = 0
        self._progress_index = 0

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
        if name == "txt2img" and request.method == "POST":
            return self._txt2img(request)
        if name == "progress":
            return self._public_progress()
        return httpx.Response(404, json={"detail": "Not Found"})

    def _txt2img(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.txt2img_bodies.append(body)
        if self.txt2img_status != 200:
            return httpx.Response(self.txt2img_status, json=self.txt2img_error_body)

        override = body.get("override_settings") or {}
        requested = override.get("sd_model_checkpoint")
        names = {c["model_name"] for c in self.checkpoints}
        used_model = requested if requested in names else self.current_model

        batch = int(body.get("batch_size", 1))
        seed = int(body.get("seed", 0))
        all_seeds = [seed + i for i in range(batch)]
        images = (
            []
            if self.return_no_images
            else [b64(png_bytes((i * 20 % 255, 0, 0))) for i in range(batch + self.extra_images)]
        )
        infotexts = [
            f"{body.get('prompt', '')}\nSteps: {body.get('steps')}, Seed: {s}, Model: {used_model}"
            for s in all_seeds
        ]
        info = {
            "prompt": body.get("prompt"),
            "seed": seed,
            "all_seeds": all_seeds,
            "sd_model_name": used_model,
            "infotexts": infotexts,
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
