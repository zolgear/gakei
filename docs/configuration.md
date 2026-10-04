# 環境変数

GAKEI は環境変数、またはリポジトリ直下の `.env` から設定を読む。通常は設定しなくても動く。API キーは起動後の設定画面から登録できる。

`.env` を使う場合は、[`.env.example`](../.env.example)(英語。日本語版は [`.env.example.ja`](../.env.example.ja))をリポジトリ直下に `.env` としてコピーして編集する。`.env` はコミットしない。項目は「OpenAI」「保存先とサーバー」「ComfyUI」「認証」の4つの区画に分けてあり、`#KEY=value` の行は無効な設定(値は既定値)、`# 文章` の行は説明である。

## 優先順位

同じ項目を複数の場所で指定した場合は、上にあるものが優先される。

1. 起動オプション(`--host`、`--port`、`--data-dir`)
2. 環境変数
3. `backend/.env`
4. リポジトリ直下の `.env`
5. 設定画面で保存した API キー / 接続先(Base URL)

`OPENAI_API_KEY` と `OPENAI_BASE_URL` は、設定画面で保存した値があっても環境変数(`.env` を含む)が常に優先される(上の優先順位の5番目)。画面から変更・削除できるのは、環境変数が指定されていないときだけ。

次の項目は例外で、設定画面で保存すると、以降は画面の設定が環境変数より優先される(環境変数は、画面で保存するまでの既定値として使う)。

- ComfyUI の接続先(`COMFYUI_URL`): 設定画面で一度でも接続または切り離しをしたとき
- `MODERATION`: 設定 → OpenAI(`/settings/openai`)で変えて、ページ上部の「保存」を押したとき
- `COMFYUI_TIMEOUT_SECONDS`: 設定 → ComfyUI(`/settings/comfyui`)で変えて、ページ上部の「保存」を押したとき

## 一覧

