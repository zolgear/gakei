/**
 * ビューア・比較の大きな画像に置く、マスクに戻すボタン(ADR-0049 5章「マスクに戻す」。2026-10-15)。
 * 設定ではマスクする段階の画像を一時的に外しているときだけ出し、押すとマスクに戻す。画像の右下に
 * 目を閉じたアイコンだけを控えめに重ねる(親は `position: relative`)。文言はツールチップと読み上げに回す。
 */
import { EyeOffIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import styles from './ContentRating.module.css'

interface CanvasRemaskButtonProps {
  onRemask: () => void
}

export function CanvasRemaskButton({ onRemask }: CanvasRemaskButtonProps) {
  const { t } = useI18n()
  return (
    <button
      type="button"
      className={styles.canvasRemask}
      aria-label={t.contentRating.remask}
      title={t.contentRating.remaskHint}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={onRemask}
    >
      <EyeOffIcon size={16} />
    </button>
  )
}
