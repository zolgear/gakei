# ADR-0002: 技術スタック(FastAPI + React SPA + PostgreSQL)

**Status:** Proposed(バックエンドの Python / FastAPI は決定済み)
**Date:** 2026-09-21

## Context

バックエンドは Python(FastAPI)と決めた。OpenAI SDK、画像処理(Pillow)、将来の ComfyUI 連携との相性が理由。残りはフロントエンドとデータベースの選択。

フロントエンドには次の要求がある。
- 4K画像のパン/ズーム表示
- マスクを描くキャンバス(入力画像と同寸のPNGを出力)
- 多数のサムネイルを並べるギャラリー、世代ツリー表示
- ジョブの進捗をリアルタイムに反映

データベースには、世代を辿る再帰クエリと、プロバイダーごとに異なるパラメータの保存が必要。

## Decision

- **バックエンド:** Python 3.12+ / FastAPI / SQLAlchemy 2 + Alembic / OpenAI Python SDK / Pillow
- **フロントエンド:** React + TypeScript + Vite の SPA。FastAPI が静的ファイルとして配信する(コンテナ1種類で完結)
- **データベース:** PostgreSQL(Azure Database for PostgreSQL Flexible Server)
- **API 形式:** REST + OpenAPI。型は OpenAPI から TypeScript に自動生成する
- **進捗通知:** Server-Sent Events

## Options Considered

### フロントエンド

| 案 | 複雑さ | 要求への適合 | 評価 |
|---|---|---|---|
| A: React SPA(Vite)(採用) | 中 | キャンバス、ビューア、ツリーのライブラリが豊富 | ビルド成果物を FastAPI から配信でき、運用が単純 |
| B: Next.js | 中〜高 | 同上 | Node のサーバーが増える。SSR は組織内ツールに不要 |
| C: Gradio / Streamlit | 低 | マスク描画や4Kビューア、ツリー表示の自由度が足りない | 試作には良いが Open WebUI と同種の限界に当たる |
| D: HTMX + Jinja | 低 | キャンバス操作と状態管理が苦しい | 画像編集UIには不向き |

### データベース

| 案 | 評価 |
|---|---|
| A: PostgreSQL(採用) | 再帰CTEで世代を辿れる。JSONB でパラメータを保存できる。`SKIP LOCKED` でジョブキューも兼ねられる(ADR-0005) |
| B: SQLite | 最も単純だが、api と worker の複数コンテナからの同時書き込みと Azure 上の永続化が難しい |
| C: Cosmos DB | グラフや再帰の問い合わせが不得意。トランザクション境界の設計が増える |

## Trade-off Analysis

フロントは「作り込まない」方針と矛盾して見えるが、動機そのものが Edit のUIと4K表示であり、ここを簡易フレームワークで済ませると目的を達成できない。複雑さは SPA に閉じ込め、サーバー側は FastAPI 1種類に保つ。

DB は PostgreSQL 一つに、メタデータ、世代グラフ、ジョブキュー、監査イベントを全部載せる。部品を増やさないことを優先した。

## Consequences

- コンテナイメージは1種類(起動コマンドで api / worker を切り替え)。
- TypeScript / React の保守が必要になる。主要言語ではないため、UIライブラリは少数に絞る。
- (2026-09-29 注記: 本線に先立ち、ADR-0027 でローカルMVPのセルフホスト版でも `DATABASE_URL` を指定すると PostgreSQL を選べるようになった。ジョブキューとしての `SKIP LOCKED` はまだ入っていない。)
- 候補ライブラリ(着手時に確定): ズーム表示 `react-zoom-pan-pinch`、マスク描画は素の Canvas API、ツリー表示 `React Flow`、データ取得 `TanStack Query`。

## Action Items

1. [ ] フロントの UI コンポーネント方針を決める(素のCSS / Tailwind / 既存のコンポーネント集)
2. [ ] OpenAPI → TypeScript の型生成を CI に組み込む
