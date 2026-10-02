# 画像を Azure Blob Storage / S3 互換ストレージに保存する(ADR-0028)

GAKEI は既定で、画像の原本と派生(サムネイル・プレビュー)を `DATA_DIR` の下(`assets/`、`derived/`)に保存する。サーバーに置く場合は、環境変数 `STORAGE_BACKEND` で保存先を **Azure Blob Storage** か **S3 互換ストレージ**(AWS S3、Cloudflare R2 など)に切り替えられる。容量の心配とバックアップ(論理削除、バージョニング、冗長化)をストレージ側に任せられる。

- 切り替わるのは画像の原本と派生だけ。アバター(`DATA_DIR/avatars/`)、`secrets.json`、ONNX のモデル、SQLite の DB などは、これまでどおり `DATA_DIR` に置く。**オブジェクトストレージを使う場合も `DATA_DIR` は要る。**
- 設定画面からは変えない(`DATABASE_URL` と同じく、データの置き場所が変わるため)。
- キー(オブジェクトの名前)はローカルと同じ(例: `assets/openai/gpt-image-2.5/2026-09/20260929-093015_1a2b3c4d.png`、`derived/{sha256}/thumb.webp`)。そのため、ローカルから移すときに DB を書き換えずに済む。
- 配信は、これまでどおり GAKEI の API を経由する(署名付き URL へのリダイレクトはしない)。**コンテナ(バケット)は非公開のままでよい。**
- オブジェクトストレージからの原本の配信では、Range 要求(ファイルの一部だけの取得)に応えない。ビューアは原本を丸ごと読むので影響はない。
- GAKEI のプロセス(コンテナ)は、これまでどおり1つだけにする(ADR-0027 3章)。

**確認の範囲:** Azure Blob Storage は Azurite(公式のエミュレーター)で確かめた。S3 は moto(AWS の API をまねるライブラリ)と Cloudflare R2 の実機で確かめた(R2 は 2026-09-30)。AWS S3 そのものでは確かめていない。

## 共通の準備

- **コンテナ(バケット)は先に作っておく。** GAKEI は作らない(作成の権限を持たせないため)。
- GAKEI に要る権限は、オブジェクトの読み取り・書き込み・存在の確認だけ(削除はしない)。存在の確認は、オブジェクトの有無を HEAD で問い合わせ、無ければ 404 を受け取ることを指す。AWS S3 では、これに**バケットに対する `s3:ListBucket`** が要る(無いと、存在しないキーに 404 ではなく 403 が返る。下の「S3 互換ストレージ」を参照)。Azure Blob Storage では、読み取りの権限があれば 404 が返る。
- 起動時に、コンテナ(バケット)に接続でき読み書きできるかを確かめる。確かめるために `.gakei/startup-check` というオブジェクトを毎回上書きする(消さずに残る)。バージョニングを有効にしている場合は、起動のたびにこのオブジェクトの版が1つ増える。確かめられなければ、理由を表示して起動を中止する。
- S3 互換ストレージでは、起動時にさらに次の2つを確かめる。無いキー(`.gakei/startup-check-absent`。書き込まない)の確認が 404 になること(403 なら権限不足として起動を中止する)と、条件付きの書き込み(`If-None-Match: *`)に対応していること(下の「同名のオブジェクトについて」)。
- 保存先への接続は 5 秒、応答の途切れは 30 秒で諦める。
- 接続文字列や秘密鍵は、ログにも画面にも出さない。

## Azure Blob Storage

```dotenv
STORAGE_BACKEND=azure_blob
AZURE_STORAGE_CONTAINER=gakei-images
```

接続は、次のどちらか一方で指定する(両方を書くと起動を中止する)。

### 接続文字列で接続する

Azure portal のストレージアカウント →「アクセスキー」にある接続文字列をそのまま書く。

```dotenv
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net
```

接続文字列にはアカウントのキーが入っている。`.env` を他人と共有しない。

### マネージド ID などで接続する(`DefaultAzureCredential`)

```dotenv
AZURE_STORAGE_ACCOUNT_URL=https://<アカウント名>.blob.core.windows.net
```

認証は `DefaultAzureCredential` に任せる。Azure 上ではマネージド ID、ローカルでは `az login` のログイン、あるいは `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_CLIENT_SECRET`(サービスプリンシパル)などが使われる。使う ID に、コンテナに対する「ストレージ BLOB データ共同作成者」(Storage Blob Data Contributor)のロールを割り当てる。

## S3 互換ストレージ

```dotenv
STORAGE_BACKEND=s3
S3_BUCKET=gakei-images
S3_REGION=ap-northeast-1
```

| 変数 | 内容 |
|---|---|
| `S3_BUCKET` | バケット名(必須) |
| `S3_REGION` | リージョン。AWS S3 では指定する |
| `S3_ENDPOINT_URL` | AWS 以外の互換ストレージの接続先 |
| `S3_FORCE_PATH_STYLE` | `true` にすると、パス形式の URL(`https://endpoint/bucket/key`)で接続する。仮想ホスト形式を受けない互換ストレージで使う |

資格情報は boto3 の標準の探し方に任せる。環境変数 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`、`~/.aws/credentials`、EC2 や ECS の IAM ロールなどが使われる。

```dotenv
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

