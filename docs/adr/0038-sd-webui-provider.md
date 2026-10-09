# ADR-0038: Stable Diffusion WebUI(A1111 互換の API)をプロバイダーに加える

**Status:** Proposed
**Date:** 2026-10-10

## Context

ローカル版(ADR-0012)の利用者には、ComfyUI(ADR-0013)ではなく、Stable Diffusion WebUI の系統(AUTOMATIC1111、Forge、reForge、SD.Next など)を使っている人も多い。これらは起動時に `--api` を付けると、共通の HTTP API(`/sdapi/v1/*`。以下「A1111 互換の API」)を出す。Draw Things や KoboldCpp なども、この API の一部を真似ている。

GAKEI から A1111 互換の API を呼べれば、ComfyUI と同じように、OpenAI の画像 API とローカルのモデルの実行を、同じ履歴、同じ系列グラフ、同じビューアで扱える。ComfyUI と違って、ワークフローを登録しなくても、チェックポイントを選んでプロンプトを入れるだけで使える。

一方で、ADR-0001 は「ComfyUI 以外の他プロバイダー対応」を非ゴール(Phase 3)にしており、ADR-0013 の「作らないもの」にも「ComfyUI 以外のプロバイダー」がある。この ADR はその2つを改める。

A1111 互換の API で使うもの(2026-10-10 時点。実装時に Forge と A1111 のソースで確かめ直す):

- `POST /sdapi/v1/txt2img`、`POST /sdapi/v1/img2img`。本文は JSON で、入力画像とマスクは base64。応答は `images`(base64 の配列)と `info`(JSON 文字列。`seed`、`all_seeds`、`infotext` など)。
- `GET /sdapi/v1/progress`(`progress`、`eta_relative`、`state`、`current_image`(途中経過の画像、base64))。これは WebUI で今動いているもの(誰が始めたかを問わない)の進み具合を返す。
- 本文の `force_task_id` で実行に ID を付けると、`POST /internal/progress`(`id_task`、`live_preview`)で、その実行だけの進み具合(`active`、`queued`、`completed`、`progress`、`live_preview`)を取れる。`/internal/*` は WebUI の画面が使う内部の API で、`/sdapi/v1/*` ほど安定していない。`--api-auth` は `/internal/*` には掛からない(Forge で確認)。GAKEI は、同じ資格情報をどちらにも付けて送る。
- `GET /sdapi/v1/sd-models`、`/samplers`、`/schedulers`、`/options`。Forge には `/sdapi/v1/sd-vae` が無く、VAE やテキストエンコーダーは `/sdapi/v1/sd-modules` と設定の `forge_additional_modules` で扱う。
- `override_settings.sd_model_checkpoint` に一覧に無い名前を渡しても、エラーにならず、WebUI で今読み込まれているチェックポイントで描く(Forge で確認。応答は 200)。何で描いたかは `info` の `sd_model_name` で分かる。
- チェックポイントを切り替えても、VAE(Forge の `forge_additional_modules`、A1111 の `sd_vae`)は WebUI の設定のまま残る。SDXL 用の VAE を選んだ WebUI で SD1.5 のチェックポイントに切り替えると、画像が崩れる(Forge で確認)。`override_settings` で VAE も1回だけ変えられ、`override_settings_restore_afterwards` で元に戻る。
- 本文の `save_images` の既定は false で、API からの実行は WebUI の出力フォルダーに保存されない。Forge は Flux 用の `distilled_cfg_scale` も受け付ける(A1111 には無い)。
- `override_settings`(`sd_model_checkpoint` などをその1回だけ変える)と `override_settings_restore_afterwards`。
- img2img のマスクは **白が描き直す範囲**(`inpainting_mask_invert = 0` のとき)。GAKEI の規約(alpha = 0 が編集範囲。OpenAI の Edit と同じ)とは逆である。
- 出力の PNG には、WebUI が生成の設定を `parameters` のテキストとして埋め込む。GAKEI はこれを ADR-0018 で既に読める。
- `--api` を付けずに起動すると、`/sdapi/v1/*` は 404 になる(拡張機能が足したルートだけが残ることがある)。`--api-auth user:pass` を付けると、API に Basic 認証が掛かる。

