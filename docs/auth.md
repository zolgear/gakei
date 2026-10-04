# 認証(OIDC)の設定

GAKEI は既定では認証を持たず、同じ PC からだけ開ける(`HOST=127.0.0.1`)。複数人で使うときは、OIDC(OpenID Connect)の発行者でログインするようにできる(ADR-0019)。OpenID Connect Discovery に対応する発行者なら使える。ここでは、すぐ試せる **Google** と、組織で利用者を管理する **Keycloak** の手順を書く。

認証は、管理者設定の「認証」ページ(`/settings/authentication`)で設定して有効にする(ADR-0034)。再起動は要らない。有効にする前に管理者のテストログインを求めるので、設定の誤りで締め出されることはない。これまでどおり `.env` だけで設定することもできる(「.env で設定する」の節)。

## 仕組み

- ログインはサーバーが仲介する(Authorization Code + PKCE)。ブラウザは `/api/auth/login` から IdP へ移り、`/api/auth/callback` に戻ってくる。
- ログイン後はサーバー側にセッションを持ち、ブラウザには HttpOnly の Cookie(`gakei_session`)だけを渡す。画像の表示と進捗の配信(SSE)もこの Cookie で通る。
- セッションは既定で 30日有効(1〜720 時間)。期限はログインした時刻から数える。長さを変えると、次のログインから効く。GAKEI は IdP のトークンを持たず、期限内に IdP へ問い合わせ直さないので、IdP 側でユーザーを無効にしても期限までは使える。早く締め出したいときは短くする。
- ロールは `user` と `admin` の2つ。「管理者のメール」に載せた人が `admin` になり、API キーや ComfyUI の接続、認証など「管理者設定」を変えられる。それ以外の人は生成と閲覧はできるが、管理者設定は変えられない。管理者のメールはリクエストのたびに評価するので、変えると次のリクエストから効く。
- **ログインできる人の範囲は「ログインを許すドメイン」で絞る。** 空だと、IdP が認証した人は誰でもログインできる。Keycloak のように IdP 側で利用者を管理するなら空でよいが、Google のように誰でもアカウントを持てる IdP では必ず指定する(指定しないと、世界中の誰でも `user` としてログインし、組織のキーで画像を生成できる)。管理者のメールの人は常にログインできる。
- 生成した Run とアップロードした画像に、誰が実行したかが記録され、履歴と Run 詳細に表示される。

## 閲覧範囲(誰に何が見えるか)

認証モードでは、**本人が作ったものだけ**が見え、操作できる(ADR-0025)。管理者も同じで、他人の Run や画像は見られない。

- 対象は Run(履歴、Run 詳細、取り消し、削除、再実行)、画像(ストック、ビューア、原本・プレビュー・サムネイルの配信、系列グラフ、比較、削除)、グループ、プロンプトセット、検索、進捗の配信(SSE)と App バーの実行中・待機中の件数、MCP の全ツール。
- 他人のものの URL を開いても「見つからない」(404)になる。存在するかどうかも分からない。
- 生成の入力画像・マスク・出力先のグループには、自分のものだけを指定できる。
- 他人がダウンロードした画像を受け取ってアップロードすると、元の画像とは別の、自分の画像として取り込まれる(元の画像は見えない。埋め込まれていた系列情報は「このインスタンスの別の Asset(表示できません)」と出る)。
- 個人モード(認証なし)の頃に作ったデータは実行者が記録されていないので、認証モードにした後は **管理者だけ** が見られる。
- ログインせずに他人に見せたいときは、共有リンク(ADR-0029、[sharing.md](sharing.md))を使う。共有リンクは管理者が「共有リンクの公開」で有効にしたときだけ作れる。
- 個人モードでは、これまでどおり全部が見える。

## 設定の出どころと優先順位

| 項目 | 優先順位 |
|---|---|
| 認証モード | `.env` に `AUTH_MODE` を書いていればそれ(画面では変えられない)→ 画面の設定 → 既定 `none` |
| 発行者、クライアント ID、スコープ、`PUBLIC_BASE_URL`、管理者のメール、ログインを許すドメイン、セッションの長さ | 画面の設定 → `.env` → 既定 |
| クライアントシークレット | `DATA_DIR/secrets.json` → `.env` の `OIDC_CLIENT_SECRET`。画面で接続を登録した後(または画面で有効にした後)は `secrets.json` だけを読み、`.env` の値は使わない |
| `AUTH_SECRET` | `.env`(画面では扱わない。未指定なら自動で作って `secrets.json` に保存する) |

