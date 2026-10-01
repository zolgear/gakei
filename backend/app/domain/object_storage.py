"""オブジェクトストレージのストア(ADR-0028)。Azure Blob Storage と S3 互換ストレージ。

- キーはローカルFS(`LocalFsStore`)と同じ(ADR-0026。`original_key_base` を共用)。派生は
  `derived/{sha256}/thumb.webp` など。そのため、ローカルFSから移しても `asset.blob_key` を
  書き換えずに済む。
- 原本は上書きしない条件付きの書き込み(`If-None-Match: *`)で名前を確保し、同名があれば
  `-2`、`-3` を付ける(ADR-0028 3章)。派生は内容から作り直せるので上書きしてよい。
- SDK(azure-storage-blob / azure-identity / boto3)は使うときだけ import する(起動時間と、
  使わないときの失敗を避けるため。ADR-0028 5章)。
- `prefix` はキーの前に付ける接頭辞。利用者向けの設定ではなく、実機のストレージでテストを
  回すとき(テストごとに別の接頭辞を使って後片付けする)のためにある。
- 接続文字列・秘密鍵はログにも例外の文言にも出さない。
"""

from __future__ import annotations

import logging
import mimetypes
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Literal

from app.domain.storage import (
    CHUNK_SIZE,
    OriginalKeyInfo,
    StorageConditionalWriteError,
    StorageIOError,
    StoragePermissionError,
    StoredContent,
    Variant,
    _first_line,
    candidate_names,
    content_key,
    derived_key,
    original_key_base,
)

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

# Azure の読み出しの単位。既定(最初の 32MB を1回で取る)のままだと、4K の原本を配信する
# たびに丸ごとメモリに載るので、小さめにして順に流す。
_AZURE_GET_SIZE = 4 * 1024 * 1024

# タイムアウト(秒)。SDK の既定は Azure が接続・読み取りとも 300 秒、boto3 が 60 秒で、
# 保存先が応答しないと配信や生成の保存が数分止まる。接続は同じリージョン・同じネットワーク
# なら 1 秒もかからないので 5 秒で諦める。読み取りのタイムアウトは応答の途切れ(ソケットの
# 1回の読み取りを待つ時間)で、転送全体の時間ではないので、大きな原本でも 30 秒で足りる。
# boto3 の再試行は、最初の1回を含めて 3 回まで(既定の legacy は 5 回)。
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 30
S3_MAX_ATTEMPTS = 3

# 起動時に読み書きを確かめるためのキー(毎回同じキーに上書きする。GAKEI は削除の権限を
# 前提にしないので、消さずに残す)。
HEALTHCHECK_KEY = ".gakei/startup-check"
# 起動時に「無いキー」の確認が 404 になるかを確かめるためのキー(書かない)。
HEALTHCHECK_ABSENT_KEY = ".gakei/startup-check-absent"


class StorageConfigError(ValueError):
    """`STORAGE_BACKEND` に必要な環境変数が足りない・組み合わせが不正(文言は i18n 済み)。"""


def _content_type(key: str) -> str:
    return mimetypes.guess_type(key)[0] or "application/octet-stream"


def _io_error(e: BaseException) -> StorageIOError:
    """SDK の例外を `StorageIOError`(`OSError` の派生)に包み直す。鍵・署名は伏せる。

    呼び出し側は `raise _io_error(e) from None` とする(元の例外の全文をつながない)。
    """
    return StorageIOError(f"{type(e).__name__}: {_first_line(e)}")


def _azure_errors() -> tuple[type[BaseException], ...]:
    # HttpResponseError(ResourceNotFoundError などを含む)、ServiceRequestError、
    # azure-identity の ClientAuthenticationError は、どれも AzureError の派生。
    from azure.core.exceptions import AzureError

    return (AzureError,)


def _s3_errors() -> tuple[type[BaseException], ...]:
    # EndpointConnectionError、ReadTimeoutError、NoCredentialsError などは BotoCoreError の派生。
    from botocore.exceptions import BotoCoreError, ClientError

    return (ClientError, BotoCoreError)


