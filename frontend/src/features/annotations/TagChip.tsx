/**
 * タグのチップ(ADR-0024 2章)。自動で付いたタグ(`source="auto"`)と人が付けたタグ(`"user"`)を
 * 文字のラベルではなく見た目で分ける: 人のタグは塗りと実線の枠、自動のタグは塗りなしと点線の枠で、
 * 文字も控えめな色にする(色だけに頼らないよう、枠線の形も変える)。
 * `onSelect` を渡すと名前の部分がボタンになり(ストックをそのタグで絞り込むなど)、`onRemove` を
 * 渡すと右端に × を出す。`translation` を渡すと、辞書の訳を名前の後ろに小さく添える(ADR-0041 4章)。
 */
import type { TagSource } from '../../api/client'
import styles from './TagChip.module.css'

interface TagChipProps {
  name: string
  source: TagSource
  /** タグ辞書の訳(ADR-0041 4章)。名前の後ろに小さく添える(表示だけ)。 */
  translation?: string
  onSelect?: () => void
  selectLabel?: string
  onRemove?: () => void
  removeLabel?: string
  removeDisabled?: boolean
}

export function TagChip({
  name,
  source,
  translation,
  onSelect,
  selectLabel,
  onRemove,
  removeLabel,
  removeDisabled,
}: TagChipProps) {
  const label = (
    <>
      {name}
      {translation && <span className={styles.translation}>{translation}</span>}
    </>
  )
  return (
    <span className={styles.chip} data-source={source} data-removable={Boolean(onRemove)}>
      {onSelect ? (
        <button type="button" className={styles.label} title={selectLabel} aria-label={selectLabel} onClick={onSelect}>
          {label}
        </button>
      ) : (
        <span className={styles.label}>{label}</span>
      )}
      {onRemove && (
        <button
          type="button"
          className={styles.removeButton}
          aria-label={removeLabel}
          title={removeLabel}
          disabled={removeDisabled}
          onClick={onRemove}
        >
          <svg width="10" height="10" viewBox="0 0 12 12" fill="none" aria-hidden="true">
            <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
      )}
    </span>
  )
}
