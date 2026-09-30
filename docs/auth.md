# 認証(OIDC)の設定

GAKEI は既定では認証を持たず、同じ PC からだけ開ける(`HOST=127.0.0.1`)。複数人で使うときは、`.env` で `AUTH_MODE=oidc` にすると OIDC(OpenID Connect)の発行者でログインするようになる(ADR-0019)。OpenID Connect Discovery に対応する発行者なら使える。ここでは、すぐ試せる **Google** と、組織で利用者を管理する **Keycloak** の手順を書く。

## 仕組み

- ログインはサーバーが仲介する(Authorization Code + PKCE)。ブラウザは `/api/auth/login` から IdP へ移り、`/api/auth/callback` に戻ってくる。
- ログイン後はサーバー側にセッションを持ち、ブラウザには HttpOnly の Cookie(`gakei_session`)だけを渡す。画像の表示と進捗の配信(SSE)もこの Cookie で通る。
- ロールは `user` と `admin` の2つ。`AUTH_ADMIN_EMAILS` に載せたメールアドレスの人が `admin` になり、API キーや ComfyUI の接続など「管理者設定」を変えられる。それ以外の人は生成と閲覧はできるが、管理者設定は変えられない。
- **ログインできる人の範囲は `AUTH_ALLOWED_EMAIL_DOMAINS` で絞る。** 空だと、IdP が認証した人は誰でもログインできる。Keycloak のように IdP 側で利用者を管理するなら空でよいが、Google のように誰でもアカウントを持てる IdP では必ず指定する(指定しないと、世界中の誰でも `user` としてログインし、組織のキーで画像を生成できる)。`AUTH_ADMIN_EMAILS` の人は常にログインできる。
- 生成した Run とアップロードした画像に、誰が実行したかが記録され、履歴と Run 詳細に表示される。

## 閲覧範囲(誰に何が見えるか)

認証モードでは、**本人が作ったものだけ**が見え、操作できる(ADR-0025)。管理者も同じで、他人の Run や画像は見られない。

- 対象は Run(履歴、Run 詳細、取り消し、削除、再実行)、画像(ストック、ビューア、原本・プレビュー・サムネイルの配信、系列グラフ、比較、削除)、グループ、プロンプトセット、検索、進捗の配信(SSE)と App バーの実行中・待機中の件数、MCP の全ツール。
- 他人のものの URL を開いても「見つからない」(404)になる。存在するかどうかも分からない。
- 生成の入力画像・マスク・出力先のグループには、自分のものだけを指定できる。
- 他人がダウンロードした画像を受け取ってアップロードすると、元の画像とは別の、自分の画像として取り込まれる(元の画像は見えない。埋め込まれていた系列情報は「このインスタンスの別の Asset(表示できません)」と出る)。
- 個人モード(`AUTH_MODE=none`)の頃に作ったデータは実行者が記録されていないので、認証モードにした後は **管理者だけ** が見られる。
- 共有(他人に見せる)機能は無い。見せたい画像はダウンロードして渡す。
- 個人モードでは、これまでどおり全部が見える。

## Google での手順(すぐ試す)

### 1. OAuth クライアントを作る

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを選ぶ(無ければ作る)。
2. **APIs & Services → OAuth consent screen**(「Google Auth Platform」と表示される場合もある)で同意画面を設定する。個人で試すなら User type は **External** のまま、公開状態を **Testing** にして、**Test users** に自分の Google アカウントを追加する(Testing のままだと、ここに載せた人しかログインできない)。
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**
   - Authorized redirect URIs: `{PUBLIC_BASE_URL}/api/auth/callback`(例: `http://127.0.0.1:8000/api/auth/callback`。Google はループバックの `http` を受け付ける)
   - 「承認済みの JavaScript 生成元」は空でよい(GAKEI はサーバー側でコードを交換するので使わない)。
   - 作成すると **Client ID** と **Client secret** が表示される。

### 2. GAKEI の `.env`

```dotenv
AUTH_MODE=oidc
OIDC_ISSUER=https://accounts.google.com
OIDC_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
OIDC_CLIENT_SECRET=GOCSPX-...
PUBLIC_BASE_URL=http://127.0.0.1:8000
AUTH_ADMIN_EMAILS=you@gmail.com
AUTH_ALLOWED_EMAIL_DOMAINS=example.co.jp
```