| 変数 | 既定 | 内容 |
|---|---|---|
| `OPENAI_API_KEY` | なし | OpenAI の API キー。設定画面で保存したキーより優先される |
| `OPENAI_BASE_URL` | なし(OpenAI 本体) | OpenAI 互換 API(LiteLLM などのプロキシ)の接続先。通常 `/v1` まで含む(例: `http://127.0.0.1:4000/v1`)。設定画面で保存した値より優先される。ループバック以外への `http` を指定すると、起動時に警告を出す |
| `DATA_DIR` | `./data` | 画像、画面で保存した API キー・接続先の保存先。`DATABASE_URL` 未指定時は SQLite のファイルもここに置く。変える場合は絶対パスで書く |
| `HOST` | `127.0.0.1` | 待ち受けるアドレス |
| `PORT` | `8000` | 待ち受けるポート |
| `DATABASE_URL` | なし(SQLite) | メタデータの DB を PostgreSQL にする場合の接続先(`postgresql://user:pass@host:5432/gakei`。ADR-0027)。指定しても、画像のために `DATA_DIR` は引き続き必要。詳しくは [postgresql.md](postgresql.md) |
| `STORAGE_BACKEND` | `local` | 画像の原本と派生の保存先。`local`(`DATA_DIR`)、`azure_blob`(Azure Blob Storage)、`s3`(S3 互換ストレージ)。ADR-0028。`local` 以外でも、アバターや `secrets.json` などのために `DATA_DIR` は引き続き必要。詳しくは [object-storage.md](object-storage.md) |
| `AZURE_STORAGE_CONTAINER` | なし | `azure_blob` のときのコンテナ名(必須)。コンテナは先に作っておく |
| `AZURE_STORAGE_CONNECTION_STRING` | なし | `azure_blob` の接続文字列(アカウントのキーを含む)。`AZURE_STORAGE_ACCOUNT_URL` とはどちらか一方 |
| `AZURE_STORAGE_ACCOUNT_URL` | なし | `azure_blob` のアカウントの URL(`https://<アカウント名>.blob.core.windows.net`)。認証は `DefaultAzureCredential`(マネージド ID、`az login` など) |
| `S3_BUCKET` | なし | `s3` のときのバケット名(必須)。バケットは先に作っておく。資格情報は `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` や IAM ロールなど、boto3 の標準の探し方に任せる |
| `S3_REGION` | なし | `s3` のリージョン(Cloudflare R2 は `auto`) |
| `S3_ENDPOINT_URL` | なし(AWS S3) | AWS 以外の S3 互換ストレージの接続先(例: `https://<アカウント ID>.r2.cloudflarestorage.com`) |
| `S3_FORCE_PATH_STYLE` | `false` | `true` でパス形式の URL で接続する(仮想ホスト形式を受けない互換ストレージ向け) |
| `OPENAI_MAX_RETRIES` | `4` | 429 などの再試行回数。再試行は OpenAI SDK が行う |
| `OPENAI_TIMEOUT_SECONDS` | `600` | 1リクエストのタイムアウト(秒)。4K や高品質の生成は数分かかる |
| `MODERATION` | `low` | Generate のときに送る表現の制限。`auto` または `low`。設定画面(設定 → OpenAI)で保存すると、そちらが優先される |
| `COMFYUI_URL` | なし(無効) | ローカルの ComfyUI の URL(例: `http://127.0.0.1:8188`)。通常は設定画面(設定 → ComfyUI)から接続する。ループバック以外を指定すると、起動時に警告を出す |
| `COMFYUI_TIMEOUT_SECONDS` | `1800` | ComfyUI の1回の実行を待つ上限(秒)。設定画面(設定 → ComfyUI)で保存すると、そちらが優先される |
| `AUTH_MODE` | なし(`none`) | `none`(個人モード。認証なし)か `oidc`(OIDC でログイン。ADR-0019)。通常は書かず、管理者設定の「認証」で切り替える(ADR-0034)。書くと画面の設定より優先し、画面では切り替えられなくなる。締め出されたときは `AUTH_MODE=none` を書いて再起動する。手順は [auth.md](auth.md) |
| `OIDC_ISSUER` | なし | OIDC 発行者の URL(例: `https://keycloak.example.com/realms/gakei`)。末尾に `/.well-known/openid-configuration` を付けた Discovery 文書を読む。oidc のとき必須。画面の設定が優先する |
| `OIDC_CLIENT_ID` | なし | IdP に登録したクライアント ID。oidc のとき必須。画面の設定が優先する |
| `OIDC_CLIENT_SECRET` | なし | クライアントシークレット。空なら public client(PKCE のみ)として扱う。`DATA_DIR/secrets.json` に画面で保存したものがあればそちらを使う。画面で接続を登録した後(または画面で有効にした後)は、この値は読まない |
| `OIDC_SCOPES` | `openid profile email` | 要求するスコープ(空白区切り)。画面の設定が優先する |
| `PUBLIC_BASE_URL` | なし | 利用者がブラウザで開く URL(例: `https://gakei.example.com`)。IdP からの戻り先は `{PUBLIC_BASE_URL}/api/auth/callback`。`https` ならセッション Cookie に `Secure` を付ける。oidc のとき必須。認証の設定では画面の値が優先する。共有リンク(`{PUBLIC_BASE_URL}/s/{トークン}`)と MCP の接続先の表示にも使う(無ければリクエストの URL から組み立てる。[sharing.md](sharing.md)) |
| `AUTH_ADMIN_EMAILS` | 空 | 管理者にするメールアドレス(カンマ区切り。大文字小文字は無視)。リクエストのたびに評価する(ログインし直す必要もセッションを消す必要もない。L-3、2026-09-27 追記)。画面の設定が優先する |
| `AUTH_ALLOWED_EMAIL_DOMAINS` | 空(制限なし) | ログインを許すメールアドレスのドメイン(カンマ区切り。例: `example.co.jp,example.com`)。Google のように誰でもアカウントを持てる IdP では必ず指定する。`AUTH_ADMIN_EMAILS` の人は常に許す。これもリクエストのたびに評価する(L-3、2026-09-27 追記)。画面の設定が優先する |
| `AUTH_SESSION_HOURS` | `720` | ログインしてからセッションが切れるまでの時間。1〜720(30日)の範囲(I-10、2026-09-27 追記)。既定は 30日(2026-10-01 改訂。以前は 12)。画面の設定が優先する |
| `AUTH_SECRET` | なし | ログイン手続き中の一時 Cookie の署名鍵。未指定なら起動時に生成して `DATA_DIR/secrets.json` に保存する。画面では扱わない |

