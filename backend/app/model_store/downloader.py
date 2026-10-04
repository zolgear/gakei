"""Hugging Face からのモデルのダウンロード(ADR-0024 3章、ADR-0033 2章)。

WD Tagger と埋め込み(CLIP 系)の両方が使う。モデルの一覧(カタログ)と置き場所は使う側が
渡す。

- 取得元のリビジョンを固定し、ファイルごとに sha256 と大きさを持つ。
- ダウンロードは同じディレクトリの一時ファイル(`.<name>.part`)に書き、sha256 が一致して
  から `os.replace` で置き換える。一致しなければ一時ファイルを消して失敗にする。
- カタログのファイルがすべてそろっていれば「ダウンロード済み」。
- `FAKE_PROVIDER=1` ではダウンロードせず、ダミーのファイルを置く(推論はダミーのエンジンが行う)。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from app.i18n import t

logger = logging.getLogger(__name__)

_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class RemoteFile:
    """取得するファイル1つ。`name` は置き場所でのファイル名、`path` はリポジトリの中の
    パス(省略すると `name` と同じ)。"""

    name: str
    size: int
    sha256: str
    path: str | None = None

    @property
    def remote_path(self) -> str:
        return self.path or self.name


class HubModel(Protocol):
    """カタログの1項目に要る属性(`WdModel`、`ClipModel` が満たす)。"""

    @property
    def name(self) -> str: ...

    @property
    def repo(self) -> str: ...

    @property
    def revision(self) -> str: ...

    @property
    def files(self) -> tuple[RemoteFile, ...]: ...

    @property
    def size_bytes(self) -> int: ...


def hub_file_url(model: HubModel, remote: RemoteFile) -> str:
    return f"https://huggingface.co/{model.repo}/resolve/{model.revision}/{remote.remote_path}"


def files_present(directory: Path, model: HubModel) -> bool:
    return all((directory / f.name).is_file() for f in model.files)


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


def default_client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(60.0, connect=15.0))


async def download_file(
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
                        "modelStore.download.httpError",
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
            raise DownloadError(t("modelStore.download.hashMismatch", file=expected.name))
        os.replace(tmp_path, final_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def write_placeholder_files(model: HubModel, directory: Path) -> None:
    """`FAKE_PROVIDER=1` 用のダミー(ダウンロードしない)。"""
    for remote in model.files:
        (directory / remote.name).write_bytes(b"fake-model-file")


class ModelDownloader:
    """モデルのダウンロードをバックグラウンドで1モデル1本ずつ行う(プロセス内)。

    `catalog` は呼ぶたびに引く(テストで項目を差し替えられるよう、写さずに持つ)。
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        catalog: Mapping[str, HubModel],
        directory_for: Callable[[Path, str], Path],
        fake: bool = False,
        fake_writer: Callable[[HubModel, Path], None] = write_placeholder_files,
        client_factory: Callable[[], httpx.AsyncClient] = default_client_factory,
        label: str = "model",
    ) -> None:
        self.data_dir = data_dir
        self.catalog = catalog
        self.directory_for = directory_for
        self.fake = fake
        self.fake_writer = fake_writer
        self.client_factory = client_factory
        self.label = label
        self.states: dict[str, DownloadState] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def state(self, name: str) -> DownloadState:
        return self.states.setdefault(name, DownloadState())

    def is_downloading(self, name: str) -> bool:
        task = self._tasks.get(name)
        return task is not None and not task.done()

    def start(self, name: str) -> None:
        """ダウンロードを始める(既に実行中なら何もしない)。呼び出し側で存在確認をしておく。"""
        if self.is_downloading(name):
            return
        model = self.catalog[name]
        state = self.state(name)
        state.status = "downloading"
        state.downloaded_bytes = 0
        state.total_bytes = model.size_bytes
        state.error = None
        self._tasks[name] = asyncio.create_task(
            self._run(name, model), name=f"gakei-{self.label}-{name}"
        )

    async def _run(self, name: str, model: HubModel) -> None:
        state = self.state(name)
        directory = self.directory_for(self.data_dir, name)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if self.fake:
                self.fake_writer(model, directory)
            else:

                def on_bytes(n: int) -> None:
                    state.downloaded_bytes += n

                async with self.client_factory() as client:
                    for remote in model.files:
                        await download_file(
                            client, hub_file_url(model, remote), remote, directory, on_bytes
                        )
            state.status = "idle"
            state.error = None
        except Exception as e:  # noqa: BLE001 - 失敗は状態として画面に出す
            logger.warning("モデル %s(%s)のダウンロードに失敗しました: %s", name, self.label, e)
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
        directory = self.directory_for(self.data_dir, name)
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
        self.states.pop(name, None)
