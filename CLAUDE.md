# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 現状

ローカルMVP(ADR-0008、ADR-0009、ADR-0010)が `backend/` と `frontend/` にある。OpenAI API のキーだけで動き、画像はローカルFS(`data/`)、メタデータは SQLite に保存する。本線(Azure OpenAI、Entra ID、Blob、PostgreSQL、別プロセス worker)は未着手で、下の「アーキテクチャ」は本線の設計を指す。ローカルMVPが本線と違う点は ADR-0008 の対比表にまとめてある。

作業を始める前に ADR を全部読む。ユーザーへの回答は日本語で行う。

### ADR の置き場所

- ADR は `docs/adr/` にあり、一覧は `docs/adr/README.md`。ファイル名の番号と中身の `# ADR-000N` は一致している(2026-09-27 に修正)。
- ADR-0007(ホスティング)は Azure と決めたこと以外の本文が未作成。

## コマンド

利用者向けの起動(リポジトリ直下。ADR-0012):

```bash
./run.sh [--port P] [--data-dir DIR] [--no-browser]   # Windows は run.bat。uv を用意し、backend/app/launch.py がフロントを必要なときだけビルドしてから起動する
FAKE_PROVIDER=1 DATA_DIR=$(mktemp -d) PORT=8792 ./run.sh --no-browser   # 確認用(課金なし、実データに触れない)
```

バックエンド(`backend/`、uv 管理、Python 3.12):

```bash
uv run pytest -q                          # 全テスト
uv run pytest -q tests/test_lineage.py    # 1ファイル
uv run pytest -q -k cancel                # 名前で絞る
uv run ruff check . && uv run ruff format --check .
uv run python -m app                      # 起動(.env の HOST / PORT / OPENAI_BASE_URL などを使う。既定は 127.0.0.1:8000)
FAKE_PROVIDER=1 DATA_DIR=$(mktemp -d) uv run python -m app   # 課金なしで起動(ダミー画像)
uv run python -m app.tools.export_openapi <出力パス>        # サーバーを立てずに OpenAPI を書き出す
uv run python -m app.tools.backfill_embedded_meta --dry-run   # 既存の upload Asset の埋め込み生成メタ情報を埋め戻す(ADR-0018。--dry-run を外すと書き込む。サーバー停止中に)
```

フロントエンド(`frontend/`、Node 24、Vite + React + TS):

```bash
npm run dev       # 開発サーバー。/api は 127.0.0.1:8000 にプロキシ
npm run build     # tsc -b && vite build → dist/(バックエンドが配信する)
npm run lint      # oxlint
npm test          # vitest
npm run gen:api   # バックエンドの OpenAPI から src/api/schema.d.ts を再生成(API を変えたら必ず実行)
```

Docker(サーバーに置く人向け。ADR-0016。個人の PC では `run.sh`/`run.bat` を使う):

```bash
docker compose up -d --build                              # ビルドして起動(http://127.0.0.1:8000)
docker build -t gakei:test . && docker run --rm -e FAKE_PROVIDER=1 -p 127.0.0.1:8792:8000 gakei:test   # 確認用(課金なし。.env を読まず、ボリュームも作らない)
docker compose logs -f                                     # ログ
docker compose down                                         # 停止(ボリュームは残る)
```

リリース(タグ push で GHCR にイメージを公開する。ADR-0021): 手順は [docs/release.md](docs/release.md)。バージョンの正は `backend/pyproject.toml` の `version` の1か所で、`GET /api/about` で確認できる(画面では設定 →「GAKEI について」)。

### 実行時の注意

- リポジトリ直下の `.env` に本物の `OPENAI_API_KEY` がある。確認用にサーバーを起動するときは `FAKE_PROVIDER=1` と一時ディレクトリの `DATA_DIR` を必ず明示する。`DATA_DIR` を省くとリポジトリ直下の `data/`(ユーザーの実データ)に書き込む。
- 自動テストは実 API を呼ばない。`backend/tests/conftest.py` が `.env` と `frontend/dist` をテストから切り離しているので、この2つの autouse フィクスチャを消さない。
- バックエンドは起動時に Alembic の `upgrade head` を自動実行する。書きかけのマイグレーションがある状態で、実データに対して起動しない。
- 起動したプロセスは PID かポートを指定して止める。`pkill -f "python -m app"` のような広いパターンは、ユーザーが使用中のサーバーを巻き込む。
- バックエンドは `frontend/dist` をリクエスト時に読む。`npm run build` が成功すれば、サーバーを再起動しなくても新しい画面になる。Vite の出力先は `static/`(既定の `assets/` は SPA のルート `/assets/:id` と衝突する)。
- 既定は個人モード(`AUTH_MODE=none`、認証なし)で、待ち受けの既定は `127.0.0.1`。LAN に出すのはユーザーが明示したときだけ。`AUTH_MODE=oidc` にすると OIDC(Google、Keycloak など)でログインが要る(ADR-0019、`docs/auth.md`)。Google のような公開 IdP では `AUTH_ALLOWED_EMAIL_DOMAINS` で範囲を絞る。確認用に oidc モードで起動するときは `OIDC_ISSUER`・`OIDC_CLIENT_ID`・`PUBLIC_BASE_URL` が要る(欠けると起動を中止する)。

