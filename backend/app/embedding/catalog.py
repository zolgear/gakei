"""埋め込みのローカルの ONNX モデルの一覧とダウンロード(ADR-0033 2章・11章)。

モデルは配布物に含めず、管理者が設定画面から Hugging Face の固定リビジョンを取得して
`DATA_DIR/models/clip/<モデル>/` に置く。ファイル名はリポジトリの中のパスから
ディレクトリを除いたもの(`onnx/vision_model_uint8.onnx` → `vision_model_uint8.onnx`)。

- リビジョン、sha256、大きさは 2026-10-03 に HF API
  (`https://huggingface.co/api/models/<repo>/tree/<revision>` と `.../tree/<revision>/onnx`
  の `size` と `lfs.oid`)で調べた値。LFS ではない `vocab.json`・`merges.txt` は、固定
  リビジョンのファイルを取得して sha256 を計算した。
- `memory_*` は、読み込みと推論1回でプロセスが使うメモリの目安(最大 RSS)。2026-10-03 に
  Raspberry Pi 5(aarch64、onnxruntime 1.30、推論スレッド 2、prepacking とメモリパターンを
  切った条件)で、新しいプロセスで読み込んで推論し、`ru_maxrss` を測った値を 10 進の MB に
  直し、少し上に丸めた。
  - CLIP 画像 uint8: 257MiB、CLIP 文章 fp32: 415MiB、CLIP 画像 fp32: 508MiB、CLIP 両方 fp32:
    831MiB、LY 画像: 500MiB、LY 文章: 582MiB、LY 両方: 924MiB。
  - 既定の組み合わせ(画像 uint8 + 文章 fp32)の「両方」は、別々に測った値からの見込み
    (257 + 415 − 基準の 36MiB ≒ 636MiB)で目安を決めた。実装後に同じ Pi 5 で、GAKEI の
    エンジン(`OnnxClipEngine`)で画像2枚と文章3件を計算したときの最大 RSS は 556MiB だった
    (アプリの import を含む)。目安は見込みのまま余裕を持たせている。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.model_store.downloader import (
    HubModel,
    ModelDownloader,
    RemoteFile,
    default_client_factory,
    files_present,
)

ModelFamily = Literal["openai_clip", "ly_clip"]

XENOVA_CLIP_REPO = "Xenova/clip-vit-base-patch32"
XENOVA_CLIP_REVISION = "d15189d7028b43f1d3e65039190477f6af591c2a"
LY_CLIP_REPO = "line-corporation/clip-japanese-base"
LY_CLIP_REVISION = "77a62f8af977acd73ec2f927fb73fdaeb13af7d1"

_MB = 1000 * 1000

# Xenova の文章側(fp32)とトークナイザー。uint8 と fp32 の2つのモデルで共通。
_CLIP_TEXT = RemoteFile(
    "text_model.onnx",
    254058553,
    "3f6571f5bad13a97c469c1622e1cfc4d9aef78b79fdbfcff804ca357bfada8cc",
    path="onnx/text_model.onnx",
)
_CLIP_VOCAB = RemoteFile(
    "vocab.json", 862328, "5047b556ce86ccaf6aa22b3ffccfc52d391ea4accdab9c2f2407da5b742d4363"
)
_CLIP_MERGES = RemoteFile(
    "merges.txt", 524619, "9fd691f7c8039210e0fced15865466c65820d09b63988b0174bfe25de299051a"
)


@dataclass(frozen=True)
class ClipModel:
    name: str
    family: ModelFamily
    repo: str
    revision: str
    files: tuple[RemoteFile, ...]
    # 画像側・文章側の ONNX のファイル名(`files` の `name`)。
    vision_file: str
    text_file: str
    # 対応する言語(画面の注意書きに使う)。
    languages: tuple[str, ...]
    dim: int
    # 読み込みと推論に要るメモリの目安(モジュールの docstring)。
    memory_vision_bytes: int
    memory_text_bytes: int
    memory_bytes: int
    license: str
    # 画像側を量子化したモデルか。動的量子化はまとめて計算する枚数で値が変わるので、1枚ずつ
    # 計算する(ADR-0033 2章)。
    quantized_vision: bool = False

    @property
    def size_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def image_batch_size(self) -> int:
        """画像を1回にまとめて計算する枚数。量子化したモデルと LY の画像側は1枚ずつ
        (LY はまとめても1枚あたりが速くならない。実測)。"""
        if self.quantized_vision or self.family == "ly_clip":
            return 1
        return 8


CLIP_MODELS: dict[str, ClipModel] = {
    "clip-vit-b32-u8": ClipModel(
        name="clip-vit-b32-u8",
        family="openai_clip",
        repo=XENOVA_CLIP_REPO,
        revision=XENOVA_CLIP_REVISION,
        files=(
            RemoteFile(
                "vision_model_uint8.onnx",
                88648915,
                "b95448754a6ae56964ec80c570c80bb9863787ee85f51b664abed8c0e5f22a7a",
                path="onnx/vision_model_uint8.onnx",
            ),
            _CLIP_TEXT,
            _CLIP_VOCAB,
            _CLIP_MERGES,
        ),
        vision_file="vision_model_uint8.onnx",
        text_file="text_model.onnx",
        languages=("en",),
        dim=512,
        memory_vision_bytes=300 * _MB,
        memory_text_bytes=450 * _MB,
        # 見込み(モジュールの docstring。実測は 556MiB)。
        memory_bytes=700 * _MB,
        license="MIT",
        quantized_vision=True,
    ),
    "clip-vit-b32": ClipModel(
        name="clip-vit-b32",
        family="openai_clip",
        repo=XENOVA_CLIP_REPO,
        revision=XENOVA_CLIP_REVISION,
        files=(
            RemoteFile(
                "vision_model.onnx",
                351685709,
                "fd6e1402a588279d1723c7534d4bcba5bc0b14b47dfab0e46f8c47b8270d7d40",
                path="onnx/vision_model.onnx",
            ),
            _CLIP_TEXT,
            _CLIP_VOCAB,
            _CLIP_MERGES,
        ),
        vision_file="vision_model.onnx",
        text_file="text_model.onnx",
        languages=("en",),
        dim=512,
        memory_vision_bytes=550 * _MB,
        memory_text_bytes=450 * _MB,
        memory_bytes=900 * _MB,
        license="MIT",
    ),
    "clip-japanese-base": ClipModel(
        name="clip-japanese-base",
        family="ly_clip",
        repo=LY_CLIP_REPO,
        revision=LY_CLIP_REVISION,
        files=(
            RemoteFile(
                "clyp_visual.onnx",
                347813462,
                "a91c5686cf47ff91c09736a8ce0f8a518001f52f63cb5ad123a0f6183e3e4ccf",
                path="onnx/clyp_visual.onnx",
            ),
            RemoteFile(
                "clyp_textual.onnx",
                441988972,
                "8104b764c88099e5395e93300d52b17bdf34a4f43a6fd681eb83a34aa4db31f8",
                path="onnx/clyp_textual.onnx",
            ),
            RemoteFile(
                "spiece.model",
                805634,
                "b5cbdfa8aa7c54c8c5af85b78c309c54a5f2749a20468bf6f60eee007fe6fec1",
                path="onnx/spiece.model",
            ),
        ),
        vision_file="clyp_visual.onnx",
        text_file="clyp_textual.onnx",
        languages=("ja", "en"),
        dim=512,
        memory_vision_bytes=550 * _MB,
        memory_text_bytes=650 * _MB,
        memory_bytes=1000 * _MB,
        license="Apache-2.0",
    ),
}

DEFAULT_MODEL = "clip-vit-b32-u8"


def models_root(data_dir: Path) -> Path:
    return data_dir / "models" / "clip"


def model_dir(data_dir: Path, name: str) -> Path:
    if name not in CLIP_MODELS:
        raise KeyError(name)
    return models_root(data_dir) / name


def is_downloaded(data_dir: Path, name: str) -> bool:
    if name not in CLIP_MODELS:
        return False
    return files_present(model_dir(data_dir, name), CLIP_MODELS[name])


def onnx_model_key(model: ClipModel) -> str:
    """ベクトル空間の識別子(ADR-0033 3章)。リビジョンを変えたら別の空間とみなす。"""
    return f"onnx:{model.name}@{model.revision}"


def _write_fake_model(model: HubModel, directory: Path) -> None:
    """`FAKE_PROVIDER=1` 用のダミー(ダウンロードしない)。推論はダミーのエンジンが行う。"""
    for remote in model.files:
        (directory / remote.name).write_bytes(b"fake-clip-model-file")


class ClipModelDownloader(ModelDownloader):
    """埋め込みのモデルのダウンロード(仕組みは `ModelDownloader`)。"""

    def __init__(
        self,
        data_dir: Path,
        fake: bool = False,
        client_factory=default_client_factory,  # noqa: ANN001
    ) -> None:
        super().__init__(
            data_dir,
            catalog=CLIP_MODELS,
            directory_for=model_dir,
            fake=fake,
            fake_writer=_write_fake_model,
            client_factory=client_factory,
            label="clip",
        )
