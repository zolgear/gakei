/**
 * ストックの絞り込み(種類、お気に入り、タグ)。見出し行にはじょうごのアイコンのボタン1つだけを置き、押すと見出し行の
 * 下に小さなポップオーバーを開く(履歴のツールバーの絞り込みと同じ考え方)。中身は種類のチップ
 * (「表示」の設定で出す種類だけ。ADR-0035)、種類とは独立に切り替えるお気に入りのチップ(ADR-0055)、
 * タグの入力(`StockTagFilter`。ADR-0024 5章)。
 * 見出しの文字は置かない。
 *
 * 絞り込み中(種類が「すべて」以外か、お気に入りかタグで絞っている)は、ボタンをアクセント色にして小さな点を付ける。
 * ポップオーバーは見出し行(`position: relative`)の幅いっぱいに出す(狭いサイドバーでも右にはみ出さない)。
 * 種類を選んでも閉じない(続けてタグも選べるように)。外側の押下と Esc で閉じる。
 */
import { useEffect, useRef } from 'react'
import { FilterIcon, HeartIcon } from '../../components/icons'
import { useI18n, type Messages } from '../../i18n'
import type { StockKindFilter } from './stockQueryKey'
import { StockTagFilter } from './StockTagFilter'
import { useHeaderPopover } from './useHeaderPopover'
import styles from './StockPanel.module.css'

function kindLabel(t: Messages, kind: StockKindFilter): string {
  switch (kind) {
    case 'all':
      return t.stock.kindAll
    case 'generated':
      return t.stock.kindGenerated
    case 'upload':
      return t.stock.kindUpload
    case 'sketch':
      return t.stock.kindSketch
    case 'mask':
      return t.stock.kindMask
    case 'composite':
      return t.stock.kindComposite
    case 'regions':
      return t.stock.kindRegions
  }
}

interface StockFilterMenuProps {
  kind: StockKindFilter
  kindChoices: StockKindFilter[]
  onKindChange: (kind: StockKindFilter) => void
  favoriteOnly: boolean
  onFavoriteOnlyChange: (value: boolean) => void
  active: boolean
}

export function StockFilterMenu({
  kind,
  kindChoices,
  onKindChange,
  favoriteOnly,
  onFavoriteOnlyChange,
  active,
}: StockFilterMenuProps) {
  const { t } = useI18n()
  const label = t.stock.filter
  const { open, toggle, rootRef, triggerRef } = useHeaderPopover()
  const panelRef = useRef<HTMLDivElement | null>(null)

  // 開いたら選んでいる種類のチップにフォーカスを移す。
  useEffect(() => {
    if (!open) return
    panelRef.current?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus()
  }, [open])

  return (
    <div className={styles.popoverRoot} ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className={styles.iconButton}
        data-active={active}
        data-stock-filter-trigger=""
        aria-label={label}
        title={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={toggle}
      >
        <FilterIcon size={16} />
        {active && <span className={styles.activeDot} aria-hidden="true" />}
      </button>

      {open && (
        <div ref={panelRef} className={styles.filterPopover} role="dialog" aria-label={label}>
          <div className={styles.chips} role="radiogroup" aria-label={t.stock.kindHeading}>
            {kindChoices.map((id) => (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={kind === id}
                className={styles.chip}
                data-active={kind === id}
                onClick={() => onKindChange(id)}
              >
                {kindLabel(t, id)}
              </button>
            ))}
          </div>
          <div className={styles.chips}>
            <button
              type="button"
              aria-pressed={favoriteOnly}
              className={`${styles.chip} ${styles.favoriteChip}`}
              data-active={favoriteOnly}
              onClick={() => onFavoriteOnlyChange(!favoriteOnly)}
            >
              <HeartIcon size={12} filled={favoriteOnly} />
              {t.stock.filterFavorite}
            </button>
          </div>
          <StockTagFilter />
        </div>
      )}
    </div>
  )
}
