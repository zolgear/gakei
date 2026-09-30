# ADR-0019: OIDC によるユーザー認証と Admin ロール(個人モードは既定のまま)

**Status:** Proposed
**Date:** 2026-09-27

## Context

- ローカルMVP(ADR-0008)は認証を持たず、既定で `127.0.0.1` だけを待ち受けることで代替してきた。個人が自分の PC で使う分(ADR-0012)にはこれで足りるが、Docker でサーバーに置く(ADR-0016)、あるいは LAN に出すと、同じネットワークの誰でも画像を生成し、API キーを差し替えられる。README は「認証付きのリバースプロキシを前段に置く」よう求めているが、それでも「誰が実行したか」(ADR-0001 ゴール3)は残らない。
- ADR-0006(`docs/adr/0006-auth-and-audit.md`)は、Azure の Easy Auth が付けるヘッダー(`X-MS-CLIENT-PRINCIPAL`)を信用する前提で、Azure 以外のセルフホスト(ADR-0011、0012、0016)では使えない。
- 利用者から、OIDC(例として Keycloak)でのログインと、Admin ロールによる設定の権限分けが要望された。あわせて、設定画面を「管理者が決めるもの」と「利用者ごとに変えてよいもの」に分けたい。

## Decision

### 1. 認証モードは `.env` で切り替え、既定は個人モード

- `AUTH_MODE=none`(既定): これまでどおり認証なし。暗黙の利用者1人が管理者として扱われ、設定はすべて操作できる。
- `AUTH_MODE=oidc`: OIDC の発行者(Keycloak、Entra ID など OpenID Connect Discovery に対応するもの)でログインしないと `/api/*` を使えない。SPA の `index.html` と `/static/*` は公開のままで、画面側がログイン画面を出す。
- oidc モードで `OIDC_ISSUER`、`OIDC_CLIENT_ID`、`PUBLIC_BASE_URL` のいずれかが無ければ起動を中止する(ADR-0017 の `PROVIDER` の移行安全策と同じ扱い)。Discovery 文書は初回ログイン時に取得してキャッシュし、IdP が落ちていても起動はできる。
- 設定項目は `docs/configuration.md` に載せる: `AUTH_MODE`、`OIDC_ISSUER`、`OIDC_CLIENT_ID`、`OIDC_CLIENT_SECRET`(空なら public client)、`OIDC_SCOPES`(既定 `openid profile email`)、`PUBLIC_BASE_URL`、`AUTH_ADMIN_EMAILS`、`AUTH_SESSION_HOURS`(既定 12)、`AUTH_SECRET`(未指定なら生成して `DATA_DIR/secrets.json` に保存)。

### 2. サーバー側で Authorization Code + PKCE を行い、セッションは Cookie で持つ(BFF)

- ログインはサーバーが仲介する: `GET /api/auth/login` → IdP → `GET /api/auth/callback`。コードの交換と ID トークンの検証(署名、`iss`、`aud`、`nonce`、期限)は Authlib に任せ、自前でトークン検証を書かない(ADR-0006 の Trade-off「トークン検証の実装ミスが起きない」を踏襲)。
- ログイン後は **サーバー側セッション** を `auth_session` 表に持ち、ブラウザには HttpOnly の Cookie `gakei_session` にランダムなトークンだけを渡す(DB にはそのハッシュを置く)。ログアウトで即時失効し、期限(`AUTH_SESSION_HOURS`)を過ぎたものは次のログイン時に掃除する。**同じ利用者が複数の端末で同時にログインできる。1ユーザーあたり最大 10 セッション(2026-09-28 改訂)。** ログインしても、別のブラウザや端末のセッションは消さない。callback で新しいセッションを作る直前に、同じ `app_user.id` のセッションが 10 件以上あれば、古い順に消して新しいものを含め 10 件に収める。
  - 2026-09-27 には、セキュリティ監査の指摘 I-3 を受けて「1ユーザー1セッション」にしていた。再ログインしても、別の端末に残った古いセッションが使われ続けることを防ぐためだった。
  - 2026-09-28、別の端末でログインすると既存のログインが切れることが問題になり、ユーザーの判断で改めた。残ったセッションの懸念は、有効期限(`AUTH_SESSION_HOURS`、既定 12 時間)と件数の上限で抑える。
