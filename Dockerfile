# ADR-0016: サーバーに置きたい人向けの追加の配布手段。個人の PC 向けの主な導入方法は
# 引き続き ADR-0012 の起動スクリプト(run.sh / run.bat)。
# ADR-0021: このイメージは GHCR にも公開する。$BUILDPLATFORM、GAKEI_COMMIT、
# OCI ラベル、LICENSE / NOTICE の同梱はそのための追加。

# --- 1段目: フロントのビルド(Node) -----------------------------------------
# frontend/dist を作るためだけに使う。実行イメージには Node を含めない。
# ADR-0021: --platform=$BUILDPLATFORM でビルド元(≒ CI ランナー)のアーキテクチャで
# 常に1回だけ動かす。QEMU 上で npm ci / vite build を動かすと数倍遅く、
# 成果物(dist)は実行段のアーキテクチャに依らず同じものが使える。
FROM --platform=$BUILDPLATFORM node:24-bookworm-slim AS frontend-build
WORKDIR /src/frontend

# 依存関係だけ先にコピーしてレイヤーキャッシュを効かせる。
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# --- 2段目: バックエンド(Python + uv) ---------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# ADR-0021: リリースのワークフローがコミットの SHA を渡す(未指定なら空文字のまま
# 環境変数になり、app 側は空文字を null として扱う)。
ARG GAKEI_COMMIT=""

# OCI のイメージラベル。version と revision はビルドのたびに変わるので、
# `docker build` 単体では付けず、release.yml の docker/metadata-action が付ける。
LABEL org.opencontainers.image.title="GAKEI" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/zolgear/gakei"

# uv 本体だけをコピーする(uv のインストーラーは使わない)。バージョンは
# 手元で `uv --version` を確認して固定している。
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/backend/.venv/bin:${PATH}" \
    GAKEI_COMMIT=${GAKEI_COMMIT}

WORKDIR /app/backend

# 依存関係の定義だけ先にコピーし、`uv sync` をレイヤーキャッシュに乗せる
# (バックエンドのソースを変えただけでは依存関係の再インストールが走らない)。
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# バックエンド本体をコピーしてから、プロジェクト自体を sync する
# (`[tool.uv] package = false` なのでパッケージのビルドは発生しない)。
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./alembic.ini
RUN uv sync --frozen --no-dev

# フロントのビルド成果物をリポジトリと同じ配置(backend/ の1つ上の frontend/dist)に置く。
# app/main.py のパス解決(_FRONTEND_DIST = backend の親 / frontend / dist)を変えないため。
COPY --from=frontend-build /src/frontend/dist /app/frontend/dist

# ADR-0011: 配布物には LICENSE と NOTICE を添える(.dockerignore の *.md には
# 当たらない、拡張子なしのファイル)。
COPY LICENSE NOTICE /app/

# root 以外のユーザーで動かす。DATA_DIR(既定 /data)はこのユーザーが書き込めるようにする。
RUN groupadd --gid 10001 gakei \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin gakei \
    && mkdir -p /data \
    && chown -R gakei:gakei /data /app
USER gakei

ENV HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/data

VOLUME ["/data"]
EXPOSE 8000

# capabilities は認証もキーも要らない、最も軽い生存確認用エンドポイント。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/capabilities', timeout=3)"

# ランチャー(run.sh/run.bat)ではなく `python -m app` を直接使う。
# ビルド判定・uv 用意・ブラウザ起動はイメージの中では不要なため。
CMD ["python", "-m", "app"]
