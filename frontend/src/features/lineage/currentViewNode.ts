/**
 * 現在の URL から「今どのノード(Asset/Run)を見ているか」を判定する純粋関数。
 * サイドバーの系列パネルが、ピン留め中でも「現在地」を強調表示するために使う。
 */
export interface CurrentViewNode {
  id: string
  type: 'asset' | 'run'
}

export function deriveCurrentViewNode(
  pathname: string,
  studioAssetId: string | null = null,
): CurrentViewNode | null {
  const assetMatch = pathname.match(/^\/assets\/([^/]+)/)
  if (assetMatch) return { id: assetMatch[1], type: 'asset' }
  const runMatch = pathname.match(/^\/runs\/([^/]+)/)
  if (runMatch) return { id: runMatch[1], type: 'run' }
  // スタジオは URL に表示中の Asset が載らない(生成直後など)ので、ResultPane が
  // 共有コンテキストに書いた「表示中のプレビュー」を現在地とする。
  if (studioAssetId && /^\/studio(\/|$)/.test(pathname)) return { id: studioAssetId, type: 'asset' }
  return null
}
