# ADR-0021: リリース(バージョンとタグ)とコンテナイメージの公開

**Status:** Proposed
**Date:** 2026-09-27

## Context

- ADR-0011 でソースを Apache-2.0 で公開することを決め、公開の準備を進めている。公開後は「どの時点のものを使っているか」を利用者と開発者が同じ言葉で指せる必要がある(不具合の報告、更新の案内、ADR の「取り込み済み」の目印)。今はバージョンもタグもなく、`backend/pyproject.toml` の `0.1.0` と `frontend/package.json` の `0.0.0` は何も指していない。
- ADR-0016 では Docker イメージをレジストリに公開せず、利用者が clone してビルドする方針にした。Decision 6 で「公開は OSS 公開の後に改めて検討する」としていた。イメージを公開すれば、サーバーに置く人は clone も Node も uv も要らなくなり、`docker run` 一行で動かせる。
- イメージを配布物として出すには、同梱する第三者のライブラリのライセンス表記が要る(ADR-0011 Consequences、Action Item 4)。Python のホイールはライセンス文書を `dist-info` に含めたままイメージに入るが、フロントのバンドル(`frontend/dist`)にはライブラリのライセンス文書が入らない。
- 公開用のリポジトリは新しく作る(ADR-0011 Decision 2)ので、リリースの仕組みは private リポジトリのうちに用意して試し、公開後は最初のタグを打つだけで動く状態にしておきたい。

## Decision

### 1. バージョンとタグ

- **バージョンは SemVer。** ローカルMVPの間は `0.y.z` で、`0.y` を上げるのは利用者に見える機能の追加や互換性のない変更(設定名や DB の意味が変わるとき)、`z` は修正と小さな改善。`1.0.0` にするのは本線(Azure)に着手したときではなく、「ローカルMVPとして完成」とユーザーが判断したとき。
- **バージョンの正は `backend/pyproject.toml` の `version` の1か所。** `frontend/package.json` の `version` は使わない(`"private": true` で npm に公開しないパッケージなので `0.0.0` のまま)。サーバーは起動時に `pyproject.toml` を読み、画面と API に出す(3章)。
- **タグは `vX.Y.Z`**(先頭に `v`)。プレリリースは `vX.Y.Z-rc.N`。タグは main のコミットにだけ打つ。
- **ブランチは `dev` と `main` の2本**(2026-09-27 追記)。開発は `dev` に PR で集め、リリースのときだけ `dev` → `main` の PR を入れる。`main` は常にリリース済みの状態を指し、既定ブランチも `main`(利用者が clone して手にするのはリリース済みの状態。PR の向き先は `dev` を明示する)。両方とも PR 経由のみ・CI 必須・force push 禁止をルールセットで強制し、`v*` タグは削除と上書きを禁止する。
- **リリースの手順:** (1) `dev` で `pyproject.toml` のバージョンを上げる PR を作り、マージする。(2) `dev` → `main` の PR をマージする。(3) main で `git tag vX.Y.Z && git push origin vX.Y.Z`。(4) 以降は自動(2章)。手順は `docs/release.md` に書く。
- **リリースノートは GitHub の自動生成**(マージされた PR の一覧)を使い、手で書かない。書きたいことがあれば、GitHub Release の本文を後から編集する。
- CHANGELOG ファイルは作らない。変更の経緯は ADR と GitHub Release で追う。

### 2. リリースの自動化(GitHub Actions `release.yml`)

`v*` のタグが push されたら動く。手順は次のとおりで、途中で失敗したらそこで止まる。

