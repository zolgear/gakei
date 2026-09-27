/**
 * ストック一覧の React Query キー。kind に加えてグループ絞り込み(ADR-0022)を含める。
 * 既存の `['assets', kind]`(`StockPickerGrid` など)という形とプレフィックスを揃えつつ、
 * 絞り込みが無いときは 'all' を要素にする(配列の要素に `undefined`/`null` を混ぜると
 * React Query のキー比較・ログで扱いにくいため)。
 */
export type StockKindFilter = 'all' | 'generated' | 'upload' | 'sketch' | 'mask'

export function stockAssetsQueryKey(kind: StockKindFilter, groupId: string | null) {
  return ['assets', kind, groupId ?? 'all'] as const
}