## Decision

### 1. 範囲

- 対象は **A1111 互換の API を出す WebUI 1台** だけにする。プロバイダーの ID は `sdwebui`、表示名は「SD WebUI」。主な確認の対象は Forge と AUTOMATIC1111 で、それ以外の実装は「互換の範囲で動けば使える」扱いにする(個別の対応はしない)。
- InvokeAI、Fooocus、SwarmUI など、API の形が違うものは対象にしない。
- **既定は無効。** 利用者が管理者設定から接続先を入れて有効にする(6章)。ComfyUI、OpenAI と同時に有効にでき、Run ごとに選ぶ(ADR-0013 2章をそのまま使う)。系列はプロバイダーをまたいでつながる。
- 利用者向けには、ComfyUI と同じく **実験的な機能** と表示する。
- 操作は2つ。
  - **Generate:** `txt2img`。
  - **Edit:** `img2img`。入力画像は1枚、マスクは任意で1枚。

### 2. モデルとパラメーター

- **モデル = チェックポイント。** `GET /sdapi/v1/sd-models` の一覧を、capabilities のモデルとして見せる。実行のときは `override_settings.sd_model_checkpoint` で指定し、`override_settings_restore_afterwards = true` を付ける。WebUI の画面を直接使っている人の選択を、GAKEI の実行で変えたままにしないため。
  - **VAE はパラメーター `vae` で毎回指定する。** 選択肢は「チェックポイントに内蔵のもの」(既定)と、接続先の VAE の一覧(Forge は `/sdapi/v1/sd-modules`、A1111 は `/sdapi/v1/sd-vae`)。WebUI の設定のままにはしない。チェックポイントの種類(SD1.5 と SDXL など)と合わない VAE が残っていると、画像が崩れるため。送り方は接続先に合わせる(`/options` に `forge_additional_modules` があれば Forge として `forge_additional_modules`、無ければ `sd_vae`)。
  - チェックポイントは、capabilities の一覧に無い名前を 422 で断る。実行の後、`info.sd_model_name` が指定と違えば、画像を取り込まずに `failed` + `sdwebuiModelMismatch` にする。WebUI は知らない名前を黙って無視し、別のチェックポイントで描くため。
  - Flux など、テキストエンコーダーを別に読み込むチェックポイントは、VAE だけの指定では足りないので、この ADR では対象にしない(動くかは WebUI の設定次第)。
  - チェックポイントの切り替えには数十秒かかることがある。同じチェックポイントの実行が続くときは切り替えが起きない(WebUI 側の動き)。GAKEI は並べ替えなどの工夫をしない。
  - 一覧は接続先から取るので、接続できないときはモデルの選択肢を出さない(プロバイダーごと選べなくする)。一覧は短い時間だけキャッシュし、設定画面に「一覧を読み直す」を置く。
- パラメーターは **GAKEI が固定で定義する**(ComfyUI のような登録はしない)。
  - 共通: `negative_prompt`、`sampler_name`、`scheduler`、`steps`、`cfg_scale`、`seed`(ADR-0013 8章と同じ seed の欄)、`width` / `height`、`batch_size`(GAKEI の枚数 `n`)。
  - Edit だけ: `denoising_strength`、`resize_mode`。マスクがあるときは `mask_blur`、`inpainting_fill`、`inpaint_full_res`、`inpaint_full_res_padding`。
  - `sampler_name` と `scheduler` の選択肢は接続先から補う。取れなければ自由入力にはせず、項目を出さない(WebUI の既定に任せる)。
  - サイズは 8 の倍数、上限は長辺 2048px にする(大きいサイズは WebUI 側で VRAM が足りなくなりやすいため。ADR-0004 の 3840px とは別)。