class AzureBlobStore:
    """Azure Blob Storage のストア。"""

    kind = "azure_blob"

    def __init__(self, container_client: Any, *, prefix: str = "") -> None:
        # `container_client` は azure.storage.blob.ContainerClient
        self._container = container_client
        self.container_name: str = container_client.container_name
        self.prefix = prefix

    @classmethod
    def from_connection_string(
        cls, connection_string: str, container: str, *, prefix: str = ""
    ) -> AzureBlobStore:
        from azure.storage.blob import ContainerClient

        return cls(
            ContainerClient.from_connection_string(
                connection_string,
                container,
                max_single_get_size=_AZURE_GET_SIZE,
                max_chunk_get_size=_AZURE_GET_SIZE,
                connection_timeout=CONNECT_TIMEOUT,
                read_timeout=READ_TIMEOUT,
            ),
            prefix=prefix,
        )

    @classmethod
    def from_account_url(
        cls, account_url: str, container: str, *, prefix: str = ""
    ) -> AzureBlobStore:
        # マネージド ID、Azure CLI のログインなどで認証する(ADR-0028 2章)。
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import ContainerClient

        return cls(
            ContainerClient(
                account_url,
                container,
                credential=DefaultAzureCredential(),
                max_single_get_size=_AZURE_GET_SIZE,
                max_chunk_get_size=_AZURE_GET_SIZE,
                connection_timeout=CONNECT_TIMEOUT,
                read_timeout=READ_TIMEOUT,
            ),
            prefix=prefix,
        )

    def _blob(self, key: str) -> Any:
        return self._container.get_blob_client(self.prefix + key)

    def _upload(self, key: str, data: bytes, *, overwrite: bool) -> None:
        from azure.storage.blob import ContentSettings

        self._blob(key).upload_blob(
            data,
            overwrite=overwrite,
            content_settings=ContentSettings(content_type=_content_type(key)),
        )

    def write_original(self, data: bytes, ext: str, info: OriginalKeyInfo) -> str:
        return _write_original(self, data, ext, info)

    def write_derived(self, sha256: str, variant: Literal["thumb", "preview"], data: bytes) -> None:
        self.overwrite(derived_key(sha256, variant), data)

    def overwrite(self, key: str, data: bytes) -> None:
        """上書きしてよいキー(派生)に書く。"""
        try:
            self._upload(key, data, overwrite=True)
        except _azure_errors() as e:
            raise _io_error(e) from None

    def write_new(self, key: str, data: bytes) -> None:
        """キーを指定して、上書きせずに書く(移行ツール用)。あれば `FileExistsError`。"""
        from azure.core.exceptions import ResourceExistsError

        try:
            # overwrite=False は `If-None-Match: *` の条件付き書き込み。同名があれば失敗する。
            self._upload(key, data, overwrite=False)
        except ResourceExistsError as e:
            raise FileExistsError(key) from e
        except _azure_errors() as e:
            raise _io_error(e) from None

    def size_of(self, key: str) -> int | None:
        """キーの大きさ(バイト)。無ければ None。"""
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return int(self._blob(key).get_blob_properties().size)
        except ResourceNotFoundError:
            return None
        except _azure_errors() as e:
            raise _io_error(e) from None

    def read(self, blob_key: str) -> bytes:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self._blob(blob_key).download_blob().readall()
        except ResourceNotFoundError as e:
            raise FileNotFoundError(blob_key) from e
        except _azure_errors() as e:
            raise _io_error(e) from None

    def exists(self, blob_key: str) -> bool:
        try:
            return bool(self._blob(blob_key).exists())
        except _azure_errors() as e:
            raise _io_error(e) from None

    def content_exists(self, blob_key: str, sha256: str, variant: Variant) -> bool:
        return self.exists(content_key(blob_key, sha256, variant))

    def open_content(self, blob_key: str, sha256: str, variant: Variant) -> StoredContent | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            downloader = self._blob(content_key(blob_key, sha256, variant)).download_blob()
        except ResourceNotFoundError:
            return None
        except _azure_errors() as e:
            raise _io_error(e) from None
        return StoredContent(size=int(downloader.size), chunks=_iter_azure(downloader))

    def check(self) -> None:
        """コンテナに接続でき、読み書きできるかを確かめる(ADR-0028 2章)。

        条件付きの書き込み(`overwrite=False`)の対応は確かめない。Azure Blob Storage の
        実装は Azure 本体と公式のエミュレーター(Azurite)だけで、どちらも対応している
        (S3 互換ストレージのように、実装ごとに対応が分かれることがない)。
        """
        self.overwrite(HEALTHCHECK_KEY, b"ok")
        if self.read(HEALTHCHECK_KEY) != b"ok":
            raise StorageIOError("読み書きの確認で、書いた内容と読んだ内容が一致しません")


