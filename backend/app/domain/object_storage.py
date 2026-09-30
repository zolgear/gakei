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

import mimetypes
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Literal

from app.domain.storage import (
    CHUNK_SIZE,
    OriginalKeyInfo,
    StoredContent,
    Variant,
    candidate_names,
    content_key,
    derived_key,
    original_key_base,
)

if TYPE_CHECKING:
    from app.config import Settings

# 起動時に読み書きを確かめるためのキー(毎回同じキーに上書きする。GAKEI は削除の権限を
# 前提にしないので、消さずに残す)。
HEALTHCHECK_KEY = ".gakei/startup-check"


class StorageConfigError(ValueError):
    """`STORAGE_BACKEND` に必要な環境変数が足りない・組み合わせが不正(文言は i18n 済み)。"""


def _content_type(key: str) -> str:
    return mimetypes.guess_type(key)[0] or "application/octet-stream"


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
            ContainerClient.from_connection_string(connection_string, container), prefix=prefix
        )

    @classmethod
    def from_account_url(
        cls, account_url: str, container: str, *, prefix: str = ""
    ) -> AzureBlobStore:
        # マネージド ID、Azure CLI のログインなどで認証する(ADR-0028 2章)。
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import ContainerClient

        return cls(
            ContainerClient(account_url, container, credential=DefaultAzureCredential()),
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
        from azure.core.exceptions import ResourceExistsError

        folder, stem, ext = original_key_base(info, ext)
        for name in candidate_names(stem, ext):
            key = f"{folder}/{name}"
            try:
                # overwrite=False は `If-None-Match: *` の条件付き書き込み。同名があれば失敗する。
                self._upload(key, data, overwrite=False)
            except ResourceExistsError:
                continue
            return key
        raise FileExistsError(f"同名のファイルが多すぎます: {folder}/{stem}.{ext}")

    def write_derived(self, sha256: str, variant: Literal["thumb", "preview"], data: bytes) -> None:
        self._upload(derived_key(sha256, variant), data, overwrite=True)

    def read(self, blob_key: str) -> bytes:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self._blob(blob_key).download_blob().readall()
        except ResourceNotFoundError as e:
            raise FileNotFoundError(blob_key) from e

    def exists(self, blob_key: str) -> bool:
        return bool(self._blob(blob_key).exists())

    def content_exists(self, blob_key: str, sha256: str, variant: Variant) -> bool:
        return self.exists(content_key(blob_key, sha256, variant))

    def open_content(self, blob_key: str, sha256: str, variant: Variant) -> StoredContent | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            downloader = self._blob(content_key(blob_key, sha256, variant)).download_blob()
        except ResourceNotFoundError:
            return None
        return StoredContent(size=int(downloader.size), chunks=iter(downloader.chunks()))

    def check(self) -> None:
        """コンテナに接続でき、読み書きできるかを確かめる(ADR-0028 2章)。"""
        self._upload(HEALTHCHECK_KEY, b"ok", overwrite=True)
        if self.read(HEALTHCHECK_KEY) != b"ok":
            raise OSError("読み書きの確認で、書いた内容と読んだ内容が一致しません")


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

        config = Config(s3={"addressing_style": "path" if force_path_style else "auto"})
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
    def _error_code(e: Exception) -> str:
        response = getattr(e, "response", None) or {}
        return str(response.get("Error", {}).get("Code", ""))

    def _put(self, key: str, data: bytes, **kwargs: Any) -> None:
        self._client.put_object(
            Bucket=self.bucket,
            Key=self.prefix + key,
            Body=data,
            ContentType=_content_type(key),
            **kwargs,
        )

    def write_original(self, data: bytes, ext: str, info: OriginalKeyInfo) -> str:
        from botocore.exceptions import ClientError

        folder, stem, ext = original_key_base(info, ext)
        for name in candidate_names(stem, ext):
            key = f"{folder}/{name}"
            try:
                # `If-None-Match: *` の条件付き書き込み。同名があれば 412 PreconditionFailed。
                # 同じキーへの書き込みが同時に進んでいると 409 ConditionalRequestConflict に
                # なりうるので、これも「使われている」として次の連番へ進む。
                self._put(key, data, IfNoneMatch="*")
            except ClientError as e:
                if self._error_code(e) in ("PreconditionFailed", "ConditionalRequestConflict"):
                    continue
                raise
            return key
        raise FileExistsError(f"同名のファイルが多すぎます: {folder}/{stem}.{ext}")

    def write_derived(self, sha256: str, variant: Literal["thumb", "preview"], data: bytes) -> None:
        self._put(derived_key(sha256, variant), data)

    def _get(self, key: str) -> dict[str, Any] | None:
        from botocore.exceptions import ClientError

        try:
            return self._client.get_object(Bucket=self.bucket, Key=self.prefix + key)
        except ClientError as e:
            if self._error_code(e) in ("NoSuchKey", "404"):
                return None
            raise

    def read(self, blob_key: str) -> bytes:
        response = self._get(blob_key)
        if response is None:
            raise FileNotFoundError(blob_key)
        with response["Body"] as body:
            return body.read()

    def exists(self, blob_key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=self.prefix + blob_key)
        except ClientError as e:
            if self._error_code(e) in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
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
        """バケットに接続でき、読み書きできるかを確かめる(ADR-0028 2章)。"""
        self._put(HEALTHCHECK_KEY, b"ok")
        if self.read(HEALTHCHECK_KEY) != b"ok":
            raise OSError("読み書きの確認で、書いた内容と読んだ内容が一致しません")


def _iter_body(body: Any) -> Iterator[bytes]:
    """botocore の StreamingBody をチャンクで読み、読み終えたら(途中でやめても)閉じる。"""
    try:
        yield from body.iter_chunks(CHUNK_SIZE)
    finally:
        body.close()


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