- `save_images` は false のまま送る。原本は GAKEI が持つ(ADR-0004)。
- **送らないもの:** `distilled_cfg_scale`(Forge だけの項目)、7章で対応を決めた拡張機能以外の `alwayson_scripts`(ControlNet などの拡張)、hires fix、refiner、`script_name`。LoRA は、プロンプトに `<lora:名前:重み>` と書けば、そのまま WebUI に届く(GAKEI は解釈も管理もしない。ADR-0001 の非ゴール「LoRA 管理」は変えない)。

### 3. Run の記録(`run.params`)

ADR-0003 のルール4(`params` には API に送った値をそのまま保存する)を守るため、ComfyUI と同じく **Run を作る時点で送る本文を確定し、`run.params` に保存する。**

- 利用者が指定した値に加えて、サーバーが次の値を付け足す。
  - `sdwebui_seed`: 指定がなければサーバーが決める(2^32 未満。WebUI の seed の範囲)。`-1`(WebUI 側で乱数)は送らない。記録と実際が食い違うため。
  - `sdwebui_task_id`: `force_task_id` に送る ID(`gakei-` + 新しい UUID。`finalize_params` の時点では Run の ID がまだ無いため)。
  - `sdwebui_request`: 送る本文そのもの。ただし `init_images` と `mask` は base64 を入れず、`{"asset_sha256": ...}` に置き換える。入力画像は `run_input` に記録されているので、実行時にそこから組み立てる。
- `sdwebui_` で始まる項目はサーバーだけが書く。クライアントから送られたら 422 にする。
- 応答の `info` から `all_seeds` と `infotext`(1枚目)を `usage` に入れる。`provider_request_id` には `sdwebui_task_id` を入れる。料金は表示しない。
- 保存先のフォルダー(ADR-0026)は `assets/sdwebui/{チェックポイント名}/...`。チェックポイント名は、フォルダー名に使えない文字を置き換え、拡張子とハッシュの表記(`[abcd1234]`)を除く。

### 4. 実行

- プロバイダーごとの実行レーン(ADR-0013 5章)に乗せ、同じプロバイダーの中では直列に実行する。
- `txt2img` / `img2img` は応答まで返らない長い要求になる。その間、`POST /internal/progress` に `sdwebui_task_id` を渡して 1 秒に 1 回問い合わせ、`progress` を SSE の `progress` イベントで流す。`live_preview` があれば、既存の途中経過画像の仕組みで流す(0.5 秒に 1 回まで。Asset にしない。ADR-0005)。WebUI の中で順番を待っている間(`queued`)は、待機中と示す。
  - `/internal/progress` が使えない実装(404 など)では、`GET /sdapi/v1/progress` に切り替える。こちらは WebUI を直接使っている人の進み具合が混ざることがあるので、目安として扱う。
  - 進捗は表示のためだけに使い、Run の結果には使わない。
- **マスクは送るときだけ変換する。** GAKEI のマスク(alpha = 0 が編集範囲)から、白が編集範囲の白黒 PNG を作って送る。Asset の原本は変えない(ADR-0004)。
- 出力は `images` の順に Asset にする(`output_index`)。WebUI が応答に足すことがある補助の画像は取り込まない。先頭のグリッドは `info.index_of_first_image` で飛ばし(`return_grid` が有効な WebUI は先頭にグリッドを足す)、残りは `batch_size` を超える分を捨てる。
- **キャンセルは待機中(`queued`)の Run だけ。** `POST /sdapi/v1/interrupt` は、誰が始めたかに関係なく WebUI の実行中のものを止めるので使わない(ADR-0013 5章と同じ理由)。
- 再起動で中断した Run は `failed` + `interrupted`(ADR-0008)。起動時に、有効でないプロバイダーの `queued` の Run は `failed` + `providerUnavailable`(ADR-0013 と同じ)。

### 5. 接続できないとき、失敗したとき

