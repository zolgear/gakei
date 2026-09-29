# PostgreSQL を使う(ADR-0027)

GAKEI は既定でメタデータを `DATA_DIR/gakei.db`(SQLite)に保存する。個人の PC ではこれで十分だが、複数人でサーバーに置く場合は、`DATABASE_URL` を指定して PostgreSQL に切り替えられる。

対象は **PostgreSQL 15 以上**(CI では 17 で確認している)。`DATABASE_URL` を指定しても、画像やアバターなどのファイルは引き続き `DATA_DIR` に置く(移らない)。

今回の対応はメタデータの DB を選べるようにするだけで、ジョブの実行は変わらない。**GAKEI のプロセス(コンテナ)は PostgreSQL を使う場合も1つだけ**にする。同じ DB に2つの GAKEI をつなぐと、進捗の配信(SSE)が届かなくなったり、起動時の「実行中のまま止まった Run を戻す」処理が他方の Run を壊したりする。

## Compose で使う

リポジトリの `compose.yaml` は PostgreSQL を同梱している。`.env` に `POSTGRES_PASSWORD` を指定するだけでよい(未指定だと起動しない)。パスワードは `DATABASE_URL` に埋め込まれるので、英数字だけにする(`@` `/` `:` `#` などの記号が入ると接続できない)。

```dotenv
POSTGRES_PASSWORD=好きなパスワード
```

```bash
docker compose up -d --build
```

SQLite のまま使いたい場合は、`compose.yaml` の中の「SQLite で使う場合」のコメントに従って、`postgres` サービス、`gakei` の `depends_on` と `DATABASE_URL`、`gakei-pg` ボリュームをコメントアウトする。

**既存の Compose 利用者への注意:** 更新後にそのまま `docker compose up` すると、空の PostgreSQL につながり、今までのデータ(履歴・世代情報)が見えなくなる。画像のファイル自体はボリューム `gakei-data` に残っていて消えない。下の「SQLite から PostgreSQL への移行」で移すか、上のコメントアウトで SQLite のまま使う。

## 外部の PostgreSQL に接続する

`run.sh` / `run.bat`、Docker いずれの場合も、`DATABASE_URL` を指定すればマネージドサービスなど外部の PostgreSQL につなげる。

```dotenv
DATABASE_URL=postgresql://user:pass@host:5432/gakei
```

`postgresql://` と `postgres://` はどちらも使える。ドライバは psycopg 3。接続できない場合は、分かる文言を出して起動を中止する。接続文字列はログや画面には出さない(出す場合もパスワードは伏せる)。

GHCR の公開イメージ(`ghcr.io/zolgear/gakei`)を `docker run` する場合も、`-e DATABASE_URL=...` を渡すだけでよい。

## SQLite から PostgreSQL への移行

既に SQLite で使っている GAKEI のデータを PostgreSQL に移すツールを用意している。

```bash
uv run python -m app.tools.migrate_to_postgres --to postgresql://user:pass@host/gakei [--from PATH] [--dry-run]
```

- 先に **GAKEI を止める**。動かしたまま移すと、移行中に書き込まれた分が抜ける。
- 読み込み元は既定で `DATA_DIR/gakei.db`。別の場所にある場合は `--from` で指定する。
- 読み込み元のスキーマが最新でないと中止する。先に今の版の GAKEI を一度起動すればマイグレーションが最新になる。
- **移行先は空の DB に限る。** GAKEI のテーブルが1つでもあれば中止する。移行先には Alembic で最新のスキーマを作ってから、全テーブルを外部キーの順にコピーし、最後に行数を突き合わせる。途中で失敗したら全体をロールバックする。
- `--dry-run` を付けると、書き込みはせず、両方に接続してテーブルごとの行数を表示するだけになる。
- **画像のファイル(`DATA_DIR`)は移さない。** 移行後も同じ `DATA_DIR` を使い続ける。
- **元の SQLite ファイルは消さない。** 戻したい場合は `.env` などから `DATABASE_URL` を外せば、元の SQLite の状態(移行時点)に戻る。移行後に PostgreSQL 側だけで増えたデータは戻らない。
- 逆(PostgreSQL → SQLite)のツールは用意していない。

### Docker での実行例

Compose でコンテナを起動したまま、一時的な別コンテナでツールを走らせる(GAKEI 本体は先に止める)。

```bash
docker compose stop gakei
docker compose run --rm gakei python -m app.tools.migrate_to_postgres \
  --from /data/gakei.db --to "$DATABASE_URL"
docker compose up -d gakei
```

`$DATABASE_URL` は `.env` の `POSTGRES_PASSWORD` を使って組み立てる(`postgresql://gakei:${POSTGRES_PASSWORD}@postgres:5432/gakei`)。`/data/gakei.db` はボリューム `gakei-data` の中の、これまで使っていた SQLite ファイル。

## バックアップ

- **SQLite の場合:** これまでどおり `DATA_DIR` をまるごと取ればよい。
- **PostgreSQL の場合:** DB は `pg_dump` などで別に取り、画像のために `DATA_DIR` も取る。**DB と `DATA_DIR` は対で戻す必要がある**(片方だけ古いものに戻すと、来歴の記録と実際のファイルが食い違う)。

Compose で同梱の PostgreSQL を使っている場合の例:

```bash
# DB
docker compose exec postgres pg_dump -U gakei gakei | gzip > gakei-db.sql.gz

# 画像・secrets.json など(DATA_DIR)
docker run --rm -v gakei-data:/data -v "$PWD":/backup busybox \
  tar czf /backup/gakei-data.tgz -C /data .
```

戻すときは、両方を同じタイミングで取ったものに揃える。

```bash
docker compose stop gakei
gunzip -c gakei-db.sql.gz | docker compose exec -T postgres psql -U gakei gakei
docker run --rm -v gakei-data:/data -v "$PWD":/backup busybox \
  sh -c "rm -rf /data/* && tar xzf /backup/gakei-data.tgz -C /data"
docker compose up -d gakei
```
