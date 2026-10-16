# Mod とフック

GAKEI が決まった時点(イベント)で、外部のシステム(Webhook)や、サーバーに置いた Python のコード(Python の Mod)を呼ぶ機能。組織で、生成 AI に送る前の検査や記録、生成した画像の類似・侵害の判定、決まったフォルダへの自動の保存などにつなぐために使う。設計は [ADR-0051](adr/0051-mods-and-hooks.md)。

既定では、Mod は1つも動かない。管理者が管理者設定の「Mod」(`/settings/mods`)で登録・有効にしたものだけを呼ぶ。

## 1. できること・できないこと

できること:

- **送る前のチェック:** 外部の AI へ送る直前に呼ばれ、許可か拒否を返す(`ai.before_send`)。拒否すると、その処理は送らずに止まる。
- **判定の記録:** 画像や Run についての判定(`ok` / `flag` / `deny`、名前、スコア、外部の受付番号、詳細へのリンク)を返し、GAKEI が記録して画面に出す。`deny` を受けた画像は保存しない(5章)。
- **手元の処理:** 通知を受けて、Mod の側で何かをする(ローカルへの保存、外部の台帳への記録など)。GAKEI は呼び出しの結果を記録するだけ。

できないこと:

- プロンプト、パラメーター、画像の書き換え。GAKEI は送った値をそのまま記録する(ADR-0003)。
- 人の判断を待つ「保留」。拒否は、外部のシステムが自動で即答する場合だけ。
- 画面、API のルート、プロバイダーの追加。Mod の設定は、マニフェストに書いた項目を GAKEI が共通の形で出すだけ。
- Mod どうしをつないで処理の流れを組むこと。
- URL からのインストールや配布。Python の Mod のサンドボックス(10章の「危険」)。

## 2. イベント

| イベント | 呼ばれる時点 | 呼び方 | 返せるもの |
|---|---|---|---|
| `ai.before_send` | 外部の AI へ送る直前(下の表) | 同期(応答を待つ) | 許可・拒否 |
| `asset.ingested` | 画像を取り込み、形式と大きさの確認を終えた後 | 非同期(購読ごとに「判定を待つ」も選べる) | 判定(その Asset に) |
| `run.finished` | Run が succeeded / failed / canceled になった後 | 非同期(購読ごとに「判定を待つ」も選べる) | 判定(その Run と出力の Asset に) |

`ai.before_send` の目的(`purpose`):

| `purpose` | 呼ばれる時点 | 拒否されたとき |
|---|---|---|
| `image_run` | Run を実行する直前(OpenAI、ComfyUI、SD WebUI のどれでも。ローカルの ComfyUI・SD WebUI も含む) | Run は失敗になる(`error.code = hook_denied`、理由と Mod の名前を記録) |
| `annotation` | 自動タイトル・タグで LLM・VLM を呼ぶ前(タグの訳を含む)。ローカルの WD Tagger は呼ばない | その画像の自動付与は失敗になる |
| `assistant` | アシスタントが LLM を呼ぶたび(道具の呼び出しの往復を含む) | その回の応答を止め、理由をチャットに出す |
| `embedding` | リモートの埋め込みを呼ぶ前(画像・文章)。ローカルの ONNX は呼ばない | その埋め込みは失敗になる |

管理者設定の「接続を確認」は、検査用の固定の文しか送らないので対象にしない。MCP から作った Run も `image_run` を通る。繰り返しの実行は Run ごとに呼ぶ。

`asset.ingested` の取り込みの経路(`source`):

| `source` | 何の取り込みか |
|---|---|
| `generated` | 生成の出力 |
| `upload` | ローカルの画像・貼り付け |
| `url` | URL からの取り込み |
| `sketch` | スケッチ |
| `mask` | マスク |
| `composite` | 入力画像の配置 |
| `import` | 系列の取り込み(ZIP) |
| `mcp` | MCP からの取り込み |

購読ごとに、イベント、`purpose`、`source`、プロバイダーで絞り込める。`ai.before_send` は外部の AI を呼ぶたびに待ちが入るので、検査が要るものだけに絞るとよい。

## 3. 契約

Webhook と Python の Mod は同じ中身を受け取る。すべてのイベントは同じ形の封筒を持つ。

