# ADR-0027: メタデータの DB に PostgreSQL も選べるようにする

**Status:** Proposed
**Date:** 2026-09-29

2026-09-29 にユーザーが次の3点を決めた。

- 今回は DB を選べるようにするだけにする。ジョブの実行は今と同じく api プロセスの中で行い、コンテナは1つのまま。worker の分離と複数台の構成は別の Issue にする。
- 今の SQLite のデータを PostgreSQL に移すツールを用意する。
- Docker Compose のファイルは1つのまま、PostgreSQL を同梱する構成に変える。コメントアウトで SQLite に戻せるようにする(当初は別ファイルを足す案だったが、同日ユーザーが1ファイルにすると決めた)。

文字列の列の扱い(2章)を足したうえで、同日、この ADR の内容がユーザーに承認された。

## Context

ローカルMVP(ADR-0008)は、メタデータを `DATA_DIR/gakei.db`(SQLite、WAL)に保存する。個人の PC ではこれで十分だが、複数人でサーバーに置く運用(Docker、ADR-0016。OIDC 認証、ADR-0019)では、次の点で PostgreSQL が欲しくなる。

- 管理者が使い慣れた道具(`pg_dump`、マネージドサービスの自動バックアップ)でバックアップを取れる。
- 同時に書き込む人が増えても、ロックの待ちが DB 全体に掛からない。
- 本線の設計(ADR-0002)は PostgreSQL 前提で、先にアプリが両方で動くことを確かめておける。

コードは当初から両対応を意識して書いてある(`Uuid` 型、tz 付き日時の `UtcDateTime`、マイグレーションの `batch_alter_table`)。SQLite に依存しているのは、接続の作り方(`db.py`、`main.py`、`backfill_embedded_meta`)と、検索で使う `json_extract`(`domain/search.py`)くらいである。

