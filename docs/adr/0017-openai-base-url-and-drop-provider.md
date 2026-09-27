# ADR-0017: OpenAI の接続先(Base URL)を変えられるようにし、`PROVIDER` 設定を廃止する

**Status:** Proposed
**Date:** 2026-09-26

## Context

- **`PROVIDER` の役割が小さくなった。** ADR-0013 でプロバイダーの登録簿を導入し、OpenAI と ComfyUI を同時に使えるようになった。`PROVIDER` が今も決めているのは、主プロバイダーを `openai` にするか `fake` にするかだけである。`fake` はダミー画像を返す開発・テスト用の実装で、利用者が選ぶものではない。それなのに `PROVIDER` は利用者向けの設定一覧(`docs/configuration.md`、`.env.example`、README)に載っている。
- **OpenAI 互換のプロキシを経由したい。** LiteLLM などのプロキシを使うと、キーの一元管理、利用量の集計、利用者ごとの上限を組織側で持てる。今は接続先が OpenAI 本体に固定されていて、プロキシを使えない。

## Decision

### 1. `PROVIDER` を廃止し、`fake` は開発用のフラグに格下げする

- OpenAI を常に主プロバイダーにする(登録簿のキーは `openai`)。
- 環境変数 `FAKE_PROVIDER=1` を指定したときだけ、OpenAI の代わりに `fake` を主プロバイダーとして登録する(登録簿のキーは `fake`。Run の `provider` 列にも `fake` と記録されるので、ダミーの Run と区別できる)。このフラグは開発・CI・確認用で、利用者向けの設定一覧(`docs/configuration.md`、`.env.example`、README のクイックスタート)には載せない。載せるのは README の「開発」節と CLAUDE.md だけにする。
- 自動テストは環境変数を経由せず、`create_app` に設定を渡して `fake` を使う(`conftest.py` の既存のフィクスチャを置き換える)。
- **移行の安全策:** `PROVIDER` が設定されたまま起動した場合、
  - `PROVIDER=openai`: 無視して警告を出す(従来と同じ動作)。
  - それ以外(`fake` など): 起動を中止し、`FAKE_PROVIDER=1` を使うよう案内する。`PROVIDER=fake` のつもりで実 API に課金される事故を防ぐため、黙って OpenAI で起動しない。

### 2. OpenAI の Base URL を設定できるようにする

- 環境変数 `OPENAI_BASE_URL` と、設定画面の両方で指定できる。扱いは API キー(ADR-0012 Decision 4)と同じにする。
  - 優先順位: 環境変数 / `.env` > 画面で保存した値 > 未設定(OpenAI 本体)。
  - 環境変数で指定している間は、画面から変更・削除できない(409)。
  - 画面で保存した値は、キーと同じ `DATA_DIR/secrets.json` に入れる(接続先の組み合わせを1か所で持つ。社内のホスト名を含みうるので、キーと同じ権限で守る)。
- 値は OpenAI SDK の `base_url` にそのまま渡す(通常は `/v1` まで含む。例: `http://127.0.0.1:4000/v1`)。受け付けるのは `http` / `https` で、ユーザー情報(`user:pass@`)、クエリー、フラグメントを含むものは拒否する。末尾の `/` は取り除く。
- 画面から Base URL を保存するときは、有効なキーがあれば、その URL に対してキーの確認(モデル一覧。ADR-0012 と同じく 401 だけを拒否)を行う。キーがなければ形式の確認だけで保存する。逆に、キーを保存するときの確認は、その時点で有効な Base URL に対して行う。
- 画面では、Base URL は全文を表示する(秘密ではない)。
- ループバック以外への `http`(TLS なし)を指定した場合は、起動時と保存時に警告をログに出す。キーと画像が平文で流れるため(ComfyUI の警告と同じ扱い。ADR-0013)。
- Run には Base URL を記録しない(キーも記録していない。ADR-0003 の `params` は API に送った値で、接続先は含まない)。
- プロキシ側のモデル名は、GAKEI が送るモデル名(`openai_spec.py` の定義)と同じにしておく必要がある。モデル名の読み替え(エイリアス)は GAKEI では行わない。料金の目安(pricing)は OpenAI の価格のままで、プロキシ経由では実際の請求と一致しないことがある。

### 3. 画面

- 設定画面の OpenAI の節に、API キーと並べて「接続先(Base URL)」を置く。空なら OpenAI 本体を使う旨を示す。環境変数で指定されているときは、キーと同じく読み取り専用で出所を示す。
- 文言は ADR-0015 に従い、日本語と英語の JSON に置く。

## Options Considered

| `fake` の残し方 | 評価 |
|---|---|
| A: 開発用フラグ `FAKE_PROVIDER=1`(採用) | 利用者向けの設定から消せる。実キーなしで起動して画面を確認する手段と CI の起動確認は残る |
| B: テスト専用にする | 最も単純だが、実キーなしでサーバーを起動して確認する手段がなくなる |
| C: OpenAI 互換のダミーサーバーを作り、Base URL で向ける | 本物の経路を通せるが、ストリーミングや部分画像まで模倣する必要があり、作業量が大きい |

| Base URL の設定場所 | 評価 |
|---|---|
| A: 環境変数と設定画面(採用) | キーと同じ扱いで一貫する。Docker(ADR-0016)でも `.env` を触らずに設定できる |
| B: 環境変数だけ | 変更は小さいが、キーだけ画面で設定できて接続先は `.env`、という分かりにくさが残る |

## Consequences

- `docs/configuration.md`、`.env.example`、README から `PROVIDER` が消え、`OPENAI_BASE_URL` が加わる。
- CLAUDE.md、`docs/localization.md`、CI の `PROVIDER=fake` は `FAKE_PROVIDER=1` に置き換わる。ADR-0016 の Docker の CI ジョブ(PR #24)も、マージ後に同じく置き換える。
- ADR-0013 の「主プロバイダー(設定 `PROVIDER`)」は、「主プロバイダー(OpenAI。`FAKE_PROVIDER=1` のときは fake)」と読み替える。ADR-0013 の本文は変えず、この ADR で上書きする。
- プロキシを経由すると、キーとプロンプトと画像がプロキシに渡る。プロキシの信頼性は利用者の責任になる。
- 本線(Azure OpenAI。ADR-0005)の接続先の扱いとは別の話で、本線の方針は変えない。

## Action Items

1. [x] `PROVIDER` の廃止、`FAKE_PROVIDER` の導入、起動時の移行チェック
2. [x] テスト(`conftest.py`)の fake の指定を環境変数から設定の注入に変える
3. [x] `OPENAI_BASE_URL` と `secrets.json` への保存、設定 API、キーの確認で Base URL を使う
4. [x] 設定画面(日本語と英語)
5. [x] ドキュメント(`docs/configuration.md`、`.env.example`、README、`docs/localization.md`、CLAUDE.md)と CI
