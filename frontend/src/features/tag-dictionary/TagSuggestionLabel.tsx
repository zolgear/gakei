/**
 * タグの候補1件の中身(ADR-0041 2章)。タグモードのチップの入力欄の候補と、テキストモードの入力アシストの
 * 両方で使う。並びは「カテゴリーの色の印、(別名・訳で一致したときは)一致した語 → タグ名、訳、件数」。
 * 色だけに頼らないよう、印の title と読み上げにカテゴリー名を出す。
 */
import type { TagSuggestion } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { formatTagCount, tagCategoryTone } from './tagDisplay'
import styles from './TagSuggestionLabel.module.css'

interface TagSuggestionLabelProps {
  item: TagSuggestion
  /** 訳を添えるか(画面の言語と設定。`useTagTranslationsEnabled`)。 */
  showTranslation: boolean
}

export function TagSuggestionLabel({ item, showTranslation }: TagSuggestionLabelProps) {
  const { t } = useI18n()
  const d = t.tagDictionary
  const tone = tagCategoryTone(item.category_scheme, item.category)
  const categoryName =
    tone === null
      ? null
      : tone === 'other'
        ? fmt(d.categories.other, { number: item.category ?? '' })
        : d.categories[tone]
  // 別名は辞書の表記(`_` 区切り)のままなので、タグ名と同じく空白にして見せる。
  const matched =
    item.match && item.match !== 'name' && item.matched
      ? item.match === 'alias'
        ? item.matched.replace(/_/g, ' ')
        : item.matched
      : null
  const translation = showTranslation && item.translation && item.translation !== matched ? item.translation : null

  return (
    <span className={styles.label}>
      {tone !== null ? (
        <span className={styles.dot} data-tone={tone} title={categoryName ?? undefined} role="img" aria-label={categoryName ?? undefined} />
      ) : (
        <span className={styles.dotSpacer} aria-hidden="true" />
      )}
      <span className={styles.main}>
        {matched && (
          <>
            <span className={styles.matched}>{matched}</span>
            <span className={styles.arrow} aria-hidden="true">
              →
            </span>
          </>
        )}
        <span className={styles.name}>{item.name}</span>
        {translation && <span className={styles.translation}>{translation}</span>}
      </span>
      {item.count > 0 && (
        <span className={styles.count} title={item.count.toLocaleString()}>
          {formatTagCount(item.count)}
        </span>
      )}
    </span>
  )
}
