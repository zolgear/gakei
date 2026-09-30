# ADR 一覧

設計判断の記録(Architecture Decision Record)。ファイル名の番号と中身の `# ADR-000N` は一致している。

| ADR | ファイル | タイトル | 状態 |
|---|---|---|---|
| ADR-0001 | [0001-scope-and-non-goals.md](0001-scope-and-non-goals.md) | スコープと非ゴール | Accepted |
| ADR-0002 | [0002-tech-stack.md](0002-tech-stack.md) | 技術スタック(FastAPI + React SPA + PostgreSQL) | Proposed(Python / FastAPI は決定済み) |
| ADR-0003 | [0003-data-model-lineage.md](0003-data-model-lineage.md) | データモデル — Asset と Run による世代グラフ | Proposed |
| ADR-0004 | [0004-image-storage-and-delivery.md](0004-image-storage-and-delivery.md) | 画像の保存と配信(4K対応) | Proposed |
| ADR-0005 | [0005-provider-abstraction-and-jobs.md](0005-provider-abstraction-and-jobs.md) | プロバイダー抽象化とジョブ実行 | Proposed |
| ADR-0006 | [0006-auth-and-audit.md](0006-auth-and-audit.md) | 認証と証跡 | Proposed |
| ADR-0007 | [0007-hosting-azure.md](0007-hosting-azure.md) | ホスティング(Azure 日本リージョン) | Proposed(本文は未作成。Azure は決定済み) |
| ADR-0008 | [0008-local-mvp.md](0008-local-mvp.md) | ローカルMVP(OpenAI API 直 / ローカルFS + SQLite) | Proposed |
| ADR-0009 | [0009-studio-shell-prompt-sets-lineage-graph.md](0009-studio-shell-prompt-sets-lineage-graph.md) | スタジオの画面構成、プロンプトセット、系列グラフの前倒し | Proposed |
| ADR-0010 | [0010-sketch-input.md](0010-sketch-input.md) | スケッチ入力(GPT Image 2.5 の「Sketch」相当) | Proposed |
| ADR-0011 | [0011-license-and-publication.md](0011-license-and-publication.md) | ライセンスとソースの公開 | Proposed |
| ADR-0012 | [0012-easy-local-setup.md](0012-easy-local-setup.md) | 個人がローカルで手軽にセルフホストできるようにする | Proposed |
| ADR-0013 | [0013-comfyui-provider.md](0013-comfyui-provider.md) | ローカル ComfyUI をプロバイダーに加える | Proposed(実験的な機能として main に取り込み済み) |
| ADR-0014 | [0014-embedded-lineage-metadata.md](0014-embedded-lineage-metadata.md) | ダウンロードする PNG に系列情報を埋め込む | Proposed(main に取り込み済み) |
| ADR-0015 | [0015-i18n-english-ui.md](0015-i18n-english-ui.md) | 画面の多言語化(日本語と英語) | Proposed |
| ADR-0016 | [0016-docker-compose.md](0016-docker-compose.md) | Docker イメージと Compose ファイルを用意する | Proposed |
| ADR-0017 | [0017-openai-base-url-and-drop-provider.md](0017-openai-base-url-and-drop-provider.md) | OpenAI の接続先(Base URL)を変えられるようにし、`PROVIDER` 設定を廃止する | Proposed |
| ADR-0018 | [0018-embedded-generation-metadata.md](0018-embedded-generation-metadata.md) | 他のツールが画像に埋め込んだ生成メタ情報の表示 | Proposed |
| ADR-0019 | [0019-oidc-auth-and-admin-role.md](0019-oidc-auth-and-admin-role.md) | OIDC によるユーザー認証と Admin ロール(個人モードは既定のまま) | Proposed |
| ADR-0020 | [0020-user-avatar.md](0020-user-avatar.md) | ユーザーのアバター画像(アップロードと生成画像からの選択) | Proposed |
| ADR-0021 | [0021-release-and-container-image.md](0021-release-and-container-image.md) | リリース(バージョンとタグ)とコンテナイメージの公開 | Proposed |
| ADR-0022 | [0022-asset-groups.md](0022-asset-groups.md) | グループ(ストックの手動整理) | Proposed |
| ADR-0023 | [0023-mcp-server.md](0023-mcp-server.md) | MCP サーバー(外部の AI エージェントから GAKEI を操作する) | Proposed |
| ADR-0024 | [0024-auto-title-and-tags.md](0024-auto-title-and-tags.md) | タイトルとタグ(人の編集と、VLM・ONNX タガーによる自動付与) | Proposed |
| ADR-0025 | [0025-owner-only-visibility.md](0025-owner-only-visibility.md) | 認証モードでは本人のものだけを見せる(他人のプライバシーを守る) | Proposed |
| ADR-0026 | [0026-storage-directory-hierarchy.md](0026-storage-directory-hierarchy.md) | 原本をプロバイダー・モデル別のフォルダに保存する(ローカルFS) | Proposed |
| ADR-0027 | [0027-postgresql-option.md](0027-postgresql-option.md) | メタデータの DB に PostgreSQL も選べるようにする | Proposed |
| ADR-0028 | [0028-object-storage-option.md](0028-object-storage-option.md) | 画像の保存先に Azure Blob Storage と S3 互換ストレージも選べるようにする | Proposed |

新しい ADR は `00NN-<slug>.md` で足し、この表に行を加える。書式は [0017](0017-openai-base-url-and-drop-provider.md) を参考にする(Status、Date、Context、Decision、Options Considered、Trade-off Analysis、Consequences、Action Items)。
