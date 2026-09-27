# ADR-0018: 他のツールが画像に埋め込んだ生成メタ情報の表示

**Status:** Proposed
**Date:** 2026-09-26

## Context

Stable Diffusion WebUI(AUTOMATIC1111、Forge、SD.Next)、ComfyUI、NovelAI、InvokeAI、SwarmUI などの画像生成ツールは、生成した画像の PNG テキストチャンクや EXIF に、プロンプト、モデル名、seed、サンプラーなどの設定を埋め込む。OpenAI(gpt-image、ChatGPT)や Adobe は C2PA(Content Credentials)のマニフェストを埋め込む。GAKEI にそれらの画像をアップロードして Edit の入力にするとき、「この画像は何でどう作ったか」を GAKEI の画面で参照したい。

いまの GAKEI は、GAKEI 自身が埋め込んだ `gakei` チャンク(ADR-0014)しか読まず、他のツールの情報は捨てている。ADR-0014 は「ComfyUI の `workflow` と `prompt` チャンクの取り込み」を作らないものに挙げているが、これはワークフローとして登録する(ADR-0013 の非ゴール)ことを指す。表示のためだけに読むことは、この ADR で決める。

制約:

- 埋め込まれた内容には署名がなく、誰でも書き換えられる。証跡の正本は DB の Run の記録(ADR-0003、ADR-0006)である。
- ADR-0001 は「C2PA 署名、IPTC 埋め込み」を非ゴール(Phase 3)にしている。
- 来歴の列は追記のみ(ADR-0003)。

この ADR は ADR-0001、ADR-0003、ADR-0014 に追記する。

## Decision

### 1. 対象と保存

- `kind = upload` の Asset だけを対象にする。GAKEI が生成した画像(`generated`)は Run が正本であり、ComfyUI の出力が持つ `prompt` チャンクは `run.params` の `comfyui_prompt` と同じものなので読まない。マスクとスケッチも読まない。
- 取り込み時(`POST /api/assets`)に解析し、追記のみの列 `asset.embedded_meta`(JSON、null 可。migration `0009`)に保存する。何も見つからなければ null のままにする。
- 既存の行は、コマンド `uv run python -m app.tools.backfill_embedded_meta [--dry-run] [--limit N]` で埋める。追記のみの例外として、このコマンドだけが `embedded_meta` が null の `upload` 行を一度だけ更新する(値のある行は上書きしない)。migration では行わない(原本の読み書きは `AssetStore` を介する。ADR-0004)。何も見つからなかった行は null のままなので、次回の実行でも再走査される。
- 解析は `POST /api/assets` の応答を遅らせない範囲(原本のテキストチャンクと EXIF を読むだけ。画素はデコードしない)に留め、どんな入力でも例外を投げず、取り込みを妨げない。

### 2. 読む形式

| ツール | 置き場所 | 読む項目 |
|---|---|---|
| AUTOMATIC1111 / Forge / SD.Next | PNG `parameters`(tEXt / iTXt)、JPEG・WebP の EXIF `UserComment` | 1行目からのプロンプト、`Negative prompt:` 以降、最終行の `Steps: …, Sampler: …, CFG scale: …, Seed: …, Model: …` |
| ComfyUI | PNG `prompt`(API 形式のグラフ) | サンプラー(`KSampler` 系)の seed、steps、cfg、sampler、scheduler。`positive` / `negative` の配線先の `CLIPTextEncode` の文字列。`model` の配線先のチェックポイント名。`workflow` チャンクは読まない |
| NovelAI | PNG `Software`、`Description`、`Comment`(JSON)、`Source` | プロンプト、`uc`(ネガティブ)、seed、steps、sampler、scale、モデル名(`Source`) |
| InvokeAI | PNG `invokeai_metadata`(JSON) | `positive_prompt`、`negative_prompt`、`model.name`、seed、steps、cfg_scale、scheduler |
| SwarmUI | PNG `parameters` または EXIF `UserComment` の JSON `sui_image_params` | prompt、negativeprompt、model、seed、steps、cfgscale |
| C2PA(Content Credentials) | PNG `caBX` チャンク、JPEG APP11(JUMBF) | マニフェストの存在、`c2pa.claim` の claim generator、`c2pa.actions` の softwareAgent。**署名は検証しない**。構造が読めなければ「C2PA あり(内容不明)」とだけ記録する |
| 一般(上のどれにも当たらないもの) | EXIF `Software`、`ImageDescription`、`UserComment`、`Artist`、XMP `dc:description`、PNG の標準キーワード `Software`、`Description`、`Comment`、`Title`、`Author` | 説明系の項目をプロンプトとして扱う。**`Software` しか無い画像は記録しない**(カメラ写真や画像編集ソフトで保存しただけの画像を「生成情報あり」にしないため) |