- 画面には、各値の出どころ(画面 / .env / 既定)が出る。
- 画面で保存した値は DB に、シークレットは `DATA_DIR/secrets.json` に置く。シークレットは画面にも API の応答にも一部も出さない(設定済みかどうかと出どころだけ)。
- 画面で有効にしたとき、`.env` から来ていた値を DB と `secrets.json` に書き写す。以後は `.env` から消しても動く。
- `.env` だけで oidc を使っている環境は、何もしなくても今までどおり動く。

## IdP 側の準備

### Google(すぐ試す)

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを選ぶ(無ければ作る)。
2. **APIs & Services → OAuth consent screen**(「Google Auth Platform」と表示される場合もある)で同意画面を設定する。個人で試すなら User type は **External** のまま、公開状態を **Testing** にして、**Test users** に自分の Google アカウントを追加する(Testing のままだと、ここに載せた人しかログインできない)。
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**
   - Authorized redirect URIs: `{PUBLIC_BASE_URL}/api/auth/callback`(例: `http://127.0.0.1:8000/api/auth/callback`。Google はループバックの `http` を受け付ける。GAKEI の接続のダイアログにも、登録する URI が出る)
   - 「承認済みの JavaScript 生成元」は空でよい(GAKEI はサーバー側でコードを交換するので使わない)。
   - 作成すると **Client ID** と **Client secret** が表示される。

GAKEI に入れる値:

- 発行者: `https://accounts.google.com`
- クライアント ID / シークレット: 上で表示されたもの
- ログインを許すドメイン: 組織のドメイン(Google Workspace なら会社のドメイン)。個人の Gmail で試すだけなら、管理者のメールに自分を載せておけば、この項目は空でも(同意画面が Testing の間は)テストユーザー以外はログインできない。

Google には `end_session_endpoint` が無いので、GAKEI のログアウトは GAKEI のセッションを消すだけで、Google 側にはログインしたまま(次に「ログイン」を押すとアカウント選択に進む)。

### Keycloak(組織で運用する)

#### 1. Keycloak を起動する(ローカルで試す場合)

```bash
docker run --rm -p 8080:8080 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  quay.io/keycloak/keycloak:26.2 start-dev
```

`http://localhost:8080` の管理コンソールに `admin` / `admin` で入る(この `http://localhost:8080` はローカルの確認用。本番では https にする)。

#### 2. レルムとクライアントを作る

1. **レルム**を作る(例: `gakei`)。
2. **Clients → Create client**:
   - Client type: `OpenID Connect`
   - Client ID: `gakei`
   - Client authentication: 組織で使うなら **On**(confidential client。Credentials タブのシークレットを GAKEI に入れる)。**Off** にすると public client になり、シークレットなし(PKCE のみ)で動く。
   - Authentication flow: `Standard flow` だけ On
   - Valid redirect URIs: `{PUBLIC_BASE_URL}/api/auth/callback`(例: `http://127.0.0.1:8000/api/auth/callback`)
   - Valid post logout redirect URIs: `{PUBLIC_BASE_URL}/*`
   - Web origins: `+`
3. **Realm settings → Login** で **Verify email** を **ON** にする(GAKEI はメールで利用者と管理者、許可ドメインを判定するため、確認済みでないメールアドレスは信用しない。ON にすると、ユーザーは初回ログイン時にメールでの確認を求められる)。あわせて、Account Console からユーザー自身がメールアドレスを変更できないようにしておく(確認済みの状態のまま別のメールアドレスに差し替えられると、上記の判定が意味を失う)。
4. **Users** でユーザーを作る。**Email を必ず入れる**(GAKEI はメールで利用者と管理者を区別する。メールの無いユーザーはログインを断られる)。Credentials タブでパスワードを設定する。

GAKEI に入れる値:

- 発行者: `http://localhost:8080/realms/gakei`
- クライアント ID: `gakei`
- シークレット: Credentials タブの値(public client なら空)
- ログインを許すドメイン: 空でよい(利用者はレルムで管理する)

## 画面で設定する

### 始める前に