- `AUTH_ALLOWED_EMAIL_DOMAINS` には組織のドメインを書く(Google Workspace なら会社のドメイン)。個人の Gmail で試すだけなら、`AUTH_ADMIN_EMAILS` に自分を載せておけば、この項目は空でも(同意画面が Testing の間は)テストユーザー以外はログインできない。
- 起動して `PUBLIC_BASE_URL` を開くと、ログイン画面が出る(`run.sh` / `run.bat` が開くのは `http://127.0.0.1:8000/` なので、`PUBLIC_BASE_URL` と redirect URI もこれに揃えてある。下の「`PUBLIC_BASE_URL` について」も参照)。
- Google には `end_session_endpoint` が無いので、GAKEI のログアウトは GAKEI のセッションを消すだけで、Google 側にはログインしたまま(次に「ログイン」を押すとアカウント選択に進む)。

## Keycloak での手順(組織で運用する)

### 1. Keycloak を起動する(ローカルで試す場合)

```bash
docker run --rm -p 8080:8080 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  quay.io/keycloak/keycloak:26.2 start-dev
```

`http://localhost:8080` の管理コンソールに `admin` / `admin` で入る(この `http://localhost:8080` はローカルの確認用。本番では https にする)。

### 2. レルムとクライアントを作る

1. **レルム**を作る(例: `gakei`)。
2. **Clients → Create client**:
   - Client type: `OpenID Connect`
   - Client ID: `gakei`
   - Client authentication: 組織で使うなら **On**(confidential client。Credentials タブのシークレットを `OIDC_CLIENT_SECRET` に書く)。**Off** にすると public client になり、シークレットなし(PKCE のみ)で動く。
   - Authentication flow: `Standard flow` だけ On
   - Valid redirect URIs: `{PUBLIC_BASE_URL}/api/auth/callback`(例: `http://127.0.0.1:8000/api/auth/callback`)
   - Valid post logout redirect URIs: `{PUBLIC_BASE_URL}/*`
   - Web origins: `+`
3. **Realm settings → Login** で **Verify email** を **ON** にする(GAKEI はメールで利用者と管理者、許可ドメインを判定するため、確認済みでないメールアドレスは信用しない。ON にすると、ユーザーは初回ログイン時にメールでの確認を求められる)。あわせて、Account Console からユーザー自身がメールアドレスを変更できないようにしておく(確認済みの状態のまま別のメールアドレスに差し替えられると、上記の判定が意味を失う)。
4. **Users** でユーザーを作る。**Email を必ず入れる**(GAKEI はメールで利用者と管理者を区別する。メールの無いユーザーはログインを断られる)。Credentials タブでパスワードを設定する。

### 3. GAKEI の `.env`

```dotenv
AUTH_MODE=oidc
OIDC_ISSUER=http://localhost:8080/realms/gakei
OIDC_CLIENT_ID=gakei
OIDC_CLIENT_SECRET=<Credentials タブの値。public client なら空>
PUBLIC_BASE_URL=http://127.0.0.1:8000
AUTH_ADMIN_EMAILS=you@example.com
```

各項目の意味は [configuration.md](configuration.md)。Keycloak では利用者をレルムで管理するので `AUTH_ALLOWED_EMAIL_DOMAINS` は空でよい。

## `PUBLIC_BASE_URL` について

`PUBLIC_BASE_URL` は「利用者がブラウザで開く URL」で、IdP に登録した redirect URI と一致している必要がある。`run.sh` / `run.bat`(と `python -m app`)は `http://{HOST}:{PORT}/`、既定では `http://127.0.0.1:8000/` をブラウザで開くので、ローカルで試すときはこの形(`localhost` ではなく `127.0.0.1`)に揃える。`localhost` で開きたい場合は、`PUBLIC_BASE_URL` と IdP の redirect URI の両方を `localhost` にする(ブラウザの URL、`PUBLIC_BASE_URL`、redirect URI の3つが一致していればどちらでもよい)。リバースプロキシの後ろに置くなら、プロキシの外側の URL(`https://gakei.example.com`)を書く。`https` のとき、セッション Cookie に `Secure` が付く。

## 確認