```json
{
  "schema_version": 1,
  "event": "ai.before_send",
  "event_id": "0b6f4c1e-3f8a-4b7e-9c55-2d1a7a0c9e10",
  "occurred_at": "2026-10-16T03:04:05.123456+00:00",
  "instance": { "version": "0.8.1", "name": "design-team" },
  "actor": { "id": "5e0c…", "email": "user@example.com", "name": "山田" },
  "subject": { "…": "イベントごとの中身" }
}
```

- `instance.name` は、管理者設定の「Mod」で決めた名前(未設定なら `null`)。
- `actor` は実行したユーザー。個人モード(認証なし)では `null`。
- `occurred_at` は ISO 8601(UTC)。

### `ai.before_send` の `subject`

```json
{
  "purpose": "image_run",
  "target": {
    "provider": "comfyui",
    "model": "3f0e…",
    "host": "192.168.1.20",
    "locality": "unset",
    "locality_source": "default",
    "workflow": { "id": "3f0e…", "name": "edit-qwen" },
    "connection": null
  },
  "text": [
    { "role": "prompt", "text": "…" },
    { "role": "negative_prompt", "text": "…" }
  ],
  "params": { "size": "1024x1024" },
  "images": [
    { "asset_id": "…", "sha256": "…", "width": 1024, "height": 1024, "bytes": 812345, "mime": "image/png", "role": "primary" }
  ],
  "run_id": "…",
  "asset_id": null
}
```

- `target` は送り先。API キーは含めない。`connection` は LLM・VLM・埋め込みの接続先のとき(`{ "id", "name", "builtin" }`)。
- `text` の `role` は `prompt`、`negative_prompt`、`system`、`user`、`assistant`、`tool`、`tags`、`query` など。
- `images` の `role` は `primary`(主たる入力)、`reference`(参照)、`mask`、`attachment`(アシスタントへの添付)。画像で探すときの画像のように Asset でないものは `asset_id` が `null`。
- `run_id` は `image_run` のとき、`asset_id` は自動タイトル・タグの対象の画像。
- ComfyUI の Run の `params` は、共有リンクと同じ規則で秘密に見える値を伏せる。

### `asset.ingested` の `subject`

```json
{
  "asset": { "asset_id": "…", "sha256": "…", "width": 1536, "height": 1024, "bytes": 2345678, "mime": "image/png", "role": null },
  "kind": "generated",
  "source": "generated",
  "run_id": "…",
  "rating": null
}
```

`run_id` は生成の出力のとき、`rating` はレーティングを判定済みのとき([content-rating.md](content-rating.md))。

### `run.finished` の `subject`

```json
{
  "run_id": "…",
  "status": "succeeded",
  "provider": "openai",
  "model": "gpt-image-1",
  "operation": "edit",
  "prompt": "…",
  "params": { "size": "1024x1024", "quality": "low" },
  "inputs": [ { "asset_id": "…", "sha256": "…", "width": 1024, "height": 1024, "bytes": 1, "mime": "image/png", "role": "primary" } ],
  "outputs": [ { "asset_id": "…", "sha256": "…", "width": 1024, "height": 1024, "bytes": 1, "mime": "image/png", "role": null } ],
  "usage": { "…": "…" },
  "error": null
}
```

失敗のときは `error` に `{ "code", "message" }` が入る。

### JSON Schema と版

すべての定義は JSON Schema(2020-12)で書き出せる。

```bash
cd backend
uv run python -m app.tools.export_mod_schema mods-schema.json   # - なら標準出力
```

`$defs` に、封筒(`AiBeforeSendEvent`、`AssetIngestedEvent`、`RunFinishedEvent`)、`subject`、`ai.before_send` の応答(`Decision`)、判定の応答(`VerdictsResponse`)、判定の受付の API の本文(`VerdictSubmission`)が入る。

`schema_version` の互換性の規則:

- **項目を足すときは、版を上げない。** 受ける側は、知らない項目を無視すること。
- **項目を消す・意味を変えるときは、`schema_version` を上げる。** 受ける側は、知らない版を受けたら、何もしないか拒否する。
- Python の Mod の関数の呼ばれ方の版は、マニフェストの `api_version`(今は `1`)。合わない Mod は読み込みエラーになる。

## 4. 応答

**`ai.before_send`:**

```json
{ "decision": "deny", "reason": "社外秘の語を含みます", "reference": "DLP-2026-0042" }
```

- `decision` は `allow` か `deny`(必須)。
- `reason` は利用者に見せる文(画面に「◯◯ が送信を止めました: 理由」と出る)。`reference` は外部の受付番号など。どちらも省略できる。