認証の設定は、管理者設定の「認証」(`/settings/authentication`)でも行える(ADR-0034)。`AUTH_MODE` を除く認証の変数(`OIDC_*`、`PUBLIC_BASE_URL`、`AUTH_ADMIN_EMAILS`、`AUTH_ALLOWED_EMAIL_DOMAINS`、`AUTH_SESSION_HOURS`)は、画面で保存していない項目の初期値になる。画面から認証を有効にすると、`.env` から来ていた値は DB(シークレットは `secrets.json`)に書き写され、以後は `.env` から消しても動く。`AUTH_MODE` だけは `.env` が画面より優先する(緊急の無効化に使う。[auth.md](auth.md) の「締め出されたとき」)。

実効のモードが oidc で、発行者、クライアント ID、`PUBLIC_BASE_URL`(`OIDC_ISSUER`、`OIDC_CLIENT_ID`、`PUBLIC_BASE_URL`。画面の設定と `.env` を合わせて見る)のいずれかが無いと、起動を中止して足りない項目を表示する。モードが画面の設定から来ているときは、`.env` に `AUTH_MODE=none` を書いて再起動するよう案内する。`OIDC_ISSUER` と `PUBLIC_BASE_URL` の形式(`http`/`https` で、ホストを含む URL であること)も起動時に検査し、不正なら起動を中止する。ループバック(`127.0.0.1` など)以外への `http` は、起動は続けるが警告を出す(L-4、2026-09-27 追記)。

`DATA_DIR` に相対パスを書くと、サーバーの作業ディレクトリ(`backend/`)からの位置として解釈される。

`DATA_DIR/assets/` の原本は、プロバイダー・モデル別、月別のフォルダに保存される(例: `assets/openai/gpt-image-2.5/2026-09/20260929-093015_1a2b3c4d.png`。アップロードは `assets/uploads/`、マスクは `assets/masks/`、スケッチは `assets/sketches/`。ADR-0026)。以前のバージョンで保存した画像は `assets/{2文字}/` に残り、移さない。同じ内容の画像は、最初に保存した場所の 1 つのファイルを共有する。

設定画面からダウンロードしたモデルは `DATA_DIR/models/` に置く(自動タグの WD Tagger は `models/wd/`、画像の埋め込みの CLIP は `models/clip/`。[auto-tags.md](auto-tags.md)、[embeddings.md](embeddings.md))。`STORAGE_BACKEND` を変えても、モデルは `DATA_DIR` に置く。消しても設定画面からダウンロードし直せる。画像の埋め込みに環境変数は無く、管理者設定の「埋め込み」で設定する。

**`DATA_DIR` の中のファイルを、ファイルマネージャーなどで直接消したり動かしたり名前を変えたりしないこと。** DB に記録した場所と合わなくなり、画像が表示できなくなる。画像の削除は画面から行う。`DATA_DIR` をフォルダごとバックアップに取るのは構わない。

`STORAGE_BACKEND` を `azure_blob` か `s3` にすると、原本と派生は `DATA_DIR` ではなく、コンテナ(バケット)の中に同じキーで保存される。起動時に接続と読み書きを確かめ、できなければ起動を中止する。ローカルから移すツールもある([object-storage.md](object-storage.md))。

## Docker(ADR-0016、ADR-0021)

`compose.yaml` で起動する場合、コンテナ内の `HOST`/`PORT`/`DATA_DIR` は `0.0.0.0`/`8000`/`/data` に固定されており、`.env` で指定しても上書きされない。ホスト側に公開するアドレスとポートは、代わりに Compose 専用の変数で指定する。

`ghcr.io/zolgear/gakei` の公開イメージを使う場合(clone 不要)も同様で、`docker run -e` や `--env-file` で渡した環境変数がそのまま使える。手順は README の Docker の節を参照。

| 変数 | 既定 | 内容 |
|---|---|---|
| `GAKEI_BIND` | `127.0.0.1` | ホスト側の待ち受けアドレス |
| `GAKEI_PORT` | `8000` | ホスト側の待ち受けポート |
| `POSTGRES_PASSWORD` | なし(必須) | リポジトリの `compose.yaml` に同梱した PostgreSQL(ADR-0027)のパスワード。未指定だと `docker compose up` が起動しない。SQLite で使う場合の切り替え方は [postgresql.md](postgresql.md) |
