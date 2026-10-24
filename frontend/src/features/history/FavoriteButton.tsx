/**
 * 履歴の画像の右上のお気に入りのボタン(ADR-0055 4章)。ハートで、お気に入りなら塗る。
 * 画像のリンクの外(兄弟の要素)に置くので、押してもビューアへは移らない。
 * 履歴ページ(`FavoriteToggleContext` を与える画面)でだけ出す。
 *
 * - 重ねるカード(`overlay`): 常に出す。
 * - ギャラリー(`gallery`): お気に入りなら常に。そうでなければ、ホバーできる端末ではマウスを載せた
 *   とき・中にフォーカスがあるときだけ、ホバーできない端末(タッチ)では常に出す(CSS)。
 */
import type { RunOutputRef } from '../../api/client'
import { HeartIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import { useFavoriteToggleContext } from './useFavoriteToggle'
import styles from './HistoryCard.module.css'

interface FavoriteButtonProps {
  output: RunOutputRef
  variant: 'overlay' | 'gallery'
}

export function FavoriteButton({ output, variant }: FavoriteButtonProps) {
  const { t } = useI18n()
  const toggle = useFavoriteToggleContext()
  if (!toggle) return null
  const favorite = Boolean(output.favorite)
  const label = t.history.card.favorite
  return (
    <button
      type="button"
      className={styles.favoriteButton}
      data-variant={variant}
      data-favorite={favorite}
      aria-pressed={favorite}
      aria-label={label}
      title={label}
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        toggle(output.asset_id, !favorite)
      }}
    >
      <HeartIcon size={16} filled={favorite} />
    </button>
  )
}
