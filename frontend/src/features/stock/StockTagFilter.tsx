/**
 * ストックの「タグで絞り込む」(ADR-0024 5章)。種別のチップ行の下に置く。絞っていないときは
 * 候補付きの入力欄(`TagAutocomplete`、候補は件数順)、絞っているときはそのタグのチップと
 * 解除の × を出す。選んだタグは `stockTagFilterStore` に置く(ビューアのタグのチップからも設定できる)。
 */
import { useId } from 'react'
import { fmt, useI18n } from '../../i18n'
import { TagAutocomplete } from '../annotations/TagAutocomplete'
import { TagChip } from '../annotations/TagChip'
import { normalizeTagName } from '../annotations/tagInput'
import { setStockTagFilter, useStockTagFilter } from './stockTagFilterStore'
import styles from './StockTagFilter.module.css'

interface StockTagFilterProps {
  /** 見出しの見た目(種別の見出しと揃える)。 */
  headingClassName: string
}

export function StockTagFilter({ headingClassName }: StockTagFilterProps) {
  const { t } = useI18n()
  const f = t.stock.tagFilter
  const tag = useStockTagFilter()
  const headingId = useId()

  return (
    <div className={styles.root} role="group" aria-labelledby={headingId}>
      <span className={headingClassName} id={headingId}>
        {f.heading}
      </span>
      {tag ? (
        <div className={styles.activeRow}>
          <TagChip
            name={tag}
            source="user"
            onRemove={() => setStockTagFilter(null)}
            removeLabel={fmt(f.clear, { name: tag })}
          />
        </div>
      ) : (
        <TagAutocomplete
          placeholder={f.placeholder}
          ariaLabel={f.placeholder}
          suggestionsLabel={f.suggestions}
          onPick={(name) => setStockTagFilter(normalizeTagName(name) || null)}
        />
      )}
    </div>
  )
}
