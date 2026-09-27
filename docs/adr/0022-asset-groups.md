# ADR-0022: グループ(ストックの手動整理)

**Status:** Proposed
**Date:** 2026-09-27

## Context

ストック(`GET /api/assets`)は kind による絞り込みと、削除しかない。ADR-0003 の採用/不採用(`asset_mark`)とタグは設計だけで、ローカル MVP には実装していない。生成と取り込みを重ねるとストックが数百枚になり、「この案件の画像」「参考画像」のようなまとまりを利用者が作れないと探せなくなる。

系列(lineage)は生成の来歴を表す軸で、利用者の意図による分類とは別の軸が要る。ADR-0001 の非ゴール表は「汎用 DAM(任意ファイル管理、フォルダ階層)」を作らない理由として「プロジェクト+フラグ+タグで足りる」としている。この「プロジェクト」に当たるものがまだ無い。

Issue [#6](https://github.com/zolgear/gakei/issues/6)。自動整理(タグや VLM による分類。[#5](https://github.com/zolgear/gakei/issues/5))は後続で、この ADR は手動整理の器だけを決める。

## Decision

### 1. 範囲

- **グループは階層なしのフラットな集まり。** 1 つの Asset は複数のグループに属せる(多対多)。フォルダ階層、グループの入れ子、グループ内の並べ替えは作らない。
- **グループは証跡ではない。** 名前の変更、メンバーの追加・削除、グループの削除は自由に行える(ADR-0003 の追記のみの規則の対象外。タグや `asset_mark` と同じ扱い)。
- グループは利用者全員で共有する(oidc モードでも個人ごとに分けない。ADR-0019 の「閲覧範囲は全員が全件」に合わせる)。作成・変更は管理者に限定しない。
- 対象は削除済みでない Asset で、kind は問わない(マスクも入れられる)。
- 自動でメンバーを入れる仕組みは持たない。#5 で足すときも、自動で入れたメンバーを人が外せることを前提にする。

### 2. データモデル

```
asset_group(
  id UUID PK,
  name VARCHAR(100) NOT NULL,
  created_by_user_id UUID NULL FK app_user.id,   -- none モードは NULL(ADR-0019 と同じ)
  created_at, updated_at TIMESTAMPTZ NOT NULL,
  deleted_at TIMESTAMPTZ NULL                     -- 論理削除(prompt_set と同じ。復元の余地を残す)
)
asset_group_member(
  asset_group_id UUID FK asset_group.id,
  asset_id UUID FK asset.id,
  added_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (asset_group_id, asset_id)
)
INDEX ix_asset_group_member_asset_id (asset_id)
```

- メンバーの削除は物理削除。グループの削除は `deleted_at` を立てるだけで、メンバー行は残す(復元 API は今は作らない)。
- Asset を論理削除してもメンバー行は残し、一覧では `asset.deleted_at IS NULL` で除く。Asset の復元(ADR-0008)でグループにも戻る。
- 表紙画像は列に持たず、`added_at` が最新のメンバーを一覧の応答で返す。
- 名前は前後の空白を除いて 1〜100 文字。同名のグループを禁止しない(利用者が付けた名前をサーバーが拒む理由が弱い)。

### 3. API

すべて `require_user`(ADR-0019 の既定)。

| メソッド | パス | 内容 |
|---|---|---|
| `GET` | `/api/asset-groups` | 削除済みでないグループを `updated_at DESC` で全件。各行は `{id, name, member_count, cover_asset_id, created_at, updated_at}`(`member_count` と `cover_asset_id` は削除済みでない Asset だけを数える。ページングなし。prompt-sets と同じ) |
| `POST` | `/api/asset-groups` | `{name}` → 201、作成した行 |
| `PATCH` | `/api/asset-groups/{id}` | `{name}` → 更新した行 |
| `DELETE` | `/api/asset-groups/{id}` | 論理削除 → 204 |
| `POST` | `/api/asset-groups/{id}/assets` | `{asset_ids: [UUID, ...]}`(1〜200 件)。既に入っているものは無視し、削除済みや存在しない Asset は 404 で全体を拒む → 更新後のグループ行 |
| `POST` | `/api/asset-groups/{id}/assets/remove` | `{asset_ids}` → 入っていないものは無視 → 更新後のグループ行 |
| `GET` | `/api/assets?group_id=` | 既存の一覧に `group_id` を足す。`kind` と併用でき、並びとカーソルは既存のまま(`created_at DESC, id DESC`)。存在しない・削除済みのグループは 404 |

- `AssetDetail.groups: [{id, name}]` を足す(`name` 昇順)。ビューアの詳細で表示と追加・削除に使う。`AssetSummary` には足さない(一覧が重くなる)。
- メンバーの追加・削除・名前変更でグループの `updated_at` を進める。
- 削除済みグループに対する `PATCH` / メンバー操作 / `group_id` 絞り込みは 404。

### 4. 画面

- **ストックパネルの絞り込み**: kind のチップ行の上に、折り畳める「グループ」の節を置く。見出し行は「グループ」と現在の選択(「すべて」またはグループ名と件数)、開閉の印で、選択中のグループがあればその横に「⋯」(名前の変更と削除。削除は既存の Asset 削除と同じ確認ダイアログを通す)。開くと縦のリストで「すべて」、各グループ(名前と件数)、末尾に「新しいグループ」(押すとその場で名前の入力)が並ぶ。横スクロールのチップ行にはしない(2026-09-27 の画面確認で、横に並べると kind のチップと見分けがつかず、スクロールバーも目立った)。開閉の状態は localStorage に持つ。グループを選ぶと一覧が `group_id` で絞られ、kind の絞り込みと併用できる。
- **一括追加**: ストックパネルのヘッダーに「選択」の切り替えを置く。選択モードではタイルがチェックできる(`StockPickerGrid` の複数選択。ADR-0020 で作った `isTileSelectable` を流用)。下部に「N 枚を選択中」と「グループに追加」「選択を解除」を出し、グループで絞り込み中は「このグループから外す」も出す。「グループに追加」はグループ一覧と「新しいグループ…」を持つポップオーバー。
- **単体の操作**: ビューア(`/assets/:id`)の詳細に「グループ」の節を足し、所属グループをチップで並べ、各チップの × で外す。「追加」で同じポップオーバーを開く。
- 履歴、検索結果、系列グラフにはグループを出さない(今回の範囲外)。
- 狭い幅(768px 未満)でも選択モードと下部の操作が使えること(ADR-0009)。
- 文言は `frontend/src/i18n/locales/{ja,en}.json` の `stock.groups.*` と `viewer.groups.*`、サーバー側は `backend/app/locales/{ja,en}.json` の `assetGroups.*`(ADR-0015)。

## Options Considered

### Option A: タグを先に実装し、グループはタグの一種にする
**Pros:** テーブルが 1 組で済む。ADR-0003 のタグ設計をそのまま使える。
**Cons:** 「まとまり」と「性質のラベル」は使い方が違う。グループは数が少なく一覧で選び、タグは多くて検索で使う。#5 の自動タグ付けを入れると、人が作った分類が自動タグに埋もれる。

### Option B: フラットなグループ(採用)
**Pros:** 利用者が欲しい「案件ごとのまとまり」を最短で出せる。データモデルは自動整理を足しても変わらない。
**Cons:** タグを後で足すと整理の軸が 2 つになる。ADR-0001 の「プロジェクト+フラグ+タグ」に沿っているので許容する。

### Option C: フォルダ階層
**Pros:** ファイラーに近く直感的。
**Cons:** ADR-0001 の非ゴール(汎用 DAM)。1 つの Asset を複数の文脈に置けない。

### 一覧の取り方: `GET /api/asset-groups/{id}/assets` を新設するか、`GET /api/assets?group_id=` にするか
既存の一覧に `group_id` を足す方を採る。ストックパネルの無限スクロール、kind の併用、カーソルの実装をそのまま使え、フロントは引数を 1 つ渡すだけになる。

## Trade-off Analysis

- 名前の重複を許すので、同名のグループが並ぶことがある。並びが `updated_at DESC` なので、直近に触ったものが上に来て見分けはつく。制約が必要になったら足す。
- メンバー行を物理削除するため、「いつ誰が外したか」は残らない。グループは証跡ではないので割り切る。
- `member_count` を一覧のたびに数える。グループ数は少数で、メンバー表には主キーと `asset_id` の索引があるので、SQLite / PostgreSQL のどちらでも問題にならない量。
- マイグレーションは新規テーブルの作成だけで `batch_alter_table` を使わない。#4(PostgreSQL)でそのまま通る。

## Consequences

- ADR-0001 の非ゴール表「汎用 DAM」に、フラットなグループは「プロジェクト」に当たり非ゴールではない旨を追記する。
- Alembic `0012_asset_groups`。`backend/app/domain/models.py` に `AssetGroup` / `AssetGroupMember`、`backend/app/domain/asset_groups.py` に操作、`backend/app/api/asset_groups.py` にルーター。`AssetDetail` の変更で `npm run gen:api` が要る。
- #5(自動タグ・タイトル)は、この ADR のグループに「自動で入れる」規則を足す形で設計する。#8(エージェント)はこの API をツールとして使う。

## Action Items

- [ ] AI-1: マイグレーション、モデル、ドメイン、ルーター、テスト(backend)
- [ ] AI-2: ストックパネルのグループ行、選択モード、ポップオーバー、ビューアの節、i18n、テスト(frontend)
- [ ] AI-3: ADR-0001 の追記、ADR README の行、CLAUDE.md の構成の更新
