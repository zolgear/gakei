/**
 * ノード(Asset / Run)を「開く」ときの遷移先を決める純粋関数。スタジオ(`/studio`)にいる間は
 * ページを離れず、結果エリアに表示する(`/studio?asset=` / `/studio?run=`)。入力欄を残した
 * まま、ストックや系列パネルから他の画像やその Run を参照できるようにするため
 * (ADR-0009 1章・2026-09-26 追記のサイドバー配置の狙い)。それ以外のページでは従来どおり
 * ビューア(`/assets/:id`)と Run 詳細(`/runs/:id`)へ移動する。
 */
import { buildStudioPath } from '../workspace/assetQueryParam'

export interface OpenableNodeRef {
  id: string
  type: 'asset' | 'run'
}

export function isStudioPath(pathname: string): boolean {
  return /^\/studio(\/|$)/.test(pathname)
}

export function nodeTargetPath(node: OpenableNodeRef, pathname: string): string {
  if (isStudioPath(pathname)) {
    return node.type === 'asset' ? buildStudioPath(node.id) : buildStudioPath(null, null, node.id)
  }
  return node.type === 'asset' ? `/assets/${node.id}` : `/runs/${node.id}`
}