- **設定画面は、`PUBLIC_BASE_URL`と同じ URL で開いて操作する。** テストログインの結果は、同じオリジンのウィンドウにしか返らない。たとえば`PUBLIC_BASE_URL` を `http://127.0.0.1:8000` にするなら、`http://localhost:8000` ではなく `http://127.0.0.1:8000` で開く。
- 個人モードの間は、開ける人は誰でも管理者として扱われる。設定を終えるまでは、LAN やインターネットに出さない。
- ポップアップをブロックしているときは、GAKEI の URL で許可する。

### 有効にする手順

1. **接続を設定する。** 管理者設定 →「認証」で接続のダイアログ(「OIDC の接続を設定」)を開き、発行者、クライアント ID、シークレット、スコープ(既定 `openid profile email`)、`PUBLIC_BASE_URL` を入れる。
   - 入れた値は**仮登録**になる。GAKEI は形式を確かめ、発行者の Discovery 文書(`{発行者}/.well-known/openid-configuration`)を取得できるかまで確かめる。この時点では、実際の設定は変わらない。
   - ダイアログに出るリダイレクト URI(`{PUBLIC_BASE_URL}/api/auth/callback`)を IdP に登録する。
2. **管理者のメールを保存する。** 自分のメールアドレス(IdP に登録されているもの)を入れ、ページ上部の「保存」を押す。必要ならログインを許すドメインとセッションの長さもここで保存する。テストログインで使うアカウントのメールが、ここに載っている必要がある。
3. **テストログインをする。** ポップアップで IdP にログインする。次の2つを満たせば成功する。
   - ID トークンの検証が通る。
   - メールが確認済みで、管理者のメールに載っている。

   成功すると、仮登録が本登録になる。失敗したときは、ポップアップに理由が出る。直して、もう一度試す。
4. **認証モードをオンにして保存する。** テストに成功した接続のままで、テストでログインした人が管理者のメールに載っているときだけ、スイッチを押せる。押せない理由はスイッチの近くに出る。
   - テストでログインした管理者は、そのままログイン済みとして残る(締め出されない)。
   - ほかの人は、次に開いたときにログイン画面が出る。

### 有効にした後の操作

- **接続を差し替える。** 接続のダイアログで入れ直してテストログインをする。成功するまでは、今の接続のまま動く。発行者を変えると、利用者は別人として扱われる(発行者とサブジェクトで区別するため)。前の発行者で作ったデータは付け替えない。
- **管理者のメール、ログインを許すドメイン、セッションの長さを変える。** ページの「保存」で反映する。自分を管理者のメールから外して保存することはできない(外すときは、別の管理者に頼む)。
- **無効にする。** 認証モードをオフにし、確認のダイアログで了承してから保存する。セッションと利用者の記録は消さないので、もう一度有効にするとそのまま使える。無効にした時点で、開ける人は誰でも管理者になる。外に出している場合は、先に待ち受けを絞る。

## .env で設定する(従来どおり・自動化向け)

画面を使わず `.env` だけで設定することもできる。Docker などで設定をファイルで管理したいとき向け。各項目の意味は [configuration.md](configuration.md)。

Google の例:

```dotenv
AUTH_MODE=oidc
OIDC_ISSUER=https://accounts.google.com
OIDC_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
OIDC_CLIENT_SECRET=GOCSPX-...
PUBLIC_BASE_URL=http://127.0.0.1:8000
AUTH_ADMIN_EMAILS=you@gmail.com
AUTH_ALLOWED_EMAIL_DOMAINS=example.co.jp
```

Keycloak の例:

```dotenv
AUTH_MODE=oidc
OIDC_ISSUER=http://localhost:8080/realms/gakei
OIDC_CLIENT_ID=gakei
OIDC_CLIENT_SECRET=<Credentials タブの値。public client なら空>
PUBLIC_BASE_URL=http://127.0.0.1:8000
AUTH_ADMIN_EMAILS=you@example.com
```

- 起動して `PUBLIC_BASE_URL` を開くと、ログイン画面が出る(`run.sh` / `run.bat` が開くのは `http://127.0.0.1:8000/` なので、`PUBLIC_BASE_URL` と redirect URI もこれに揃えてある。下の「`PUBLIC_BASE_URL` について」も参照)。
- `.env` に `AUTH_MODE` を書くと、モードは `.env` に固定され、画面では切り替えられない。ほかの項目は、画面で保存すればそちらが優先する。
- `AUTH_MODE` を書かずに `OIDC_*` などだけを書いておき、画面から有効にすることもできる。`.env` の値が各項目の初期値になり、テストログインを経て有効にした時点で DB に書き写される。
- oidc なのに `OIDC_ISSUER`、`OIDC_CLIENT_ID`、`PUBLIC_BASE_URL` のどれかが(画面の設定にも `.env` にも)無いと、起動を中止して足りない項目を表示する。

