"""ローカルで動かすモデル(ONNX)の共通の部品(ADR-0024 3章、ADR-0033 2章)。

- `downloader`: Hugging Face から固定リビジョンのファイルを取り、sha256 を確かめて置く。
- `onnx_runtime`: onnxruntime のセッションの設定と、読み込む前の空きメモリの確認。
- `residency`: WD Tagger と埋め込みのモデルを同時にメモリに載せないための調停。

モデルの一覧(カタログ)と置き場所は、使う側(`app/annotation/wd_models.py`、
`app/embedding/catalog.py`)が持つ。
"""