- Cookie を使う理由: 画像の配信(`<img src="/api/assets/{id}/content">`)と進捗の SSE(`EventSource`)はリクエストヘッダーを付けられない。Bearer トークンでは、この2つの経路(ADR-0004 の配信方針)が通らない。
- Cookie の属性: `HttpOnly`、`SameSite=Lax`、`Path=/`。`Secure` は `PUBLIC_BASE_URL` が `https` のときだけ付ける(ローカルの `http://127.0.0.1` でも試せるように)。
- Authlib が state / nonce / code_verifier を一時的に置く場所として、Starlette の `SessionMiddleware`(署名付き Cookie `gakei_oidc`、有効 10 分)を oidc モードのときだけ追加する。この Cookie はログイン手続き中しか使わず、ログイン済みセッションとは別物。`SessionMiddleware` は純粋 ASGI で本文をバッファしないため、SSE に影響しない(`BaseHTTPMiddleware` を避ける方針は `main.py` の `LocaleMiddleware` と同じ)。
- CSRF: Cookie が `SameSite=Lax` で、更新系の呼び出しはすべて `fetch`(JSON か multipart)なので、別途トークンは導入しない。
- ログアウトは `POST /api/auth/logout`。セッションを消したうえで、IdP の `end_session_endpoint` に `post_logout_redirect_uri` と `client_id` を付けた URL を返し、画面がそこへ遷移する(Keycloak は `id_token_hint` が無くても `client_id` で受ける。ID トークンはサーバーに保存しない)。`end_session_endpoint` が無い IdP では `/` に戻るだけにする。

### 3. ロールは `user` と `admin` の2つ。Admin は `.env` のメール一覧で決める

- `AUTH_ADMIN_EMAILS`(カンマ区切り、大文字小文字は無視)に載っているメールアドレスのユーザーが `admin`、それ以外は `user`。
- ログインのたびに再評価して `app_user.role` を更新する(表示用。ログイン一覧・Run の実行者表示などが使う)。
- **一覧の変更は次のリクエストから効く(セッションを消さなくてよい。2026-09-27 追記)。** API の認可判定(`require_user`/`require_admin`)は、DB に保存済みの `app_user.role` ではなく、Cookie のセッションからユーザーを引くたびに `AUTH_ADMIN_EMAILS` と `AUTH_ALLOWED_EMAIL_DOMAINS` の現在値で再評価する(`auth/sessions.py::find_user_for_token`)。管理者一覧を変える、あるいは許可ドメインからメールアドレスが外れると、サーバー再起動後の次のリクエストから反映される(ログインし直す必要もセッションを消す必要もない)。許可ドメインから外れた人は、有効なセッションを持っていても未ログイン扱い(401)にする(セキュリティ監査の指摘 L-3)。
- **ログインできる人の範囲は `AUTH_ALLOWED_EMAIL_DOMAINS`(メールのドメインの一覧)で絞る(2026-09-27 追記)。** 空なら IdP が認証した人は誰でもログインできる。Keycloak のように IdP 側で利用者を管理する場合は空でよいが、Google のように誰でもアカウントを持てる IdP では必ず指定する(指定しないと、世界中の誰でも `user` としてログインし、組織のキーで画像を生成できる)。`AUTH_ADMIN_EMAILS` の人は常に許す。ドメイン外の人は `app_user` を作らずに断り、ログイン画面に理由を出す。
- IdP のロールやグループのクレームは使わない。IdP ごとにクレームの置き方が違い(Keycloak の `realm_access.roles`、Entra ID の `roles`)、マッパーの設定が要る。メール一覧なら IdP 側の設定が不要で、`.env` を見れば誰が管理者か分かる。
- `app_user` 表: `id`、`issuer`、`subject`(この2つで一意)、`email`、`name`、`role`、`created_at`、`last_login_at`。初回ログイン時に作る(ADR-0006 と同じ)。メールが取れないユーザーはログインを断る(400)。
- **`email_verified` クレームが明示的に `false` のメールアドレスは信用しない(M-1、2026-09-27 追記)。** IdP によってはメール変更直後など未確認のメールアドレスをそのまま `email` クレームに載せることがあり、これを信用すると、確認していない(=本人が所有していない可能性がある)メールアドレスで `AUTH_ADMIN_EMAILS` や `AUTH_ALLOWED_EMAIL_DOMAINS` の判定・アカウント作成をしてしまう(権限昇格の余地)。そのため `oidc.py::complete_login` は `email_verified is False` のとき `email` を落とし、ログインをメール未設定と同様に断る(専用の文言 `email_unverified` を出す)。クレーム自体を返さない IdP(`email_verified` が無い)は、これまでどおり通す。
- テーブル名は `user` にしない(PostgreSQL の予約語。本線 ADR-0002)。既存の `app_setting` に合わせて `app_user` とする。

