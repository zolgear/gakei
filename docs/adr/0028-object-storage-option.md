# ADR-0028: 画像の保存先に Azure Blob Storage と S3 互換ストレージも選べるようにする

**Status:** Proposed
**Date:** 2026-09-30

2026-09-30 に、この ADR の内容がユーザーに承認された。あわせて、依存パッケージは通常の依存に入れる(5章)、移行ツールを作る(6章)ことをユーザーが決めた。

2026-10-01 改訂: PR #37 のレビューの指摘を受けて、2章(起動時の確認、SDK の例外、タイムアウト)と6章(派生の大きさの違い)に追記した(ユーザー承認済み)。

## Context

ローカルMVP(ADR-0008)は、画像の原本と派生(サムネイル・プレビュー)を `DATA_DIR` の下のローカル FS に保存する(`LocalFsStore`。キー規則は ADR-0026)。本線の設計(ADR-0004)は Azure Blob Storage 前提で、`AssetStore` の実装を差し替える想定にしてある。

サーバーに置く運用(Docker、ADR-0016。PostgreSQL、ADR-0027)では、メタデータを PostgreSQL に置けるようになったが、画像はまだコンテナのボリュームにある。画像もマネージドなストレージに置けば、容量の心配とバックアップ(論理削除、バージョニング、冗長化)をストレージ側に任せられる。

Azure Blob Storage に加えて、S3 互換のストレージ(AWS S3、Cloudflare R2 など)も求められている。S3 は Azure Blob とプロトコルが違うため、実装は別になる。メンテナーは AWS の環境を持っていないが、S3 互換の Cloudflare R2 のアカウントは持っている。

今の保存の抽象には、ローカル FS を前提にした箇所が1つある。`AssetStore.content_path()` が `Path` を返し、配信(`FileResponse`)、ファイルの有無の確認、MCP の `get_image`、タガーがそれを直接使っている。

