# Stable Diffusion WebUI に接続する

GAKEI から、Stable Diffusion WebUI の系統(AUTOMATIC1111、Forge など)の HTTP API(`/sdapi/v1/*`。以下「A1111 互換の API」)を呼んで画像を生成できる。OpenAI や ComfyUI と同じ履歴・系列・ビューアで扱える。設計は [ADR-0038](adr/0038-sd-webui-provider.md)。

利用者向けには **実験的な機能** である。主に Forge と AUTOMATIC1111 で確かめている。それ以外の実装(reForge、SD.Next など)は、A1111 互換の範囲で動けば使える。

## できること

- WebUI のチェックポイントを「モデル」として選び、プロンプトから生成する(Generate。WebUI の txt2img)
- ネガティブプロンプト、サンプラー、スケジューラー、ステップ数、CFG スケール、seed、サイズ(8 の倍数、長辺 2048px まで)、枚数(1〜8)、VAE を指定する
- 実行中の進み具合と途中経過の画像を表示する

チェックポイントは、その1回の実行だけ切り替え、終わったら WebUI の設定を元に戻す(`override_settings_restore_afterwards`)。WebUI の画面を直接使っている人の選択を変えたままにしない。WebUI が出力フォルダーに画像を保存することもない(`save_images` は false で送る)。原本は GAKEI が持つ。

LoRA は、プロンプトに `<lora:名前:重み>` と書けば、そのまま WebUI に届く。GAKEI は LoRA を解釈も管理もしない。

## WebUI を起動する

WebUI は **`--api` を付けて起動する**。付けないと、画面は動いていても API(`/sdapi/v1/*`)が無く、GAKEI からは「API が見つかりません(404)」になる。

```bash
# 例(起動の方法は WebUI ごとに違う。webui-user.sh / webui-user.bat の COMMANDLINE_ARGS などに足す)
--api
```

### Basic 認証(`--api-auth`)

WebUI を LAN に出すときは、`--api-auth` で API に Basic 認証を掛けられる。

```bash
--api --api-auth ユーザー名:パスワード
```

- 書式は **`ユーザー名:パスワード`**(コロンで区切る)。書式を誤ると、Forge は画面だけを起動し、API は 404 のままになる(起動のログにエラーが出る)。接続テストで 404 になったら、起動のログを確かめる。
- GAKEI では、設定 → SD WebUI の「資格情報」に同じユーザー名とパスワードを入れる。値は `DATA_DIR/secrets.json` に保存し、画面にも API の応答にも出さない(「設定済み」とだけ表示する)。
- URL に `ユーザー名:パスワード@` を書くことはできない。
- 資格情報が無い・違うと、接続テストは「Basic 認証が必要か、資格情報が違います(401)」になる。

## GAKEI から接続する

1. 管理者設定 → SD WebUI(`/settings/sdwebui`)を開く。
2. WebUI の URL(例: `http://127.0.0.1:7860`)を入れて「接続テスト」を押す。Forge か AUTOMATIC1111 か、チェックポイントの数が表示される。
3. 保存する。再起動は要らない。生成フォームのプロバイダーに「SD WebUI」が出る。

- 環境変数 `SDWEBUI_URL` と `SDWEBUI_TIMEOUT_SECONDS` は、画面で一度も設定していないときの初期値としてだけ使う([configuration.md](configuration.md))。
- 1回の実行を待つ上限は既定で 600 秒。同じページで変えられる(60〜10800 秒)。
- チェックポイントを WebUI に足したときは「一覧を読み直す」を押す(WebUI の `refresh-checkpoints` を呼び、GAKEI の一覧も読み直す)。一覧は 60 秒ほどキャッシュしている。
- SD WebUI の Run が待機中か実行中の間は、接続先と資格情報を変えられない。

## VAE の選び方

VAE は **実行ごとに選ぶ**。既定は「チェックポイントに内蔵のもの」。

WebUI はチェックポイントを切り替えても VAE の設定を残すので、たとえば SDXL 用の VAE を選んだまま SD1.5 のチェックポイントで描くと、画像が崩れる。GAKEI は毎回 VAE を指定して送り(Forge は `forge_additional_modules`、AUTOMATIC1111 は `sd_vae`)、実行の後に WebUI の設定を元に戻す。

- VAE を内蔵しているチェックポイントは、「チェックポイントに内蔵のもの」のままでよい。
- VAE を内蔵していないチェックポイントは、種類(SD1.5、SDXL など)に合う VAE を選ぶ。
- 選択肢は接続先の VAE の一覧(Forge は `VAE` のフォルダーにあるもの)。

Flux など、テキストエンコーダーを別に読み込むチェックポイントは対象外(動くかは WebUI の設定次第)。

## 指定したチェックポイントで描かれなかったとき

WebUI は、知らないチェックポイント名を渡されてもエラーにせず、今読み込んでいるもので描く。GAKEI は応答の情報で実際に使われたチェックポイントを確かめ、違えば画像を取り込まずに Run を失敗(`sdwebuiModelMismatch`)にする。チェックポイントのファイルを消したり名前を変えたりしたときは、「一覧を読み直す」を押す。

## LAN の別の PC で WebUI を動かすとき

GPU のある PC で WebUI を動かし、GAKEI は別の PC で動かす使い方ができる。

- WebUI を LAN から受けられるように起動する(例: `--listen`)。**`--api-auth` で Basic 認証を掛ける。** 認証の無い WebUI を LAN に出すと、同じネットワークの誰でも WebUI を使える。
- GAKEI でループバック以外の URL を保存するときは、画面の確認のチェックを入れる。サーバーのログにも警告が出る。
- Basic 認証は `http` では平文で流れる。信頼できるネットワークでだけ使う。
- GAKEI を Docker で動かし、同じホストの WebUI に接続するときは `http://host.docker.internal:7860`(`--add-host=host.docker.internal:host-gateway` が要る。README の Docker の節)。

## 実行中のキャンセル

キャンセルできるのは、GAKEI の中で待機中の Run だけ。WebUI で実行中のものは止めない(WebUI の中断の API は、誰が始めたかに関係なく、実行中のものを止めてしまうため)。

## 記録されるもの

Run の `params` には、利用者が指定した値に加えて、次を記録する(ADR-0038 3章)。

- `sdwebui_seed`: 実際に使った seed(指定が無ければ GAKEI が決める)
- `sdwebui_task_id`: 進み具合の問い合わせに使う ID
- `sdwebui_request`: WebUI に送った本文そのもの

`sdwebui_` で始まる項目はサーバーだけが書く。`sdwebui_request` と `sdwebui_task_id` は共有のページの公開パラメーターには出さず、`sdwebui_request` は PNG に埋め込む系列情報にも入れない(WebUI 自身が `parameters` として生成の設定を PNG に入れている)。

## 作らないもの・今後

- **今後:** img2img(Edit。入力画像とマスクを使った描き直し)
- 作らない: InvokeAI、Fooocus、SwarmUI など、A1111 互換でない API
- 作らない: 拡張機能(ControlNet、ADetailer など)の項目、hires fix、refiner、アップスケール
- 作らない: チェックポイント・LoRA・VAE のダウンロードや管理、WebUI の設定の変更
- 作らない: 実行中の Run のキャンセル
- 作らない: 複数の WebUI サーバー