- 接続できないときは、**Run を作らずに 409 を返す**(ADR-0013 6章と同じ)。確認は `GET /sdapi/v1/options` を短いタイムアウトで行い、結果を数秒キャッシュする。
- 接続テストで `/sdapi/v1/*` が 404 のときは、「WebUI を `--api` を付けて起動したか、起動のログに API のエラーがないかを確かめてください」と案内する。`--api-auth` の書式が誤っている(`user:pass` でない)と、Forge は画面だけを起動し、API は 404 のままになる(2026-10-10 に実物で確認)。401 のときは、Basic 認証の資格情報が要る(または違う)と案内する。
- 実行後の失敗は `failed` の Run として残す。`error_code` は次のとおり。
  - `sdwebuiUnavailable`: 実行の途中で接続できなくなった
  - `sdwebuiValidation`: WebUI が本文を拒否した(422 など。応答の要約を `error_message` に入れる)
  - `executionError`: 実行中に失敗した(VRAM 不足などの 500)
  - `sdwebuiNoOutput`: 画像が返らなかった
  - `sdwebuiModelMismatch`: 指定と違うチェックポイントで描かれた(2章)
  - `timeout`: 時間切れ

### 6. 設定

ComfyUI の設定(ADR-0013 7章、ADR-0031)と同じ形にする。

- 管理者設定に「SD WebUI」のページ(`/settings/sdwebui`)を足す。接続先の URL、接続テスト(Forge か A1111 かと、チェックポイントの数を表示する。バージョンは、安全に取れる API が無いので出さない。`/internal/sysinfo` は環境変数やパスを大量に含むので使わない)、切り離し、1回の実行を待つ上限(既定 600 秒)を置く。保存先は `app_setting`。変更は再起動なしで反映し、`sdwebui` の Run が `queued` か `running` の間は 409 で断る。
- 環境変数 `SDWEBUI_URL` と `SDWEBUI_TIMEOUT_SECONDS` は、画面で一度も設定していないときの既定値としてだけ使う(ComfyUI と同じ優先順位)。
- URL は `http` / `https` だけ。ループバック以外のアドレスは、画面で警告を出し、確認のチェックを入れないと保存できない。サーバーのログにも警告を出す。LAN の別の PC で WebUI を動かす使い方(GPU の PC と GAKEI の PC を分ける)は、ComfyUI と違って正式に対象にする。
- **Basic 認証(`--api-auth`)に対応する。** ユーザー名とパスワードを入れられるようにし、`DATA_DIR/secrets.json` に保存する。画面にも API の応答にも値を一部も出さず、「設定済み」だけを示す。URL に `user:pass@` を含めることは受け付けない(ADR-0017 と同じ)。
- 本線(Azure)での扱いは、ComfyUI と同じく本線に着手するときに決める。

### 7. 拡張機能(2026-10-10 追記。最初は Dynamic Prompts)

A1111 互換の WebUI の拡張機能のうち、`alwayson_scripts` で動くものは、API の本文で指定しなければ **WebUI の画面の既定値のまま動く**(Forge と sd-dynamic-prompts で確認。Dynamic Prompts は既定で有効で、`{a|b}` を 1 枚ずつ展開する)。そのため、GAKEI が何もしなくても拡張機能の効果は出るが、Run には何で動いたかが残らない。拡張機能ごとに次の形で対応する。

- **対応する拡張機能は GAKEI が1つずつ決める(アダプター)。** 接続先の `/sdapi/v1/scripts` に名前があるときだけ、その拡張機能のパラメーターをフォームに出す。任意の拡張機能の引数を汎用のフォームで出すことはしない(項目の意味が分からないため。2章の案 B と同じ理由)。
- **引数の組み立て:** `/sdapi/v1/script-info` の引数の並び(`label` と既定の `value`)を取り、GAKEI のパラメーターに当たる引数だけを **label で探して** 値を差し替え、残りは既定値のまま送る。並び順や引数の数は拡張機能の版で変わるので、位置で決め打ちしない。必要な label が見つからなければ、その拡張機能は「対応外の版」として項目を出さない。
- **記録:** 組み立てた `alwayson_scripts`(スクリプト名と引数の全体)を `sdwebui_request` に入れる(ADR-0003 ルール4)。対応する拡張機能が接続先にあるときは、利用者が値を変えなくても毎回明示して送る。WebUI の画面の既定値が後で変わっても、Run の記録と実際が食い違わないようにするため。
- **出力ごとのプロンプト:** 応答の `info.all_prompts` と `all_negative_prompts` を、出力の順に `usage` と `run.text_outputs`(ADR-0030。`output_index` 付きの `final_prompt` / `final_negative_prompt`)に記録する。画面では、Asset ごとに「展開後のプロンプト」として見せる。拡張機能を使わない Run でも記録し、Run のプロンプトと同じなら表示しない。検索や自動タイトルの対象にするかは別に決める。