**`asset.ingested` と `run.finished`:** 判定の配列。返すものが無ければ、空の配列、`null`、空の本文のどれでもよい。`{"verdicts": [...]}` の形も受ける。

```json
[
  {
    "target": { "type": "asset", "id": "…" },
    "verdict": "flag",
    "label": "類似の疑い",
    "score": 0.91,
    "message": "既存の作品 A に似ています",
    "reference": "case-1234",
    "url": "https://check.example.com/cases/1234"
  }
]
```

- `target.type` は `asset` か `run`。受けるのは、そのイベントで届いた Asset と Run だけ(`run.finished` なら、その Run と出力の Asset)。ほかは捨てる。
- `verdict` は `ok`(問題なし)、`flag`(印を付けて表示するだけ)、`deny`(拒否。5章)。
- `score` は 0〜1(省略できる)、`url` は `http`/`https`。
- 1回の応答に 100 件まで。文は長すぎれば切る。

判定は、同じ Mod が後で送り直せば、最新のものが効く(記録は追記で残る)。判定は、その画像が見える人にだけ見せる。共有リンクと系列の ZIP には含めない。

**判定の効力:** ある Asset への最新の判定が `deny` の Mod が1つでもあれば、その Asset は外部の AI へ送らない。入力に使った Run は作成の時点で断り、アシスタントへの添付、VLM、リモートの埋め込みにも使わない。`flag` は表示だけで、動作は変えない。

## 5. 判定を待つ購読と、画像を残さない処理

### 判定を待つ

`asset.ingested` と `run.finished` は、既定では、画像を保存して記録を確定させた後に、待たずに送る(非同期)。判定が返るまでの間、画像は保存され、利用者に見える(ダウンロードもできる)。

購読で「判定を待つ」を選ぶと、画像をストレージに書く前に呼び、応答を待つ。

- その分、アップロードや生成の完了が遅れる。
- `deny` なら、画像を一度もストレージに書かない。
- 失敗・時間切れは、その Mod の「失敗のとき」の扱いに従う(6章)。「止める」なら、届いた画像すべてに、GAKEI が「チェックの失敗」の `deny` を記録する。
- Webhook で画像を送る設定なら、保存前の画像をそのまま送る。

### 画像を残さない(取り消せない)

`deny` の判定を受けた画像は保存しない。

- **判定を待つ購読**では、原本を最初から書かない。**非同期の購読**では、判定を受けた時点で原本と派生(サムネイル・プレビュー)を消す。
- 残るもの: 画像の記録(sha256、大きさ、形式、来歴)、系列のノードと辺、Run の記録、判定と呼び出しの記録。系列は途切れず、ノードは「画像なし(拒否)」として表示する。ストックからも外さない。
- 画像から作ったもの(埋め込み、知覚ハッシュ、焦点など)も消す。タイトル・タグ・レーティングの文字の記録は残す。
- 中身・ダウンロード・共有リンク・系列の ZIP・MCP は、画像を返さない(中身は 410 で理由を付ける)。入力に使おうとすると断る。
- 画像を残さなかった画像を起点に、系列を書き出すことはできない(409)。ほかの画像を起点にした系列の途中にあれば、ノードとして書き出す(画像は含めない)。
- **同じ内容の画像にも及ぶ。** sha256 が同じ画像は原本のファイルを共有しているので、同じ内容のほかの画像も同じ状態にし、そのことを記録する(判定の記録の `propagated_from_id` が元の判定を指す)。後から同じ内容を取り込もうとしたときも、保存せずに同じ状態にする。
- **取り消せない。** 後で同じ Mod が `ok` を返しても、画像は戻らない。判定の誤りで消えた画像は、同じ設定で生成し直すしかない(結果は同じにならない)。
- 原本の無い画像を消すツール([reconcile-storage.md](reconcile-storage.md))は、画像を残さなかった画像を対象にしない。

非同期の `deny` は、判定が返る前に始めた Edit を止めない。外部へ送ること自体を止めたいときは、`ai.before_send` を購読してそこで止める。

## 6. 失敗のとき

| イベント | 失敗・時間切れ・契約に合わない応答のとき |
|---|---|
| `ai.before_send` | Mod ごとに選ぶ。「止める」(既定。拒否と同じ扱いで、理由は「送る前のチェックに失敗しました」)か「通す」(通したことを記録する) |
| `asset.ingested` / `run.finished`(非同期) | GAKEI の動作は止めない。間隔を延ばしながら送り直し(30 秒、60 秒、120 秒、240 秒)、5 回失敗したら配達を「失敗」として残す。サーバーが落ちても、再起動の後に配り直す |
| `asset.ingested` / `run.finished`(判定を待つ) | `ai.before_send` と同じく「止める」「通す」を選ぶ。「止める」なら画像を残さない |