class S3Store:
    """S3 互換ストレージのストア(AWS S3、Cloudflare R2 など)。"""

    kind = "s3"

    def __init__(self, client: Any, bucket: str, *, prefix: str = "") -> None:
        # `client` は boto3 の S3 クライアント
        self._client = client
        self.container_name = bucket
        self.bucket = bucket
        self.prefix = prefix

    @classmethod
    def create(
        cls,
        bucket: str,
        *,
        region: str | None = None,
        endpoint_url: str | None = None,
        force_path_style: bool = False,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        prefix: str = "",
    ) -> S3Store:
        """資格情報は省くと boto3 の標準の探し方(環境変数、IAM ロールなど)に任せる。"""
        import boto3
        from botocore.config import Config

        config = Config(
            s3={"addressing_style": "path" if force_path_style else "auto"},
            connect_timeout=CONNECT_TIMEOUT,
            read_timeout=READ_TIMEOUT,
            retries={"total_max_attempts": S3_MAX_ATTEMPTS, "mode": "standard"},
        )
        client = boto3.client(
            "s3",
            region_name=region or None,
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=config,
        )
        return cls(client, bucket, prefix=prefix)

    @staticmethod
    def _error_code(e: BaseException) -> str:
        response = getattr(e, "response", None) or {}
        return str(response.get("Error", {}).get("Code", ""))

    @staticmethod
    def _status(e: BaseException) -> int | None:
        response = getattr(e, "response", None) or {}
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return int(status) if status is not None else None

    @classmethod
    def _is_missing(cls, e: BaseException) -> bool:
        return cls._error_code(e) in ("404", "NoSuchKey", "NotFound") or cls._status(e) == 404

    def _lookup_error(self, e: BaseException, key: str) -> StorageIOError:
        """無いかもしれないキーの HEAD / GET の失敗(404 以外)を包み直す。

        AWS S3 は、`s3:ListBucket` の権限が無いと、存在しないキーに 404 ではなく 403
        AccessDenied を返す。403 を「無い」とみなすと権限の誤りを隠すので、権限不足と分かる
        文言にする(ADR-0028、2026-10-01 改訂)。
        """
        if self._status(e) == 403 or self._error_code(e) in ("403", "AccessDenied", "Forbidden"):
            from app.i18n import console_t

            return StoragePermissionError(console_t("app.storageS3LookupForbidden", key=key))
        return _io_error(e)

    def _put(self, key: str, data: bytes, **kwargs: Any) -> None:
        self._client.put_object(
            Bucket=self.bucket,
            Key=self.prefix + key,
            Body=data,
            ContentType=_content_type(key),
            **kwargs,
        )

    def write_original(self, data: bytes, ext: str, info: OriginalKeyInfo) -> str:
        return _write_original(self, data, ext, info)

    def write_derived(self, sha256: str, variant: Literal["thumb", "preview"], data: bytes) -> None:
        self.overwrite(derived_key(sha256, variant), data)

    def overwrite(self, key: str, data: bytes) -> None:
        """上書きしてよいキー(派生)に書く。"""
        try:
            self._put(key, data)
        except _s3_errors() as e:
            raise _io_error(e) from None

    def write_new(self, key: str, data: bytes) -> None:
        """キーを指定して、上書きせずに書く(移行ツール用)。あれば `FileExistsError`。"""
        try:
            # `If-None-Match: *` の条件付き書き込み。同名があれば 412 PreconditionFailed。
            # 同じキーへの書き込みが同時に進んでいると 409 ConditionalRequestConflict に
            # なりうるので、これも「使われている」とみなす。
            self._put(key, data, IfNoneMatch="*")
        except _s3_errors() as e:
            if self._error_code(e) in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise FileExistsError(key) from e
            raise _io_error(e) from None

    def size_of(self, key: str) -> int | None:
        """キーの大きさ(バイト)。無ければ None。"""
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=self.prefix + key)
        except _s3_errors() as e:
            if self._is_missing(e):
                return None
            raise self._lookup_error(e, key) from None
        return int(response["ContentLength"])

    def _get(self, key: str) -> dict[str, Any] | None:
        try:
            return self._client.get_object(Bucket=self.bucket, Key=self.prefix + key)
        except _s3_errors() as e:
            if self._is_missing(e):
                return None
            raise self._lookup_error(e, key) from None

    def read(self, blob_key: str) -> bytes:
        response = self._get(blob_key)
        if response is None:
            raise FileNotFoundError(blob_key)
        try:
            with response["Body"] as body:
                return body.read()
        except _s3_errors() as e:
            raise _io_error(e) from None

    def exists(self, blob_key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self.prefix + blob_key)
        except _s3_errors() as e:
            if self._is_missing(e):
                return False
            raise self._lookup_error(e, blob_key) from None
        return True

    def content_exists(self, blob_key: str, sha256: str, variant: Variant) -> bool:
        return self.exists(content_key(blob_key, sha256, variant))

    def open_content(self, blob_key: str, sha256: str, variant: Variant) -> StoredContent | None:
        response = self._get(content_key(blob_key, sha256, variant))
        if response is None:
            return None
        return StoredContent(
            size=int(response["ContentLength"]), chunks=_iter_body(response["Body"])
        )

    def check(self) -> None:
        """バケットに接続でき、読み書きできるかを確かめる(ADR-0028 2章、2026-10-01 改訂)。

        1. 読み書きできるか(`HEALTHCHECK_KEY` に上書きして読み戻す)。
        2. 無いキーの確認が 404 になるか。AWS S3 は `s3:ListBucket` が無いと 403 を返し、
           そのままでは配信や移行が失敗するので、ここで権限不足として止める。
        3. 条件付きの書き込み(`If-None-Match: *`)に対応しているか。既にある
           `HEALTHCHECK_KEY` に条件を付けて書き、412 なら対応。成功(条件を無視)なら
           上書きが起こりうることを警告して続ける(ADR-0028 3章が許容している)。それ以外の
           失敗(400 / 501 など)は、生成のたびに原本の保存が失敗するので起動を止める。
        """
        self.overwrite(HEALTHCHECK_KEY, b"ok")
        if self.read(HEALTHCHECK_KEY) != b"ok":
            raise StorageIOError("読み書きの確認で、書いた内容と読んだ内容が一致しません")
        self.exists(HEALTHCHECK_ABSENT_KEY)
        self._check_conditional_write()

    def _check_conditional_write(self) -> None:
        from app.i18n import console_t

        try:
            self._put(HEALTHCHECK_KEY, b"ok", IfNoneMatch="*")
        except _s3_errors() as e:
            if self._error_code(e) in ("PreconditionFailed", "ConditionalRequestConflict"):
                return
            if self._status(e) in (412, 409):
                return
            status = self._status(e)
            if status is None:
                # 接続の失敗など(HTTP の応答が無い)。
                raise _io_error(e) from None
            raise StorageConditionalWriteError(
                console_t(
                    "app.storageConditionalWriteRejected",
                    status=status,
                    code=self._error_code(e) or "-",
                )
            ) from None
        logger.warning(console_t("app.storageConditionalWriteIgnored"))


