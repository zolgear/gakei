# ADR-0011: ライセンスとソースの公開

**Status:** Proposed
**Date:** 2026-09-23

## Context

GAKEI のソースを OSS として公開したい。これまでリポジトリは private で、ライセンスを定めていなかった。

公開の前に確認したこと:

- **著作権者:** 個人(Toru Suzuki)。職務著作ではない。
- **依存のライセンス:** 本体のライセンスを縛るものはない。
  - backend の直接依存は MIT(fastapi、sqlalchemy、alembic、pydantic-settings)、BSD-3-Clause(uvicorn、httpx、websockets)、MIT-CMU(pillow)、Apache-2.0(openai、python-multipart)。
  - 推移的依存の certifi は MPL-2.0。ファイル単位のコピーレフトで、無改変のまま使う限り本体に影響しない。
  - frontend のバンドルに入るのは MIT(react、react-dom、react-router、@tanstack/react-query、@xyflow/react、react-zoom-pan-pinch)と、OFL-1.1 のフォント(@fontsource の IBM Plex Sans JP、JetBrains Mono)。
  - lightningcss(MPL-2.0)はビルド時だけ使い、成果物に入らない。
  - GPL、LGPL、AGPL、独自ライセンスのものはない。
- **秘密情報:** `.env`(API キー)、`data/`(生成画像と DB)は、最初から gitignore 済みで、履歴に入ったことがない。
- **履歴と PR:** 過去のコミットと PR には、公開向けに整理する前の文書(個人名、特定の組織の事情)が含まれる。private リポジトリをそのまま public にすると、これらも公開される。履歴を書き換えて force-push しても、GitHub の PR の参照(`refs/pull/*`)に古いコミットが残る。

## Decision

1. **ライセンスは Apache License 2.0。** リポジトリ直下に `LICENSE`(全文)と `NOTICE`(`Copyright 2026 Toru Suzuki`)を置く。`backend/pyproject.toml` と `frontend/package.json` にも `Apache-2.0` と書く。`frontend/package.json` の `"private": true` は npm への誤公開を防ぐために残す。
2. **公開用のリポジトリは新しく作る。** 公開向けに整理した時点の main を1つのコミットにまとめ、そこから履歴を始める。今の private リポジトリは改名して archive し、公開前の履歴と PR の保管場所として残す。以後の開発は公開リポジトリで行う。
3. **公開する文書から、個人名と特定の組織の事情を除く。** ADR の設計上の要件(セルフホスト、日本国内、単一組織向け、Entra ID、証跡)はそのまま残し、表現だけを一般化する。
4. **プロジェクト名は GAKEI。** 表示名は大文字で書く。パッケージ名や localStorage のキーなどの識別子は、小文字の `gakei` のまま変えない。

ソースを公開することは、ADR-0001 の非ゴール「外部公開」とは別の話である。あちらは、サービスを外部の利用者に提供しないという意味で、この決定の後も変わらない。

## Options Considered

| 案 | 評価 |
|---|---|
| A: Apache-2.0(採用) | 特許の許諾と特許訴訟時の許諾終了が明文化されている。NOTICE の仕組みがある。依存の openai SDK と同じ |
| B: MIT | 短く単純。特許の条項がない |
| C: GPL / AGPL | 利用者に改変部分の公開を求めることになる。組織内でセルフホストして改変する使い方と相性が悪い |

公開の方法:

| 案 | 評価 |
|---|---|
| A: 新しいリポジトリを1コミットから始める(採用) | 旧履歴と PR が確実に外に出ない。旧 PR の議論は private 側でしか見られない |
| B: 履歴を書き換えて同じリポジトリを公開する | PR と履歴が残るが、PR の参照に古いコミットが残り、完全には消せない |

## Consequences

- 誰でも利用、改変、再配布できる。再配布する側は LICENSE と NOTICE を添える。
- 公開リポジトリの `git log` や `git blame` は公開の時点より前に遡れない。経緯は ADR で追う。
- dist やコンテナイメージを配布物として出すときは、同梱する依存(React や @fontsource のフォントなど)のライセンス表記を別途用意する。
- 設定画面の末尾に「GAKEI について」を置き、著作権表示、ライセンス(LICENSE へのリンク)、GitHub のリポジトリへのリンク、同梱している主な依存のクレジット(名前、ライセンス、URL)を出す(2026-09-24)。依存の一覧はフロントのコードに持ち、`package.json` と `pyproject.toml` の直接依存と食い違ったらテストで落とす。これは画面上の案内で、配布物に添えるライセンス全文の代わりにはならない(Action Item 4 は残る)。
- 2026-09-25 改訂: 依存のクレジットの一覧は画面から外した。依存のライセンスは GitHub 側の LICENSE を正とし、画面には著作権表示、ライセンス、GitHub のリポジトリへのリンクだけを出す(商用ライセンスの依存を同梱する場合は、改めて表示を検討する)。一覧と、直接依存との食い違いを検出するテストも削除した。

## Action Items

1. [x] `LICENSE`、`NOTICE`、パッケージのライセンス欄を追加する
2. [x] ADR、CLAUDE.md、README から個人名と特定の組織の事情を除く
3. [ ] 公開の準備ができたら、private リポジトリを改名して archive し、公開リポジトリを作る
4. [x] 配布物を出すときに、第三者のライセンス表記を用意する(ADR-0021 4章。ビルド時に生成し、API と GitHub Release に添付(2026-09-27))
5. [x] 設定画面に「GAKEI について」(著作権、ライセンス、GitHub)を置く(2026-09-24。依存のクレジットは 2026-09-25 に外した)