### 4. Run と Asset に実行者を記録する

- `run.created_by_user_id`、`asset.created_by_user_id`(どちらも NULL 可、`app_user.id` への参照)を追加する。ADR-0008 で「後から列を足せる」としていた `created_by` に当たる。
- Run は作成時に、アップロード・マスク・スケッチはアップロード時に記録する。生成された出力 Asset は runner が作るので、その Run の実行者を引き継ぐ。
- none モードでは常に NULL。既存のデータも NULL のまま(埋め戻さない)。
- API は `RunSummary` / `RunDetail` / `AssetDetail` に `created_by: {id, name, email} | null` を添える。画面は Run 詳細と履歴に実行者名を出す。
- ~~閲覧範囲は全員が全件(単一組織向け。ADR-0006 の Action Item 1「メンバー制か全員閲覧か」を、ローカル版では「全員閲覧」で進める)。~~ **2026-09-28 に ADR-0025 で改めた: 認証モードでは本人が作ったものだけが見える(管理者も同じ。実行者が記録されていない個人モードの頃のデータは管理者だけ)。他人のものは 404。** プロジェクトやメンバー管理は作らない。
- ~~**削除・キャンセル・プロンプトセットの編集も全員可(所有者チェックはしない。2026-09-27 追記)。**~~ 一般ユーザーが他人の Run・Asset を削除・キャンセルしたり、他人のプロンプトセットを編集したりできた(セキュリティ監査の指摘 I-6)。**2026-09-28 に ADR-0025 で改め、削除・取り消し・再実行・編集は本人のものだけにした(他人のものは存在しないものと同じ 404)。**

### 5. 設定を「ユーザー設定」と「管理者設定」に分ける

| 区分 | 項目 | 保存先 | サーバー側の制限 |
|---|---|---|---|
| ユーザー設定 | 表示言語、表示(ファビコンの進捗、生成画面の入力欄の配置) | ブラウザ(localStorage) | なし(サーバーを経由しない) |
| 管理者設定 | OpenAI の API キーと接続先(Base URL)、生成(moderation)、ComfyUI(接続、タイムアウト、ワークフローの登録) | `secrets.json` / `app_setting` / `comfy_workflow` | 更新系は `admin` のみ(403) |

- 管理者に限定するエンドポイント: `PUT/DELETE /api/settings/openai-key`、`PUT/DELETE /api/settings/openai-base-url`、`PATCH /api/settings/general`、`PUT/DELETE /api/comfyui/connection`、`POST /api/comfyui/connection/test`、`POST /api/comfyui/workflows/analyze`、`POST/PATCH/DELETE /api/comfyui/workflows*`。
- 参照系の GET は、ログインしていれば誰でも呼べる(キー未設定のバナー、モデル選択、ワークフローの一覧が使うため)。`GET /api/settings/openai-key` はキーの一部も返さない(設定済みかどうかと出どころだけ。2026-09-30 に、非管理者にだけ伏せていた末尾4文字の `hint` を管理者にも返さないよう改めた。ADR-0012)。
- 画面では、設定ページを「ユーザー設定」「管理者設定」の見出しで括り、管理者設定は `admin`(none モードでは常に)にだけ表示する。ワークフローの登録画面(`/settings/comfyui*`)も同様。非管理者にはキー未設定の案内を「管理者に連絡」に変える。
- ADR-0013 7章の「本線では、この設定は `admin` だけが変えられるようにする」は、この ADR で実装したことになる。
- 設定のアイコンは歯車にする(これまでの円と8本の線は太陽に見えた)。

### 6. 認可の掛け方

