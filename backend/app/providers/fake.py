"""課金なしで動く疑似プロバイダー。テストと UI 開発用。

Pillow でダミー画像を作るだけで、実 API は一切呼ばない。capabilities は
`openai_spec` を共有するため、実プロバイダー実装時もフォームの見た目は変わらない。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
import uuid

from PIL import Image, ImageDraw, ImageOps
from sqlalchemy.orm import Session

from app.domain import general_settings
from app.domain.sizes import parse_size
from app.i18n import t
from app.providers import openai_spec
from app.providers.base import (
    InputImage,
    PartialImageEvent,
    ProgressCallback,
    ProviderCapabilities,
    ProviderError,
    RunDraft,
    RunOutputImage,
    RunRequest,
    RunResult,
)

# プロンプトに `[[fail:contentFilter]]` のように書くと、対応する ProviderError を送出する。
_FAIL_MARKER_RE = re.compile(r"\[\[fail:([a-zA-Z]+)\]\]")

_FORMAT_MAP = {
    "png": ("PNG", "image/png"),
    "jpeg": ("JPEG", "image/jpeg"),
    "webp": ("WEBP", "image/webp"),
}


def _run_color(run_id: uuid.UUID) -> tuple[int, int, int]:
    """run ごとに色を変える(見た目で run を区別できるようにするだけの演出)。"""
    digest = hashlib.sha256(str(run_id).encode("utf-8")).digest()
    return (digest[0], digest[1], digest[2])


def _make_image(
    width: int, height: int, text: str, color: tuple[int, int, int], transparent: bool
) -> Image.Image:
    mode = "RGBA" if transparent else "RGB"
    background = (0, 0, 0, 0) if transparent else color
    image = Image.new(mode, (width, height), background)
    draw = ImageDraw.Draw(image)
    if transparent:
        # 透過時も中身が見えるよう、内側に不透明な矩形を敷く。
        margin_x, margin_y = width * 0.1, height * 0.1
        draw.rectangle(
            [margin_x, margin_y, width - margin_x, height - margin_y],
            fill=(*color, 255),
        )
    else:
        draw.rectangle([0, 0, width, height], fill=color)
    text_color = (255, 255, 255, 255) if transparent else (255, 255, 255)
    draw.text((10, 10), text[:80], fill=text_color)
    return image


class FakeProvider:
    """`ImageProvider` プロトコルの疑似実装。"""

    name = "fake"
    label = "Fake"
    requires_api_key = False
    supports_pricing = True

    def __init__(self, *, moderation: str = "low") -> None:
        # generate のときだけ finalize_params が params へ足す(ADR-0013)。画面で保存した
        # 値(`app_setting`)が無いときの既定値としてだけ使う(ADR-0009 2章)。
        self._moderation = moderation

    def capabilities(self) -> ProviderCapabilities:
        return openai_spec.build_capabilities(self.name, self.label)

    def availability(self) -> tuple[bool, str | None]:
        return True, None

    def finalize_params(self, db: Session, draft: RunDraft) -> dict:
        params = dict(draft.params)
        if draft.operation == "generate":
            # 画面で保存した値を優先し、無ければコンストラクタで渡された既定値(env/組み込み)
            # を使う(再起動なしで次の Run から反映する。ADR-0009 2章)。
            saved = general_settings.get_saved_moderation(db)
            params["moderation"] = saved if saved is not None else self._moderation
        return params

    def repeat_seed(self, first_params: dict, index: int) -> int | None:
        # seed を持たない(OpenAI と同じ)。
        return None

    async def execute(self, run: RunRequest, on_progress: ProgressCallback) -> RunResult:
        self._maybe_raise_from_prompt(run.prompt)

        params = run.params
        output_format = str(params.get("output_format", "png"))
        background = str(params.get("background", "auto"))
        transparent = background == "transparent"
        n = int(params.get("n", 1))
        partial_images = int(params.get("partial_images", 0))

        base_image: Image.Image | None = None
        mask_image: Image.Image | None = None
        width, height = self._resolve_size(params)

        if run.operation == "edit":
            base_image = await asyncio.to_thread(self._load_base_image, run.inputs)
            mask_image = await asyncio.to_thread(self._load_mask_image, run.inputs)
            width, height = base_image.size

        color = _run_color(run.run_id)
        outputs: list[RunOutputImage] = []

        for index in range(n):
            if partial_images > 0:
                for step in range(partial_images):
                    partial_bytes = await asyncio.to_thread(
                        self._make_partial, width, height, color, step, partial_images, transparent
                    )
                    await on_progress(
                        PartialImageEvent(
                            output_index=index,
                            partial_index=step,
                            data=partial_bytes,
                            mime="image/png",
                        )
                    )
                    await asyncio.sleep(0.02)

            image = await asyncio.to_thread(
                self._render, run, base_image, mask_image, width, height, color, index, transparent
            )
            data, mime = await asyncio.to_thread(self._encode, image, output_format, params)
            outputs.append(RunOutputImage(data=data, mime=mime))

        input_count = max(1, len(run.inputs))
        usage = {
            "input_tokens": 50 * input_count,
            "output_tokens": 1000 * n,
            "total_tokens": 50 * input_count + 1000 * n,
        }
        return RunResult(outputs=outputs, usage=usage, provider_request_id=f"fake-{uuid.uuid4()}")

    # -- 内部ヘルパー ---------------------------------------------------

    @staticmethod
    def _maybe_raise_from_prompt(prompt: str) -> None:
        match = _FAIL_MARKER_RE.search(prompt)
        if match:
            code = match.group(1)
            raise ProviderError(
                code=code,
                message=t("fake.forcedFailureMarker", code=code),
                request_id="fake-request-id",
            )

    @staticmethod
    def _resolve_size(params: dict) -> tuple[int, int]:
        size_str = params.get("size", "auto")
        if not size_str or size_str == "auto":
            return (1024, 1024)
        parsed = parse_size(str(size_str))
        return parsed if parsed is not None else (1024, 1024)

    @staticmethod
    def _load_base_image(inputs: list[InputImage]) -> Image.Image:
        for item in inputs:
            if item.role == "image" and item.position == 0:
                return Image.open(io.BytesIO(item.data)).convert("RGBA")
        raise ProviderError(
            code="providerError",
            message=t("fake.editRequiresBaseImage"),
        )

    @staticmethod
    def _load_mask_image(inputs: list[InputImage]) -> Image.Image | None:
        for item in inputs:
            if item.role == "mask":
                return Image.open(io.BytesIO(item.data)).convert("RGBA")
        return None

    @staticmethod
    def _render(
        run: RunRequest,
        base_image: Image.Image | None,
        mask_image: Image.Image | None,
        width: int,
        height: int,
        color: tuple[int, int, int],
        index: int,
        transparent: bool,
    ) -> Image.Image:
        text = f"{run.prompt[:30]} #{index}"
        if run.operation == "generate" or base_image is None:
            return _make_image(width, height, text, color, transparent)

        # edit: ベース画像に色味を混ぜたものを作り、変化が分かるようにする。
        tint_layer = Image.new("RGBA", base_image.size, (*color, 255))
        tinted = Image.blend(base_image, tint_layer, 0.4)
        draw = ImageDraw.Draw(tinted)
        draw.text((10, 10), text[:80], fill=(255, 255, 255, 255))

        if mask_image is None:
            return tinted

        # マスクは透明部分(alpha=0)が編集対象。Image.composite は mask=255 側が image1 になる。
        alpha = mask_image.split()[-1]
        composite_mask = ImageOps.invert(alpha)
        return Image.composite(tinted, base_image, composite_mask)

    @staticmethod
    def _make_partial(
        width: int,
        height: int,
        color: tuple[int, int, int],
        step: int,
        total: int,
        transparent: bool,
    ) -> bytes:
        small_edge = max(32, min(width, height) // 4)
        image = _make_image(small_edge, small_edge, f"{step + 1}/{total}", color, transparent)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    @staticmethod
    def _encode(image: Image.Image, output_format: str, params: dict) -> tuple[bytes, str]:
        pillow_format, mime = _FORMAT_MAP.get(output_format, ("PNG", "image/png"))
        out_image = image
        if pillow_format == "JPEG" and out_image.mode == "RGBA":
            # JPEG はアルファ非対応のため白背景に合成する。
            background = Image.new("RGB", out_image.size, (255, 255, 255))
            background.paste(out_image, mask=out_image.split()[-1])
            out_image = background

        save_kwargs: dict = {}
        compression = params.get("output_compression")
        if pillow_format in ("JPEG", "WEBP") and compression is not None:
            save_kwargs["quality"] = int(compression)

        buffer = io.BytesIO()
        out_image.save(buffer, format=pillow_format, **save_kwargs)
        return buffer.getvalue(), mime
