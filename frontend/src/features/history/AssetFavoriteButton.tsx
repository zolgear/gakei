/**
 * Asset 1枚を見せる場所(ビューア、系列・マップのパネル)のお気に入りのボタン(ADR-0055)。
 * 履歴の `FavoriteButton` と違い、画面の Context が無くても出す。枠と大きさは置く場所の
 * アイコンのボタンに合わせる(`className` で渡す)。お気に入りなら塗りのハートをアクセント色で出す。
 * 失敗したら表示を戻し、トーストで知らせる。
 */
import { HeartIcon } from '../../components/icons'
import { ToastHost, useToast } from '../../components/Toast'
import { useI18n } from '../../i18n'
import { useFavoriteToggle } from './useFavoriteToggle'
import styles from './AssetFavoriteButton.module.css'

interface AssetFavoriteButtonProps {
  assetId: string
  favorite: boolean
  /** 置く場所のアイコンのボタンのクラス(枠と大きさ)。 */
  className?: string
  /** 削除済みの Asset には付け外しできない(API が 404 を返す)。 */
  disabled?: boolean
  iconSize?: number
}

export function AssetFavoriteButton({
  assetId,
  favorite,
  className,
  disabled = false,
  iconSize,
}: AssetFavoriteButtonProps) {
  const { t } = useI18n()
  const toast = useToast()
  const failed = t.history.card.favoriteFailed
  const toggle = useFavoriteToggle(() => toast.show({ message: failed }))
  const label = t.history.card.favorite
  return (
    <>
      <button
        type="button"
        className={className ? `${className} ${styles.button}` : styles.button}
        data-favorite={favorite}
        aria-pressed={favorite}
        aria-label={label}
        title={label}
        disabled={disabled}
        onClick={() => toggle(assetId, !favorite)}
      >
        <HeartIcon size={iconSize} filled={favorite} />
      </button>
      <ToastHost toast={toast.toast} onDismiss={toast.dismiss} />
    </>
  )
}
