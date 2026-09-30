# ADR-0012: 個人がローカルで手軽にセルフホストできるようにする

**Status:** Proposed
**Date:** 2026-09-23

## Context

ソースの公開(ADR-0011)に向けて、ローカルMVP(ADR-0008)を ComfyUI や Stable Diffusion WebUI のように、個人が自分の PC で手軽に動かせる状態にしたい。今の導入手順には次の問題がある。

- `npm install` が peer dependency の衝突(`openapi-typescript` 7.13.0 が TypeScript 5 系を要求し、このリポジトリは 6 系)で止まる。
- uv、Node、`.env` の作成、フロントのビルド、サーバーの起動を、利用者が手順どおりに1つずつ実行する必要がある。
- `backend/.env.example` の既定が `PROVIDER=fake` なので、README のとおりキーだけを書くと、ダミー画像しか出ない。
- API キーは `.env` を手で編集しないと設定できない。キーがないと `PROVIDER=openai` のサーバーは起動しない。
- Windows での動作を確認していない。

## Decision

1. **導入は `git clone` と起動スクリプトで行う。** リポジトリ直下に `run.sh`(Linux)と `run.bat`(Windows)を置く。利用者の手順は「clone して起動スクリプトを実行する」だけにする。更新は `git pull` してから起動スクリプトを再び実行する。
2. **起動スクリプトは uv を用意し、その後の処理を Python のランチャーに任せる。**
   - uv がなければ、インストールしてよいか確認(`[Y/n]`)してから公式のインストーラーを実行する。Python 本体は uv が用意する。
   - ランチャー(`backend/app/launch.py`)は OS に依存しない処理を受け持つ: Node のバージョン確認、フロントのビルドが必要かの判定とビルド、サーバーの起動、ブラウザを開く。
3. **フロントは利用者の PC で Node を使ってビルドする。** Node.js 22.12 以上が必要(24 推奨)。ランチャーはフロントのソースとロックファイルのハッシュを `frontend/dist` に記録し、変わっていたときだけ `npm ci` と `npm run build` を実行する。Node がなくても、ビルド済みの dist が最新なら起動できる。
4. **API キーは画面の設定からも入力できる。**
   - 保存先はデータディレクトリの `secrets.json`。POSIX ではファイルの権限を 0600 にする。Windows ではユーザープロファイルのアクセス制御に任せる。
   - 環境変数と `.env` のキーは、画面で保存したキーより優先する。そのときは画面から変更できない。
   - キーをブラウザに返さない。設定済みかどうかと出どころだけを出し、キーの一部(末尾など)も出さない(2026-09-30 改訂。当初は末尾4文字を表示していた)。保存するときは、OpenAI の無料の API(モデル一覧)で有効かどうかを確かめる。拒否するのは認証エラー(401)のときだけで、権限不足(403)は受け入れる。画像の権限だけを付けた制限付きキーはモデル一覧を読めないが、キー自体は有効なため(2026-09-23 追記)。
   - キーがなくてもサーバーは起動する。キーがない状態で実行を作ろうとすると、Run を作らずにエラーを返す。
5. **サポートする OS は Windows と Linux。** GitHub Actions で、両方の OS でテスト、ビルド、ランチャーの起動確認を行う。macOS は動く見込みだが保証しない。
6. **npm の peer dependency の衝突は `package.json` の `overrides` で解消する。** フラグなしの `npm ci` と `npm install` が通るようにする。

ADR-0001 のゴールに「個人がローカルで手軽にセルフホストできる」を加える。本線(Azure、Entra ID、証跡)の方針は変えない。ローカル版は引き続き認証と証跡を持たず、既定では `127.0.0.1` だけで待ち受ける。

## Options Considered

| 配布の方法 | 評価 |
|---|---|
| A: `git clone` と起動スクリプト(採用) | ComfyUI や SD WebUI と同じで、利用者になじみがある。更新は `git pull` で済む |
| B: PyPI(`uvx gakei`) | 1行で起動できるが、Python パッケージ名の変更(`app` → `gakei`)と、PyPI への公開の運用が必要 |
| C: Docker | Docker Desktop が必要で、個人の PC では敷居が高い |

| フロントの届け方 | 評価 |
|---|---|
| A: ローカルで Node を使ってビルドする(採用) | 仕組みが単純。利用者に Node が必要 |
| B: GitHub Releases にビルド済みのものを置く | 利用者に Node は不要だが、リリースの CI とダウンロード処理が増える |
| C: dist をコミットする | clone だけで済むが、変更のたびに大きな差分が出る |

## Consequences

- 利用者に必要なのは Git と Node.js(22.12 以上)だけで、uv と Python は起動スクリプトが用意する。
- API キーがデータディレクトリに平文で保存される。データディレクトリの扱いに注意が要ることを README に書く。
- LAN に公開している(`HOST=0.0.0.0`)と、同じネットワークの誰でもキーを差し替えられる。キーの全文は読み出せない。
- Windows の CI が加わるので、パスの扱いやファイルのロックなど、Windows 固有の不具合が見つかる可能性がある。見つかったらこの ADR の範囲で直す。

## Action Items

1. [x] `run.sh`、`run.bat`、`backend/app/launch.py`
2. [x] API キーの設定 API(`/api/settings/openai-key`)と、キーがなくても起動する provider
3. [x] 設定画面と、キーが未設定のときの案内
4. [x] npm の `overrides`、ルートの `.env.example`
5. [x] GitHub Actions(Windows と Linux)
6. [x] README のクイックスタート
