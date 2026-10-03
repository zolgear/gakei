"""WD Tagger v3 のモデルの一覧とダウンロード(ADR-0024 3章)。

モデルは配布物に含めず、管理者が設定画面から Hugging Face の `SmilingWolf/<model>` を取得して
`DATA_DIR/models/wd/<model>/` に置く。取得元のリビジョンを固定し、sha256 を確かめる。

- リビジョンと sha256 は 2026-09-28 に HF API
  (`https://huggingface.co/api/models/SmilingWolf/<model>?blobs=true` の `sha` と
  `siblings[].lfs.sha256`)で調べた値。`selected_tags.csv` は LFS ではないので、固定
  リビジョンのファイルを取得して sha256 を計算した(3モデルとも同じ内容)。
- ダウンロードの仕組み(一時ファイル、sha256 の確認)は埋め込みのモデルと共通
  (`app/model_store/downloader.py`。ADR-0033 2章)。置き場所(`DATA_DIR/models/wd/`)は変えない。
- 両方のファイルがそろっていれば「ダウンロード済み」。
- `memory_bytes` は、読み込みと推論1回でプロセスが使うメモリの目安(最大 RSS)。2026-09-29 に
  Raspberry Pi 5(aarch64、onnxruntime 1.30、`wd_tagger.session_options` の設定)で、新しい
  プロセスで読み込み、512px の画像を推論して `ru_maxrss` を測った値(vit 約 0.53〜0.59GiB、swinv2
  約 0.70GiB、eva02-large 約 1.42GiB)を 10 進の GB に直して切り上げた。設定画面の目安の
  表示と、読み込み前の空きメモリの確認(`wd_tagger.check_memory`)に使う。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.model_store.downloader import (
    DownloadError,
    DownloadState,
    HubModel,
    ModelDownloader,
    RemoteFile,
    default_client_factory,
    files_present,
    hub_file_url,
)

__all__ = [
    "DownloadError",
    "DownloadState",
    "MODEL_FILE",
    "RemoteFile",
    "TAGS_FILE",
    "WD_MODELS",
    "WdModel",
    "WdModelDownloader",
    "file_url",
    "is_downloaded",
    "model_dir",
    "models_root",
]

HF_REPO_OWNER = "SmilingWolf"
MODEL_FILE = "model.onnx"
TAGS_FILE = "selected_tags.csv"

_TAGS_SHA256 = "298633d94d0031d2081c0893f29c82eab7f0df00b08483ba8f29d1e979441217"
_TAGS_SIZE = 308468
_MB = 1000 * 1000


@dataclass(frozen=True)
class WdModel:
    name: str
    revision: str
    files: tuple[RemoteFile, ...]
    # 読み込みと推論に要るメモリの目安(モジュールの docstring を参照)。
    memory_bytes: int

    @property
    def size_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def repo(self) -> str:
        return f"{HF_REPO_OWNER}/{self.name}"


WD_MODELS: dict[str, WdModel] = {
    "wd-vit-tagger-v3": WdModel(
        name="wd-vit-tagger-v3",
        revision="7f6b584d0bd3f55c4531f14ba3d4761b2bccdc0f",
        files=(
            RemoteFile(
                MODEL_FILE,
                378536310,
                "35f23693620b668f4d53fd3c62bf65e40af739bc52c7eb0fbc49258b58d065b6",
            ),
            RemoteFile(TAGS_FILE, _TAGS_SIZE, _TAGS_SHA256),
        ),
        memory_bytes=700 * _MB,
    ),
    "wd-swinv2-tagger-v3": WdModel(
        name="wd-swinv2-tagger-v3",
        revision="627aef95638667ddcaa3ac8ae625e88ea5b02f51",
        files=(
            RemoteFile(
                MODEL_FILE,
                467460978,
                "e6774bff34d43bd49f75a47db4ef217dce701c9847b546523eb85ff6dbba1db1",
            ),
            RemoteFile(TAGS_FILE, _TAGS_SIZE, _TAGS_SHA256),
        ),
        memory_bytes=800 * _MB,
    ),
    "wd-eva02-large-tagger-v3": WdModel(
        name="wd-eva02-large-tagger-v3",
        revision="b25b82a03f7282e41aa2f257a52c7583b710bd1c",
        files=(
            RemoteFile(
                MODEL_FILE,
                1260435999,
                "9e768793060c7939b277ccb382783e8670e8a042d29d77aa736be0c8cc898bfc",
            ),
            RemoteFile(TAGS_FILE, _TAGS_SIZE, _TAGS_SHA256),
        ),
        memory_bytes=1600 * _MB,
    ),
}


def models_root(data_dir: Path) -> Path:
    return data_dir / "models" / "wd"


def model_dir(data_dir: Path, name: str) -> Path:
    if name not in WD_MODELS:
        raise KeyError(name)
    return models_root(data_dir) / name


def is_downloaded(data_dir: Path, name: str) -> bool:
    if name not in WD_MODELS:
        return False
    return files_present(model_dir(data_dir, name), WD_MODELS[name])


def file_url(model: WdModel, filename: str) -> str:
    return hub_file_url(model, RemoteFile(filename, 0, ""))


def _write_fake_model(model: HubModel, directory: Path) -> None:
    """`FAKE_PROVIDER=1` 用のダミー(ダウンロードしない)。推定はダミーのエンジンが行う。"""
    (directory / MODEL_FILE).write_bytes(b"fake-onnx-model")
    (directory / TAGS_FILE).write_text(
        "tag_id,name,category,count\n1,fake_tag,0,1\n", encoding="utf-8"
    )


class WdModelDownloader(ModelDownloader):
    """WD Tagger のモデルのダウンロード(仕組みは `ModelDownloader`)。"""

    def __init__(
        self,
        data_dir: Path,
        fake: bool = False,
        client_factory: Callable[[], httpx.AsyncClient] = default_client_factory,
    ) -> None:
        super().__init__(
            data_dir,
            catalog=WD_MODELS,
            directory_for=model_dir,
            fake=fake,
            fake_writer=_write_fake_model,
            client_factory=client_factory,
            label="wd",
        )