## 締め出されたとき(緊急の無効化)

IdP が落ちた、管理者のアカウントを失ったなどで誰もログインできないときは、`.env` で認証を一時的に止める。

1. リポジトリ直下の `.env`(Docker では渡している環境変数)に `AUTH_MODE=none` を書く。
2. GAKEI を再起動する。個人モードで起動するので、管理者設定の「認証」で接続や管理者のメールを直す。
3. 直したら `.env` から `AUTH_MODE` を消して再起動する。画面の設定に戻り、認証が有効な状態に戻る。

- `AUTH_MODE=none` を書いても、画面で保存した認証の設定は消えない。
- `.env` に `AUTH_MODE` がある間は、画面の「ログインを必須にする(OIDC)」のスイッチは押せない。
- 個人モードの間は、開ける人は誰でも管理者になる。外に出している場合は、作業の間だけ `HOST=127.0.0.1`(compose なら `GAKEI_BIND=127.0.0.1`)にする。
- 画面の設定で oidc なのに必須の値(発行者、クライアント ID、`PUBLIC_BASE_URL`)が欠けている、または形式が不正なときは、起動を中止して同じ手順を案内する。

## `PUBLIC_BASE_URL` について

`PUBLIC_BASE_URL`(画面でも同じ名前)は「利用者がブラウザで開く URL」で、IdP に登録した redirect URI と一致している必要がある。`run.sh` / `run.bat`(と `python -m app`)は `http://{HOST}:{PORT}/`、既定では `http://127.0.0.1:8000/` をブラウザで開くので、ローカルで試すときはこの形(`localhost` ではなく `127.0.0.1`)に揃える。`localhost` で開きたい場合は、`PUBLIC_BASE_URL` と IdP の redirect URI の両方を `localhost` にする(ブラウザの URL、`PUBLIC_BASE_URL`、redirect URI の3つが一致していればどちらでもよい)。リバースプロキシの後ろに置くなら、プロキシの外側の URL(`https://gakei.example.com`)を書く。`https` のとき、セッション Cookie に `Secure` が付く。

## 確認

- 管理者(管理者のメールに載せた人)でログインすると、設定画面の目次に「管理者設定」(OpenAI、LLM の接続先、自動タイトル・タグ、ComfyUI、MCP、共有リンクの公開、認証)が出る。
- 他のユーザーでログインすると「管理者設定」は出ず、API を直接叩いても 403 になる。
- 右上のユーザーメニューからログアウトすると、IdP のログアウト(あれば)を経て GAKEI に戻る。

### 認可コードとログ(I-8、2026-09-27 追記)

`/api/auth/callback?code=...` の `code`(認可コード)は uvicorn のアクセスログに残る。この `code` は1回限りで、かつ PKCE(`code_verifier`)に縛られているため、ログを見られただけで悪用できるものではない(低リスク)。気になる場合は、リバースプロキシ側でこのパスのクエリー文字列をログから除くか、uvicorn のアクセスログ自体を切る。

## Docker(compose)で使う

画面での手順はそのまま使える。シークレットは `DATA_DIR`(コンテナの `/data`)の `secrets.json` に入るので、ボリュームを消さない。`.env` で設定する場合は、リポジトリ直下の `.env` を `compose.yaml` が読むので、上と同じ項目を書けばよい。

`PUBLIC_BASE_URL` はホスト側で開く URL(`GAKEI_BIND` / `GAKEI_PORT` で決まる。例: `http://192.168.1.10:8000`)にする。Google のような外部の IdP ならこれだけでよい。ホストで動かす Keycloak を使う場合は、コンテナの中からも、ブラウザからも、同じ名前で IdP に届く必要がある(`localhost` はコンテナの中では別物になるので、ホストの IP アドレス `http://192.168.1.10:8080/realms/gakei` などにする)。

LAN の別の PC から設定画面を開いて有効にするときは、その間は個人モードで LAN に出ていることになる。作業は短く済ませ、終わるまで信頼できない人が入れる場所に出さない。

## Entra ID