- `/api/auth/*` 以外の既存ルーターすべてに、ルーター単位の依存(`require_user`)を付ける(例外として `GET /api/health`(`{"status":"ok"}` だけを返す生存確認。2026-09-30 追記、Issue #43))。oidc モードで未ログインなら 401、管理者限定のエンドポイントで `user` なら 403。本文は `Accept-Language` に従って日本語か英語(ADR-0015)。
- none モードでは `require_user` が暗黙の管理者を返すので、既存の動作は変わらない。

## Options Considered

### 認証の実装場所

| 案 | 評価 |
|---|---|
| A: サーバーで Code + PKCE、Cookie セッション(採用) | `<img>` と SSE がそのまま通る。トークンがブラウザの JS から見えない。Starlette のセッション Cookie が1つ増える |
| B: SPA で PKCE、API は Bearer を検証 | 画像と SSE にトークンを渡す手段がない(クエリーに載せるとログと履歴に残る)。ADR-0006 の案 B と同じ欠点 |
| C: 前段のリバースプロキシ(oauth2-proxy 等)のヘッダーを信用 | ADR-0006 の案 A のセルフホスト版。アプリのコードは最小だが、部品が増え、Docker の Compose(ADR-0016)を1サービスに保てない。プロキシの設定ミスでヘッダーを偽装される |

Azure 本線(ADR-0006、0007)では Easy Auth が前段に付くが、Entra ID も OIDC の発行者なので、この ADR の実装はそのまま Entra ID でも使える。Easy Auth のヘッダーを読む実装は、本線に着手するときに必要なら足す(この ADR は ADR-0006 を置き換えない)。

### トークンの検証

| 案 | 評価 |
|---|---|
| A: Authlib(採用) | Discovery、PKCE、ID トークンの検証、JWKS のキャッシュを持つ。Starlette 向けの統合がある |
| B: httpx + PyJWT で自作 | 依存は軽いが、検証の抜け(`aud`、`nonce`、鍵のローテーション)を自分で背負う |

### Admin の決め方

| 案 | 評価 |
|---|---|
| A: `.env` のメール一覧(採用) | IdP の設定が不要。誰が管理者か `.env` で分かる。変更にはログインし直しが要る。ログインできる範囲も同じ発想でメールのドメイン一覧にする |
| B: IdP のロール/グループのクレーム | IdP 側で管理できるが、クレームの置き方が IdP ごとに違い、マッパーの設定手順を IdP ごとに書く必要がある |
| C: 最初にログインした人を管理者 | 設定不要だが、共有環境で最初にログインした人が誰かに依存する |

### セッションの持ち方

| 案 | 評価 |
|---|---|
| A: サーバー側セッション(`auth_session` 表)(採用) | ログアウトで即時失効できる。表が1つ増える |
| B: 署名付き Cookie だけ | 表は増えないが、期限までは失効させられない |

## Trade-off Analysis

決め手は「画像と SSE を Cookie で通す」こと。ここが崩れると ADR-0004 の配信方針(base64 を本文に載せない、API 経由のストリーミング)を変えることになる。Authlib と Starlette のセッション Cookie は、その代償として増える部品で、oidc モードのときだけ有効にするので、個人モードの利用者には見えない。

Admin の判定をメール一覧にしたのは、IdP ごとの手順を最小にするためで、組織の規模が大きくなってロールを IdP で管理したくなったら、この ADR を更新してクレームを読む(`.env` のメール一覧は残しても両立できる)。

## Consequences

- 依存が2つ増える(`authlib`、`itsdangerous`)。
- 個人モードの利用者には変化がない(設定画面の見出しと歯車のアイコンを除く)。
- 認証を有効にした環境では、Run 詳細と履歴に実行者が出る。以前のデータの実行者は空のまま。
- ADR-0006 の `audit_event`(Run に現れない操作の記録)はまだ作らない。アップロードと削除は Run と Asset の列で追える範囲に留まる。
- README と `compose.yaml` の「認証がない」という記述は「既定では認証がない。`AUTH_MODE=oidc` で有効にできる」に改める。LAN に出すときは、認証を有効にするか、これまでどおり認証付きのリバースプロキシを前段に置く。
- ADR-0013 7章の「本線では admin だけが変えられる」と、ADR-0008 の「MVP で作らないもの」のうち `created_by` は、この ADR で実装済みになる。
- `docs/auth.md` に Google(すぐ試せる)と Keycloak(組織で運用する)の手順を書く。Google には `end_session_endpoint` が無いので、ログアウトは GAKEI のセッションを消すだけになる(Google 側にはログインしたまま)。Entra ID は発行者 URL の例だけ添える(実機で確認していない)。

## Action Items

1. [ ] 設定(`AUTH_MODE` ほか)、`app_user` / `auth_session` の表、`created_by_user_id` の列とマイグレーション
2. [ ] `backend/app/auth/`(セッション、Authlib のラッパー、`require_user` / `require_admin`)と `/api/auth/*`
3. [ ] 管理者限定のエンドポイント、実行者の記録、`created_by` の応答
4. [ ] 画面(ログイン画面、ユーザーメニュー、設定の分類、歯車のアイコン、実行者の表示。日本語と英語)
5. [ ] `docs/configuration.md`、`docs/auth.md`(Keycloak)、`.env.example`、README、CLAUDE.md
6. [ ] Keycloak の実機で、ログイン、管理者と一般ユーザーの違い、SSE と画像、ログアウトを確認する