1. **タグとバージョンの一致を確かめる。** `pyproject.toml` の `version` が `X.Y.Z` で、タグが `vX.Y.Z` でなければ失敗する(タグの打ち間違いと、バージョンを上げ忘れたタグを防ぐ)。プレリリース `vX.Y.Z-rc.N` は「これから出す `X.Y.Z` の候補」なので、`-` の前の `vX.Y.Z` だけを比べる(`pyproject.toml` は PEP 440 で `-rc.N` の形を書けず、候補のたびに版を書き換えたくもない)。
2. **イメージを `linux/amd64` で作り、起動確認する。** CI の docker ジョブと同じく `FAKE_PROVIDER=1` で起動し、`/api/capabilities` と `/`(`<title>GAKEI</title>`)を確かめる。push より前に行い、壊れたイメージを公開しない。(2026-09-30 追記、Issue #43: 起動待ちはログイン不要の生存確認 `GET /api/health` で行い、その後に `/api/capabilities` と `/` を確かめる。イメージの `HEALTHCHECK` も `/api/health` を叩く。`/api/capabilities` は oidc モードではログインが要り、ヘルスチェックが 401 で unhealthy になるため)
3. **`linux/amd64` と `linux/arm64` のマルチアーキテクチャでビルドして GHCR に push する。** イメージ名は `ghcr.io/<owner>/gakei`(`github.repository` から取る。public リポジトリでは `ghcr.io/zolgear/gakei`)。
   - タグ: `X.Y.Z`、`X.Y`、`latest`。プレリリース(`-rc.N` など)には `X.Y.Z-rc.N` だけを付け、`X.Y` と `latest` は動かさない。
   - OCI のラベル(`org.opencontainers.image.source` / `version` / `revision` / `licenses`)を付ける。`source` により GHCR のパッケージがリポジトリに紐づき、README がパッケージのページに出る。
   - フロントのビルド段(Node)は `--platform=$BUILDPLATFORM` でビルド元のアーキテクチャで1回だけ動かし、成果物の `dist` を両方の実行段にコピーする(QEMU の上で `npm ci` と `vite build` を動かすと数倍遅く、結果は同じ)。
   - `GAKEI_COMMIT`(コミットの SHA)をビルド引数で渡し、イメージの環境変数にする(3章)。
4. **第三者ライセンス表記(4章)をイメージから取り出す。**
5. **GitHub Release を作る。** 本文は自動生成のリリースノート。添付は第三者ライセンス表記の1ファイルだけ。タグに `-` が含まれていれば Pre-release にする。バイナリは配布しない(利用者は clone するか、イメージを pull する)。

その他:

- ワークフローの権限は `contents: write`(Release の作成)と `packages: write`(GHCR)だけ。`GITHUB_TOKEN` で足り、個人のトークンや secret は要らない。
- GHCR のパッケージの公開範囲はリポジトリに従う。public リポジトリの Actions から push すれば自動で public になる(2026-09-27 の `v0.1.0` で確認。当初は手で切り替える想定だったが不要だった)。private リポジトリから push した場合だけ、設定画面で手で public にする(`docs/release.md`)。
- private リポジトリのうちに `v0.1.0-rc.1` のようなプレリリースのタグで一度動かして確かめる。その際に作られたパッケージと Release は、公開リポジトリでの最初のリリースより前に削除する(名前が同じ `ghcr.io/zolgear/gakei` になり、旧リポジトリに紐づいたパッケージが残ると混乱する)。
- CI(`ci.yml`)の docker ジョブは変えない(単一アーキテクチャのビルドと起動確認のまま)。マルチアーキテクチャのビルドは時間がかかるので、リリース時だけ行う。

### 3. バージョンと第三者ライセンス表記を画面と API に出す

- **`GET /api/about`** → `{ "version": "0.1.0", "commit": "abc1234..." | null }`。`version` は `pyproject.toml` から、`commit` は環境変数 `GAKEI_COMMIT` から(未設定なら `null`。起動スクリプトで動かすときは null になる。git を呼んで補うことはしない)。他の API と同じくログインが要る(ADR-0019)。
- **設定画面の「GAKEI について」**(ADR-0011)に、著作権表示の下にバージョン(と commit の先頭 7 文字があれば括弧で)を出し、第三者ライセンス表記へのリンクを足す。
- **`GET /api/about/third-party-notices`** → `text/plain; charset=utf-8` の一覧(4章)。

### 4. 第三者ライセンス表記(ADR-0011 Action Item 4)

同梱する第三者のライブラリの名前、バージョン、ライセンス、URL と、ライセンス文書の本文を1つのテキストにまとめる。生成は2段に分ける。

- **フロント:** `frontend/scripts/third-party-notices.mjs` を `npm run build` の最後に走らせ、`frontend/dist/third-party-notices.txt` を作る。対象は `package-lock.json` の `packages` のうち `dev: true` でないもの(ルートを除く)。ライセンス名は各パッケージの `package.json` の `license`、本文は `node_modules/<name>/` の `LICENSE*` / `LICENCE*` / `COPYING*`(無ければ「本文なし」と書く)。`dist` に入るので、起動スクリプトで動かす場合も Docker でも同じ場所(`/third-party-notices.txt`)にある。
- **サーバー:** `app/domain/third_party.py` が、起動している Python 環境の配布物(`importlib.metadata`)から名前、バージョン、ライセンス(`License-Expression` → `License` → `Classifier: License ::` の順)、URL、`License-File` の本文を集め、フロントの `third-party-notices.txt` を末尾に連結する。開発用の依存(pytest、ruff など)は Docker イメージには入らないが、起動スクリプトで動かす環境には入る。一覧に載っても害はないので、除外の仕組みは作らない。
- **配布物への添付:** `python -m app.tools.third_party_notices <出力パス>`(`-` で標準出力)で同じ内容を書き出す。リリースのワークフローは、作ったイメージでこれを実行して `THIRD_PARTY_NOTICES.txt` を取り出し、GitHub Release に添付する。イメージの中では API(3章)で読める。
- GAKEI 自身の LICENSE と NOTICE はイメージの `/app/` にコピーする。

### 5. `compose.yaml` と README

- **`compose.yaml` は変えない**(clone してビルドする人向けのまま)。`image:` を GHCR の名前にすると、`--build` で作ったローカルのイメージが公開イメージと同じ名前になり、`pull` と `up --build` の使い分けで混乱する。
- README の Docker の節に、**clone せずに公開イメージで起動する例**(`docker run` と、その compose 相当)を先に置き、clone してビルドする手順を「自分で変更を加えたい場合」として後ろに回す。更新は `docker compose pull && docker compose up -d`(公開イメージ)または `git pull && docker compose up -d --build`(自前ビルド)。
- README にバージョンとリリースの見つけ方(GitHub の Releases、設定画面の「GAKEI について」)を1行足す。

## Options Considered

| バージョンの正 | 評価 |
|---|---|
| A: `backend/pyproject.toml` の1か所(採用) | 変更は1か所。Python 側は `tomllib` で読むだけ。フロントは API から受け取る |
| B: `pyproject.toml` と `package.json` の両方を同期 | 2か所を揃えるテストが要る。`package.json` の版は誰も使わない |
| C: タグから導出(ビルド時に埋め込む) | 起動スクリプトで動かす場合はタグがローカルに無いことがある(zip でダウンロードした場合など)。ファイルに書いてあるほうが確実 |

| 第三者ライセンス表記の作り方 | 評価 |
|---|---|
| A: ビルド時に生成し、API で配信、Release に添付(採用) | 依存の変更に自動で追随する。`license-checker` などの追加ツールは要らない(`package-lock.json` と `importlib.metadata` で足りる) |
| B: 手で書いた一覧をリポジトリに置く | 依存を足したときに更新を忘れる。ADR-0011 で一度作って捨てた経緯(2026-09-25)と同じ問題 |
| C: 何もしない(Python の dist-info だけ) | フロントのバンドルの表記が無く、Apache-2.0 / MIT の条件を満たさない |

| `compose.yaml` の `image:` | 評価 |
|---|---|
| A: ローカルビルドの名前のまま(採用) | 既存の手順が変わらない。公開イメージは README の `docker run` / compose 例で案内する |
| B: GHCR の名前にして `build:` も残す | `up -d` はイメージが無ければビルドし、あれば使うので、どちらが動いているか分かりにくい |

## Trade-off Analysis

- 公開イメージにより導入は楽になるが、リリースのたびに ~10 分のビルド(arm64 の Python 依存は QEMU 上で `uv sync` が動く。ホイールがあるので実行はほぼコピーだけ)が走る。頻度はタグの数だけなので受け入れる。
- `latest` を動かす運用は、`docker compose pull` で予告なく新しい版に上がることを意味する。ローカルMVPは起動時に DB を自動で `upgrade head` する(ADR-0008)ので、上がった先で戻すには DB のバックアップが要る。README のバックアップの案内に「更新の前に」と添える。
- 第三者ライセンス表記は自動生成なので、`package.json` に `license` が無いパッケージや、ライセンス文書を同梱しないパッケージは「不明」「本文なし」と出る。それ自体を隠さず、生成物を見て気になれば個別に対応する。

## Consequences

- 利用者は `ghcr.io/zolgear/gakei:latest`(または `0.y`)を pull するだけでサーバーに置ける。
- ADR-0016 Decision 6(イメージを公開しない)はこの ADR で置き換える。ADR-0011 Action Item 4(第三者のライセンス表記)は 4章で満たす。
- 依存が増えたときに何もしなくても表記に載る。
- `docs/release.md` にリリースの手順(バージョンを上げる PR → タグ → 確認 → 失敗したときの対処、GHCR を public にする手順、private での予行演習)を置く。
- 開発者向けの環境(`run.sh`)でも設定画面にバージョンが出るので、不具合の報告に版を書いてもらえる。
- (2026-09-30 追記、Issue #43)イメージの `HEALTHCHECK` と CI の起動待ちは `GET /api/health`(ログイン不要。`{"status":"ok"}` だけを返す)を使う。`AUTH_MODE=oidc` でもコンテナが healthy になる。

## Action Items

1. [x] `GET /api/about`、`GET /api/about/third-party-notices`、`app/version.py`、`app/domain/third_party.py`、`app.tools.third_party_notices`
2. [x] `frontend/scripts/third-party-notices.mjs` と `npm run build` への組み込み、「GAKEI について」にバージョンと表記へのリンク
3. [x] `Dockerfile`(`$BUILDPLATFORM`、`GAKEI_COMMIT`、LICENSE / NOTICE のコピー、OCI ラベル)、`.github/workflows/release.yml`
4. [x] `docs/release.md`、README(日英)の Docker の節、`docs/configuration.md`(`GAKEI_COMMIT` は利用者が設定するものではないので載せない)、CLAUDE.md、ADR-0016 / ADR-0011 への追記
5. [x] private リポジトリで `v0.1.0-rc.1` を打ってワークフローを一度動かし、パッケージと Release を消す(2026-09-27。同日に public で `v0.1.0` をリリース)