GAKEI 自身の `gakei` チャンクは ADR-0014 の `origin` の処理に任せ、ここでは無視する。同じ画像に他のツールの情報と C2PA の両方があるときは、ツールの情報を主にし、C2PA の claim generator を `params.c2pa` に添える。

### 3. 保存する形(`gakei.embedded/1`)

```json
{
  "schema": "gakei.embedded/1",
  "tool": "a1111 | comfyui | novelai | invokeai | swarmui | c2pa | generic",
  "software": "生の Software 文字列、または C2PA の claim generator。無ければ null",
  "prompt": "…", "negative_prompt": "…", "model": "…", "seed": 123,
  "params": {"Steps": "20", "Sampler": "DPM++ 2M", "CFG scale": "7", "Size": "1024x1024"},
  "raw": {"parameters": "元のテキストチャンクや EXIF の項目"},
  "truncated": false
}
```

- `params` は順序を保った残りの設定(値は文字列・数値・真偽値。入れ子は JSON 文字列にする)。`raw` は元のテキストをそのまま持ち、画面で折りたたんで見せる。
- 上限: `prompt`、`negative_prompt`、`raw` の各値は 64 KiB 文字、`params` は 200 件(値は 4 KiB)、全体で 256 KiB。超えたら `raw` の大きい項目から削り、`truncated` を true にする。ComfyUI の `workflow` と InvokeAI の `invokeai_graph` / `invokeai_workflow` は、大きく表示にも要らないので `raw` に入れない。
- API は `GET /api/assets/{id}` の `embedded_meta` で返す(`schema` は保存した JSON にだけ持ち、API の型には出さない)。

### 4. 扱い

- 署名の無い自己申告として扱う。画面では常に「未検証」と明示する。ADR-0014 の `origin` と同じ扱いである。
- `run`、`run_input` には書かない。系列グラフのノードや辺にもしない。
- フォームには読み込まない。Stable Diffusion のモデルやサンプラーは GAKEI の capabilities に無く、プロンプトだけを流し込んでも「同じ設定」にはならない。必要なら画面の文字列をコピーする。
- **検索(ADR-0009 6章)の対象にする(2026-09-27 改訂。当初は対象外としていた):** 画像の検索で、それを生んだ Run の `prompt` に加えて、`asset.embedded_meta` の `prompt` と `negative_prompt` も部分一致の対象にする(kind を問わない。アップロードした画像を、埋め込まれたプロンプトで探せるようにするため)。埋め込みでの一致は応答で `prompt_source: "embedded"` として Run 由来(`"run"`)と区別し、画面では「未検証」の印を付ける。リスクの評価: 検索はパラメーター化した LIKE 比較、表示は React のテキスト描画で、注入の経路は増えない。既に画面に表示している自己申告のテキストが検索の抜粋にも出るだけである。フォームへの自動読み込みはしない(上の決定のまま)。

### 5. 画面

- ビューア(`/assets/:id`)と系列インスペクター(`/lineage/:assetId`)で、ADR-0014 の「埋め込まれた系列情報(未検証)」の直後に「画像に埋め込まれた生成情報(未検証)」の節を出す。ツール名、software、モデル、プロンプト(長ければ折りたたみ)、ネガティブプロンプト、設定の表、元のデータ(折りたたみ)、省略した旨、未検証の注記の順。
- 文言は ADR-0015 に従い、日本語と英語の JSON に置く。

