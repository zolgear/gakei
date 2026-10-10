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
  - Edit だけ: `denoising_strength`、`resize_mode`。マスクがあるときは `mask_blur`、`inpainting_fill`、`inpaint_full_res`、`inpaint_full_res_padding`。マスクの無い Run でこれらを指定したら、黙って捨てずに 422 にする(capabilities では `mask_only` の印を付け、フォームはマスクが無い間は無効にする)。
  - Edit でサイズを指定しなければ、入力画像の寸法を、縦横比を保って長辺 2048px に収めて使う(8 の倍数には丸めない)。
  - `sampler_name` と `scheduler` の選択肢は接続先から補う。取れなければ自由入力にはせず、項目を出さない(WebUI の既定に任せる)。
  - サイズの上限は長辺 2048px にする(大きいサイズは WebUI 側で VRAM が足りなくなりやすいため。ADR-0004 の 3840px とは別)。幅と高さは 8 の倍数でなくてよく、そのまま送る(2026-10-10 改訂。当初は 8 の倍数に限っていたが、他のツールの画像の生成情報(9章)に 8 の倍数でないサイズがあり、ユーザーの指示でそのまま設定できるようにした)。WebUI は内部で 8 の倍数に切り捨てて描き(Forge で確認。803×601 を送ると 800×600 の画像になり、infotext には 803x601 と残る)、Asset は返った画像の寸法になる。Run には送った値が残る。
  - **Clip skip:** パラメーター `clip_skip`(1〜12、既定 1)を `override_settings.CLIP_stop_at_last_layers` で毎回送る(2026-10-10 追記)。VAE と同じく、WebUI の設定に任せると Run の記録と実際が食い違うため。
- `save_images` は false のまま送る。原本は GAKEI が持つ(ADR-0004)。
- **送らないもの:** `distilled_cfg_scale`(Forge だけの項目)、7章で対応を決めた拡張機能以外の `alwayson_scripts`(ControlNet などの拡張)、refiner、`script_name`(hires fix は10章で対応する)。LoRA は、プロンプトに `<lora:名前:重み>` と書けば、そのまま WebUI に届く(GAKEI は解釈も管理もしない。ADR-0001 の非ゴール「LoRA 管理」は変えない)。

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

### 8. LoRA の選択(2026-10-10 追記)

LoRA は、プロンプトに `<lora:名前:重み>` と書けば WebUI が読み込む(2章)。名前を覚えて打つのは難しいので、**接続先の LoRA の一覧から選んでプロンプトに入れる** 補助を作る。LoRA のファイルの管理(ダウンロード、削除、名前の変更)はしない(ADR-0001 の非ゴール「LoRA 管理」は変えない)。

A1111 互換の API で使うもの(Forge で確認):

- `GET /sdapi/v1/loras` → `[{name, alias, path, metadata}]`。`metadata` は学習時の情報(kohya の `ss_*`、`modelspec.*`)で、無いものもある。
  - ベースモデルは `ss_base_model_version`(`sdxl_base_v1-0`、`sd_v1` など)か `modelspec.architecture` で分かるが、分からない LoRA も多い(確認した環境では約 4 割)。
  - `ss_tag_frequency` は学習データのタグの出現数で、トリガーワードの手がかりになる。
  - `ss_dataset_dirs` や `ss_training_comment` など、学習した人の環境の情報(パスを含む)も入っている。
- `POST /sdapi/v1/refresh-loras` で一覧を読み直す。

決めたこと:

- **API:** `GET /api/sdwebui/loras`(`require_user`)。1件ごとに `name`、`alias`(name と違うときだけ)、`base_model`(`sdxl` / `sd1` / `null`)、`trigger_tags`(`ss_tag_frequency` を足し合わせて多い順に最大 20 個。`_` は空白に)だけを返す。**`path` とその他のメタ情報は返さない。** 一覧はチェックポイントの一覧と同じくキャッシュし、「一覧を読み直す」で LoRA も読み直す。
- **画面:** プロンプト欄の操作に「LoRA」を置く(SD WebUI のモデルを選んでいるときだけ)。検索できる一覧から選ぶと、重み(既定 1.0、0.1 刻み)を決めて `<lora:名前:重み>` をプロンプトに足す。トリガーワードの候補はチップで出し、タップでプロンプトに足す。タグモード(ADR-0039)では LoRA は1つのチップになる。
- **ベースモデルの目印:** 一覧に SDXL / SD1.5 / 不明を出す。チェックポイントのベースモデルは API から分からないので、合わない LoRA を隠したり警告したりはしない。
- **記録:** LoRA はプロンプトの一部として Run に残る(ADR-0003)。WebUI が infotext に入れる `Lora hashes` も、これまでどおり `usage` に残る。
- 作らないもの: LoRA のプレビュー画像(`/sd_extra_networks/thumb` は WebUI の画面用の内部の API)、ブロックごとの重み、LoRA のファイルの管理、ComfyUI での LoRA の選択。