- 待つ時間は Mod ごとに 1〜300 秒(既定 10 秒)。
- 複数の Mod が `ai.before_send` を購読しているときは、管理者が決めた順に呼び、最初の拒否で止める。
- 配る時点でその Mod を無効にしていたら、送らずに済ませる(記録は `skipped`)。
- 呼び出しは、許可・拒否・失敗をすべて記録する。管理者設定の「Mod」に、直近の呼び出しと配達の失敗が出る。Run の詳細には `ai.before_send` の結果、ビューアの詳細には判定が出る。

## 7. 送り先の区分(locality)

送り先が組織の中か外かを、管理者が設定で決める。値は `local`(組織の中で処理する)、`cloud`(組織の外のサービスへ送る)、`unset`(決めていない)。

- **GAKEI は自動で判定しない。** ホスト名が手元でも、ComfyUI のワークフローに外部の API を呼ぶノードがあれば、データは外に出る。逆に、OpenAI 互換の接続先が組織の中のサーバーのこともある。
- **GAKEI はこの値で動作を変えない。** `ai.before_send` の `target.locality` に載せるだけで、判断は Mod に任せる(例: 「`cloud` と `unset` だけ検査する」「`unset` は拒否する」)。
- 既定値: OpenAI の公式の接続先は `cloud`、それ以外は `unset`。
- ワークフローの値は、`unset` でなければ接続の値より優先する。どこで決まったかを `target.locality_source`(`workflow` / `connection` / `default`)で送る。

設定する場所(どれも Mod を有効にしていなくても出る。後から Mod を足したときに設定し直さなくて済むように):

| 対象 | 場所 |
|---|---|
| OpenAI、ComfyUI、SD WebUI の接続 | 管理者設定のそれぞれのページの「送り先の区分」 |
| ComfyUI のワークフロー | ワークフローの登録・編集(「接続の設定に従う」なら接続の区分を使う) |
| LLM・VLM の接続先 | 管理者設定の LLM の接続先の登録・編集 |
| リモートの埋め込みの接続先 | 同上(埋め込みが使う接続先) |

## 8. Webhook

管理者設定の「Mod」で登録する。名前、URL、署名の鍵、購読するイベントと絞り込み、画像を送るか、待つ時間、失敗のときの扱い、判定を待つか、を決める。

- 鍵は GAKEI のサーバーの `DATA_DIR/secrets.json` に保存する。画面と API の応答には、一部も出さない(設定済みかどうかだけ)。
- 要求は `POST`。リダイレクトは辿らない。2xx 以外と、JSON として読めない応答は失敗。応答の本文は 1 MB まで読む。
- ヘッダー: `X-Gakei-Event`(イベント名)、`X-Gakei-Delivery`(`event_id`)、`X-Gakei-Timestamp`(UNIX 秒)、`X-Gakei-Signature`、`User-Agent: GAKEI/<版>`。

### 署名の確かめ方

`X-Gakei-Signature` は `sha256=` に続けて、鍵による `"<X-Gakei-Timestamp>." + 本文のバイト列` の HMAC-SHA256 の hex。multipart のときも、送られたバイト列そのもの(境界を含む)に署名している。受ける側は、署名が合うことと、時刻が古すぎないこと(例: 前後 5 分以内)を確かめる。本文は、読む前に加工しない(JSON として読み直してから署名を計算すると合わない)。

Python:

```python
import hashlib
import hmac
import time


def verify(secret: str, timestamp: str, signature: str, body: bytes, max_skew: int = 300) -> bool:
    if abs(time.time() - int(timestamp)) > max_skew:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

Node.js:

```js
import { createHmac, timingSafeEqual } from "node:crypto";

