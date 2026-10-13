"""生成のアシスタント(ADR-0048)。チャットでプロンプトとプロンプトセットを作り、生成を任せる。

- `engine.py`: LLM の呼び出し(Responses / Chat Completions、tool calling、ストリーミング、Fake)。
- `prompts.py`: システムプロンプト(利用者向けの文言ではないのでコードに置く。7章)。
- `tools.py`: 道具の定義と実行(4・5・6・9章)。
- `service.py`: 1回の発言の処理(LLM と道具の往復、保存、SSE のイベント)。
- `runtime.py`: プロセス内の状態(エンジン、1時間の上限の記録、進行中の会話)。
"""