AWS S3 の IAM ポリシーでは、バケットの中のオブジェクトに対する `s3:GetObject` と `s3:PutObject` に加えて、バケットに対する `s3:ListBucket` を許可する。`s3:ListBucket` が無いと、AWS S3 は存在しないキーの問い合わせに 404 ではなく 403(AccessDenied)を返すため、GAKEI は画像の有無を判定できない。その場合は起動時の確認で、`s3:ListBucket` が要る旨を表示して起動を中止する。

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::gakei-images/*"
    },
    {
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::gakei-images"
    }
  ]
}
```

### Cloudflare R2

R2 の「API トークン」でアクセスキーを作り(権限は「オブジェクトの読み取りと書き込み」、対象はそのバケットだけ)、次のように書く。

```dotenv
STORAGE_BACKEND=s3
S3_BUCKET=gakei-images
S3_REGION=auto
S3_ENDPOINT_URL=https://<アカウント ID>.r2.cloudflarestorage.com
AWS_ACCESS_KEY_ID=<アクセスキー ID>
AWS_SECRET_ACCESS_KEY=<シークレットアクセスキー>
```

### 同名のオブジェクトについて

同じ名前のオブジェクトを上書きしないよう、原本は条件付きの書き込み(`If-None-Match: *`)で保存し、同名があれば末尾に `-2`、`-3` を付ける。起動時に、`.gakei/startup-check` に条件を付けて書き込み、対応しているかを確かめる。

- 412 が返る(対応している): そのまま起動する。
- 条件を無視して上書きする: 警告をログに出して起動する。この場合は上書きが起こりうるが、名前に Asset の id の先頭 8 文字が入るので、実際に同名になることはまずない。
- 条件付きの書き込みそのものを拒否する(400、501 など): 画像の原本を保存できないので、起動を中止する。

## Docker で使う

`.env` に上の変数を書いて `docker compose up -d` する(Compose は `.env` を読み込む)。公開イメージを `docker run` する場合は `-e` や `--env-file` で渡す。画像はコンテナのボリュームに溜まらなくなるが、`DATA_DIR`(`/data`)のボリュームは引き続き要る。

## ローカルからの移行

既にローカル(`DATA_DIR`)に画像がある GAKEI を、オブジェクトストレージに移すツールを用意している。

```bash
uv run python -m app.tools.migrate_storage --to azure_blob|s3 [--dry-run]
```

- 先に **GAKEI を止める**。動かしたまま移すと、移行中に増えた画像が抜ける。
- 移行先の接続は、上の環境変数(`AZURE_STORAGE_*` / `S3_*`、S3 の資格情報)で指定する。移行先の種類は `--to` で指定する(`STORAGE_BACKEND` の値は見ない)。
- DB の全 Asset(削除済みを含む)の原本と、`DATA_DIR/derived/` の派生を、**同じキーでコピーする**。DB は書き換えない。
- 移行先に同じキーがあれば、大きさが同じなら飛ばし、違えば中止する(上書きしない)。ただし派生(`derived/...`)は原本から作り直せるので、大きさが違えば警告を表示して上書きする。途中で止まっても、もう一度実行すれば続きからコピーする。
- 移行先との通信に失敗した場合は、理由を表示して中止する(もう一度実行すれば続きからコピーする)。
- DB にあってローカルに無い原本(手で消されたものなど)は、件数を表示するだけで中止しない。
- `--dry-run` を付けると、移行先には接続せず、コピーする対象の件数と合計サイズを表示するだけになる。
- **ローカルのファイルは消さない。** 戻したい場合は `STORAGE_BACKEND` を外せば、元の状態(移行時点)で動く。移行後に増えた画像はローカルには無い。
- オブジェクトストレージからローカルへ戻すツールは用意していない。

移行が終わったら、`STORAGE_BACKEND` ほかの環境変数を設定して GAKEI を起動する。

### Docker での実行例

```bash
docker compose stop gakei
docker compose run --rm gakei python -m app.tools.migrate_storage --to s3 --dry-run
docker compose run --rm gakei python -m app.tools.migrate_storage --to s3
# .env に STORAGE_BACKEND=s3 を足してから
docker compose up -d gakei
```

## バックアップ

- **DB と画像の保存先は対で戻す必要がある**(ADR-0027 7章と同じ)。片方だけ古いものに戻すと、来歴の記録と実際の画像が食い違う。
- オブジェクトストレージ側の論理削除(Azure の「BLOB の論理的な削除」、S3 のバージョニングなど)を有効にしておくことを勧める。誤って消したり上書きしたりしても戻せる。
- `DATA_DIR`(アバター、`secrets.json` など)も、これまでどおりバックアップに取る。

## 注意

- **コンテナ(バケット)の中のオブジェクトを、直接消したり動かしたり名前を変えたりしない。** DB に記録したキーと合わなくなり、画像が表示できなくなる。画像の削除は画面から行う(論理削除)。
- **コンテナ(バケット)は非公開にする。** GAKEI は API を通して配信するので、公開は要らない。
- 1つのコンテナ(バケット)を複数の GAKEI で共有しない。