- 管理者(`AUTH_ADMIN_EMAILS` に載せた人)でログインすると、設定画面に「管理者設定」(API キー、生成、ComfyUI)が出る。
- 他のユーザーでログインすると「管理者設定」は出ず、API を直接叩いても 403 になる。
- 右上のユーザーメニューからログアウトすると、IdP のログアウト(あれば)を経て GAKEI に戻る。

### 認可コードとログ(I-8、2026-09-27 追記)

`/api/auth/callback?code=...` の `code`(認可コード)は uvicorn のアクセスログに残る。この `code` は1回限りで、かつ PKCE(`code_verifier`)に縛られているため、ログを見られただけで悪用できるものではない(低リスク)。気になる場合は、リバースプロキシ側でこのパスのクエリー文字列をログから除くか、uvicorn のアクセスログ自体を切る。

## Docker(compose)で使う

リポジトリ直下の `.env` は `compose.yaml` が読むので、上と同じ項目を書けばよい。`PUBLIC_BASE_URL` はホスト側で開く URL(`GAKEI_BIND` / `GAKEI_PORT` で決まる。例: `http://192.168.1.10:8000`)にする。Google のような外部の IdP ならこれだけでよい。ホストで動かす Keycloak を使う場合は、コンテナの中からも、ブラウザからも、同じ名前で IdP に届く必要がある(`localhost` はコンテナの中では別物になるので、ホストの IP アドレス `http://192.168.1.10:8080/realms/gakei` などにする)。

## Entra ID

Entra ID も OIDC 発行者なので、アプリ登録(Web プラットフォーム、リダイレクト URI `{PUBLIC_BASE_URL}/api/auth/callback`、クライアントシークレット)を作れば同じ設定で使える見込み。

```dotenv
OIDC_ISSUER=https://login.microsoftonline.com/<テナント ID>/v2.0
```

`email` クレームはアプリ登録の「トークン構成」で追加する必要がある場合がある。実機での確認はまだ行っていない。

## 開発時(Vite の開発サーバー)

`npm run dev` の画面(Vite の既定で `http://localhost:5173`)は `/api` をバックエンドへプロキシする。ログインの戻り先もこの経由になるので、`PUBLIC_BASE_URL=http://localhost:5173` にし、IdP の redirect URI も `http://localhost:5173/api/auth/callback` にする(ここだけは Vite に合わせて `localhost`)。

## うまくいかないとき

| 症状 | 見るところ |
|---|---|
| 起動直後に止まる(`AUTH_MODE=oidc` の設定が足りない) | `OIDC_ISSUER`、`OIDC_CLIENT_ID`、`PUBLIC_BASE_URL` の3つが要る |
| 「ログイン」を押すと「認証サーバー(IdP)に接続できません」(502) | サーバーから `OIDC_ISSUER` に届いていない(URL の誤り、IdP が未起動、コンテナから `localhost` を指している)。サーバーのログに原因が出る |
| IdP が `redirect_uri_mismatch` / `Invalid parameter: redirect_uri` を出す | IdP に登録した redirect URI と `{PUBLIC_BASE_URL}/api/auth/callback` が一致していない(`localhost` と `127.0.0.1` の違いも含む) |
| Google が「このアプリは Google で確認されていません」/ `access_denied` を出す | 同意画面が Testing のとき、Test users に載っていないアカウント。追加するか、アプリを公開する |
| 戻ってきた直後に「ログインの処理に失敗しました」 | サーバーのログを見る。public client なのに `OIDC_CLIENT_SECRET` がある(またはその逆)、時計のずれ、IdP の URL にコンテナから届いていない、など |
| 「このメールアドレスでは GAKEI にログインできません」 | `AUTH_ALLOWED_EMAIL_DOMAINS` に載っていないドメイン。管理者なら `AUTH_ADMIN_EMAILS` に載せる |
| ログインしても管理者設定が出ない | `AUTH_ADMIN_EMAILS` のメールが IdP のユーザーのメールと一致しているか(大文字小文字は無視)。一覧を変えたらログインし直す |
| `https` のプロキシの後ろでログインが繰り返される | `PUBLIC_BASE_URL` が `https://…` になっているか(Cookie の `Secure` はこの値で決まる) |
| メールの無いユーザーが弾かれる | IdP でユーザーにメールを設定するか、`email` クレームを含むスコープを付ける |