### 9. 画像の生成情報をフォームに読み込む(2026-10-10 追記)

WebUI の「PNG 内の情報を表示 → txt2img に転送」に当たる操作を作る。A1111 互換の WebUI が画像に埋め込んだ生成情報(`parameters`。ADR-0018 で既に読める)から、SD WebUI の生成フォームの値を組み立てる。

- **入口は2つ。**
  - **ストックの画像から:** 生成情報が A1111 形式の画像(ADR-0018 の `tool = "a1111"`)のビューアに「SD WebUI のフォームに読み込む」を置く。
  - **ストックに入れずに:** 生成のフォームに「画像から設定を読み込む」を置き、ファイルの選択・ドロップ・貼り付けで画像を渡す。サーバーはメモリの上で生成情報だけを読み、**画像を保存しない**(ファイルにも DB にもログにも残さない。Asset にしない)。大きさの上限はアップロードと同じ。
- **API:** `POST /api/sdwebui/import-params`(`require_user`)。本文は画像のファイル(multipart)か、`asset_id`(本人に見える Asset。ADR-0025)。応答はフォームに入れる値(`model`、`prompt`、`params`)と、入れられなかった項目の一覧(`unapplied`。名前と値)と、注意(チェックポイントが見つからない、など)。対応付けはサーバーで行う(WebUI の一覧が要るため)。
- **対応付け:**
  - プロンプトとネガティブプロンプト。Dynamic Prompts の `Template` / `Negative Template` があれば、展開前のそれを使う(7章)。
  - `Steps`、`Sampler`、`Schedule type`(表示名から WebUI のスケジューラーの名前へ。大文字小文字を無視)、`CFG scale`、`Seed`、`Size`(サイズの制約に収まらなければ入れない)。
  - `Model` は接続先のチェックポイントの `model_name` と照合し、無ければ `Model hash` を `/sdapi/v1/sd-models` のハッシュと照合する。見つからなければモデルは変えず、注意を返す。
  - VAE(`VAE` または Forge の `Module 1` など)は、接続先の VAE の一覧にあれば入れる。
  - `Clip skip` は `clip_skip` に入れる。`Model hash` と `VAE hash` は照合に使えたら「読み込めなかった項目」に出さない。`Version` は読み込み元として別に見せる。
  - 入れられないもの(拡大後の寸法の指定など10章で作らない hires fix の項目、hires でない画像の `Denoising strength`、ADetailer や ControlNet の項目、`Version` など)は `unapplied` に並べ、画面で「読み込めなかった項目」として見せる。LoRA はプロンプトの `<lora:…>` としてそのまま入る。
- **フォームへの反映:** プロバイダーを SD WebUI、操作を Generate にし、プロンプトとパラメーターを置き換える(入力画像は変えない)。今のプロンプトが空でなければ確かめてから置き換える。SD WebUI が有効でないときは、どちらの入口も出さない。
- 作らないもの: A1111 形式以外(ComfyUI、NovelAI など)の生成情報からの読み込み、img2img への読み込み(WebUI の「img2img に転送」に当たるもの)。

### 10. 高解像度補助(hires fix)(2026-10-10 追記)

当初は「作らない」としたが、SDXL では低めの解像度で描いてから拡大して描き直す hires fix が日常的に使われるため、ユーザーの指摘を受けて対応する。txt2img だけで使う(img2img には無い)。

A1111 互換の API で使うもの(Forge で確認):

- `txt2img` の本文の `enable_hr`、`hr_scale`(倍率)、`hr_resize_x` / `hr_resize_y`(0 なら倍率を使う)、`hr_upscaler`、`hr_second_pass_steps`(0 なら本体と同じ)、`denoising_strength`(2回目の描き直しの強さ)、`hr_cfg`(Forge だけ)、`hr_additional_modules`(Forge だけ)、`hr_checkpoint_name`、`hr_sampler_name`、`hr_scheduler`、`hr_prompt`、`hr_negative_prompt`。
- アップスケーラーの一覧は `GET /sdapi/v1/upscalers`(`None`、`Lanczos`、ESRGAN 系など)と `GET /sdapi/v1/latent-upscale-modes`(`Latent` など)を合わせたもの。
- **Forge では `hr_additional_modules` を送らないと 500 になる**(`argument of type 'NoneType' is not iterable`)。`["Use same choices"]` で、1回目と同じモジュール(VAE など)を使う。
- **Forge の `hr_cfg` の既定は 1.0** で、送らないと2回目を CFG 1 で描く。
- `info` の `width` / `height` は1回目の寸法のまま。出力の画像は拡大後の寸法になる。