def _write_original(
    store: AzureBlobStore | S3Store, data: bytes, ext: str, info: OriginalKeyInfo
) -> str:
    """原本を新しいキーで書く。同名があれば `-2`、`-3` を付けて試す(ADR-0028 3章)。"""
    folder, stem, ext = original_key_base(info, ext)
    for name in candidate_names(stem, ext):
        key = f"{folder}/{name}"
        try:
            store.write_new(key, data)
        except FileExistsError:
            continue
        return key
    raise FileExistsError(f"同名のファイルが多すぎます: {folder}/{stem}.{ext}")


def _iter_body(body: Any) -> Iterator[bytes]:
    """botocore の StreamingBody をチャンクで読み、読み終えたら(途中でやめても)閉じる。"""
    try:
        try:
            yield from body.iter_chunks(CHUNK_SIZE)
        except _s3_errors() as e:
            raise _io_error(e) from None
    finally:
        body.close()


def _iter_azure(downloader: Any) -> Iterator[bytes]:
    """Azure の StorageStreamDownloader をチャンクで読む(途中の失敗も OSError に包む)。"""
    try:
        yield from downloader.chunks()
    except _azure_errors() as e:
        raise _io_error(e) from None


def _missing(*names: str) -> StorageConfigError:
    from app.i18n import console_t

    return StorageConfigError(console_t("app.storageConfigMissing", missing=", ".join(names)))


def build_azure_blob_store(settings: Settings) -> AzureBlobStore:
    from app.i18n import console_t

    container = (settings.azure_storage_container or "").strip()
    connection_string = (settings.azure_storage_connection_string or "").strip()
    account_url = (settings.azure_storage_account_url or "").strip()
    if not container:
        raise _missing("AZURE_STORAGE_CONTAINER")
    if connection_string and account_url:
        raise StorageConfigError(console_t("app.storageAzureBothConnections"))
    if connection_string:
        try:
            return AzureBlobStore.from_connection_string(connection_string, container)
        except ValueError as e:
            # 形式の誤りの文言に接続文字列(鍵)が入りうるので、例外の中身は出さない。
            raise StorageConfigError(console_t("app.storageAzureInvalidConnectionString")) from e
    if account_url:
        return AzureBlobStore.from_account_url(account_url, container)
    raise _missing("AZURE_STORAGE_CONNECTION_STRING / AZURE_STORAGE_ACCOUNT_URL")


def build_s3_store(settings: Settings) -> S3Store:
    bucket = (settings.s3_bucket or "").strip()
    if not bucket:
        raise _missing("S3_BUCKET")
    return S3Store.create(
        bucket,
        region=(settings.s3_region or "").strip() or None,
        endpoint_url=(settings.s3_endpoint_url or "").strip() or None,
        force_path_style=settings.s3_force_path_style,
    )