Issue [#35](https://github.com/zolgear/gakei/issues/35)。

## Decision

### 1. 範囲

- 選べるようにするのは、画像の原本と派生(`assets/`、`derived/`)の保存先だけにする。
- アバター(`DATA_DIR/avatars/`。ADR-0020)、`secrets.json`、ONNX のモデル、SQLite の DB、一時ファイルは、これまでどおり `DATA_DIR` に置く。そのため、オブジェクトストレージを使う場合でも `DATA_DIR` は要る。
- GAKEI のプロセス(コンテナ)は1つだけ、という制約(ADR-0027 3章)は変えない。

### 2. 設定

環境変数 `STORAGE_BACKEND` で選ぶ。未指定なら `local`(これまでどおり)。設定画面からは変えない(`DATABASE_URL` と同じく、データの置き場所が変わるため)。

| `STORAGE_BACKEND` | 使う環境変数 |
|---|---|
| `local`(既定) | なし(`DATA_DIR`) |
| `azure_blob` | `AZURE_STORAGE_CONTAINER`(必須)。接続は `AZURE_STORAGE_CONNECTION_STRING` か、`AZURE_STORAGE_ACCOUNT_URL` のどちらか。後者は `DefaultAzureCredential`(マネージド ID、Azure CLI のログインなど)で認証する |
| `s3` | `S3_BUCKET`(必須)、`S3_REGION`、`S3_ENDPOINT_URL`(AWS 以外の互換ストレージのとき)、`S3_FORCE_PATH_STYLE`(パス形式の URL しか受けない互換ストレージのとき)。資格情報は boto3 の標準の探し方(`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`、IAM ロールなど)に任せる |

- 起動時に、コンテナ(バケット)に接続できて読み書きできるかを確かめる。できなければ、分かる文言(日本語と英語)を出して起動を中止する。
- コンテナ(バケット)は GAKEI が作らない。管理者が先に作っておく(GAKEI に作成の権限を持たせないため)。
- 接続文字列や秘密鍵はログと画面に出さない。
- 設定画面の「GAKEI について」など、保存先を表示する箇所では、種類とコンテナ(バケット)名だけを出す。

**2026-10-01 改訂(起動時の確認、SDK の例外、タイムアウト):**

- **403 の扱いと `s3:ListBucket`。** AWS S3 は、`s3:ListBucket` の権限が無いと、存在しないキーへの `HeadObject` / `GetObject` に 404 ではなく 403 AccessDenied を返す。403 を「無い」とみなすと権限の誤りを隠すので、「無い」とはみなさず、`s3:ListBucket` が要ると分かる文言の例外にする。`docs/object-storage.md` の IAM ポリシーに、バケットに対する `s3:ListBucket` を加える。起動時の確認で、無いキー(`.gakei/startup-check-absent`)を1回問い合わせ、403 なら同じ文言で起動を中止する。Azure Blob Storage は、読み取りの権限があれば無いキーに 404 を返すので、この確認は S3 だけで行う。
- **条件付きの書き込みの確認(S3)。** 起動時に、既にある `.gakei/startup-check` に `If-None-Match: *` を付けて書く。412 なら対応しているとみなす。成功した(条件を無視して上書きした)なら、3章で許容しているとおり警告をログに出して起動を続ける。それ以外の 4xx / 5xx(400、501 など)なら、原本を保存するたびに失敗する(課金の後で Run が failed になる)ので、起動を中止する。Azure Blob Storage は、実装が Azure 本体と公式のエミュレーター(Azurite)だけで、どちらも条件付きの書き込みに対応しているので、この確認をしない。
- **SDK の例外を `OSError` に包む。** SDK の例外(botocore の `ClientError` / `BotoCoreError`、Azure の `AzureError` の派生)は `OSError` の派生ではないため、ローカル FS の頃から `except OSError` で劣化させている呼び出し側(MCP のサムネイルや透過の判定など)がエラーになる。ストアの各メソッドで、404 / 412 / 409 などの意味のある判定を済ませた後の SDK の例外を、`StorageIOError`(`OSError` の派生)に包み直す。文言は例外の種類と伏せ字にした1行だけにし、元の例外はつながない(鍵や接続先の全文をトレースバックに出さないため。起動時の `StorageUnavailableError` も同じ)。
- **タイムアウト。** SDK の既定(Azure は接続・読み取りとも 300 秒、boto3 は 60 秒)のままだと、保存先が応答しないときに配信や生成の保存が数分止まる。接続は 5 秒、読み取り(応答の途切れ)は 30 秒にし、boto3 の再試行は最初の1回を含めて 3 回までにする。

### 3. キーの規則

- オブジェクトストレージでも、ローカル FS と同じキーを使う(ADR-0026 1章。例: `assets/openai/gpt-image-2.5-sunburst/2026-09/20260929-093015_1a2b3c4d.png`)。派生も `derived/{sha256}/thumb.webp` のまま。ADR-0004 の「本線の Blob のキー規則は本線の着手時に決める」を、この ADR で決める。
- 同じキーなので、ローカル FS から移すときに `asset.blob_key` を書き換えずに済む(6章)。古いキー(`assets/{2文字}/{sha256}.{拡張子}`)の Asset も、そのキーのまま読める。
- 同じ内容の原本の共有(ADR-0026 3章)は、DB で探す今の仕組みのまま、どのストアでも働く。
- 同名のときの連番(`-2`、`-3`)は、上書きしない条件付きの書き込みで確保する。Azure Blob は `If-None-Match: *`(`overwrite=False`)、S3 は `PutObject` の `If-None-Match: *`。条件付きの書き込みに対応していない S3 互換ストレージでは上書きが起こりうるが、キーに Asset の id の先頭 8 文字が入るので、実際に同名になることはまずない。

### 4. 保存の抽象(`AssetStore`)

- `content_path()`(`Path` を返す)をやめ、次の2つに置き換える。
  - `content_exists(blob_key, sha256, variant) -> bool`
  - `open_content(blob_key, sha256, variant) -> StoredContent | None`。`StoredContent` は、大きさと、チャンクを順に返すイテレーターを持つ。無ければ `None`。
- ローカル FS では、これまでどおり `FileResponse` で返す(Range 要求にも応える)。オブジェクトストレージでは、読み出したチャンクを `StreamingResponse` でそのまま流し、`Content-Length` を付ける。Range には応えない(ビューアは原本を丸ごと読むので要らない)。
- `read()`(バイト列を丸ごと返す)は、系列情報の埋め込み、タガー、Edit の入力など、画像を処理するところでこれまでどおり使う。
- 配信は、これまでどおり API を経由する(ADR-0004 5章 A)。署名付き URL(SAS、presigned URL)へのリダイレクトは使わない。ストレージを外に開かずに済み、認可と証跡が1か所で済むため。

### 5. 依存パッケージ

- `azure-storage-blob`、`azure-identity`、`boto3` を、通常の依存に加える。psycopg(ADR-0027)と同じく、使わない人にも入る。import は使うときだけ行う(起動時間と、使わないときの失敗を避けるため)。

### 6. ローカル FS からの移行ツール

```bash
uv run python -m app.tools.migrate_storage --to azure_blob|s3 [--dry-run]
```

- 移行先の接続は、2章の環境変数で指定する。GAKEI を止めてから使う。
- DB の `asset.blob_key` にある原本と、`derived/` の派生を、同じキーでコピーする。DB は書き換えない。
- 移行先に同じキーがあれば、大きさを比べて同じなら飛ばし、違えば中止する(上書きしない)。途中で止まっても、もう一度実行すれば続きからコピーする。
- 2026-10-01 改訂: 派生(`derived/...`)は原本から作り直せるので、大きさが違っても中止せず、警告を表示して上書きする(原本の大きさの違いは、これまでどおり中止する)。コピー中や大きさの確認での保存先の失敗は、トレースバックではなく文言(鍵を含めない)を出して中止する。
- ローカルのファイルは消さない。戻したい場合は `STORAGE_BACKEND` を外せば、元の状態で動く(移行後に増えた画像はローカルにはない)。
- オブジェクトストレージからローカル FS へ戻すツールは作らない。

### 7. テストと CI

- ストアの共通の振る舞い(書き込み、同名の連番、読み出し、有無の確認、派生、古いキー)を1組のテストにまとめ、ローカル FS、Azure Blob、S3 のそれぞれで回す。
- **S3** は moto(AWS の API をまねる Python のライブラリ。`If-None-Match` の条件付き書き込みにも対応)を、テストの中でサーバーとして立てて確かめる。Docker が要らないので、既定の `uv run pytest` と、Windows を含む全部の CI ジョブで回る。
- **Azure Blob** は Azurite(Microsoft の公式エミュレーター)で確かめる。環境変数 `GAKEI_TEST_AZURE_BLOB_CONNECTION_STRING` があるときだけ回し、無ければ飛ばす。CI には Azurite を `services` で立てるジョブを足す(Ubuntu、PR でも走らせる)。
- 実機の S3 互換ストレージで確かめたい人のために、`GAKEI_TEST_S3_ENDPOINT_URL` ほか(バケット、資格情報)があれば、moto の代わりにそこへつないで回せるようにする。
- 実機では、Azure Blob Storage と Cloudflare R2(S3 互換)を、メンテナーの環境で確かめる。R2 は上の `GAKEI_TEST_S3_*` でつなぐ。AWS S3 そのものは確かめられないので、`docs/` には「S3 は moto と Cloudflare R2 で確かめた」と書く。
- MinIO は使わない。コミュニティ版は 2025-12 にメンテナンスモードになり、2026-02 にリポジトリがアーカイブされた。以後の脆弱性は直らず、公式のコンテナイメージも取得できなくなっている。

### 8. バックアップと注意

- DB と画像の保存先は対で戻す必要がある(ADR-0027 7章と同じ)。
- オブジェクトストレージ側の論理削除(soft delete)やバージョニングを有効にすることを勧める(ADR-0004 の Consequences)。
- コンテナ(バケット)の中のオブジェクトを直接消したり動かしたりしない(ADR-0026 5章と同じ)。
- コンテナ(バケット)は非公開にする。GAKEI は API を通して配信するので、公開は要らない。

## Options Considered

### 配信

| 案 | 評価 |
|---|---|
| A: API を経由してストリーミング(採用) | ADR-0004 の決定のまま。ストレージを外に開かずに済み、認可と証跡が1か所で済む |
| B: 署名付き URL にリダイレクト | API の負荷は減るが、ブラウザからストレージに届く経路が要る。URL が有効な間は持ち出せる。ローカル FS と振る舞いが分かれる |

### S3 の実装

| 案 | 評価 |
|---|---|
| A: boto3(採用) | 事実上の標準で、互換ストレージ(Cloudflare R2 など)の手順もこれを前提に書かれていることが多い。条件付き書き込みに対応している |
| B: Azure Blob の S3 互換機能や、S3 互換のゲートウェイで1つにまとめる | Azure Blob に S3 互換の API はなく、ゲートウェイを挟むと部品が増える |
| C: fsspec / obstore などの抽象化ライブラリ | 1つの API で両方を扱えるが、条件付き書き込みや認証の細かい指定が抽象の外になりやすい。使うのは数個の操作だけなので、直接書く方が見通しがよい |

### 依存パッケージの入れ方

| 案 | 評価 |
|---|---|
| A: 通常の依存に加える(採用) | 起動スクリプトと Docker イメージの作り方を変えずに済む。psycopg と同じ扱い |
| B: extras(`uv sync --extra s3` など)にする | 使わない人の容量は減るが、起動スクリプトと Docker で入れ分けが要る |

## Trade-off Analysis

オブジェクトストレージを使う人はサーバーに置く人に限られ、個人の PC の既定(ローカル FS、設定不要)は崩したくない。そのため `STORAGE_BACKEND` が無ければ何も変わらない形にし、違いは `AssetStore` の実装と、配信の応答の作り方に閉じ込める。キーをローカル FS と同じにしたので、移行は「同じキーでコピーするだけ」で済み、DB に手を入れずに戻すこともできる。S3 は実機で試せないが、API をまねる moto をテストの中で回すことで、使う操作の範囲は確かめられる。

## Consequences

- 画像をマネージドなストレージに置けるようになり、コンテナのボリュームに画像が溜まらなくなる(`DATA_DIR` は引き続き要る)。
- 依存に `azure-storage-blob`、`azure-identity`、`boto3` が加わる。
- オブジェクトストレージでは、原本の配信で Range 要求に応えない。
- CI のジョブが1つ増える。
- ADR-0004 の Blob のキー規則の注記と、ADR-0008 の対比表の画像保存の行に、この ADR への注記を足す。

## Action Items

1. [x] `AssetStore` の `content_path` を `content_exists` / `open_content` に置き換え、配信・ダウンロード・MCP・タガーを書き換える(ローカル FS の振る舞いは変えない)
2. [x] 設定(`STORAGE_BACKEND` ほか)と起動時の確認
3. [x] `AzureBlobStore` と `S3Store`(条件付き書き込みによる連番)
4. [x] ストアの共通テスト(S3 は moto、Azure Blob は Azurite)、Azurite の CI ジョブ
5. [x] 移行ツール `app.tools.migrate_storage`
6. [x] `docs/configuration.md`、`docs/`(オブジェクトストレージの使い方、移行、バックアップ)、README、CLAUDE.md、ADR-0004 / 0008 への注記