#### Dynamic Prompts(sd-dynamic-prompts)

- パラメーター: `dynamic_prompts`(有効/無効。既定は有効。WebUI の既定に合わせる)、`dynamic_prompts_combinatorial`(組み合わせをすべて作る。既定は無効)。
- 組み合わせ生成では、WebUI は組み合わせの数だけ画像を作り、GAKEI の枚数(`batch_size`)は効かない(Forge で確認。`{red|blue|green}` で batch 1 でも 2 でも 3 枚)。GAKEI は引数「Max generations」に上限(32)を入れて送り、作られる枚数を抑える。組み合わせ生成のときは、フォームの枚数を使わない旨を示す。画像の枚数は `info.all_prompts` の数で決め(補助の画像を混ぜないため)、上限 32 枚まで取り込む(4章の「batch_size を超える分を捨てる」は、組み合わせ生成のときはこの規則に読み替える)。捨てた枚数は `usage` に記録する。
- 出さないもの: Magic prompt、I'm feeling lucky、Attention grabber(外部のモデルやネットワークを使う、または結果の再現が難しい)、Jinja2 テンプレート、「画像を作らない」。これらは常に無効として送る。
- ワイルドカード(`__名前__`)は WebUI 側のファイルをそのまま使う。GAKEI は一覧を出さない。見つからないときは展開されずに残る(展開後のプロンプトで分かる)。
- Dynamic Prompts が無い接続先では、項目を出さない。`{a|b}` はそのまま WebUI に届く。

#### 他の拡張機能の検討(この ADR では作らない)

候補は、同じアダプターの形に収まるか(引数が label で特定でき、値が単純な型か)と、ADR-0001 の非ゴールに当たらないかで選ぶ。ControlNet(入力画像が要る)、ADetailer(検出モデルとの組)、hires fix(本体の機能)などは、個別に検討する。

### 作らないもの

- InvokeAI、Fooocus、SwarmUI など、A1111 互換でない API
- 7章で対応を決めた拡張機能(今は Dynamic Prompts)以外の拡張機能の項目、`script_name`
- hires fix、refiner、アップスケール(`extra-single-image`)
- チェックポイント・LoRA・VAE のダウンロードや管理、WebUI の設定(`/options`)の変更
- 実行中の Run のキャンセル(`/interrupt`)
- 複数の WebUI サーバー
- 入力画像が2枚以上の Edit

## Options Considered

### VAE の扱い

| 案 | 評価 |
|---|---|
| A: パラメーターで毎回指定し、既定は「チェックポイントに内蔵のもの」(採用) | チェックポイントを切り替えても崩れない。Run に何の VAE で描いたかが残る。VAE を内蔵しないチェックポイントでは、利用者が選ぶ必要がある |
| B: WebUI の設定のままにする | 項目が減るが、SDXL と SD1.5 を行き来すると画像が崩れる(Forge で確認) |

### パラメーターの持ち方

| 案 | 評価 |
|---|---|
| A: GAKEI が固定で定義する(採用) | A1111 互換の API は項目がほぼ共通で、登録の手間なく使える。拡張機能の項目は出せない |
| B: `/sdapi/v1/txt2img` の OpenAPI から自動でフォームを作る | 実装ごとの差を吸収できそうに見えるが、項目が百を超え、意味の分からない項目がフォームに並ぶ。検証も難しい |
| C: ComfyUI のように、送る本文の雛形を登録する | 拡張機能も使えるが、登録の画面と手間が要り、「チェックポイントを選んで描く」手軽さを失う |