export function verify(secret, timestamp, signature, body /* Buffer */, maxSkew = 300) {
  if (Math.abs(Date.now() / 1000 - Number(timestamp)) > maxSkew) return false;
  const expected =
    "sha256=" +
    createHmac("sha256", secret)
      .update(Buffer.concat([Buffer.from(`${timestamp}.`), body]))
      .digest("hex");
  const a = Buffer.from(expected);
  const b = Buffer.from(signature ?? "");
  return a.length === b.length && timingSafeEqual(a, b);
}
```

### multipart の形

「画像を送る」がオフ(既定)なら、本文は `application/json` のイベントだけ。オンなら `multipart/form-data` で次の部分を送る。base64 にはしない。

| 部分の名前 | ファイル名 | 中身 |
|---|---|---|
| `event` | `event.json` | イベントの JSON(`application/json`) |
| `images`(画像の数だけ) | `<Asset の id>.<拡張子>`(Asset でない画像は `<sha256>.<拡張子>`) | 画像の原本のバイト列 |

送る画像は、`ai.before_send` は `subject.images`、`asset.ingested` は `subject.asset`、`run.finished` は `subject.outputs`。画像を残さなかった画像は送らない(メタ情報だけ)。受ける側から GAKEI へ画像を取りに来る仕組みは無い(社内のシステムから GAKEI へ届かない配置が多いため)。

### テスト送信

一覧の「テスト送信」で、ダミーのイベント(id は実在しない。画像は 8×8 の灰色の PNG で、`asset_id` が `null`)を送り、応答と所要時間を確かめられる。Webhook は有効にしていなくても送れる(Python の Mod は、有効にして読み込めたものだけ)。テスト送信は記録を残さない。

## 9. 判定の受付の API

非同期の判定を後から返したいシステムのための受け口。外部の審査に時間がかかるときは、配達にはすぐ空の応答を返し、結果が出てからここへ送る。

```
POST /api/mods/{Webhook の id}/verdicts
Content-Type: application/json
X-Gakei-Timestamp: <UNIX 秒>
X-Gakei-Signature: sha256=<HMAC-SHA256(鍵, "<timestamp>." + 本文) の hex>

[ { "target": { "type": "asset", "id": "…" }, "verdict": "deny", "label": "侵害の疑い", "reference": "case-1234" } ]
```

- 認証は、その Webhook の署名の鍵による署名(ログインのセッションやアクセストークンではない)。署名の作り方は送るときと同じ。時刻が前後 300 秒を超えてずれていれば断る。
- 本文は応答と同じ判定の配列(`{"verdicts": [...]}` も可)。1 MB まで(超えると 413)。
- 送れる対象は、その Webhook の呼び出しで届いた Asset と Run だけ(`asset.ingested` で届いた Asset、`run.finished` で届いた Run とその出力の Asset)。対象外が1つでもあれば、何も記録せずに 403。
- 応答: `{"accepted": <記録した数>}`。署名が無い・合わない・古いと 401、無効な・無い Webhook は 404、形が合わないと 422。
- `deny` は応答で返したときと同じく、画像を残さない(5章)。

`curl` の例(鍵と本文はシェルの変数に入れる):

```bash
ts=$(date +%s)
sig=$(printf '%s.%s' "$ts" "$body" | openssl dgst -sha256 -hmac "$secret" -hex | sed 's/^.* //')
curl -X POST "https://gakei.example.com/api/mods/$webhook_id/verdicts" \
  -H 'Content-Type: application/json' -H "X-Gakei-Timestamp: $ts" \
  -H "X-Gakei-Signature: sha256=$sig" --data-binary "$body"
```

Webhook の id は、管理者設定の「Mod」の Webhook の一覧で確かめる。

## 10. Python の Mod

### フォルダの構成

`MODS_DIR`(既定は `DATA_DIR/mods`。[configuration.md](configuration.md))の下に、Mod ごとのフォルダを置く。

```
data/mods/
  banned-words/
    gakei-mod.toml      # マニフェスト
    main.py             # マニフェストの module
    helper.py           # 同じフォルダのほかのモジュール(from . import helper で読める)
```

- マニフェストは起動のときに読む。置いただけでは動かず、管理者が画面で有効にしたときに import する。
- **置いた・書き換えたら、GAKEI を再起動する**(マニフェストは起動のときに読み、一度 import した Mod は読み直さない)。
- マニフェストの不正や import の失敗は、その Mod を「読み込みエラー」として一覧に出すだけで、起動は止めない。
- フォルダの名前が `.` か `_` で始まるものは読まない。同じ id の Mod が2つあれば、後のもの(フォルダの名前の順)を読み込みエラーにする。同梱の Mod と同じ id のものも読み込みエラーにする(同梱のほうを使う)。

### マニフェスト(`gakei-mod.toml`)

```toml
id = "banned-words"                 # 必須。英小文字・数字・._- で 64 文字まで(先頭は英小文字か数字)
name = "禁止語のチェック"            # 必須。文字列か { ja = "...", en = "..." }。100 文字まで
version = "1.0.0"                   # 必須。Mod の版(表示用)
api_version = 1                     # 必須。関数の呼ばれ方の版。今は 1
module = "main"                     # 必須。フォルダの中のモジュール(main.py か main/__init__.py)
events = ["ai.before_send"]         # 必須。購読できるイベント(管理者が画面でさらに絞れる)
returns_verdicts = true             # 任意。asset.ingested・run.finished に判定を返すか。既定は true

