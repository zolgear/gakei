# ローカライズ

GAKEI は日本語で開発している。翻訳しているのは利用者の目に触れる部分(画面、サーバーが返すメッセージ、起動スクリプトの表示、README)だけで、コード中のコメントや設計資料は日本語のまま。いま対応している言語は日本語(`ja`)と英語(`en`)。

## 仕組み

文言は言語ごとに1つの JSON に置く。日本語の JSON が正で、他の言語はこれと同じキー構造にそろえる。

| 対象 | 置き場所 | 引き方 |
|---|---|---|
| 画面 | `frontend/src/i18n/locales/{ja,en}.json` | コンポーネントでは `useI18n()` の `t`、React の外では `msg()`。差し込みは `fmt()` |
| サーバー | `backend/app/locales/{ja,en}.json` | `t("area.key", name=...)`。ランチャーは `console_t()` |
| `run.sh` / `run.bat` | スクリプトの中 | Python が動く前に出す数行だけ、日英を並べて書く |
| `.env.example` | リポジトリ直下 | 英語が正(`.env.example`)で、日本語版は `.env.example.ja`。設定行(`KEY=`)の集合と順序を同じにそろえる |

- JSON は画面やモジュールの領域ごとに入れ子のキーで分ける。キーは領域と意味で名付ける(例: `runs.notFound`)。
- 差し込みは `{count}` のような名前付きの置き場所で書く。
- 数で文言が変わるもの(英語の単数・複数)は `{"one": "...", "other": "..."}` にし、`count` で選ぶ。日本語も同じ形にそろえる(2つとも同じ文でよい)。
- 条件で文そのものが変わるものは、キーを分けてコードの側で選ぶ。

言語の選び方:

- **画面:** 設定画面で選んだ言語(localStorage の `gakei.locale`)。未選択ならブラウザの言語が `ja` で始まれば日本語、それ以外は英語。
- **サーバー:** リクエストの `Accept-Language`。画面はすべての API 呼び出しで、選んだ言語を明示して送る。ヘッダーがないとき(`curl` など)は日本語。
- **ランチャー:** OS のロケール(`LC_ALL`、`LC_MESSAGES`、`LANG` の順に見て、`ja` で始まれば日本語)。

翻訳しないもの:

- 利用者が入力した文字列(プロンプト、プロンプトセット、ワークフロー名など)。
- 実行の記録に残る `error_message`。記録した時点の言語(サーバーの既定である日本語)のまま残る。画面は `error_code` が分かるものを選んだ言語で表示し、`error_message` は詳細として添える。

## 文言を足す・直す

1. 日本語と英語の両方の JSON に、同じキーで書く。
2. コードからキーで引く。文言をコードに直接書かない。
3. テストを通す。

```bash
cd frontend && npm test && npm run build    # tsc が英語のキーの欠けを検出する
cd backend && uv run pytest -q tests/test_i18n.py
```

テストでは次のことを確かめている。

- 日本語と英語でキー構造が一致すること(フロントは `tsc -b` でも落ちる)。
- 置き場所(`{name}`)の名前が言語間で一致すること。
- 英語の JSON に日本語の文字が混ざっていないこと。
- サーバーのコードに書いたキーが、両方の JSON にあること。

英語は日本語より長くなりやすい。画面の幅は日本語を前提に調整しているので、文言を足したら英語でも表示を確かめる。確認には課金のない `FAKE_PROVIDER=1` で起動し、設定画面で言語を切り替える。

## 言語を追加する

言語コードを `xx` とする。今の実装は2言語を前提にした箇所があるので、辞書を置くだけでは切り替わらない。

**画面(`frontend/src/i18n/`)**

1. `locales/en.json` を `locales/xx.json` にコピーして翻訳する。
2. `locale.ts` の `Locale` 型、`LOCALES`、`isLocale()` に `xx` を加える。
3. `locale.ts` の `LOCALE_LABELS` に、その言語と日本語・英語のどちらかを併記した表示名を加える(例: `'Français (French)'`)。読めない言語に切り替えてしまっても戻せるように、切り替え欄は辞書を使わない。
4. `locale.ts` の `detectLocale()` を、ブラウザの言語から `xx` を選べるように直す。
5. `index.ts` で JSON を import し、英語と同じく `Messages` 型を付けてから `DICTIONARIES` に加える(型を付けるとキーの欠けを `tsc` が検出する)。`intlLocale()` に日時・数値の書式で使うロケール名(例: `fr-FR`)を加える。
6. `locales.test.ts` のキー構造と置き場所の比較に `xx` を加える。

**サーバー(`backend/app/`)**

7. `locales/en.json` を `locales/xx.json` にコピーして翻訳する。
8. `i18n.py` の `Locale` 型と `parse_accept_language()` に `xx` を加える。
9. ランチャーの表示も翻訳する場合は、`i18n.py` の `console_t()` のロケール判定と、`run.sh` / `run.bat` の `msg` を直す。
10. `tests/test_i18n.py` の比較と `Accept-Language` のテストに `xx` を加える。

**確認**

```bash
cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .
cd frontend && npm test && npm run lint && npm run build
```

最後に `FAKE_PROVIDER=1` で起動し、設定画面で `xx` に切り替えて、フォーム、履歴、エラー表示、設定画面を一通り確かめる。README を翻訳する場合は `README.xx.md` として置き、各 README の先頭の言語リンクに加える。