### 作らないもの

- ComfyUI の `workflow` / `prompt` をワークフローとして登録すること(ADR-0013 のまま)
- 読み取った設定のフォームへの読み込み
- C2PA の署名検証、マニフェストの全文表示、GAKEI 自身による C2PA の付与(Phase 3 のまま)
- NovelAI の stealth メタ情報(アルファチャンネルの LSB)
- Fooocus、Midjourney など上記以外のツール専用の解析(一般の EXIF / XMP で拾えるものだけ)
- InvokeAI の旧形式(`sd-metadata`、`Dream`)
- 埋め込まれた情報の編集・削除、メタ情報を除いた原本の保存
- 既存行の自動的な埋め戻し(コマンドの実行に留める)

## Options Considered

| 保存の仕方 | 評価 |
|---|---|
| A: 取り込み時に解析して列に保存(採用) | 一度読めば以後は DB だけで済む。将来の検索にも使える。既存行には埋め戻しコマンドが要る |
| B: 詳細を返すたびに原本から読む | 列も migration も要らず既存行もすぐ対象になるが、Blob(本線)では詳細のたびに原本を読み直すことになる |
| C: `origin_meta` に同居させる | 列は増えないが、GAKEI の系列参照と他ツールの自己申告は意味が違い、系列グラフの処理が混ざる |

| C2PA の扱い | 評価 |
|---|---|
| A: マニフェストを辿り claim generator を表示。署名は検証しない(採用) | JUMBF のボックス走査と CBOR の読み取り(`cbor2`)だけで済む。「OpenAI で作られた」と表示できる |
| B: `caBX` / APP11 の存在検出だけ | 依存を増やさないが、何で作ったかが分からない。A の失敗経路がこれと同じ形になる |
| C: `c2pa-python` で署名検証まで行う | ネイティブ依存が増え、ADR-0001 の Phase 3 の判断を変えることになる |

| 対象の Asset | 評価 |
|---|---|
| `upload` だけ(採用) | 利用者が外から持ち込んだ画像だけが対象。Run のある画像は Run が正本 |
| すべての kind | ComfyUI の出力ごとにグラフ全体が DB に二重に入る |

## Trade-off Analysis

読み取る内容は元のファイルに既に入っていた情報なので、この ADR で新たに外へ出るものはない。埋め込みは自己申告であり、証跡(Run の記録)とは別物として扱うことで、DB が正本であることは変わらない。

ツールごとの形式は公式の仕様がなく変わりやすい(A1111 の `parameters` 文字列、ComfyUI のノード構成)。解析は控えめなヒューリスティックに留め、読めない項目は空にし、`raw` を残して画面で元のテキストを見られるようにする。

## Consequences

- `asset` に追記のみの列 `embedded_meta` が加わる(ADR-0003 に追記)。
- `GET /api/assets/{id}` と `POST /api/assets` の応答に `embedded_meta` が加わる。
- 依存に `cbor2` が加わる。
- 既存のアップロード済み画像は、埋め戻しコマンドを実行するまで `embedded_meta` が null のまま(画面には何も出ない)。
- ADR-0001 の非ゴール「C2PA 署名」に、マニフェストの存在と claim generator の表示は当たらないことを追記する。

## Action Items

1. [x] ADR-0001、ADR-0003、ADR-0014、CLAUDE.md に追記する
2. [x] `asset.embedded_meta`(migration `0009`)、`EmbeddedGenerationMeta` スキーマ、`GET /api/assets/{id}` の応答
3. [x] 解析モジュール `domain/generation_meta.py`(A1111、ComfyUI、NovelAI、InvokeAI、SwarmUI、C2PA、一般)と取り込みへの組み込み
4. [x] 埋め戻しコマンド `app.tools.backfill_embedded_meta`
5. [x] ビューアと系列インスペクターの表示(日本語と英語)