[settings.words]                    # 設定の項目(任意。いくつでも)
type = "string"                     # string / number / bool / path / secret
label = { ja = "禁止語(カンマ区切り)", en = "Banned words (comma-separated)" }   # 文字列でもよい
default = "社外秘,confidential"     # 型に合う値。secret には既定値を書けない
```

- 設定の項目のキーは Python の識別子(64 文字まで)。
- `secret` の値は `DATA_DIR/secrets.json` に保存し、画面と API の応答には出さない(設定済みかどうかだけ)。
- `path` は文字列で、画面は文字列と同じく入力させる。存在の確認などは Mod の側で行う。
- `returns_verdicts = false` は「判定を返さない Mod」(書き出すだけの Mod など)。この Mod では「判定を待つ」を選べない(画面にスイッチを出さず、API で `wait_for_verdict: true` を送ると 422)。前に「判定を待つ」で保存した設定が残っていても、待たずに非同期で呼ぶ。返した判定(`submit_verdicts` で送ったものを含む)は記録せずに捨て、サーバーのログに残す。`ai.before_send` の許可・拒否には関係しない。Webhook は受け側が判定を返すかを GAKEI から知れないので、この区別は無い。
- 名前とラベルを言語ごとの表にすると、画面の言語に合わせて出す(無い言語は `ja`、それも無ければ最初の値)。記録に残る Mod の名前は `ja`(無ければ最初の値)。

画面では、Webhook と同じく、有効・無効、購読するイベントと絞り込み、順番、待つ時間、失敗のときの扱い、判定を待つか、と、マニフェストの設定の項目の値を決める。

### 関数の形

購読するイベントの分だけ、モジュールに `async def` の関数を置く。

```python
from gakei_mod import Decision, Event, ModContext, Verdict

async def on_ai_before_send(event: Event, ctx: ModContext) -> Decision: ...
async def on_asset_ingested(event: Event, ctx: ModContext) -> list[Verdict] | None: ...
async def on_run_finished(event: Event, ctx: ModContext) -> list[Verdict] | None: ...
```

- `event` は封筒(3章)の型付きの形(`event.subject` は `BeforeSendSubject` / `AssetIngestedSubject` / `RunFinishedSubject`)。
- 戻り値は応答(4章)。`Decision.allow()` / `Decision.deny("理由")`、`Verdict(target=VerdictTarget(type="asset", id=...), verdict="flag", ...)`。辞書で返してもよい。
- 時間切れと例外は、Webhook と同じく6章の扱いになる。非同期のイベントで例外を出すと、配達の失敗として送り直される。
- 関数は GAKEI のイベントループの上で動く。ファイルの読み書きや重い計算などの同期の処理は、`await asyncio.to_thread(...)` でスレッドへ逃がす(そうしないと、その間 GAKEI 全体が止まる)。

### 公開モジュール `gakei_mod`

Mod が使ってよいのは `gakei_mod` の型と関数だけ。GAKEI の内部のモジュール(`app.…`)を import した Mod は、版が上がったときの動作を保証しない。

主なもの: `Event`、`BeforeSendSubject`、`AssetIngestedSubject`、`RunFinishedSubject`、`ImageRef`、`Target`、`TextPart`、`Decision`、`Verdict`、`VerdictTarget`、`ModContext`、`ImageWithheld`、イベント名の定数(`AI_BEFORE_SEND`、`ASSET_INGESTED`、`RUN_FINISHED`)、値の一覧(`PURPOSES`、`SOURCES`、`LOCALITIES`、`IMAGE_ROLES`、`VERDICTS`)、`SCHEMA_VERSION`、`API_VERSION`。

### `ModContext`

| 項目 | 内容 |
|---|---|
| `mod_id` | この Mod の id |
| `settings` | マニフェストの設定の項目の値(既定値 → 画面で保存した値。`secret` も値が入るので、ログに出さない) |
| `open_image(ref)` | 画像の原本のバイト列を返す(`await`)。`ImageRef` か Asset の id を渡す |
| `open_image_with_lineage(ref)` | ダウンロードと同じ、系列の情報を埋め込んだ PNG を返す([ADR-0014](adr/0014-embedded-lineage-metadata.md))。系列は、その画像を作った人に見える範囲。PNG でない原本と、まだ保存していない画像(判定を待つ購読、テスト送信)は原本のまま返す |
| `get_run(run_id)` | `run.finished` の `subject` と同じ形の Run の情報(無ければ `None`) |
| `submit_verdicts(verdicts)` | 判定を後から送る(9章の受付の API と同じ扱い)。対象は、この Mod に届いた Asset と Run だけ。受け取った `ctx` を覚えておき、後で呼んでよい。記録した数を返す |
| `logger` | この Mod のロガー(`gakei.mods.<id>`) |

画像を残さなかった画像(5章)を開こうとすると、`open_image` と `open_image_with_lineage` は `ImageWithheld` を出す。原本はもう無いので、その画像は飛ばしてよい。

### 例: 禁止語で拒否する最小の Mod

`data/mods/banned-words/gakei-mod.toml` は上のマニフェストの例。`main.py`:

```python
from gakei_mod import Decision, Event, ModContext


