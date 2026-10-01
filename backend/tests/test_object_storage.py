"""画像の保存先(ADR-0028)。

ストアの共通の振る舞い(書き込み、同名の連番、読み出し、有無の確認、派生、古いキー)を
ローカルFS / S3(moto。`GAKEI_TEST_S3_*` があれば実機)/ Azure Blob(Azurite など。
`GAKEI_TEST_AZURE_BLOB_CONNECTION_STRING` があるときだけ)で回す。加えて、設定からストアを
作るところ(`STORAGE_BACKEND`)と起動時の確認。
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.domain.object_storage import S3Store
from app.domain.storage import (
    LocalFsStore,
    OriginalKeyInfo,
    StorageUnavailableError,
    build_store,
    legacy_original_key,
    open_store,
)
from tests.conftest import S3TestTarget, make_png_bytes

_CREATED_AT = datetime(2026, 9, 30, 3, 4, 5, tzinfo=UTC)


def _info(asset_id: uuid.UUID | None = None) -> OriginalKeyInfo:
    return OriginalKeyInfo(
        kind="generated",
        asset_id=asset_id or uuid.UUID("1a2b3c4d-0000-4000-8000-000000000000"),
        created_at=_CREATED_AT,
        provider="openai",
        model="gpt-image-2.5-sunburst",
    )


def _put_raw(store: Any, key: str, data: bytes) -> None:
    """キーを指定してそのまま置く(古いキーの Asset を再現するため)。"""
    if isinstance(store, LocalFsStore):
        path = store.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    elif store.kind == "s3":
        store._put(key, data)
    else:
        store._upload(key, data, overwrite=True)


# -- 共通の振る舞い -------------------------------------------------------------


def test_write_and_read_original(any_store: Any) -> None:
    data = make_png_bytes()
    key = any_store.write_original(data, "png", _info())

    local = _CREATED_AT.astimezone()
    assert key == (
        f"assets/openai/gpt-image-2.5-sunburst/{local:%Y-%m}/{local:%Y%m%d-%H%M%S}_1a2b3c4d.png"
    )
    assert any_store.read(key) == data
    assert any_store.exists(key)
    assert any_store.content_exists(key, "0" * 64, "original")

    content = any_store.open_content(key, "0" * 64, "original")
    assert content is not None
    assert content.size == len(data)
    assert content.read_all() == data


def test_same_name_gets_numbered_suffix(any_store: Any) -> None:
    """同じ秒・同じ短ID でも上書きせず `-2`、`-3` を付ける(条件付きの書き込み)。"""
    first = any_store.write_original(b"first", "png", _info())
    second = any_store.write_original(b"second", "png", _info())
    third = any_store.write_original(b"third", "png", _info())

    assert second == first.removesuffix(".png") + "-2.png"
    assert third == first.removesuffix(".png") + "-3.png"
    assert any_store.read(first) == b"first"
    assert any_store.read(second) == b"second"
    assert any_store.read(third) == b"third"


def test_other_kinds_use_their_folders(any_store: Any) -> None:
    info = OriginalKeyInfo(kind="upload", asset_id=uuid.uuid4(), created_at=_CREATED_AT)
    key = any_store.write_original(b"x", "webp", info)
    assert key.startswith(f"assets/uploads/{_CREATED_AT.astimezone():%Y-%m}/")
    assert key.endswith(f"_{info.asset_id.hex[:8]}.webp")


def test_derived_variants(any_store: Any) -> None:
    sha = "ab" * 32
    assert not any_store.content_exists("unused", sha, "thumb")
    assert any_store.open_content("unused", sha, "preview") is None

    any_store.write_derived(sha, "thumb", b"thumb-1")
    any_store.write_derived(sha, "preview", b"preview-1")
    assert any_store.content_exists("unused", sha, "thumb")
    assert any_store.read(f"derived/{sha}/thumb.webp") == b"thumb-1"

    # 派生は内容から作り直せるので上書きしてよい。
    any_store.write_derived(sha, "thumb", b"thumb-2")
    content = any_store.open_content("unused", sha, "thumb")
    assert content is not None
    assert content.read_all() == b"thumb-2"
    preview = any_store.open_content("unused", sha, "preview")
    assert preview is not None
    assert (preview.size, preview.read_all()) == (len(b"preview-1"), b"preview-1")


def test_missing_content(any_store: Any) -> None:
    key = "assets/openai/none/2026-09/missing.png"
    assert not any_store.exists(key)
    assert not any_store.content_exists(key, "cd" * 32, "original")
    assert any_store.open_content(key, "cd" * 32, "original") is None
    with pytest.raises(FileNotFoundError):
        any_store.read(key)


def test_legacy_key_is_readable(any_store: Any) -> None:
    """ADR-0026 より前のキー(`assets/{2文字}/{sha256}.{拡張子}`)の原本もそのまま読める。"""
    data = make_png_bytes(color=(1, 2, 3))
    sha = "ef" * 32
    key = legacy_original_key(sha, "png")
    assert key == f"assets/ef/{sha}.png"
    _put_raw(any_store, key, data)

    assert any_store.exists(key)
    assert any_store.read(key) == data
    content = any_store.open_content(key, sha, "original")
    assert content is not None
    assert content.read_all() == data


def test_large_content_is_streamed_in_chunks(any_store: Any) -> None:
    """丸ごと1回で読まず、4MB 以下のチャンクで順に返す(Azure は 4MB、他は 1MB 単位)。"""
    data = os.urandom(9 * 1024 * 1024 + 123)
    key = any_store.write_original(data, "png", _info(uuid.uuid4()))
    content = any_store.open_content(key, "0" * 64, "original")
    assert content is not None
    assert content.size == len(data)
    chunks = list(content.chunks)
    assert len(chunks) >= 3
    assert max(len(c) for c in chunks) <= 4 * 1024 * 1024
    assert b"".join(chunks) == data


def test_check_passes(any_store: Any) -> None:
    any_store.check()


# -- S3 固有 ------------------------------------------------------------------


def test_s3_conditional_put_returns_412(s3_store: Any) -> None:
    """`If-None-Match: *` の書き込みが、同名があると 412 PreconditionFailed になる。
    連番の確保はこれに頼る(moto でも実機でも確かめる)。"""
    from botocore.exceptions import ClientError

    s3_store._put("probe.txt", b"1", IfNoneMatch="*")
    with pytest.raises(ClientError) as exc_info:
        s3_store._put("probe.txt", b"2", IfNoneMatch="*")
    assert exc_info.value.response["Error"]["Code"] == "PreconditionFailed"
    assert exc_info.value.response["ResponseMetadata"]["HTTPStatusCode"] == 412
    assert s3_store.read("probe.txt") == b"1"


def test_local_open_content_has_path(tmp_path: Path) -> None:
    """ローカルFSは実体のパスを渡す(配信で FileResponse を使い、Range に応えるため)。"""
    store = LocalFsStore(tmp_path)
    key = store.write_original(b"abc", "png", _info())
    content = store.open_content(key, "0" * 64, "original")
    assert content is not None
    assert content.path == tmp_path / key


# -- 設定からストアを作る(STORAGE_BACKEND) ------------------------------------


def _settings(tmp_path: Path, **kwargs: Any) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path, **kwargs)


def test_default_is_local(tmp_path: Path) -> None:
    store = open_store(_settings(tmp_path))
    assert isinstance(store, LocalFsStore)
    assert store.kind == "local"


def test_storage_backend_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "my-bucket")
    monkeypatch.setenv("S3_FORCE_PATH_STYLE", "true")
    settings = Settings(_env_file=None)
    assert settings.storage_backend == "s3"
    assert settings.s3_bucket == "my-bucket"
    assert settings.s3_force_path_style is True


def test_open_s3_store_from_settings(
    tmp_path: Path, s3_target: S3TestTarget, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 資格情報は boto3 の標準の探し方(環境変数)で渡す。
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", s3_target.access_key_id)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", s3_target.secret_access_key)
    store = open_store(
        _settings(
            tmp_path,
            storage_backend="s3",
            s3_bucket=s3_target.bucket,
            s3_region=s3_target.region,
            s3_endpoint_url=s3_target.endpoint_url,
            s3_force_path_style=s3_target.is_moto,
        )
    )
    assert store.kind == "s3"
    assert store.read(".gakei/startup-check") == b"ok"


def test_s3_missing_bucket_setting(tmp_path: Path) -> None:
    with pytest.raises(StorageUnavailableError) as exc_info:
        open_store(_settings(tmp_path, storage_backend="s3"))
    assert "S3_BUCKET" in str(exc_info.value)


def test_s3_unknown_bucket_aborts(
    tmp_path: Path, s3_target: S3TestTarget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """バケットが無ければ起動を中止する(GAKEI はバケットを作らない)。"""
    if not s3_target.is_moto:
        pytest.skip("moto のときだけ(実機に無いバケットを探しに行かない)")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with pytest.raises(StorageUnavailableError) as exc_info:
        open_store(
            _settings(
                tmp_path,
                storage_backend="s3",
                s3_bucket="no-such-bucket-for-gakei",
                s3_region="us-east-1",
                s3_endpoint_url=s3_target.endpoint_url,
                s3_force_path_style=True,
            )
        )
    message = str(exc_info.value)
    assert "s3 (no-such-bucket-for-gakei)" in message
    assert "NoSuchBucket" in message


def test_azure_settings_errors(tmp_path: Path) -> None:
    with pytest.raises(StorageUnavailableError) as exc_info:
        open_store(_settings(tmp_path, storage_backend="azure_blob"))
    assert "AZURE_STORAGE_CONTAINER" in str(exc_info.value)

    with pytest.raises(StorageUnavailableError) as exc_info:
        open_store(
            _settings(tmp_path, storage_backend="azure_blob", azure_storage_container="images")
        )
    assert "AZURE_STORAGE_CONNECTION_STRING" in str(exc_info.value)

    with pytest.raises(StorageUnavailableError) as exc_info:
        open_store(
            _settings(
                tmp_path,
                storage_backend="azure_blob",
                azure_storage_container="images",
                azure_storage_connection_string="AccountName=a;AccountKey=secret",
                azure_storage_account_url="https://a.blob.core.windows.net",
            )
        )
    assert "どちらか一方" in str(exc_info.value)


def test_azure_invalid_connection_string_does_not_leak(tmp_path: Path) -> None:
    with pytest.raises(StorageUnavailableError) as exc_info:
        open_store(
            _settings(
                tmp_path,
                storage_backend="azure_blob",
                azure_storage_container="images",
                azure_storage_connection_string="garbage-TOPSECRET",
            )
        )
    assert "TOPSECRET" not in str(exc_info.value)


def test_azure_unreachable_aborts_without_leaking_key(tmp_path: Path) -> None:
    """接続できなければ起動を中止し、文言に鍵を出さない。"""
    key = "c2VjcmV0LWtleS1mb3ItZ2FrZWktdGVzdHM="
    connection_string = (
        "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
        f"AccountKey={key};BlobEndpoint=http://127.0.0.1:1/devstoreaccount1;"
    )
    settings = _settings(
        tmp_path,
        storage_backend="azure_blob",
        azure_storage_container="images",
        azure_storage_connection_string=connection_string,
    )
    store = build_store(settings)
    # 既定の再試行で時間がかからないよう、失敗する接続を1回で諦めさせる。
    store._container._config.retry_policy.total_retries = 0
    import app.domain.storage as storage_module

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(storage_module, "build_store", lambda _settings: store)
        with pytest.raises(StorageUnavailableError) as exc_info:
            open_store(settings)
    message = str(exc_info.value)
    assert "azure_blob (images)" in message
    assert key not in message


def test_first_line_hides_secrets() -> None:
    from app.domain.storage import _first_line

    line = _first_line(
        ValueError("failed https://a/b?sv=1&sig=SECRETSIG&se=2 AccountKey=SECRETKEY;x=1")
    )
    assert "SECRETSIG" not in line
    assert "SECRETKEY" not in line


def test_app_startup_aborts_when_storage_unavailable(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(_settings(tmp_path, fake_provider=True, storage_backend="s3"))
    with pytest.raises(StorageUnavailableError), TestClient(app):
        pass


# -- 2026-10-01 改訂: 403 の扱い、条件付き書き込みの確認、SDK の例外、タイムアウト ----------


def _client_error(status: int, code: str, operation: str) -> Exception:
    from botocore.exceptions import ClientError

    return ClientError(
        {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        operation,
    )


def _forbid_lookups(store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """ListBucket の無い AWS S3 と同じく、無いキーの HEAD / GET を 403 にする。"""
    head = store._client.head_object
    get = store._client.get_object

    def _head(**kwargs: Any) -> Any:
        try:
            return head(**kwargs)
        except Exception as e:
            if S3Store._is_missing(e):
                raise _client_error(403, "403", "HeadObject") from None
            raise

    def _get(**kwargs: Any) -> Any:
        try:
            return get(**kwargs)
        except Exception as e:
            if S3Store._is_missing(e):
                raise _client_error(403, "AccessDenied", "GetObject") from None
            raise

    monkeypatch.setattr(store._client, "head_object", _head)
    monkeypatch.setattr(store._client, "get_object", _get)


def _open_with(store: Any, tmp_path: Path) -> Any:
    """`build_store` を差し替えて `open_store`(起動時の確認)を通す。"""
    import app.domain.storage as storage_module

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(storage_module, "build_store", lambda _settings: store)
        return open_store(_settings(tmp_path, storage_backend="s3", s3_bucket=store.bucket))


def test_s3_forbidden_lookup_is_permission_error(
    s3_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """403 を「無い」とみなさず、ListBucket が要ると分かる OSError にする。"""
    from app.domain.storage import StoragePermissionError

    _forbid_lookups(s3_store, monkeypatch)
    key = "assets/openai/none/2026-09/missing.png"
    for call in (
        lambda: s3_store.size_of(key),
        lambda: s3_store.exists(key),
        lambda: s3_store.read(key),
        lambda: s3_store.open_content(key, "0" * 64, "original"),
    ):
        with pytest.raises(StoragePermissionError) as exc_info:
            call()
        assert isinstance(exc_info.value, OSError)
        assert "s3:ListBucket" in str(exc_info.value)
        assert key in str(exc_info.value)


def test_s3_startup_aborts_without_list_bucket(
    s3_store: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_lookups(s3_store, monkeypatch)
    with pytest.raises(StorageUnavailableError) as exc_info:
        _open_with(s3_store, tmp_path)
    message = str(exc_info.value)
    assert "s3:ListBucket" in message
    assert exc_info.value.__cause__ is None


def test_s3_check_accepts_conditional_write(
    s3_store: Any, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """moto(と R2・AWS S3)は既にあるキーへの `If-None-Match: *` に 412 を返す。"""
    with caplog.at_level("WARNING", logger="app.domain.object_storage"):
        assert _open_with(s3_store, tmp_path) is s3_store
    assert "If-None-Match" not in caplog.text


def test_s3_check_warns_when_condition_is_ignored(
    s3_store: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """条件を無視して上書きする互換ストレージは、警告して起動を続ける(ADR-0028 3章)。"""
    put = s3_store._client.put_object

    def _put_ignoring_condition(**kwargs: Any) -> Any:
        kwargs.pop("IfNoneMatch", None)
        return put(**kwargs)

    monkeypatch.setattr(s3_store._client, "put_object", _put_ignoring_condition)
    with caplog.at_level("WARNING", logger="app.domain.object_storage"):
        _open_with(s3_store, tmp_path)
    assert "If-None-Match" in caplog.text


@pytest.mark.parametrize(("status", "code"), [(501, "NotImplemented"), (400, "InvalidRequest")])
def test_s3_check_aborts_when_condition_is_rejected(
    s3_store: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, code: str
) -> None:
    """`If-None-Match` を受け付けない互換ストレージでは、生成のたびに原本の保存が失敗する
    (課金後に Run が failed になる)ので、起動を止める。"""
    from app.domain.storage import StorageConditionalWriteError

    put = s3_store._client.put_object

    def _put_rejecting_condition(**kwargs: Any) -> Any:
        if "IfNoneMatch" in kwargs:
            raise _client_error(status, code, "PutObject")
        return put(**kwargs)

    monkeypatch.setattr(s3_store._client, "put_object", _put_rejecting_condition)
    with pytest.raises(StorageConditionalWriteError):
        s3_store.check()
    with pytest.raises(StorageUnavailableError) as exc_info:
        _open_with(s3_store, tmp_path)
    assert "If-None-Match" in str(exc_info.value)
    assert f"HTTP {status} {code}" in str(exc_info.value)


_UNREACHABLE_SECRET = "TOPSECRET-for-gakei-tests"


def _unreachable_s3_store() -> Any:
    """接続できない S3 のストア(接続は即座に拒否され、再試行もしない)。"""
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        region_name="us-east-1",
        endpoint_url="http://127.0.0.1:1",
        aws_access_key_id="testing",
        aws_secret_access_key=_UNREACHABLE_SECRET,
        config=Config(
            s3={"addressing_style": "path"}, connect_timeout=1, retries={"max_attempts": 1}
        ),
    )
    return S3Store(client, "gakei-test")


def test_s3_sdk_errors_are_wrapped_in_oserror() -> None:
    """SDK の例外は OSError の派生に包まれ、`except OSError` の呼び出し側が劣化できる。"""
    from app.domain.storage import StorageIOError

    store = _unreachable_s3_store()
    key = "assets/x.png"
    for call in (
        lambda: store.size_of(key),
        lambda: store.exists(key),
        lambda: store.read(key),
        lambda: store.open_content(key, "0" * 64, "original"),
        lambda: store.write_new(key, b"x"),
        lambda: store.write_derived("ab" * 32, "thumb", b"x"),
        lambda: store.write_original(b"x", "png", _info()),
    ):
        with pytest.raises(StorageIOError) as exc_info:
            call()
        assert isinstance(exc_info.value, OSError)
        # 閉じたポートへの接続は、Linux ではすぐ拒否されて EndpointConnectionError になるが、
        # Windows は SYN を再送するので先に接続のタイムアウトに達し ConnectTimeoutError になる
        # (2026-10-01、main・dev への push の Windows CI で再現)。どちらも接続の失敗。
        assert any(
            name in str(exc_info.value)
            for name in ("EndpointConnectionError", "ConnectTimeoutError")
        )
        assert _UNREACHABLE_SECRET not in str(exc_info.value)
        assert exc_info.value.__cause__ is None


def test_s3_stream_errors_are_wrapped_in_oserror(s3_store: Any) -> None:
    """配信の途中(本文の読み出し)で失敗しても OSError に包む。"""
    from botocore.exceptions import ResponseStreamingError

    from app.domain.storage import StorageIOError

    key = s3_store.write_original(b"abc", "png", _info())
    content = s3_store.open_content(key, "0" * 64, "original")
    assert content is not None

    class _Broken:
        def iter_chunks(self, _size: int) -> Any:
            raise ResponseStreamingError(error="connection reset")
            yield b""  # pragma: no cover

        def close(self) -> None:
            pass

    from app.domain import object_storage

    with pytest.raises(StorageIOError):
        list(object_storage._iter_body(_Broken()))
    content.read_all()


def test_s3_timeouts_are_explicit() -> None:
    from app.domain import object_storage

    store = S3Store.create("gakei-test", region="us-east-1")
    config = store._client.meta.config
    assert config.connect_timeout == object_storage.CONNECT_TIMEOUT == 5
    assert config.read_timeout == object_storage.READ_TIMEOUT == 30
    assert config.retries["total_max_attempts"] == object_storage.S3_MAX_ATTEMPTS == 3


def test_azure_timeouts_are_explicit() -> None:
    from app.domain import object_storage
    from app.domain.object_storage import AzureBlobStore

    store = AzureBlobStore.from_connection_string(
        "DefaultEndpointsProtocol=http;AccountName=a;AccountKey=YWJj;"
        "BlobEndpoint=http://127.0.0.1:1/a;",
        "images",
    )
    connection = store._container._pipeline._transport.connection_config
    assert connection.timeout == object_storage.CONNECT_TIMEOUT
    assert connection.read_timeout == object_storage.READ_TIMEOUT


def test_azure_sdk_errors_are_wrapped_in_oserror(tmp_path: Path) -> None:
    from app.domain.object_storage import AzureBlobStore
    from app.domain.storage import StorageIOError

    key = "c2VjcmV0LWtleS1mb3ItZ2FrZWktdGVzdHM="
    store = AzureBlobStore.from_connection_string(
        "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
        f"AccountKey={key};BlobEndpoint=http://127.0.0.1:1/devstoreaccount1;",
        "images",
    )
    store._container._config.retry_policy.total_retries = 0
    for call in (
        lambda: store.size_of("a.png"),
        lambda: store.exists("a.png"),
        lambda: store.read("a.png"),
        lambda: store.open_content("a.png", "0" * 64, "original"),
        lambda: store.write_new("a.png", b"x"),
        lambda: store.write_derived("ab" * 32, "thumb", b"x"),
    ):
        with pytest.raises(StorageIOError) as exc_info:
            call()
        assert key not in str(exc_info.value)


def test_mcp_degrades_when_storage_is_unreachable(client: Any) -> None:
    """保存先に接続できなくても、MCP の `get_asset` はサムネイルと透過の情報を null にして
    返す(ローカルFSの頃の `except OSError` のまま劣化する)。`get_image` はツールのエラー。"""
    from tests.test_mcp import _call, _enable, _error_text, _ok
    from tests.test_mcp_feedback import _upload_via_url

    _enable(client)
    asset_id = _upload_via_url(client, make_png_bytes(64, 64, (10, 20, 30)))
    client.app.state.store = _unreachable_s3_store()

    result = _call(client, "get_asset", {"asset_id": asset_id})
    payload = _ok(result)
    assert payload["has_alpha"] is None
    assert payload["transparent_ratio"] is None
    assert [c for c in result["content"] if c["type"] == "image"] == []

    result = _call(client, "get_image", {"asset_id": asset_id})
    assert "could not be read" in _error_text(result)
