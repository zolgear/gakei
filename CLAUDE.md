# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 何を作っているか

GAKEI は、OpenAI の画像 API(Generate / Edit)をセルフホストで使うための Web ツール。生成・編集のフォーム、4K ビューア、マスクとスケッチ、実行履歴、系列(リネージ)、プロンプトセット、任意の OIDC 認証と管理者ロール、Docker イメージ(GHCR)を持つ。`backend/`(FastAPI + SQLAlchemy。メタデータは既定 SQLite、`DATABASE_URL` で PostgreSQL も選べる。ADR-0027、`docs/postgresql.md`。画像はローカル FS の `data/`)と `frontend/`(React + TypeScript + Vite の SPA。ビルド成果物を FastAPI が配信)から成る。名前は GAKEI(表示は大文字。パッケージ名や localStorage のキーなどの識別子は小文字の `gakei`)。

設計判断は `docs/adr/` にある。作業を始める前に `docs/adr/README.md`(一覧)を読み、作業に関連する ADR を読む。Azure での組織向けホスティング(ADR-0002〜0007)は設計だけで未着手。

## コマンド

利用者向けの起動(リポジトリ直下。ADR-0012):

```bash
./run.sh [--port P] [--data-dir DIR] [--no-browser]   # Windows は run.bat。uv を用意し、backend/app/launch.py がフロントを必要なときだけビルドしてから起動する
FAKE_PROVIDER=1 DATA_DIR=$(mktemp -d) PORT=8792 ./run.sh --no-browser   # 確認用(課金なし、実データに触れない)
```

バックエンド(`backend/`、uv 管理、Python 3.12):

```bash
uv run pytest -q                          # 全テスト(-n auto で並列)
uv run pytest -q tests/test_lineage.py    # 1ファイル
uv run pytest -q -k cancel                # 名前で絞る
uv run ruff check . && uv run ruff format --check .
uv run python -m app                      # 起動(.env の HOST / PORT / OPENAI_BASE_URL などを使う。既定は 127.0.0.1:8000)
FAKE_PROVIDER=1 DATA_DIR=$(mktemp -d) uv run python -m app   # 課金なしで起動(ダミー画像)
uv run python -m app.tools.export_openapi <出力パス>        # サーバーを立てずに OpenAPI を書き出す
uv run python -m app.tools.third_party_notices <出力パス|->  # 第三者ライセンス表記を書き出す(ADR-0021)
uv run python -m app.tools.backfill_embedded_meta --dry-run   # 既存の upload Asset の埋め込み生成メタ情報を埋め戻す(ADR-0018。サーバー停止中に)
uv run python -m app.tools.migrate_to_postgres --to postgresql://... --dry-run   # SQLite → PostgreSQL 移行(ADR-0027。docs/postgresql.md)
GAKEI_TEST_DATABASE_URL=postgresql://... uv run pytest -q   # 同じテストを PostgreSQL でも回す(既定は SQLite のまま)
```

フロントエンド(`frontend/`、Node 24):

```bash
npm run dev       # 開発サーバー。/api は 127.0.0.1:8000 にプロキシ
npm run build     # tsc -b && vite build → dist/(最後に dist/third-party-notices.txt も作る)
npm run lint      # oxlint
npm test          # vitest
npm run gen:api   # バックエンドの OpenAPI から src/api/schema.d.ts を再生成(API を変えたら必ず実行)
```

Docker(ADR-0016、ADR-0021、ADR-0027):

```bash
POSTGRES_PASSWORD=x docker compose up -d --build          # clone してビルドする人向け(http://127.0.0.1:8000)。PostgreSQL を同梱、POSTGRES_PASSWORD が必須(docs/postgresql.md)
docker build -t gakei:test . && docker run --rm -e FAKE_PROVIDER=1 -p 127.0.0.1:8792:8000 gakei:test   # 確認用(課金なし。.env を読まず、ボリュームも作らない。SQLite のまま)
```

公開イメージは `ghcr.io/zolgear/gakei`。リリースは `docs/release.md`(バージョンの正は `backend/pyproject.toml` の `version`。`GET /api/about` と設定画面の「GAKEI について」で確認できる)。

### 実行時の注意