### ローカルMVPの構成

- `backend/app/api/`: ルーター(capabilities、assets と lineage、runs、events(SSE)、prompt_sets、`about.py` はバージョンと第三者ライセンス表記(ADR-0021))。
- `backend/app/domain/`: モデル、スキーマ、サイズ検証、`AssetStore`(ローカルFS)、派生画像、`ingest`、Run の検証、系列グラフの探索、`third_party.py`(`importlib.metadata` から第三者ライセンス表記を集める。ADR-0021)。
- 系列情報の埋め込み(ADR-0014): `download=1` の PNG 原本の応答にだけ iTXt チャンク `gakei` を加える(保存している原本は変えない)。`domain/embedded_meta.py` が読み書きし、アップロード時に同じインスタンスの Asset と一致すれば既存の Asset を返す。
- 他のツール(A1111 / Forge、ComfyUI、NovelAI、InvokeAI、SwarmUI)や C2PA が埋め込んだ生成メタ情報(ADR-0018): `domain/generation_meta.py` が `upload` の取り込み時に読み、追記のみの列 `asset.embedded_meta`(`gakei.embedded/1`)に保存する。自己申告なので画面では常に「未検証」。C2PA は署名を検証せず claim generator を表示するだけ。
- `backend/app/providers/`: `ImageProvider`、`registry.py`(複数プロバイダーの登録簿)と、`openai_images.py`、`fake.py`、`comfyui/`(ローカル ComfyUI。ADR-0013。利用者向けには実験的な機能)。パラメーターの定義は `openai_spec.py` に集約し、フォームはここから組み立てられる。実機で使えなかった項目(`input_fidelity`)は、理由をコメントに残して外してある。主プロバイダーは常に `openai`(`FAKE_PROVIDER=1` のときだけ `fake`。開発・CI・確認用で、利用者向けの設定一覧には載せない)。OpenAI の接続先は `OPENAI_BASE_URL`(または設定画面)で LiteLLM などの互換プロキシに変えられる(ADR-0017)。
- `backend/app/worker/`: api プロセス内で動く runner(`run` 行がキュー)と、SSE 用のプロセス内 pub/sub。
- `backend/app/auth/`(ADR-0019): `identity.py`(`CurrentUser`。none モードは暗黙の管理者)、`sessions.py`(`app_user` / `auth_session`)、`oidc.py`(Authlib のラッパー `OidcClient`。テストは `get_oidc_client` を差し替える)、`deps.py`(`require_user` / `require_admin`)。ルーターは `api/auth.py`。アバター(ADR-0020)は `domain/avatars.py` と `api/users.py`(`DATA_DIR/avatars/{user_id}.webp`。Asset にはしない)。`/api/auth/*` 以外の全ルーターに `require_user` が掛かり、管理者設定の更新系だけ `require_admin`。Run と Asset に `created_by_user_id` を記録する。
- `frontend/src/shell/`: App バー、アイコンレール、サイドバー、ユーザーメニュー。`features/` に run-form、run-status、history、viewer、mask、stock、prompt-sets、lineage、comfyui-workflows、auth(`authState` の外部ストア、`AuthGate`、ログイン画面。`/api/auth/me` で個人モードか、ログイン済みか、管理者かを決める)。設定画面は「ユーザー設定」(言語、表示)と「管理者設定」(OpenAI、生成、ComfyUI)に分かれ、後者は管理者にだけ出す。
- 画面の文言は日本語と英語(ADR-0015)。文言はコードに直接書かず、言語ごとに1つの JSON に置く。フロントは `frontend/src/i18n/locales/{ja,en}.json`、サーバーは `backend/app/locales/{ja,en}.json`。日本語が正で、差し込みは `{count}`、単数・複数は `{"one", "other"}`。フロントは `useI18n()` の `t`(React の外は `msg()`)で引き、差し込みは `fmt()`。サーバーは `t("key", count=...)`(ランチャーは `console_t`)で、`Accept-Language` で切り替わる。キーの欠けや置き場所の不一致はテスト(フロントは `tsc` も)で落ちる。コード中のコメントは日本語のまま。ただし起動スクリプト `run.sh` / `run.bat` のコメントは英語で書く(`run.bat` は ASCII 限定。ADR-0015 6章)。
- 画面のデザインの基準は ADR-0009。狭い幅(768px 未満)でも一通り操作できること(最低限のモバイル対応)。
- サーバーに置きたい人向けに Docker 一式(ADR-0016): リポジトリ直下の `Dockerfile`(多段ビルド、`python -m app` で起動)、`compose.yaml`(単一サービス、名前付きボリューム `gakei-data`)、`.dockerignore`。個人の PC 向けの主な導入方法(ADR-0012 の起動スクリプト)を置き換えるものではない。
- 第三者ライセンス表記(ADR-0021): `frontend/scripts/third-party-notices.mjs` が `npm run build` の最後に `frontend/dist/third-party-notices.txt` を作り、`backend/app/domain/third_party.py` がそれに Python 側の依存を連結する。配布物には `python -m app.tools.third_party_notices` で書き出す。

