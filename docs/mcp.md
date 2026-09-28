# AI エージェントから使う(MCP)

GAKEI は MCP(Model Context Protocol)のサーバーとして動き、Claude Code などの AI エージェントから画像の生成、ストックの検索、グループへの整理ができる。設計は [ADR-0023](adr/0023-mcp-server.md)。

エンドポイントは GAKEI と同じサーバーの `/mcp`(Streamable HTTP)。別のプログラムをインストールする必要はない。

## 1. 有効にする

既定では無効になっている。管理者が設定画面の「管理者設定」→「MCP」で有効にする。

- **1 時間あたりの上限:** MCP 経由で作れる Run の数。既定は 30 件で、0 にすると生成を止め、検索などの参照だけ使える。エージェントは人より速く繰り返し呼ぶので、課金の暴走を防ぐために使う。
- 接続先の URL と登録コマンドの例も、この画面に出る。

## 2. エージェントに登録する

### 個人モード(既定。認証なし)

Claude Code の場合:

```bash
claude mcp add --transport http gakei http://127.0.0.1:8000/mcp
```

ポートを変えている場合は URL を合わせる。

### 認証モード(`AUTH_MODE=oidc`)

エージェントはブラウザのログインを使えないので、アクセストークンで接続する。

1. 画面の「ユーザー設定」→「アクセストークン」で、名前を付けてトークンを発行する。値は発行したときに 1 回だけ表示されるので、その場でコピーする。
2. トークンをヘッダーに付けて登録する。

```bash
claude mcp add --transport http gakei https://gakei.example.com/mcp \
  --header "Authorization: Bearer gakei_..."
```

- トークンは発行した人として動き、そのトークンで作った Run の実行者もその人になる。
- 漏れた、または使わなくなったトークンは、同じ画面で失効させる。
- トークンは `/mcp` でしか使えない。REST API には使えない。

### その他のエージェント

Streamable HTTP に対応したクライアントなら、URL(と認証モードではヘッダー)を登録すれば使える。stdio のサーバーしか登録できないクライアント(Claude Desktop の設定ファイルなど)では、`mcp-remote` のような中継を使う。

```json
{
  "mcpServers": {
    "gakei": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://127.0.0.1:8000/mcp"]
    }
  }
}
```

## 3. 使えるツール

| ツール | 内容 |
|---|---|
| `get_capabilities` | 使えるプロバイダー、モデル、パラメーター、サイズ |
| `generate_image` | Generate / Edit を実行する(**課金を伴う**)。入力画像とマスクは Asset ID で渡す。既定では完了まで待ち、出力を返す |
| `get_run` | Run の状態と出力。完了まで待つこともできる |
| `cancel_run` | 待機中の Run を取り消す |
| `search_assets` | ストックを検索する(キーワード、種類、グループ) |
| `get_asset` | Asset の情報、主たる親、生成した Run |
| `upload_image` | 画像(base64)を取り込み、Edit の入力にできるようにする |
| `list_prompt_sets` | プロンプトセットの一覧 |
| `list_groups` / `create_group` / `move_to_group` | グループの一覧、作成、Asset の移動 |

削除と設定の変更は提供していない。画面で行う。

- 出力は Asset ID、原本の URL、画面で開く URL で返る。本文に載る画像はサムネイル(512px)だけで、4K の原本は URL から取得する。認証モードでは、URL を開くのにブラウザのログインが要る。
- MCP 経由で作った Run は、Run の詳細に「実行元: MCP」と出る。

## 4. 注意

- **公開範囲:** 個人モードの `/mcp` には認証がない。既定の `127.0.0.1` での待ち受けのまま使う。LAN やインターネットに出す場合は認証モードにする([auth.md](auth.md))。
- **ブラウザからの呼び出し:** DNS リバインディング対策として、`Origin` ヘッダーの付いた呼び出しは、GAKEI 自身の origin でなければ拒否する。ホスト名で公開していて、ブラウザで動く MCP クライアントを使う場合は `PUBLIC_BASE_URL` を設定する([configuration.md](configuration.md))。
- **上限は歯止め:** 上限はインスタンス全体の件数で、利用者ごとではない。同時に呼ばれると数件超えることがある。
