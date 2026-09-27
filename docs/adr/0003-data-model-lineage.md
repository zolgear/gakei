# ADR-0003: データモデル — Asset と Run による世代グラフ

**Status:** Proposed
**Date:** 2026-09-21

## Context

本ツールの中心は「何から何が生まれたか」の記録である。要件は次のとおり。

- Edit で使った元画像(アップロード画像、過去の生成物、マスク)を管理する。
- 1回の実行で複数枚(`n` 1〜10)が出力される。
- Edit は最大16枚の入力画像を取れる。つまり親は1つとは限らず、**世代は木ではなく有向非巡回グラフ(DAG)になる**。
- 記録は証跡であり、後から書き換えられてはならない。
- 将来 ComfyUI など別プロバイダーの実行も同じ形で記録したい。

## Decision

画像そのものを表す **Asset** と、1回のAPI実行を表す **Run** の2種類のノードで二部グラフを作る。

```
Asset(入力) ──RunInput──▶ Run ──produced_by──▶ Asset(出力)
```

### テーブル(概略)

| テーブル | 主な列 | 備考 |
|---|---|---|
| `project` | id, name, owner_id, created_at | 整理の単位 |
| `asset` | id, project_id, kind(`upload` / `generated` / `mask` / `sketch`), sha256, blob_key, mime, width, height, bytes, produced_by_run_id(null可), output_index, source_asset_id(null可。上描きスケッチの下地。ADR-0010), created_by, created_at, deleted_at | バイナリは不変。同一 sha256 は Blob を共有 |
| `run` | id, project_id, batch_id(null可), provider, model, deployment, operation(`generate` / `edit`), prompt, params(JSONB), status, error_code, error_message, usage(JSONB), provider_request_id, created_by, queued_at, started_at, finished_at | 実行の全記録。失敗も残す |
| `run_input` | run_id, asset_id, role(`image` / `mask` / `reference`), position | position 0 の `image` を「主たる親」とする |
| `asset_mark` | asset_id, user_id, flag(`pick` / `reject`), rating, note | 採用/不採用。証跡ではないので更新可 |
| `tag`, `asset_tag` | | 任意のラベル |
| `audit_event` | ADR-0006 参照 | |

### ルール

1. `run` と `run_input`、`asset` の来歴に関わる列は **追記のみ**。UPDATE するのは `run.status` など実行状態の遷移だけ。
2. 削除は `deleted_at` による論理削除のみ。他の Run の入力になっている Asset は Blob も消さない。
3. プロンプトを変えてやり直すのは、既存 Run の更新ではなく **新しい Run** を作る。UIの「再実行」「パラメータを変えて実行」はすべて新規 Run。
4. `params` には API に送った値をそのまま保存する。プロバイダー固有の項目を列にしない。
5. 世代ツリーの表示は、`run_input.position = 0` の親だけを辿って木として描く。その他の入力は「参照」として別表示する。
6. API を介さずにアプリ内で作った派生画像(上描きスケッチ)は、`asset.source_asset_id` で下地の Asset を指す。系列グラフでは下地 → 派生 Asset の辺として描き、主たる親と同じく辿る(2026-09-23 追記。ADR-0010)。
7. アップロードした PNG に GAKEI の系列情報が埋め込まれていて、同じインスタンスの Asset と内容が一致すれば、新しい行を作らずに既存の Asset を使う。一致しなければ新しい `upload` Asset を作り、追記のみの列 `origin_asset_id`(null可)と `origin_meta`(読み取った内容。自己申告)に記録する。系列グラフでは `origin_asset_id` からの辺を「未検証」として描く(2026-09-24 追記。ADR-0014)。
8. 他の画像生成ツール(Stable Diffusion WebUI、ComfyUI、NovelAI など)や C2PA が画像に埋め込んだ生成メタ情報は、`upload` の取り込み時に読み、追記のみの列 `embedded_meta`(null可。自己申告)に記録する。`run` / `run_input` には書かず、画面では「未検証」として表示するだけにする(2026-09-26 追記。ADR-0018)。

### 世代を辿るクエリ(例)

```sql
WITH RECURSIVE lineage AS (
  SELECT a.id, a.produced_by_run_id, 0 AS depth FROM asset a WHERE a.id = :asset_id
  UNION ALL
  SELECT p.id, p.produced_by_run_id, l.depth + 1
  FROM lineage l
  JOIN run_input ri ON ri.run_id = l.produced_by_run_id AND ri.role = 'image'
  JOIN asset p ON p.id = ri.asset_id
)
SELECT * FROM lineage;
```

## Options Considered

### Option A: 画像テーブルに `parent_id` を持たせる(隣接リスト)
**Pros:** 最も単純。木の表示が簡単。
**Cons:** 複数入力を表せない。1回の実行で出た複数枚が同じ実行に属することを表しにくい。プロンプトやパラメータを画像ごとに重複して持つ。失敗した実行(出力なし)を記録できない。

### Option B: Asset と Run の二部グラフ(採用)
**Pros:** 複数入力、複数出力、失敗した実行をすべて自然に表せる。Run が証跡の単位になる。
**Cons:** テーブルが増える。ツリー表示には「主たる親」という決まりごとが要る。

### Option C: グラフDB(Neo4j, Cosmos DB Gremlin)
**Pros:** 経路の問い合わせが書きやすい。
**Cons:** 部品が増える。この規模なら PostgreSQL の再帰CTEで足りる。

## Trade-off Analysis

A は最初は楽だが、Edit の複数入力と証跡の要件で早々に破綻する。後からの移行は来歴データの作り直しになり高くつく。B は最初から少し手間がかかるが、モデルの形が API の実態(1リクエスト=複数入力・複数出力)と一致している。

「バージョン」という独立した概念は作らない。ある画像の版とは、同じ親から生まれた兄弟と、その子孫のことであり、グラフから導ける。

## Consequences

- 証跡は Run を見れば完結する。
- 大量生産は `batch_id` で束ねる(Phase 2)。モデルの変更は不要。
- ストレージは増え続ける。保存期間の方針が別途必要(未解決事項)。
- ComfyUI 対応時は `params` にワークフローJSONを入れれば同じ形で記録できる。

## Action Items

1. [ ] Alembic の初期マイグレーションを作成する
2. [ ] `run` / `run_input` への UPDATE / DELETE をDB権限かトリガーで制限するか検討する