## 何を作るか

Azure OpenAI の gpt-image-2.5 系 API(特に Edit)を組織で使うための、セルフホスト・日本国内・単一組織向けの Web ツール。生成/編集UI、4K画像の閲覧、世代管理(リネージ)、証跡を一体で提供する。プロジェクト名は GAKEI(表示名は大文字。パッケージ名や localStorage のキーなどの識別子は小文字の `gakei`)。

## 作業ルール

- **ブランチ:** 既定ブランチは `dev`。作業ブランチは `origin/dev` から切り、PR は `dev` に出す。`main` はリリース済みの状態だけを指し、`dev` → `main` の PR でしか更新しない(ADR-0021、`docs/release.md`)。`main` と `dev` は PR 経由のみ・CI 必須で、直接 push はできない。
- **迷ったら「作らない」。** 非ゴールは ADR-0001 に列挙されている(ノードエディタ、マルチテナント、承認ワークフロー、高機能な画像編集、C2PA/IPTC、類似検索、汎用DAM、モバイル最適化、他プロバイダー実装など。ただしローカルの ComfyUI は ADR-0013 で対応した)。追加したい場合は、実装より先に ADR の更新を提案する。
- ADR と矛盾する実装をしない。変えたい場合も ADR の更新が先。
- ADR-0001 だけが Accepted。0002〜0021 は Proposed(Claude の提案)で、ユーザーのレビュー待ち。ADR-0001 は 2026-09-21 に改訂した(ゴールにプロンプトセットを追加、モバイルは最低限だけ対応。経緯は ADR-0009)。ただし Python/FastAPI と Azure でのホスティングは決定済み。
- API 仕様(モデル名、サイズ制約、Edit の入力上限、レート制限など)は変わりやすい。実装時に Microsoft Learn / OpenAI の公式ドキュメントで再確認する。
- 着手前の未解決事項は、gpt-image-2.5 の日本リージョン可否、閲覧範囲、保存期間、想定利用量。

## オーケストレーション(モデルの使い分け)

メインセッションは計画と統括に徹し、実装はサブエージェントに任せる。

| 作業 | モデル | 実行場所 |
|---|---|---|
| 計画、設計判断、ADR の起草、成果物のレビュー | ユーザーが選択したモデル(Fable または Opus) | メインセッション |
| 実装(コード、テスト、マイグレーション、設定ファイル) | Sonnet。ユーザーが指定した場合は Opus | サブエージェント(Agent ツールの `model: "sonnet"` / `"opus"`) |
| コードの読み込み、検索、ファイル一覧などの単純なタスク | Haiku | サブエージェント(`model: "haiku"`。探索は `Explore` を使う) |

- メインセッションのモデルを勝手に切り替えない。計画に使うモデルはユーザーの選択に従う。
- 実装を Opus に上げるのはユーザーの指定があるときだけ。難しそうだという理由で自分の判断では上げない。
- サブエージェントは会話の経緯を知らない。依頼文には、対象ステップ、関連する ADR と計画ファイルの該当箇所、作成・変更するファイル、完了条件(通すべきテストや lint)を書く。
- 互いに依存しない作業(例: backend と frontend)は、1つのメッセージで複数のサブエージェントを同時に起動する。
- サブエージェントの報告はそのまま信用せず、メインセッションで差分を確認し、テストと lint を実行してから完了とする。
- 1ファイルの確認や数行の修正など、依頼文を書くより早く終わる作業はメインセッションで直接行ってよい。