決めたこと:

- **パラメーター(Generate だけ):** `hires`(有効/無効、既定は無効)、`hr_scale`(1〜4、0.05 刻み、既定 2)、`hr_upscaler`(接続先の一覧。既定は一覧に `Latent` があればそれ)、`hr_second_pass_steps`(0〜150、既定 0 = 本体と同じ)、`hr_denoising_strength`(0〜1、既定 0.5。本文では `denoising_strength` として送る)、`hr_cfg`(Forge のときだけ。1〜30。指定がなければ本体の `cfg_scale` と同じ値を送る)。`hires` が無効のときは、ほかの項目を無効にする。
- アップスケーラーの選択肢は latent の方式 → `/upscalers` の順で、`None` は出さない(単純な拡大で、`Lanczos` で足りる)。2つの一覧がどちらも取れなければ、hires の項目を出さない。
- `hires` が無効のときは `enable_hr` も hr の値も送らない。無効なのに hr の項目を指定したら 422(マスクの無い Run のマスクの項目と同じ考え方)。Edit での hr の項目、A1111 での `hr_cfg` も 422。
- 拡大後の寸法を指定する方式(`hr_resize_x` / `hr_resize_y`)、2回目だけ別のチェックポイント・サンプラーにする項目は作らない(倍率だけにする)。
- **2回目のプロンプト:** `hr_prompt`、`hr_negative_prompt`(空なら本体と同じ。送らない)を足す(2026-10-10 追記。9章の読み込みで `Hires prompt` / `Hires negative prompt` もフォームに入れる)。
- **Forge では `hr_additional_modules: ["Use same choices"]` を常に送る。** VAE は1回目の指定(2章)がそのまま使われる。
- **寸法の上限:** 1回目の寸法は2章の制約(長辺 2048)。拡大後の長辺は 4096 まで(超える倍率は 422)。
- 送った値はこれまでどおり `sdwebui_request` に残る。9章の読み込みでも、`Hires upscale`、`Hires steps`、`Hires upscaler`、`Hires CFG Scale`、`Denoising strength`(hires の画像のとき)をフォームに入れる(拡大後の寸法の指定 `Hires resize` や、別のチェックポイント・プロンプトは「読み込めなかった項目」)。

### 作らないもの

- InvokeAI、Fooocus、SwarmUI など、A1111 互換でない API
- 7章で対応を決めた拡張機能(今は Dynamic Prompts)以外の拡張機能の項目、`script_name`
- refiner、アップスケール(`extra-single-image`)。hires fix は10章で対応する(2026-10-10 改訂)
- チェックポイント・LoRA・VAE のダウンロードや管理、WebUI の設定(`/options`)の変更(LoRA を一覧から選んでプロンプトに入れる補助は8章で作る)
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
4. [x] SD WebUI のクライアントとプロバイダーを作る(偽の WebUI でのテストを含む。Generate と Edit(img2img・inpaint))
5. [ ] 管理者設定のページと、フォームの細部を作る
6. [ ] `docs/sdwebui.md` を書く
7. [ ] Dynamic Prompts に対応する(7章。txt2img と img2img の両方。img2img は img2img 用の script-info で組み立てる)
8. [ ] 実物の Forge で、t2i、img2img、inpaint、チェックポイントの切り替え、Basic 認証を手動で確認する
9. [x] LoRA の選択(8章)を作る(`GET /api/sdwebui/loras`、「一覧を読み直す」での `refresh-loras`、プロンプト欄の「LoRA」。偽の WebUI でのテストを含む)
10. [x] 画像の生成情報をフォームに読み込む(9章)を作る(`POST /api/sdwebui/import-params`、スタジオの「画像から設定を読み込む」、ビューアの「SD WebUI のフォームに読み込む」。偽の WebUI でのテストと、画像を保存しないことのテストを含む。実物の WebUI の画像での確認は別に行う)
11. [x] 高解像度補助(10章)を作る(Generate の `hires` と `hr_*`、アップスケーラーの一覧、Forge の `hr_additional_modules` と `hr_cfg`、拡大後の長辺 4096 の上限、9章の読み込みの対応付け。偽の WebUI でのテストを含む。実物の Forge での確認は別に行う)
