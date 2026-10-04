/**
 * ストック一覧の種類の絞り込みと React Query キー。
 *
 * キーには、サーバーに `kind` で渡す種類の集合(ADR-0035)、節(各グループか「グループなし」。ADR-0022)、
 * タグの絞り込み(ADR-0024 5章)を含める。どのキーも `'assets'` で始め、
 * `invalidateQueries({ queryKey: ['assets'] })` でストックのパネルとピッカーがまとめて作り直されるようにする。
 * ピッカーのキーは 2 番目に `'picker'` を置き、パネルの節のキー(2 番目は種類の集合)と取り違えないようにする。
 */
export type StockAssetKind = 'generated' | 'upload' | 'sketch' | 'mask'
export type StockKindFilter = 'all' | StockAssetKind

/** 「スケッチとマスクをストックに出す」がオフのときに出さない種類(ADR-0035)。 */
const SKETCH_MASK_KINDS: readonly StockKindFilter[] = ['sketch', 'mask']
/** 設定がオフのときに「すべて」で取る種類。 */
const DEFAULT_VISIBLE_KINDS: readonly StockAssetKind[] = ['generated', 'upload']

const PANEL_KIND_CHOICES: readonly StockKindFilter[] = ['all', 'generated', 'upload', 'sketch', 'mask']

/** ストックのパネルに出す種類のチップ(設定がオフならスケッチとマスクを外す)。 */
export function stockKindChoices(showSketchMask: boolean): StockKindFilter[] {
  return PANEL_KIND_CHOICES.filter((k) => showSketchMask || !SKETCH_MASK_KINDS.includes(k))
}

/** 選んでいるチップが今の設定で出せないとき(スケッチ・マスクを選んだまま設定をオフにした)は「すべて」に戻す。 */
export function normalizeStockKindFilter(kind: StockKindFilter, showSketchMask: boolean): StockKindFilter {
  return stockKindChoices(showSketchMask).includes(kind) ? kind : 'all'
}

/**
 * `GET /api/assets` に渡す `kind`。null は省く(全種類)。設定がオンの「すべて」だけが null で、
 * 設定がオフの「すべて」は生成画像とアップロードに絞る。
 */
export function stockListKinds(kind: StockKindFilter, showSketchMask: boolean): StockAssetKind[] | null {
  const normalized = normalizeStockKindFilter(kind, showSketchMask)
  if (normalized !== 'all') return [normalized]
  return showSketchMask ? null : [...DEFAULT_VISIBLE_KINDS]
}

/** `GET /api/asset-groups` に渡す `kind`(件数と表紙をタイルと合わせる)。チップの選択には従わない。 */
export function stockGroupCountKinds(showSketchMask: boolean): StockAssetKind[] | null {
  return showSketchMask ? null : [...DEFAULT_VISIBLE_KINDS]
}

/** キーに入れる種類の集合の表し方。順序に依らず同じ集合なら同じ文字列。省いた(全種類)ときは 'all'。 */
export function stockKindsKey(kinds: readonly StockAssetKind[] | null): string {
  return kinds && kinds.length > 0 ? [...kinds].sort().join(',') : 'all'
}

/** ストックパネルの節が何を一覧するか。 */
export type StockSectionScope = { groupId: string } | { ungrouped: true }

/** タグで絞っていなければ null(キーにも含めない。絞り込み前のキーと同じ形のまま)。 */
export function stockAssetsQueryKey(
  kinds: readonly StockAssetKind[] | null,
  scope: StockSectionScope,
  tag: string | null = null,
) {
  const kindsKey = stockKindsKey(kinds)
  const base =
    'groupId' in scope
      ? (['assets', kindsKey, 'group', scope.groupId] as const)
      : (['assets', kindsKey, 'ungrouped'] as const)
  return tag ? ([...base, 'tag', tag] as const) : base
}

// -- ストックピッカー(入力画像の追加とアバターの選択。ADR-0020) --------------------

/** ピッカーのチップ。マスクは選ぶ対象にしないので常に出さない。 */
export type StockPickerKindFilter = 'all' | 'generated' | 'upload' | 'sketch'

const PICKER_KIND_CHOICES: readonly StockPickerKindFilter[] = ['all', 'generated', 'upload', 'sketch']

/** ピッカーに出すチップ(スケッチは設定に従う)。 */
export function stockPickerKindChoices(showSketchMask: boolean): StockPickerKindFilter[] {
  return PICKER_KIND_CHOICES.filter((k) => showSketchMask || k !== 'sketch')
}

/** ピッカーが `GET /api/assets` に渡す `kind`。マスクを除くため、常に明示する。 */
export function stockPickerKinds(kind: StockPickerKindFilter, showSketchMask: boolean): StockAssetKind[] {
  const normalized = stockPickerKindChoices(showSketchMask).includes(kind) ? kind : 'all'
  if (normalized !== 'all') return [normalized]
  return showSketchMask ? ['generated', 'upload', 'sketch'] : [...DEFAULT_VISIBLE_KINDS]
}

export function stockPickerQueryKey(kinds: readonly StockAssetKind[]) {
  return ['assets', 'picker', stockKindsKey(kinds)] as const
}
