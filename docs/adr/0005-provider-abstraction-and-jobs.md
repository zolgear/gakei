# ADR-0005: プロバイダー抽象化とジョブ実行

**Status:** Proposed
**Date:** 2026-09-21

## Context

当面の生成バックエンドは Azure OpenAI の gpt-image-2.5 系だけだが、構想には ComfyUI API や他の AI API も含まれる。一方で、今それらを実装するのは方針(作り込まない)に反する。

実行面では次の制約がある。
- 高品質・4K の生成は数十秒〜数分かかる。HTTP リクエストの中で待つとタイムアウトする。
- 既定のレート制限は 5 images/分 程度。複数人や一括生成ではすぐ 429 になる。
- コンテナの再起動でジョブが消えてはならない(証跡の欠落になる)。
- `stream` + `partial_images` で途中経過の画像を受け取れる。

## Decision

### プロバイダー抽象化

最小のインターフェースを1つ定義し、Phase 1 では Azure OpenAI アダプターだけを実装する。

```python
class ImageProvider(Protocol):
    name: str
    def capabilities(self) -> ProviderCapabilities: ...   # 対応サイズ、最大入力枚数、対応パラメータ
    async def execute(self, run: RunRequest, on_progress: ProgressCallback) -> RunResult: ...
```

- `RunRequest`: operation, prompt, params(辞書のまま), 入力 Asset の実体
- `RunResult`: 出力画像のバイト列のリスト, usage, provider_request_id, 生のレスポンス情報
- パラメータは共通化しない。UI のフォームは `capabilities()` から組み立て、値は `run.params` にそのまま保存する(ADR-0003)。
- ComfyUI アダプターは作らない。このインターフェースに収まることを机上で確認するに留める。(2026-09-23 追記: ローカルの ComfyUI については ADR-0013 で置き換え。実験中)

### ジョブ実行

- **キューは PostgreSQL の `run` テーブルそのもの。** `status = 'queued'` の行を worker が `SELECT ... FOR UPDATE SKIP LOCKED` で取得する。
- worker は api と同じコンテナイメージを別コマンドで起動する。最初は1プロセス。
- レート制限はデプロイメント単位のトークンバケットで worker 側が守る。429 は指数バックオフで再試行し、回数上限で `failed` にする。
- 状態遷移: `queued → running → succeeded | failed | canceled`。コンテンツフィルターによる拒否は `failed` + `error_code = contentFilter` として残す。
- 起動時に、一定時間以上 `running` のままの行を `queued` に戻す(worker が落ちた場合の回復)。
- 進捗は worker が DB に書き(`LISTEN/NOTIFY`)、api が SSE でブラウザに流す。途中経過画像は一時領域に置き、Asset にはしない。

## Options Considered

| 案 | 複雑さ | 再起動耐性 | 評価 |
|---|---|---|---|
| A: FastAPI の BackgroundTasks | 低 | なし | プロセスが落ちるとジョブが消える。レート制御を複数プロセスで共有できない |
| B: PostgreSQL をキューにする(採用) | 低〜中 | あり | 部品が増えない。Run の記録とキューが同じ行なので不整合が起きない |
| C: Celery / RQ + Redis | 中 | あり | Redis の運用が増える。この規模には過剰 |
| D: Azure Service Bus / Storage Queue | 中 | あり | DBの行とメッセージの二重管理になる。ローカル開発が面倒になる |

## Trade-off Analysis

1分あたり数枚という処理量では、キューの性能は問題にならない。重要なのは「依頼した時点で必ず記録が残り、結果か失敗のどちらかで必ず終わる」ことで、Run の行をそのままキューにする B が最も単純にこれを満たす。処理量が桁違いに増えたら D に移行するが、その時も `run` テーブルが正本である点は変わらない。

## Consequences

- 依頼は即座に `run` として記録され、ブラウザを閉じても実行は続く。
- 一括生成(Phase 2)は Run を複数行入れるだけで実現できる。
- worker を複数にする場合は、レート制限の状態を DB で共有する必要がある(その時に対応)。
- Azure OpenAI への接続はマネージド ID を使い、APIキーを持たない(ADR-0006)。

## Action Items

1. [ ] gpt-image-2.5 の実際のレート制限とクォータを確認し、必要なら引き上げを申請する
2. [ ] ストリーミング(`partial_images`)が Edit でも使えるか実機で確認する
3. [ ] ComfyUI API(`/prompt`, `/history`, WebSocket)がこのインターフェースに収まるか机上確認する(ADR-0013 で置き換え)