Entra ID も OIDC 発行者なので、アプリ登録(Web プラットフォーム、リダイレクト URI `{PUBLIC_BASE_URL}/api/auth/callback`、クライアントシークレット)を作れば同じ手順で使える見込み。発行者は次の形になる。

```
https://login.microsoftonline.com/<テナント ID>/v2.0
```

`email` クレームはアプリ登録の「トークン構成」で追加する必要がある場合がある。実機での確認はまだ行っていない。

## 開発時(Vite の開発サーバー)

`npm run dev` の画面(Vite の既定で `http://localhost:5173`)は `/api` をバックエンドへプロキシする。ログインの戻り先もこの経由になるので、`PUBLIC_BASE_URL` を `http://localhost:5173` にし、IdP の redirect URI も `http://localhost:5173/api/auth/callback` にする(ここだけは Vite に合わせて `localhost`)。設定画面も `http://localhost:5173` で開いて操作する。

## うまくいかないとき

| 症状 | 見るところ |
|---|---|
| 接続のダイアログで「Discovery 文書を取得できませんでした」と出る | サーバーから発行者の URL に届いていない(URL の誤り、IdP が未起動、コンテナから `localhost` を指している)。発行者の URL に `/.well-known/openid-configuration` を付けて開けるか確かめる |
| テストログインのポップアップが開かない | ブラウザがポップアップをブロックしている。GAKEI の URL で許可する |
| テストログインに成功したのに、設定画面に結果が出ない | 設定画面を`PUBLIC_BASE_URL` と違う URL(`localhost` と `127.0.0.1` の違いも含む)で開いている。`PUBLIC_BASE_URL` と同じ URL で開き直す |
| テストログインで「管理者のメールに含まれていません」と出る | 先に管理者のメールを保存しておく。IdP のユーザーのメールと一致しているか(大文字小文字は無視)を確かめる |
| テストログインで「ログインの処理に失敗しました」と出る | 発行者、クライアント ID、シークレット(public client なのに入れている、またはその逆)、IdP に登録したリダイレクト URI を確かめる。サーバーのログに原因が出る |
| 「ログインを必須にする(OIDC)」のスイッチが押せない | スイッチの近くの理由を見る。`.env` に `AUTH_MODE` がある、テストログインが済んでいない(接続を変えた後はやり直す)、テストでログインした人が管理者のメールに無い、のどれか |
| 起動直後に止まる(認証の設定が足りない) | 発行者、クライアント ID、`PUBLIC_BASE_URL` の3つが要る。画面の設定で oidc のときは、`.env` に `AUTH_MODE=none` を書いて起動し、画面で直す(「締め出されたとき」) |
| 誰もログインできない | 「締め出されたとき」の手順で一時的に無効にして直す |
| 「ログイン」を押すと「認証サーバー(IdP)に接続できません」(502) | サーバーから発行者に届いていない(URL の誤り、IdP が未起動、コンテナから `localhost` を指している)。サーバーのログに原因が出る |
| IdP が `redirect_uri_mismatch` / `Invalid parameter: redirect_uri` を出す | IdP に登録した redirect URI と `{PUBLIC_BASE_URL}/api/auth/callback` が一致していない(`localhost` と `127.0.0.1` の違いも含む) |
| Google が「このアプリは Google で確認されていません」/ `access_denied` を出す | 同意画面が Testing のとき、Test users に載っていないアカウント。追加するか、アプリを公開する |
| 戻ってきた直後に「ログインの処理に失敗しました」 | サーバーのログを見る。public client なのにシークレットがある(またはその逆)、時計のずれ、IdP の URL にコンテナから届いていない、など。画面で接続を登録した後は `.env` の `OIDC_CLIENT_SECRET` は使わない |
| 「このメールアドレスでは GAKEI にログインできません」 | ログインを許すドメインに載っていないドメイン。管理者なら管理者のメールに載せる |
| ログインしても管理者設定が出ない | 管理者のメールが IdP のユーザーのメールと一致しているか(大文字小文字は無視)。画面の設定が `.env` より優先するので、`AUTH_ADMIN_EMAILS` を変えても効かないときは画面の値を見る |
| `https` のプロキシの後ろでログインが繰り返される | `PUBLIC_BASE_URL` が `https://…` になっているか(Cookie の `Secure` はこの値で決まる) |
| メールの無いユーザーが弾かれる | IdP でユーザーにメールを設定するか、`email` クレームを含むスコープを付ける |