- リポジトリ直下の `.env` に本物の `OPENAI_API_KEY` がある。確認用にサーバーを起動するときは `FAKE_PROVIDER=1` と一時ディレクトリの `DATA_DIR` を必ず明示する。`DATA_DIR` を省くとリポジトリ直下の `data/`(ユーザーの実データ)に書き込む。
- 自動テストは実 API を呼ばない。`backend/tests/conftest.py` が `.env` と `frontend/dist` をテストから切り離しているので、この2つの autouse フィクスチャを消さない。
- バックエンドは起動時に Alembic の `upgrade head` を自動実行する。書きかけのマイグレーションがある状態で、実データに対して起動しない。
- 起動したプロセスは PID かポートを指定して止める。`pkill -f "python -m app"` のような広いパターンは、ユーザーが使用中のサーバーを巻き込む。
- バックエンドは `frontend/dist` をリクエスト時に読む。`npm run build` が成功すれば、サーバーを再起動しなくても新しい画面になる。Vite の出力先は `static/`(既定の `assets/` は SPA のルート `/assets/:id` と衝突する)。
- 既定は個人モード(`AUTH_MODE=none`、認証なし)で、待ち受けの既定は `127.0.0.1`。LAN に出すのはユーザーが明示したときだけ。`AUTH_MODE=oidc` にすると OIDC でログインが要る(ADR-0019、`docs/auth.md`)。oidc モードで起動するには `OIDC_ISSUER`・`OIDC_CLIENT_ID`・`PUBLIC_BASE_URL` が要る(欠けると起動を中止する)。

## 構成

- `backend/app/api/`: ルーター(capabilities、assets と lineage、runs、events(SSE)、prompt_sets、asset_groups(グループ。ADR-0022)、search、pricing、settings、comfyui、auth、users、about)。`/api/auth/*` 以外の全ルーターに `require_user` が掛かり、管理者設定の更新系だけ `require_admin`。
- `backend/app/domain/`: モデル、スキーマ、サイズ検証、`AssetStore`(ローカル FS)、派生画像、`ingest`、Run の検証、系列グラフの探索、`embedded_meta.py`(ダウンロード PNG への系列情報の埋め込み。ADR-0014)、`generation_meta.py`(他ツールが埋め込んだ生成メタ情報の読み取り。ADR-0018)、`avatars.py`(ADR-0020)、`third_party.py`(ADR-0021)、`asset_groups.py`(グループ。証跡ではないので更新・論理削除できる。ADR-0022)。
- `backend/app/providers/`: `ImageProvider`、`registry.py`(登録簿)、`openai_images.py`、`fake.py`、`comfyui/`(ローカル ComfyUI。ADR-0013。利用者向けには実験的)。パラメーターの定義は `openai_spec.py` に集約し、フォームはここから組み立てる。主プロバイダーは常に `openai`(`FAKE_PROVIDER=1` のときだけ `fake`。利用者向けの設定一覧には載せない)。接続先は `OPENAI_BASE_URL` または設定画面で変えられる(ADR-0017)。
- `backend/app/mcp/`(ADR-0023): MCP サーバー。`/mcp`(Streamable HTTP、stateless)を FastAPI に同居させる。既定は無効で、管理者設定で有効にする。ツールは REST を呼ばず、REST と同じドメイン関数(Run 作成は `domain/run_create.py`)を直接呼ぶ。認証モードは `api_token`(ユーザー設定で発行するアクセストークン)の Bearer だけを受ける。MCP で作った Run は `run.origin = 'mcp'`。削除と設定変更のツールは作らない。
- `backend/app/worker/`: api プロセス内で動く runner(`run` 行がキュー)と、SSE 用のプロセス内 pub/sub。
- `backend/app/auth/`(ADR-0019): `identity.py`(`CurrentUser`。none モードは暗黙の管理者)、`sessions.py`(`app_user` / `auth_session`)、`oidc.py`(Authlib のラッパー。テストは `get_oidc_client` を差し替える)、`deps.py`(`require_user` / `require_admin`)。Run と Asset に `created_by_user_id` を記録する。
- タイトルとタグ(ADR-0024、`docs/auto-tags.md`): `domain/annotations.py`(人の編集を優先する規則)、`domain/annotation_settings.py`、`backend/app/annotation/`(LLM・VLM・WD Tagger の ONNX エンジンとモデルのダウンロード)、`worker/annotator.py`(`asset_annotation.auto_status='queued'` を待ち行列にするプロセス内 worker)。`FAKE_PROVIDER=1` ではダミーのエンジンになる。
- `frontend/src/shell/`: App バー、アイコンレール、サイドバー、ユーザーメニュー。`features/` に run-form、run-status、run-detail、history、search、viewer、compare、mask、sketch、stock、prompt-sets、lineage、comfyui-workflows、crop、auth、settings、workspace、favicon。設定画面は「ユーザー設定」と「管理者設定」(OpenAI、生成、ComfyUI。管理者にだけ出す)に分かれる。
- 文言は日本語と英語(ADR-0015)。コードに直接書かず、言語ごとに1つの JSON に置く: フロントは `frontend/src/i18n/locales/{ja,en}.json`、サーバーは `backend/app/locales/{ja,en}.json`。日本語が正。フロントは `useI18n()` の `t`(React の外は `msg()`)と `fmt()`、サーバーは `t("key", count=...)`(ランチャーは `console_t`)。キーの欠けはテスト(フロントは `tsc` も)で落ちる。コード中のコメントは日本語。ただし `run.sh` / `run.bat` のコメントは英語(`run.bat` は ASCII 限定)。
- 画面のデザインの基準は ADR-0009。狭い幅(768px 未満)でも一通り操作できること。
- 第三者ライセンス表記(ADR-0021): `frontend/scripts/third-party-notices.mjs` が `npm run build` の最後に `frontend/dist/third-party-notices.txt` を作り、`backend/app/domain/third_party.py` が Python 側の依存と連結して `GET /api/about/third-party-notices` で出す。

