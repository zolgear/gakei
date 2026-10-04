/**
 * 共有リンクの範囲(ADR-0029 2章)の並びと、画面に出す名前。
 */
import type { ShareScope } from '../../api/client'
import { msg } from '../../i18n'

export const SHARE_SCOPES: readonly ShareScope[] = ['single', 'ancestors', 'lineage']

export function shareScopeLabel(scope: ShareScope): string {
  return msg().share.scopes[scope]
}

/** 系列のグラフを見せる範囲か(「この1枚」は画像1枚だけなので出さない)。 */
export function showsLineage(scope: ShareScope): boolean {
  return scope !== 'single'
}
