"""WD Tagger v3 のモデルの一覧とダウンロード(ADR-0024 3章)。

モデルは配布物に含めず、管理者が設定画面から Hugging Face の `SmilingWolf/<model>` を取得して
`DATA_DIR/models/wd/<model>/` に置く。取得元のリビジョンを固定し、sha256 を確かめる。

- リビジョンと sha256 は 2026-09-28 に HF API
  (`https://huggingface.co/api/models/SmilingWolf/<model>?blobs=true` の `sha` と
  `siblings[].lfs.sha256`)で調べた値。`selected_tags.csv` は LFS ではないので、固定
  リビジョンのファイルを取得して sha256 を計算した(3モデルとも同じ内容)。
- ダウンロードは同じディレクトリの一時ファイル(`.<name>.part`)に書き、sha256 が一致して
  から `os.replace` で置き換える。一致しなければ一時ファイルを消して失敗にする。
- 両方のファイルがそろっていれば「ダウンロード済み」。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.i18n import t

logger = logging.getLogger(__name__)

HF_REPO_OWNER = "SmilingWolf"
MODEL_FILE = "model.onnx"
TAGS_FILE = "selected_tags.csv"

_TAGS_SHA256 = "298633d94d0031d2081c0893f29c82eab7f0df00b08483ba8f29d1e979441217"
_TAGS_SIZE = 308468


@dataclass(frozen=True)
class RemoteFile:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class WdModel:
    name: str
    revision: str
    files: tuple[RemoteFile, ...]

    @property
    def size_bytes(self) -> int:
        return sum(f.size for f in self.files)


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
    ),
}

_CHUNK = 1024 * 1024


def models_root(data_dir: Path) -> Path:
    return data_dir / "models" / "wd"


def model_dir(data_dir: Path, name: str) -> Path:
    if name not in WD_MODELS:
        raise KeyError(name)
    return models_root(data_dir) / name


def is_downloaded(data_dir: Path, name: str) -> bool:
    if name not in WD_MODELS:
        return False
    directory = model_dir(data_dir, name)
    return all((directory / f.name).is_file() for f in WD_MODELS[name].files)


def file_url(model: WdModel, filename: str) -> str:
    return (
        f"https://huggingface.co/{HF_REPO_OWNER}/{model.name}/resolve/{model.revision}/{filename}"
    )


class DownloadError(Exception):
    pass


@dataclass
class DownloadState:
    status: str = "idle"  # idle | downloading | failed
    downloaded_bytes: int = 0
    total_bytes: int = 0
    error: str | None = None

    @property
    def progress(self) -> float | None:
        if self.status != "downloading" or self.total_bytes <= 0:
            return None
        return min(1.0, self.downloaded_bytes / self.total_bytes)


def _default_client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(60.0, connect=15.0))


async def _download_file(
    client: httpx.AsyncClient,
    url: str,
    expected: RemoteFile,
    directory: Path,
    on_bytes: Callable[[int], None],
) -> None:
    final_path = directory / expected.name
    tmp_path = directory / f".{expected.name}.part"
    digest = hashlib.sha256()
    try:
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise DownloadError(
                    t(
                        "annotations.download.httpError",
                        status=response.status_code,
                        file=expected.name,
                    )
                )
            with tmp_path.open("wb") as f:
                async for chunk in response.aiter_bytes(_CHUNK):
                    f.write(chunk)
                    digest.update(chunk)
                    on_bytes(len(chunk))
        actual = digest.hexdigest()
        if actual != expected.sha256:
            raise DownloadError(t("annotations.download.hashMismatch", file=expected.name))
        os.replace(tmp_path, final_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


@dataclass
class WdModelDownloader:
    """モデルのダウンロードをバックグラウンドで1モデル1本ずつ行う(プロセス内)。"""

    data_dir: Path
    fake: bool = False
    client_factory: Callable[[], httpx.AsyncClient] = _default_client_factory
    states: dict[str, DownloadState] = field(default_factory=dict)
    _tasks: dict[str, asyncio.Task] = field(default_factory=dict)

    def state(self, name: str) -> DownloadState:
        return self.states.setdefault(name, DownloadState())

    def is_downloading(self, name: str) -> bool:
        task = self._tasks.get(name)
        return task is not None and not task.done()

    def start(self, name: str) -> None:
        """ダウンロードを始める(既に実行中なら何もしない)。呼び出し側で存在確認をしておく。"""
        if self.is_downloading(name):
            return
        model = WD_MODELS[name]
        state = self.state(name)
        state.status = "downloading"
        state.downloaded_bytes = 0
        state.total_bytes = model.size_bytes
        state.error = None
        self._tasks[name] = asyncio.create_task(self._run(model), name=f"gakei-wd-{name}")

    async def _run(self, model: WdModel) -> None:
        state = self.state(model.name)
        directory = model_dir(self.data_dir, model.name)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if self.fake:
                _write_fake_model(directory)
            else:

                def on_bytes(n: int) -> None:
                    state.downloaded_bytes += n

                async with self.client_factory() as client:
                    for remote in model.files:
                        await _download_file(
                            client, file_url(model, remote.name), remote, directory, on_bytes
                        )
            state.status = "idle"
            state.error = None
        except Exception as e:  # noqa: BLE001 - 失敗は状態として画面に出す
            logger.warning("WD タガーのモデル %s のダウンロードに失敗しました: %s", model.name, e)
            state.status = "failed"
            state.error = str(e)[:500]

    async def wait(self, name: str) -> None:
        """テスト用。実行中のダウンロードの終了を待つ。"""
        task = self._tasks.get(name)
        if task is not None:
            await task

    async def stop(self) -> None:
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        for task in self._tasks.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    def delete(self, name: str) -> None:
        directory = model_dir(self.data_dir, name)
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
        self.states.pop(name, None)


def _write_fake_model(directory: Path) -> None:
    """`FAKE_PROVIDER=1` 用のダミー(ダウンロードしない)。推定はダミーのエンジンが行う。"""
    (directory / MODEL_FILE).write_bytes(b"fake-onnx-model")
    (directory / TAGS_FILE).write_text(
        "tag_id,name,category,count\n1,fake_tag,0,1\n", encoding="utf-8"
    )