## 設計の要点(詳細は各 ADR)

- **データモデル(ADR-0003):** 画像の `asset` と 1 回の API 実行の `run` の二部グラフ(`asset → run_input → run → asset`)。Edit は複数入力なので DAG。`run`、`run_input`、`asset` の来歴列は追記のみで、更新してよいのは `run.status` などの状態遷移だけ。再実行は必ず新しい Run。削除は `deleted_at` の論理削除。`run.params` には API に送った値をそのまま保存する。系列の表示は `position = 0` の主たる親だけを辿る。 グループ(`asset_group`、ADR-0022)は利用者の分類で証跡ではないので、名前もメンバーも自由に変えられる。
- **ジョブ(ADR-0005):** キューは `run` テーブルそのもの。`queued → running → succeeded | failed | canceled`。失敗も証跡として残す。進捗は SSE。途中経過画像は Asset にしない。
- **プロバイダー(ADR-0005、0013):** `ImageProvider`(`capabilities()` と `execute()`)だけを共通にし、パラメーターは共通化せず UI は `capabilities()` からフォームを組み立てる。
- **画像(ADR-0004):** base64 をブラウザにも DB にも渡さない。原本は sha256 で保存して不変、派生(thumb 512px / preview 2048px の WebP)を作り、配信は API 経由(`GET /api/assets/{id}/content?variant=`)。ビューアは `<img>` + パン/ズーム。ローカルFSの原本のキーは ADR-0026 で `assets/{プロバイダー}/{モデル}/{YYYY-MM}/{日時}_{短ID}.{拡張子}`(アップロード等は `assets/uploads/` など)に改めた。同じ内容は `ingest` が sha256 で DB を引いて既存のファイルを共有し、古いキー(`assets/{2文字}/{sha256}`)の Asset もそのまま読む。
- **認証(ADR-0019):** 既定は認証なし。OIDC は BFF 方式(サーバー側で Authorization Code + PKCE、サーバー側セッション + HttpOnly Cookie)。ロールは `user` / `admin` で、管理者は `AUTH_ADMIN_EMAILS`。

## 作業ルール

- **ブランチ:** 既定ブランチは `main`(利用者が clone するとリリース済みの状態が落ちてくるように)。開発は `dev` で行う。作業ブランチは `origin/dev` から切り、PR は必ず `--base dev` で `dev` に出す(GitHub の PR 作成画面の既定は `main` になるので注意)。`main` はリリース済みの状態だけを指し、`dev` → `main` の PR でしか更新しない(ADR-0021、`docs/release.md`)。`main` と `dev` は PR 経由のみ・CI 必須で、直接 push はできない。
- **迷ったら「作らない」。** 非ゴールは ADR-0001 に列挙されている。追加したい場合は、実装より先に ADR の更新を提案する。
- ADR と矛盾する実装をしない。変えたい場合も ADR の更新が先。ADR の Status は各ファイルと `docs/adr/README.md` で確認する。
- API 仕様(モデル名、サイズ制約、Edit の入力上限、レート制限など)は変わりやすい。実装時に OpenAI の公式ドキュメントで再確認する。

## オーケストレーション(モデルの使い分け)

メインセッションは計画と統括に徹し、実装はサブエージェントに任せる。

| 作業 | モデル | 実行場所 |
|---|---|---|
| 計画、設計判断、ADR の起草、成果物のレビュー | ユーザーが選択したモデル(Fable または Opus) | メインセッション |
| 実装(コード、テスト、マイグレーション、設定ファイル、画面の修正) | Opus | サブエージェント(Agent ツールの `model: "opus"`) |
| コードの読み込み、検索、ファイル一覧などの単純なタスク | Sonnet | サブエージェント(`model: "sonnet"`。探索は `Explore` を使う) |

- メインセッションのモデルを勝手に切り替えない。計画に使うモデルはユーザーの選択に従う。
- 実装を Sonnet に下げない(2026-09-27 に、Sonnet の実装は画面の細部で手戻りが出たため Opus に固定した)。単純なタスクを Haiku に下げるのも、ユーザーの指定があるときだけ。
- サブエージェントは会話の経緯を知らない。依頼文には、対象ステップ、関連する ADR と計画ファイルの該当箇所、作成・変更するファイル、完了条件(通すべきテストや lint)を書く。
- 互いに依存しない作業(例: backend と frontend)は、1つのメッセージで複数のサブエージェントを同時に起動する。
- サブエージェントの報告はそのまま信用せず、メインセッションで差分を確認し、テストと lint を実行してから完了とする。
- 1ファイルの確認や数行の修正など、依頼文を書くより早く終わる作業はメインセッションで直接行ってよい。
