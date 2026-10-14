/**
 * レーティングの段階のバッジ(ADR-0049 4章)。グリーンは目立たない色にする。未判定(null)は何も出さない。
 * - `size="corner"`: カードのサムネイルの隅に重ねる小さな印(グリーンは「G」と略す)。
 * - `size="inline"`: 情報欄などの文中に置く(グリーンも略さない)。
 * - `size="mask"`: マスクの中央に重ねる。
 */
import type { ContentRatingLevel } from '../../api/client'
import { useI18n } from '../../i18n'
import styles from './ContentRating.module.css'

interface RatingBadgeProps {
  level: ContentRatingLevel | null | undefined
  size?: 'corner' | 'inline' | 'mask'
  className?: string
  /** 隅に重ねるときの位置(`AssetThumb` が使う。CSS の `.corner[data-position]`)。 */
  position?: string
}

export function RatingBadge({ level, size = 'inline', className, position }: RatingBadgeProps) {
  const { t } = useI18n()
  if (!level) return null
  const label = t.contentRating.levels[level]
  const text = size === 'corner' ? t.contentRating.shortLevels[level] : label
  return (
    <span
      className={`${styles.badge} ${className ?? ''}`}
      data-level={level}
      data-size={size}
      data-position={position}
      title={size === 'corner' ? `${t.contentRating.label}: ${label}` : undefined}
    >
      {text}
    </span>
  )
}
