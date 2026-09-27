/**
 * プロンプト本文用の textarea。内容に応じて自動で高さが伸びる(最小 minRows 行、
 * 上限は画面の maxHeightVh% でそれ以上はスクロール)。右下に文字数(n / maxLength)を表示する。
 * 折り返しあり・モバイルは font-size 16px(iOS のズーム対策、グローバル CSS 済みだが明示もする)。
 */
import { useLayoutEffect, useRef, type KeyboardEvent } from 'react'
import { clampTextareaHeight } from './textareaSizing'
import { PROMPT_TEXT_MAX_LENGTH } from './promptSetValidation'
import styles from './AutoGrowTextarea.module.css'

interface AutoGrowTextareaProps {
  id?: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  minRows?: number
  maxHeightVh?: number
  maxLength?: number
  readOnly?: boolean
  autoFocus?: boolean
  onKeyDown?: (e: KeyboardEvent<HTMLTextAreaElement>) => void
  className?: string
}

const DEFAULT_MIN_ROWS = 4
const DEFAULT_MAX_HEIGHT_VH = 40
// 目安の1行あたりの高さ + 上下パディング(px)。CSS(font-size 12px, line-height 1.5)と合わせた概算。
const APPROX_LINE_HEIGHT_PX = 18
const VERTICAL_PADDING_PX = 28

export function AutoGrowTextarea({
  id,
  value,
  onChange,
  placeholder,
  minRows = DEFAULT_MIN_ROWS,
  maxHeightVh = DEFAULT_MAX_HEIGHT_VH,
  maxLength = PROMPT_TEXT_MAX_LENGTH,
  readOnly,
  autoFocus,
  onKeyDown,
  className,
}: AutoGrowTextareaProps) {
  const ref = useRef<HTMLTextAreaElement | null>(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    const minHeightPx = minRows * APPROX_LINE_HEIGHT_PX + VERTICAL_PADDING_PX
    const maxHeightPx =
      typeof window !== 'undefined' ? (window.innerHeight * maxHeightVh) / 100 : Number.POSITIVE_INFINITY
    const nextHeight = clampTextareaHeight(el.scrollHeight, minHeightPx, maxHeightPx)
    el.style.height = `${nextHeight}px`
    el.style.overflowY = el.scrollHeight > maxHeightPx ? 'auto' : 'hidden'
  }, [value, minRows, maxHeightVh])

  return (
    <div className={`${styles.wrap} ${className ?? ''}`}>
      <textarea
        id={id}
        ref={ref}
        className={styles.textarea}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={minRows}
        maxLength={maxLength}
        readOnly={readOnly}
        autoFocus={autoFocus}
        onKeyDown={onKeyDown}
      />
      <span className={styles.count}>
        {value.length} / {maxLength}
      </span>
    </div>
  )
}
