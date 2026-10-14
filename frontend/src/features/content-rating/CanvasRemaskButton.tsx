/**
 * ビューア・比較の大きな画像に置く「マスクする」のボタン(ADR-0049 5章「マスクに戻す」。2026-10-15)。
 * 設定ではマスクする段階の画像を一時的に外しているときだけ出し、押すとマスクに戻す。画像の右下に
 * 控えめに重ねる(親は `position: relative`)。
 */
import type { ContentRatingLevel } from '../../api/client'
import { EyeOffIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import { RatingBadge } from './RatingBadge'
import styles from './ContentRating.module.css'

interface CanvasRemaskButtonProps {
  /** 外している画像の段階(比較では2枚分)。未判定(null)はバッジを出さない。 */
  levels: (ContentRatingLevel | null | undefined)[]
  onRemask: () => void
}

export function CanvasRemaskButton({ levels, onRemask }: CanvasRemaskButtonProps) {
  const { t } = useI18n()
  return (
    <button
      type="button"
      className={styles.canvasRemask}
      title={t.contentRating.remaskHint}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={onRemask}
    >
      <EyeOffIcon size={14} />
      <span>{t.contentRating.remask}</span>
      {levels.map((level, i) => (level ? <RatingBadge key={i} level={level} size="corner" /> : null))}
    </button>
  )
}
