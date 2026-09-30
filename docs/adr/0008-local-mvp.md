# ADR-0008: ローカルMVP(OpenAI API 直 / ローカルFS + SQLite)

**Status:** Proposed
**Date:** 2026-09-21

## Context

本線(ADR-0002〜0007)は Azure OpenAI、Entra ID、Blob Storage、PostgreSQL、別プロセスの worker を前提にしている。これらを揃えてからでないと1枚も生成できない構成は、UI と API の挙動を確かめるには重い。未解決事項(日本リージョンでの提供可否など)も残っている。

そこで、OpenAI API のトークンだけで動くローカルMVPを先に作る。目的は、gpt-image-2.5 の全パラメーターを指定した Generate、4Kプレビュー、結果を Edit に流用する流れ(マスク描画を含む)を、実際に触れる形で早く通すこと。

このMVPは使い捨てではなく、本線の土台にする。

## Decision

ADR-0003 のデータモデル(`asset` / `run` / `run_input`)と ADR-0005 の `ImageProvider` は最初から使う。保存先、認証、ジョブ実行の仕組みだけを簡略化し、差し替え点をインターフェースに閉じ込める。

| 項目 | 本線(ADR) | ローカルMVP | 本線への戻し方 |
|---|---|---|---|
| プロバイダー | Azure OpenAI + マネージドID(ADR-0005, 0006) | OpenAI API + APIキー(`.env` または画面の設定。サーバー側のみ。ADR-0012) | `ImageProvider` の実装を追加する。クライアント生成以外は共有する |
| 画像保存 | Azure Blob(ADR-0004) | ローカルFS。キー規則 `assets/{sha256先頭2文字}/{sha256}.{拡張子}` は同じ(2026-09-30 注記: ADR-0026 でキー規則を改め、ADR-0028 で `STORAGE_BACKEND` により Azure Blob Storage / S3 互換ストレージも選べるようになった。未指定の既定は引き続きローカルFS) | `AssetStore` の実装を差し替える |
| DB | PostgreSQL / JSONB(ADR-0002) | SQLite(WAL)。SQLAlchemy の `JSON` / `Uuid` 型で両対応にする(2026-09-29 注記: ADR-0027 で `DATABASE_URL` を指定すると PostgreSQL も選べるようになった。未指定の既定は引き続き SQLite) | 接続URLを変え、Alembic を PostgreSQL で流し直す |
| ジョブ実行 | 別プロセス worker + `SKIP LOCKED`(ADR-0005) | api プロセス内の asyncio タスク。`run` 行がキューである点は同じ | runner を別コマンドに切り出す |
| 進捗通知 | `LISTEN/NOTIFY` → SSE | プロセス内 pub/sub → SSE。ブラウザから見た I/F は同じ | pub/sub の実装を差し替える |
| 中断した `running` | 起動時に `queued` へ戻す | 起動時に `failed` + `error_code = interrupted` にする | 設定で切り替える |
| レート制限 | デプロイメント単位のトークンバケット | 作らない。429 は SDK の再試行に任せる | worker 側に追加する |
| 認証と証跡 | Easy Auth、`audit_event`、DBロール制限(ADR-0006) | なし。既定で `127.0.0.1` のみ待ち受ける | ADR-0006 を実装する |

MVP で作らないもの: プロジェクト、`created_by`、採用/不採用(`asset_mark`)、タグ、監査イベント。いずれも後から追加できる(列とテーブルの追加で済む)。

### 削除(2026-09-22 追加)

ADR-0003 のルール2(`deleted_at` による論理削除のみ)に従い、次の範囲で削除を提供する。

