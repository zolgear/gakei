/**
 * Asset のサムネイル(ADR-0049 5章)。今までどおりの `<img>` を描き、ユーザー設定でマスクする段階なら
 * ぼかして、中央に段階のバッジを重ねる。
 *
 * - `<img>` は渡された className・style(焦点の `object-position` など)をそのまま使い、DOM の形も
 *   変えない(各画面の寸法の CSS を崩さないため)。ぼかしは `<img>` 自体に `filter: blur()` を掛け、
 *   はみ出しを `clip-path: inset(0)` で切る(重ねる要素の位置がずれても、画像はぼけたままになる)。
 * - マスクと隅のバッジは `<img>` の兄弟に `position: absolute; inset: 0` で重ねる。親が `<img>` と同じ
 *   大きさで `position: relative` であること(各画面の CSS で用意する)。
 * - マスクをクリック(タップ)すると、その画像だけマスクを外す(`revealStore`。ページを読み直すと戻る)。
 *   下のカードのリンクやボタンは動かさない(`preventDefault` と `stopPropagation`)。外したあとは
 *   普段どおりクリックできる。
 * - 外した画像(設定ではマスクする段階のもの)には、隅のバッジの位置に、目を閉じたアイコンと段階を
 *   出す小さなボタンを置き、押すとマスクに戻す(5章「マスクに戻す」。2026-10-15)。`badge` の
 *   有無に関わらず出し、隅のバッジと置き換えるので、ほかの印やボタンとは重ならない。カードの中に
 *   入るので `<button>` ではなく `role="button"` の要素にし(リンクやボタンの入れ子にしない)、
 *   マスクと同じく下のカードへクリックとポインタを渡さない。
 * - `rating` が `undefined`(応答に段階の欄が無い)ならマスクもバッジも出さない。
 */
import type { CSSProperties, ImgHTMLAttributes, KeyboardEvent, MouseEvent } from 'react'
import { assetUrl } from '../../api/assetUrl'
import type { ContentRatingLevel } from '../../api/client'
import { EyeOffIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import { RatingBadge } from './RatingBadge'
import { isActivationKey, remaskFromEvent } from './remask'
import { revealAsset } from './revealStore'
import { useAssetMaskState } from './useAssetMasked'
import styles from './ContentRating.module.css'

/** ぼかした `<img>` に足す style。はみ出したぼかしを要素の枠で切る。 */
const MASKED_IMAGE_STYLE: CSSProperties = {
  filter: 'blur(var(--gakei-rating-blur, 14px))',
  clipPath: 'inset(0)',
}

export type BadgePosition = 'bottom-left' | 'top-left' | 'top-right' | 'bottom-right'

interface AssetThumbProps extends Omit<ImgHTMLAttributes<HTMLImageElement>, 'src'> {
  assetId: string
  /** 段階。null は未判定、undefined は「応答に無い」(マスクしない)。 */
  rating: ContentRatingLevel | null | undefined
  /** 既定は `assetUrl(assetId, 'thumb')`。系列のグラフのように別の URL を使うときに渡す。 */
  src?: string
  /** マスクしていないとき、隅に段階のバッジを出す(カード向け)。 */
  badge?: boolean
  /** 隅のバッジの位置。既定は左下(ほかの印やボタンと重なる画面で変える)。 */
  badgePosition?: BadgePosition
}

export function AssetThumb({
  assetId,
  rating,
  src,
  badge = false,
  badgePosition = 'bottom-left',
  style,
  ...imgProps
}: AssetThumbProps) {
  const { t } = useI18n()
  const { masked, revealed } = useAssetMaskState(assetId, rating)

  function reveal(e: MouseEvent) {
    e.preventDefault()
    e.stopPropagation()
    revealAsset(assetId)
  }

  function remaskByKey(e: KeyboardEvent) {
    if (isActivationKey(e.key)) remaskFromEvent(e, assetId)
  }

  return (
    <>
      <img
        {...imgProps}
        src={src ?? assetUrl(assetId, 'thumb')}
        style={masked ? { ...style, ...MASKED_IMAGE_STYLE } : style}
        data-rating-masked={masked ? 'true' : undefined}
        draggable={masked ? false : imgProps.draggable}
        onDragStart={masked ? undefined : imgProps.onDragStart}
      />
      {masked && (
        <span
          className={styles.mask}
          title={t.contentRating.revealHint}
          // ドラッグやパンの始まりも下へ渡さない(マスクの上からはドラッグしない)。
          onPointerDown={(e) => e.stopPropagation()}
          onClick={reveal}
        >
          {rating ? (
            <RatingBadge level={rating} size="mask" />
          ) : (
            <span className={styles.badge} data-size="mask" data-level="unrated">
              {t.contentRating.unrated}
            </span>
          )}
        </span>
      )}
      {revealed && (
        <span
          role="button"
          tabIndex={0}
          className={`${styles.badge} ${styles.corner} ${styles.remask}`}
          data-level={rating ?? 'unrated'}
          data-size="corner"
          data-position={badgePosition}
          aria-label={t.contentRating.remask}
          title={t.contentRating.remaskHint}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => remaskFromEvent(e, assetId)}
          onKeyDown={remaskByKey}
        >
          <EyeOffIcon size={11} />
          {rating && <span aria-hidden="true">{t.contentRating.shortLevels[rating]}</span>}
        </span>
      )}
      {!masked && !revealed && badge && rating && (
        <RatingBadge level={rating} size="corner" className={styles.corner} position={badgePosition} />
      )}
    </>
  )
}
