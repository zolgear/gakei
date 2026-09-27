/**
 * プロンプト欄で `@` を打ったときに出す候補リスト(メンション風の呼び出し)。
 * `InputPane.tsx` が使う。見た目のみを持ち、キー操作の状態(選択中インデックスなど)は
 * 呼び出し側(InputPane)が持つ。
 *
 * 入力エリアは独自のスクロール領域を持つため、`position: absolute` で textarea 直下に出すと
 * 領域の外にはみ出て切れてしまう。`createPortal` で `document.body` 直下に描き、
 * `position: fixed` + `useMentionPlacement` が計算した座標(caret 位置基準、既定は行の
 * すぐ上)で配置する。
 */
import { useEffect, useRef, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import type { MentionCandidate } from './mentionQuery'
import type { MentionPlacement } from './mentionPlacement'
import { useI18n } from '../../i18n'
import styles from './MentionPopover.module.css'

interface MentionPopoverProps {
  id: string
  candidates: MentionCandidate[]
  activeIndex: number
  placement: MentionPlacement | null
  onSelect: (candidate: MentionCandidate) => void
  onHoverIndex: (index: number) => void
}

export function MentionPopover({
  id,
  candidates,
  activeIndex,
  placement,
  onSelect,
  onHoverIndex,
}: MentionPopoverProps) {
  const { t } = useI18n()
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([])

  // 上下キーで選択中の項目が変わったら、ホイールなしでも見える位置までスクロールする。
  useEffect(() => {
    optionRefs.current[activeIndex]?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex])

  // 位置がまだ計算できていない(初回レイアウト前)間は描かない。`useMentionPlacement` は
  // `useLayoutEffect` で同期的に計算するため、ペイント前に確定し、ちらつきは出ない。
  if (!placement) return null

  const style: CSSProperties = {
    top: placement.top,
    left: placement.left,
    width: placement.width,
    maxHeight: placement.maxHeight,
  }

  return createPortal(
    <div
      id={id}
      className={styles.popover}
      style={style}
      data-direction={placement.direction}
      role="listbox"
      aria-label={t.promptSets.mention.ariaLabel}
    >
      {candidates.length === 0 ? (
        <p className={styles.empty}>{t.promptSets.mention.noMatch}</p>
      ) : (
        candidates.map((candidate, index) => (
          <button
            key={`${candidate.setId}:${candidate.itemId}`}
            ref={(el) => {
              optionRefs.current[index] = el
            }}
            id={`${id}-option-${index}`}
            type="button"
            role="option"
            aria-selected={index === activeIndex}
            data-active={index === activeIndex}
            className={styles.option}
            onMouseEnter={() => onHoverIndex(index)}
            onMouseDown={(e) => {
              // mousedown は textarea の blur より先に発火する。ここで preventDefault して
              // フォーカスを textarea に残したまま確定させる(確定後のカーソル復帰に必要)。
              e.preventDefault()
              onSelect(candidate)
            }}
          >
            <span className={styles.optionMeta}>
              {candidate.setName} › {candidate.label || t.promptSets.mention.untitled}
            </span>
            <span className={styles.optionPreview}>{candidate.text.split('\n')[0]}</span>
          </button>
        ))
      )}
    </div>,
    document.body,
  )
}