async def on_ai_before_send(event: Event, ctx: ModContext) -> Decision:
    words = [w.strip() for w in str(ctx.settings.get("words") or "").split(",") if w.strip()]
    text = "\n".join(part.text for part in event.subject.text)
    for word in words:
        if word.lower() in text.lower():
            return Decision.deny(f"禁止語「{word}」を含みます")
    return Decision.allow()
```

置いたら GAKEI を再起動し、管理者設定の「Mod」で有効にして、「テスト送信」で確かめる(ダミーのイベントの文は `GAKEI test event`)。

### 依存のパッケージ

GAKEI は Mod の依存のパッケージを入れない。必要なら、GAKEI と同じ Python の環境に入れる。

- `run.sh` / `run.bat` で動かしているとき: `cd backend && uv pip install <パッケージ>`。`uv sync` を実行すると消えるので、そのときは入れ直す。
- Docker: イメージを派生させて入れる。

```dockerfile
# 使う版に固定する(例: 0.y.z)
ARG GAKEI_VERSION=latest
FROM ghcr.io/zolgear/gakei:${GAKEI_VERSION}
USER root
RUN uv pip install --python /app/backend/.venv/bin/python requests
USER gakei
```

```bash
docker build -t gakei-with-mods .
# Mod は DATA_DIR(/data)のボリュームの mods/ に置く(または MODS_DIR を変える)
```

### 危険

- **Python の Mod は、GAKEI のプロセスの中で、GAKEI と同じ権限で動く。** DB、画像、`secrets.json`(API キーを含む)を読み書きでき、外へ送ることもできる。サンドボックスは無い。
- 作りの悪い Mod は、GAKEI を遅くしたり落としたりできる。時間切れと例外は捕まえるが、隔離はしない。
- 置けるのは、サーバーのファイルを書ける人だけ(画面からは置けない)。中身を読んで信頼できるものだけを置く。`MODS_DIR` を、ほかの人が書き込めるフォルダにしない。

## 11. 同梱の Mod「ローカルに保存」

生成の出力(と、選べば取り込んだ画像)を、決めたフォルダへ自動で書き出す。Python の Mod の例を兼ねて GAKEI に同梱している(`backend/app/mods/builtin/local_export/`。`gakei_mod` だけを使う)。id は `local-export`。既定は無効で、管理者設定の「Mod」で有効にする。

**保存先は GAKEI のサーバーの上のフォルダ**で、利用者の PC ではない。

- Docker では、コンテナの中のパスを指定し、そのパスにホストのフォルダをマウントする。コンテナは uid 10001 で動くので、マウントするフォルダはこの uid で書けるようにする。

  ```bash
  docker run -d --name gakei -p 127.0.0.1:8000:8000 \
    -v gakei-data:/data -v /srv/gakei-export:/export \
    ghcr.io/zolgear/gakei:latest
  # 保存先のフォルダ: /export
  ```

- 共有のファイルサーバーや同期されるフォルダに書くときは、サーバーにマウントしておく。

設定の項目:

| 項目 | 既定 | 内容 |
|---|---|---|
| 保存先のフォルダ(`folder`) | なし(必須) | サーバーの上の絶対パス。無ければ作る |
| ファイル名のひな形(`filename`) | `{date}/{time}_{run_id8}_{index}.{ext}` | 保存先からの相対パス。`/` でフォルダを分けられる |
| PNG に系列の情報を埋め込む(`embed_lineage`) | オン | ダウンロードと同じ PNG(ADR-0014)で書く。PNG でない原本はそのまま |
| Run の情報の JSON を横に置く(`write_json`) | オフ | 画像と同じ名前で拡張子が `.json` のファイルに、画像の情報、Run の情報、イベントの JSON を書く |
| 取り込みも保存する経路(`ingested_sources`) | 空 | `asset.ingested` で保存する経路をカンマ区切りで書く(例: `upload,url`)。空なら、生成の出力(`run.finished`)だけを保存する。`generated` を書くと、生成の出力を `run.finished` と二重に保存する |

ファイル名のひな形で使える置き換え:

| 置き換え | 値 |
|---|---|
| `{date}` | `2026-10-16`(イベントの時刻を GAKEI のサーバーのローカル時刻にしたもの。Docker のコンテナでは既定で UTC) |
| `{time}` | `153012`(時分秒) |
| `{year}` `{month}` `{day}` | `2026` `10` `16` |
| `{run_id}` `{run_id8}` | Run の id と、その先頭 8 文字(Run の無い取り込みでは空) |
| `{asset_id}` `{asset_id8}` | 画像(Asset)の id と、その先頭 8 文字 |
| `{sha256}` `{sha8}` | 画像の sha256 と、その先頭 8 文字 |
| `{index}` | Run の出力の番号(0 から)。取り込みでは 0 |
| `{ext}` | `png`、`jpg`、`webp`、`gif` |
| `{provider}` `{model}` | Run のプロバイダーとモデル(Run の無い取り込みでは空) |
| `{source}` | 取り込みの経路(`run.finished` では `generated`) |

書き方の約束:

- 置き換えの値は、英数字と `._-` 以外を `_` にし、先頭の `.` を落とす。値から `/` や `..` は作れない。
- ひな形が絶対パス、`..`、空のフォルダ名、Windows で使えない文字や名前(`:` `*` `?` `CON` など)を含むと失敗にする。組み立てたパスが保存先の外に出ても(シンボリックリンクを辿った先を含む)失敗にする。
- 同じ名前のファイルがあれば上書きせず、`_1`、`_2` … を付ける。中身が同じファイルが既にあれば書かない(送り直しで同じファイルを重ねて作らない)。
- 同じフォルダの一時ファイルに書いてから置き換える(書きかけのファイルを残さない)。
- 画像を残さなかった画像(5章)は書かない。
- 書けなかったとき(保存先が無い・書けない、ひな形が不正など)は、配達の失敗として残り、送り直される。管理者設定の「Mod」の配達の失敗で確かめる。
- 大きな画像をネットワークのフォルダに書くと、既定の待つ時間(10 秒)を超えることがある。超えるときは待つ時間を延ばす。
- この Mod は判定を返さない(マニフェストに `returns_verdicts = false`)ので、「判定を待つ」は選べない。書けなかったことが `deny` として記録され、画像を残さない扱いになることは無い。
- 「テスト送信」は、ダミーの画像(8×8 の灰色の PNG)を実際に保存先へ書く。書けるかどうかの確認に使える。

## 12. 運用の注意

- **イベントには、実行した人の情報(id、メール、表示名)と、プロンプトや画像が入る。** 外部のシステムに送ってよいかは組織の判断で、管理者が Mod を有効にすることで、その判断をしたことになる。送り先の取り扱い(保存期間、アクセスできる人)を確かめてから有効にする。
- `ai.before_send` を「止める」で使うと、検査のシステムが止まったときに、GAKEI の生成・自動タイトル・タグ・アシスタント・リモートの埋め込みも止まる。
- `ai.before_send` を自動タイトル・タグや埋め込みに掛けると、取り込みのたびに検査のシステムへ要求が飛ぶ。`purpose` の絞り込みで範囲を決める。
- 画像を残さない処理は取り消せない(5章)。外部のシステムの誤判定に備え、`deny` を返す条件は慎重に決める。
- Webhook の URL には、組織の中の受け口だけを登録する。鍵は推測できない長い値にし、漏れたら画面で差し替える。