### チェックポイントの扱い

| 案 | 評価 |
|---|---|
| A: 一覧をモデルとして見せ、`override_settings` で1回だけ切り替える(採用) | GAKEI から選べて、Run にどのチェックポイントで描いたかが残る。WebUI の画面の選択も元に戻る。切り替えの時間は掛かる |
| B: WebUI で今読み込まれているものだけを使う | 単純で速いが、Run の記録にチェックポイントを残すには毎回問い合わせが要り、GAKEI から変えられない |

### 進捗の受け取り方

| 案 | 評価 |
|---|---|
| A: `force_task_id` を付け、実行の要求と並行して `/internal/progress` を問い合わせる。使えなければ `/sdapi/v1/progress`(採用) | 自分の実行だけの進み具合と途中経過の画像を出せる。`/internal/*` は内部の API なので、変わったときのために切り替え先を持つ |
| A': `/sdapi/v1/progress` だけを問い合わせる | 公開の API だけで済むが、WebUI を直接使っている人の実行と重なると、進み具合が混ざる |
| B: 進捗を出さない | 単純だが、数十秒〜数分の間、何も表示できない |

### Basic 認証

| 案 | 評価 |
|---|---|
| A: 対応し、資格情報を `secrets.json` に置く(採用) | LAN の別の PC の WebUI を、`--api-auth` で守ったまま使える |
| B: 対応しない | 小さいが、LAN に出す WebUI を認証なしで開くよう利用者に求めることになる |

## Trade-off Analysis

ADR-0013 で ComfyUI を加えたときと同じく、ADR-0001 の「迷ったら作らない」との境目にある。それでも加えるのは、A1111 互換の API が ComfyUI と並んでローカルの画像生成で広く使われていて、利用者の多くが GAKEI の履歴と系列の中で使いたいものだからである。

複数プロバイダーの土台(登録簿、capabilities の形、プロバイダーごとのレーン、設定の仕組み)は ADR-0013 で出来ているので、追加の大部分は API の呼び出しとパラメーターの定義に収まる。拡張機能、hires fix、キャンセルを作らないことで、WebUI 固有の処理を小さく保つ。

## Consequences

- ローカル版で、OpenAI、ComfyUI、SD WebUI を同じ画面で使い分けられ、プロバイダーをまたいだ系列が残る。
- ADR-0001 の非ゴール「他プロバイダー対応」から、A1111 互換の API を外す。その他は Phase 3 のまま。
- ADR-0013 の「作らないもの」の「ComfyUI 以外のプロバイダー」に、この ADR で A1111 互換の API を外したことを書き足す。
- ComfyUI の接続設定の仕組み(`comfyui_connection.py`、登録簿の切り替え)を、プロバイダーごとに使える形に一般化する。
- 自動テストは偽の WebUI(HTTP)だけを使い、実物に接続しない。実物での確認は手動で行う。

## Action Items

1. [ ] ADR-0001 の非ゴールと、ADR-0013 の「作らないもの」を改訂する
2. [x] 実物の Forge で、2026-10-10 時点の API を確かめる(txt2img、inpaint、`/internal/progress`、チェックポイントと VAE の切り替えと復元、一覧に無いチェックポイント、`--api-auth`)
3. [ ] 接続設定の仕組みを、ComfyUI と共通にできる形に切り出す
4. [ ] SD WebUI のクライアントとプロバイダーを作る(偽の WebUI でのテストを含む)
5. [ ] 管理者設定のページと、フォームの細部を作る
6. [ ] `docs/sdwebui.md` を書く
7. [ ] Dynamic Prompts に対応する(7章)
8. [ ] 実物の Forge で、t2i、img2img、inpaint、チェックポイントの切り替え、Basic 認証を手動で確認する