Issue [#4](https://github.com/zolgear/gakei/issues/4)。

## Decision

### 1. 接続先の指定

- 環境変数 `DATABASE_URL` を足す。未指定なら、これまでどおり `DATA_DIR/gakei.db`(SQLite)を使う。個人の PC(`run.sh` / `run.bat`)の既定は変えない。
- PostgreSQL は `postgresql://user:pass@host:5432/gakei` の形で指定する。ドライバは psycopg 3(`psycopg[binary]`)で、`postgresql://` と `postgres://` は内部で `postgresql+psycopg://` に読み替える。
- PostgreSQL のバージョンは 15 以上を対象にする(CI は 17 で回す)。
- `DATABASE_URL` を使う場合でも `DATA_DIR` は要る。画像(`assets/`、`derived/`)、アバター、`secrets.json`、ONNX のモデルは、これまでどおり `DATA_DIR` に置く。
- 接続文字列はログや画面に出さない。出す場合はパスワードを伏せる(`render_as_string(hide_password=True)`)。
- 起動時に PostgreSQL に接続できなければ、分かる文言(日本語と英語)を出して起動を中止する。
- 設定画面からは変えない(`.env` と環境変数だけ)。DB を変えるとデータの置き場所が変わるため。

### 2. 型と書き方

- JSON の列は、PostgreSQL では `JSONB` にする。モデルでは `JSON().with_variant(JSONB(), "postgresql")` の型を1つ定義して全列で使い、マイグレーション `0020` で PostgreSQL の既存の列を `JSONB` に変える(SQLite では何もしない)。`json` 型は等値比較ができず、`DISTINCT` や重複の確認で失敗するため。
- 文字列の列は、次のように分ける。PostgreSQL は `VARCHAR(n)` を超える値を拒むが、SQLite は長さを無視して通す。これまで SQLite で実質「長さ制限なし」で動いてきたため、利用者や外部から来る値で PostgreSQL だけが失敗することを避ける。
  - **利用者や外部から来る文字列**(タイトル、タグ、名前、プロンプト、エラー文言、メールアドレス、ワークフロー名など)は `TEXT` にする。モデルでは長さのない `String` / `Text` にし、PostgreSQL の既存の列はマイグレーション `0020` で `TEXT` に変える(SQLite では何もしない)。長さの上限が要るものは、API の入力検証(Pydantic の `max_length`)で弾く。
  - **コードが決める値**(`status`、`kind`、`role`、`origin`、sha256 など)は `VARCHAR(n)` のままにする。長さが決まっているので、超えたらバグとして気づけるほうがよい。
  - PostgreSQL の `TEXT` と `VARCHAR(n)` は格納も性能も同じで、違いは長さの検査だけである。索引と一意制約もそのまま張れる。
- 検索の `json_extract` は、SQLAlchemy の `Asset.embedded_meta["prompt"].as_string()` に書き換える。SQLite では `json_extract`、PostgreSQL では `->>` になり、1つの書き方で両方に通る。
- マイグレーション 0001〜0019 は書き換えない。PostgreSQL でもそのまま通ることをテストで確かめる。
- 次の違いは、実装のときに全体を見直して揃える。
  - **失敗後のトランザクション:** PostgreSQL では、一意制約違反などの後はロールバックするまで同じトランザクションを使えない。例外を捕まえて続ける箇所は、`begin_nested()`(SAVEPOINT)で囲む。
  - **並び順:** `NULL` の位置(SQLite は昇順で先頭、PostgreSQL は末尾)と文字列の照合順が違う。画面に出る並びが変わる箇所は、`nulls_last()` などで明示するか、同順位の場合の並べ方を id などで決める。
  - **大文字小文字:** 部分一致は今の `lower(...) LIKE ... ESCAPE '\'` のままにする(両方で同じ結果になる)。
- **NUL 文字(2026-10-01 追記):** PostgreSQL は NUL(`\u0000`)を含む文字列を `TEXT` / `JSONB` に保存できず、問い合わせの引数にあっても失敗する(SQLite は通す)。両方の DB で同じ振る舞いにするため、入口で次のように揃える(共通の関数は `domain/text_safety.py`)。
  - **利用者のリクエスト**(REST の本文・パス・クエリ・フォーム、MCP のツール引数)は、どこかの文字列(入れ子の値と dict のキーを含む)に NUL があれば 422(MCP はツールのエラー)で拒む。黙って消すと、利用者が送ったものと違う値が保存される(ComfyUI のテンプレートは書き換えない。ADR-0013)。REST はアプリ全体の依存(`api/request_text.py`)で全ルートに掛け、個々のスキーマには書かない。
  - **外部から来るテキスト**(アップロードした画像の埋め込みメタ情報、プロバイダーの応答とエラー、自動タイトル・タグの推定結果とエラー、IdP のクレーム)は、NUL を取り除き、UTF-8 にできない文字(対になっていないサロゲート)を置き換えてから保存する。拒むと Run の記録や取り込みが失敗するため。

### 3. ジョブの実行

今回は変えない。runner は api プロセスの中で動き、`status = 'queued'` の行を選んでから、`WHERE status = 'queued'` を付けて `running` に更新し、取れなければ諦める。この取り方は PostgreSQL でも正しい。`SELECT ... FOR UPDATE SKIP LOCKED` と `LISTEN/NOTIFY`(ADR-0005)は、worker を分けるときに入れる。

そのため、PostgreSQL を使う場合でも、GAKEI のプロセス(コンテナ)は1つだけにする。同じ DB に2つの GAKEI をつなぐと、SSE の通知が届かず、起動時の「実行中のまま止まった Run を戻す」処理が他方の Run を壊す。この制約は `docs/` に書く。

### 4. SQLite から PostgreSQL への移行ツール

```bash
uv run python -m app.tools.migrate_to_postgres --to postgresql://user:pass@host/gakei [--dry-run]
```

- 読み込み元は、`DATA_DIR/gakei.db`(`--from` で変えられる)。GAKEI を止めてから使う。
- 移行先は空の DB に限る(GAKEI のテーブルが1つでもあれば中止する)。移行先には Alembic で最新のスキーマを作ってから、全テーブルを外部キーの順にコピーし、最後に行数を突き合わせる。途中で失敗したら全体をロールバックする。
- 読み込み元のスキーマが最新でなければ中止する(先に今の版の GAKEI を1回起動すれば最新になる)。
- 画像のファイル(`DATA_DIR`)は移さない。移行後も同じ `DATA_DIR` を使う。
- `--dry-run` は、両方に接続して、テーブルごとの行数を表示するだけにする。
- 元の SQLite ファイルは消さない。戻したい場合は `DATABASE_URL` を外せば元の状態で動く(移行後に PostgreSQL 側で増えたデータは戻らない)。
- PostgreSQL から SQLite へ戻すツールは作らない。

### 5. Docker Compose

- `compose.yaml` を、`postgres:17` のサービス(名前付きボリューム `gakei-pg`、ヘルスチェック付き)を同梱する構成に変える。GAKEI には `DATABASE_URL` を設定し、PostgreSQL が起動してから立ち上がるようにする(`depends_on` の `condition: service_healthy`)。
- SQLite で使いたい場合は、PostgreSQL のサービス、`depends_on`、`DATABASE_URL` の行をコメントアウトする。どこを外せばよいかをファイルの中のコメントに書く。
- DB のパスワードは `.env` の `POSTGRES_PASSWORD` で指定する。未指定なら起動しない(Compose の `${POSTGRES_PASSWORD:?...}`)。
- PostgreSQL のポートはホストに公開しない。
- **これまで Compose で使ってきた人への影響:** 更新後にそのまま `docker compose up` すると、空の PostgreSQL につながり、今までのデータが見えなくなる(SQLite のファイルはボリューム `gakei-data` に残っていて消えない)。移行ツール(4章)で移すか、コメントアウトで SQLite のまま使う。リポジトリの Compose をそのまま使っている利用者はほぼいないと見込み、この案内はリリースノートに書くだけにする(同日のユーザーの判断)。
- GHCR のイメージを使う README の `docker run` の例は、SQLite のまま変えない。外部の PostgreSQL につなぐ場合は `DATABASE_URL` を渡すだけでよい。

### 6. テストと CI

- テストは、環境変数 `GAKEI_TEST_DATABASE_URL`(PostgreSQL のサーバーへの接続)があれば、テストごとに一時的な DB を作って PostgreSQL で走る。無ければこれまでどおり SQLite。既定の `uv run pytest` は変わらない。
- CI に PostgreSQL のジョブを足す(Ubuntu、`services` で `postgres:17`、PR でも走らせる)。バックエンドのテストを全部 PostgreSQL で回す。
- マイグレーションのテストは、両方の DB で `upgrade head` と `downgrade` を通す。

### 7. バックアップ

- SQLite: これまでどおり `DATA_DIR` をまるごと取る。
- PostgreSQL: DB は `pg_dump` などで取り、画像のために `DATA_DIR` も取る。DB と `DATA_DIR` は対で戻す必要がある。`docs/` に例を書く。

## Options Considered

### 範囲

| 案 | 評価 |
|---|---|
| A: DB だけ選べるようにする(採用) | 変更が接続・型・移行ツール・CI に収まる。今の1コンテナの構成のまま使える |
| B: worker の分離(`SKIP LOCKED`、`LISTEN/NOTIFY`)まで | 複数台にできるが、SSE の配信と runner の作りを変える必要があり、規模が大きい。別の Issue にする |

### ドライバ

| 案 | 評価 |
|---|---|
| A: psycopg 3(採用) | 同期の SQLAlchemy(`db.py` の方針)でそのまま使える。Linux(arm64 を含む)、Windows、macOS 向けのビルド済みパッケージがある |
| B: psycopg2 | 保守中心で、新規には勧められていない |
| C: asyncpg | 非同期専用で、今の同期セッションに合わない |

### 移行ツール

| 案 | 評価 |
|---|---|
| A: GAKEI のモデルを使って行をコピーするツール(採用) | 型の変換(UUID、日時、JSON)を SQLAlchemy に任せられる。スキーマは Alembic で作るので、手で作った DB と同じになる |
| B: 外部ツール(pgloader など)の手順だけ書く | 型の対応を利用者が調べる必要がある。Alembic の版の記録も別に要る |
| C: 用意しない | サーバーで SQLite のまま使ってきた人が移れない。Compose の既定を PostgreSQL にするので、特に要る |

## Trade-off Analysis

PostgreSQL を使う人はサーバーに置く人に限られ、個人の PC の既定(SQLite、設定不要)は崩したくない。そのため `DATABASE_URL` が無ければ何も変わらない形にし、違いは接続の作り方と、両方で通る書き方に閉じ込める。worker を分けないので、PostgreSQL にしても同時に動かせる GAKEI は1つのままだが、バックアップと同時書き込みの利点はこれだけで得られる。

## Consequences

- 依存に `psycopg[binary]` が加わる(個人の PC で使わなくても入る)。
- テストを両方の DB で回すので、CI の時間が延びる(PostgreSQL のジョブは Ubuntu の1つだけ)。
- 以後のマイグレーションは、両方の DB で通すことが CI で確かめられる。SQLite だけで通る書き方は入らなくなる。
- Compose の既定の DB が SQLite から PostgreSQL に変わる。既存の利用者には移行かコメントアウトが要る(5章。リリースノートで案内する)。
- ADR-0008 の対比表の DB の行、ADR-0016 の「サービスは1つ」「DB は SQLite なので」の記述に、この ADR への注記を足す。
- ADR-0002 の「PostgreSQL」は、本線に先立ってセルフホスト版でも選べるようになる。本線の JSONB、`SKIP LOCKED`、`LISTEN/NOTIFY` のうち、今回入るのは JSONB だけ。

## Action Items

1. [ ] `DATABASE_URL` と接続の作り方(SQLite の PRAGMA は SQLite のときだけ)、起動時の接続確認
2. [ ] JSON 型の `JSONB` と、利用者や外部から来る文字列の `TEXT` への切り替え(モデルとマイグレーション 0020)、検索の `json_extract` の書き換え
3. [ ] `TEXT` にした列の入力検証の漏れ、失敗後のトランザクション、並び順の見直し
4. [ ] 移行ツール `app.tools.migrate_to_postgres`
5. [ ] `compose.yaml` への PostgreSQL の同梱(コメントアウトで SQLite)。既存の利用者向けの案内はリリースノートに書く
6. [ ] テストの DB の切り替え(`GAKEI_TEST_DATABASE_URL`)と CI の PostgreSQL ジョブ
7. [ ] `docs/configuration.md`、`docs/`(PostgreSQL の使い方、バックアップ、移行)、README、CLAUDE.md、ADR-0008 / 0016 への注記
