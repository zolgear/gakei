/**
 * ビューアのタグ欄の折り畳み(ADR-0024 5章の「タグの追加と削除」の置き場所)。
 *
 * タグが多いと情報欄が縦に伸び、その下の大きさ・プロンプト・ダウンロードなどが見えなくなる。
 * そこで件数が `TAG_COLLAPSE_THRESHOLD` を超えたら先頭の `TAG_COLLAPSED_COUNT` 件だけを出し、
 * 「すべて表示」で残りを開く。高さ(max-height)ではなく件数で切るのは、チップの幅が文字数で
 * 大きく変わっても判定がぶれず、測定(ResizeObserver)なしで確実に決まるため。
 * 並びはサーバーの順(人のタグが先、自動は確信度順)のまま変えない。
 * 閾値より先頭の件数を小さくして、畳んでも 1〜2 件しか隠れない中途半端な状態を避ける。
 * 情報欄は幅が狭く(デスクトップで 1 行にチップ 2〜3 個)、8 件でおよそ 3〜4 行になる。
 */
export const TAG_COLLAPSE_THRESHOLD = 12
export const TAG_COLLAPSED_COUNT = 8

export interface TagCollapseState {
  /** 折り畳みのボタンを出すか(件数が閾値を超えているか)。 */
  collapsible: boolean
  /** いま表示する件数(先頭から)。 */
  visibleCount: number
  /** 畳んでいて隠れている件数(展開中は 0)。 */
  hiddenCount: number
}

export function tagCollapseState(total: number, expanded: boolean): TagCollapseState {
  const count = Math.max(0, Math.floor(total))
  const collapsible = count > TAG_COLLAPSE_THRESHOLD
  const visibleCount = collapsible && !expanded ? TAG_COLLAPSED_COUNT : count
  return { collapsible, visibleCount, hiddenCount: count - visibleCount }
}