## アーキテクチャ(ADR の要約)

**構成:** Python 3.12+ / FastAPI / SQLAlchemy 2 + Alembic / OpenAI Python SDK / Pillow。フロントは React + TypeScript + Vite の SPA で、ビルド成果物を FastAPI が静的配信する。コンテナイメージは1種類で、起動コマンドで api と worker を切り替える。API は REST + OpenAPI で、TypeScript の型は OpenAPI から自動生成する。想定ディレクトリは `backend/app/{api,domain,providers,worker}`、`backend/migrations`、`frontend/`、`infra/`(Bicep)。

**PostgreSQL に全部載せる:** メタデータ、世代グラフ、ジョブキュー、監査イベントを1つの DB で持つ。Redis や Service Bus などの部品は増やさない。

**データモデル(ADR-0003):** 画像を表す `asset` と、1回の API 実行を表す `run` の二部グラフ。`asset → run_input → run → asset(produced_by_run_id)`。Edit は最大16枚の入力を取るので世代は木ではなく DAG になる。
- `run`、`run_input`、`asset` の来歴列は追記のみ。UPDATE してよいのは `run.status` など実行状態の遷移だけ。
- 「再実行」「パラメータを変えて実行」は既存 Run の更新ではなく、必ず新しい Run を作る。
- 削除は `deleted_at` による論理削除のみ。
- `run.params`(JSONB)には API に送った値をそのまま保存し、プロバイダー固有の項目を列にしない。
- 世代ツリーの表示は `run_input.position = 0` かつ role `image` の「主たる親」だけを辿る。他の入力は参照として別表示する。
- 採用/不採用(`asset_mark`)とタグは証跡ではないので更新可。

**ジョブ実行(ADR-0005):** キューは `run` テーブルそのもの。worker が `status = 'queued'` の行を `SELECT ... FOR UPDATE SKIP LOCKED` で取得する。状態遷移は `queued → running → succeeded | failed | canceled`。失敗も証跡として残し、コンテンツフィルター拒否は `failed` + `error_code = contentFilter`。レート制限はデプロイメント単位のトークンバケットを worker 側で守り、429 は指数バックオフで再試行する。起動時に長時間 `running` の行を `queued` に戻す。進捗は worker が `LISTEN/NOTIFY` で流し、api が SSE でブラウザに届ける。途中経過画像は Asset にしない。

**プロバイダー抽象化(ADR-0005):** `ImageProvider` プロトコル(`capabilities()` と `execute()`)を1つだけ定義し、Phase 1 では Azure OpenAI アダプターのみ実装する。パラメータは共通化せず、UI のフォームは `capabilities()` から組み立てる。

**画像の保存と配信(ADR-0004):** 動機そのものが「base64 を本文に載せると4Kが表示できない」問題なので、base64 をブラウザに渡さず、DB にも入れない。バックエンドでデコードして Azure Blob Storage に保存する。キーは `assets/{sha256先頭2文字}/{sha256}.{拡張子}` で、同一内容は共有される。原本は不変で、worker が派生(サムネイル 512px WebP、プレビュー 2048px WebP)を作る。配信は認可チェックを通す API 経由のストリーミング(`GET /api/assets/{id}/content?variant=thumb|preview|original`)で、URL 発行はサーバー側の1関数に閉じ込める。ビューアは `<img>` + パン/ズームで、タイル分割はしない。

**認証と証跡(ADR-0006):** Entra ID の SSO を基盤の組み込み認証(Easy Auth)で前段に置き、API は `X-MS-CLIENT-PRINCIPAL` 等のヘッダーからユーザーを得る。ロールは `user` と `admin` のみ。ローカル開発は環境変数で有効化する固定ユーザーを使い、本番で無効であることを起動時に検査する。Azure OpenAI、Blob、PostgreSQL へはマネージド ID で接続し、APIキーや接続文字列を持たない。証跡は Run の記録が正本で、Run に現れない操作(アップロード、原本ダウンロード、削除、メンバー追加など)を `audit_event` に記録する。`run_input` と `audit_event` はアプリの DB ロールに INSERT と SELECT だけを許可する。

## 最初の実装順

縦に薄い一本を先に通す: ログイン → Generate 1枚 → Blob 保存 → DB に Run/Asset 記録 → ビューアで4K表示。その次に Edit(入力画像アップロード+マスク)を足す。