- **Asset の削除:** `asset.deleted_at` を設定する。ストックと一覧から消え、新しい Run の入力に使えなくなる。原本と派生ファイルは消さない(他の Run の来歴に現れるため)。Run 詳細と系列グラフからは「削除済み」として引き続き参照できる。
- **Run の削除:** `run.deleted_at` を追加して設定する。履歴から消える。その Run の出力 Asset も同時に論理削除する(「生成履歴を削除する」の期待に合わせる)。`run` / `run_input` の他の列は変更しない。終了状態(succeeded / failed / canceled)の Run だけ削除できる。実行中・待機中は先にキャンセルする。
  - **入口は履歴一覧だけ(2026-09-23 追加):** Generated 詳細(ページ、系列グラフのインスペクター)からは削除できない。画像が見えていない状態で消すのは勘違いしやすく、系列グラフの途中を消す操作はこのアプリの主旨(来歴を残す)に反する。出力が他の Run の入力に使われている Run も削除できるが、確認ダイアログでその件数を警告する。
- **Asset の復元(2026-09-22 追加):** 論理削除した Asset は `deleted_at` を外して復元できる。ただし、それを生んだ Run が削除済みの場合は復元できない(Run の削除は出力をまとめて消す操作なので、整合を保つ)。Run の復元は作らない。
- 物理削除と監査イベントへの記録は本線(ADR-0006)で扱う。

MVP でも守るもの:
- base64 をブラウザに渡さず、DBにも入れない(ADR-0004)。
- 原本は不変。派生(サムネイル 512px、プレビュー 2048px の WebP)を別に作る(ADR-0004)。
- 配信は `GET /api/assets/{id}/content?variant=thumb|preview|original` の1経路(ADR-0004)。
- Run は追記のみ。再実行とパラメータ変更は新しい Run(ADR-0003)。
- `run.params` には API に送った値をそのまま保存する(ADR-0003)。
- 失敗した Run も残す。コンテンツ拒否は `failed` + `error_code = contentFilter`(ADR-0005)。
- 途中経過画像は Asset にしない(ADR-0005)。

## Options Considered

| 案 | 評価 |
|---|---|
| A: FastAPI + ローカルFS + SQLite(採用) | 起動に必要なものが `.env` のAPIキーだけ。コードの大半を本線に持ち越せる |
| B: ブラウザのみ(IndexedDB、OpenAI を直接呼ぶ) | 最も軽いが、APIキーがブラウザに載る。本線(FastAPI)へほぼ何も引き継げない |
| C: FastAPI + ローカルFS + PostgreSQL(Docker) | DB の移行は最も楽だが、MVP の起動手順と部品が増える |
| D: 使い捨ての試作(データモデルなし) | 早いが、リネージの記録という中心部分を検証できない |

## Trade-off Analysis

SQLite と PostgreSQL の差(`JSONB`、`SKIP LOCKED`、`LISTEN/NOTIFY`)は、MVP では単一プロセス・単一ユーザーなので必要にならない。差が出る箇所は `db.py`、`worker/`、`domain/storage.py` に閉じ、ドメインと API の層には漏らさない。

中断した `running` を `queued` に戻さないのは、開発中は `--reload` で頻繁に再起動するためである。自動で再実行されると、気付かないうちに課金される。再実行は UI から新しい Run として行えば足り、ADR-0003 のルールとも整合する。

認証がない点は、待ち受けを既定で `127.0.0.1` に限ることで補う。別の端末から使う場合は SSH ポートフォワードを使うか、`HOST` を明示的に変える。

## Consequences

- APIキーを `.env` に置くだけで、Generate から Edit までを試せる。
- 証跡の要件(ADR-0006)は満たさない。本番環境や共有環境での利用は本線の実装後とする。
- MVP のデータ(SQLite と `data/`)を本線に移行する手順は用意しない。必要になったらその時に検討する。
- OpenAI 直と Azure OpenAI でモデル名やパラメーターに差がある可能性がある。差は `capabilities()` に閉じ込める。

## Action Items

1. [ ] `stream` と `n > 1` の併用可否、Edit での `partial_images`、コンテンツ拒否時の実際のエラーコードを実機で確認する
2. [ ] 4K PNG の実サイズと生成時間を実測する(ADR-0004 の Action Item 1 を兼ねる)
3. [ ] 本線へ移る時点で、この ADR を Superseded にする
