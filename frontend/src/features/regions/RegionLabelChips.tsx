/**
 * 領域の名前のチップ(ADR-0053)。検出の候補の名前と「四角 1」などを並べ、押すとその文字を入力欄の
 * カーソル位置へ入れる。マウスを重ねる・指で触れる・フォーカスすると、対応する四角を強調する。
 *
 * チップを押しても入力欄のフォーカス(とスマートフォンのキーボード)を奪わないよう、`mousedown` の
 * 既定の動作を止める。
 */
import type { RegionHighlight } from './RegionOverlay'
import type { RegionChipItem } from './regionChips'
import { regionBadgeTextColor, regionColor } from './regions'
import styles from './RegionPromptAssist.module.css'

interface RegionLabelChipsProps {
  items: readonly RegionChipItem[]
  onInsert: (text: string) => void
  onHighlight?: (highlight: RegionHighlight) => void
  ariaLabel: string
}

export function RegionLabelChips({ items, onInsert, onHighlight, ariaLabel }: RegionLabelChipsProps) {
  if (items.length === 0) return null
  return (
    <div className={styles.chips} role="group" aria-label={ariaLabel}>
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          className={styles.chip}
          data-kind={item.colorIndex === undefined ? 'label' : 'region'}
          // 入力欄のフォーカス(とキーボード)を保つ。
          onMouseDown={(e) => e.preventDefault()}
          onPointerEnter={(e) => e.pointerType === 'mouse' && onHighlight?.(item.highlight)}
          onPointerLeave={(e) => e.pointerType === 'mouse' && onHighlight?.(null)}
          onPointerDown={(e) => e.pointerType !== 'mouse' && onHighlight?.(item.highlight)}
          onFocus={() => onHighlight?.(item.highlight)}
          onBlur={() => onHighlight?.(null)}
          onClick={() => onInsert(item.text)}
        >
          {item.colorIndex !== undefined && (
            <span
              className={styles.chipSwatch}
              style={{ background: regionColor(item.colorIndex), color: regionBadgeTextColor(item.colorIndex) }}
              aria-hidden="true"
            />
          )}
          {item.display ?? item.text}
        </button>
      ))}
    </div>
  )
}
