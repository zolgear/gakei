/**
 * ストックパネルのタイル 1 枚(ADR-0022 4章で節ごとに使い回すため `StockPanel` から切り出した)。
 * 履歴の詳細のカードと同じく、画像をタイルいっぱいに出し、情報と操作は画像の上に重ねる。
 *
 * - 画像ボタン(タイル全体): クリックでビューア/結果エリア、選択モードでは選択の切り替え。
 *   画像はドラッグ元になり、入力欄やグループの節の見出しに落とせる(マスクと選択モード中は除く)。
 * - 下端に重ねるアイコンの1行(`stockTileActions`): 入力に使う(マスクと画像を残さなかったものは除く)、
 *   このグループから外す(グループの節だけ)、削除。どれも aria-label と同じ文の title を付ける。
 *   ホバーできる端末ではホバー(とキーボードで中に入ったとき)だけ出し、ホバーできない端末
 *   (`hover: none`)では常に出す。
 * - ホバーできる端末では、操作の上の1行にタイトル(ADR-0024。あれば)と大きさ(幅×高さ)を出す。
 *   大きさは縮めず(途中で切らず)、狭ければタイトルのほうを省略する。タッチ端末では操作の1行だけに
 *   して、画像を隠さない(大きさの入る幅が無い)。
 * - 段階のバッジ(ADR-0049)は左上。右上はお気に入りのハート(ADR-0055。履歴のギャラリーと同じ見せ方):
 *   お気に入りなら常に塗りで出し、そうでなければホバーできる端末ではマウスを載せたとき・中にフォーカスが
 *   あるときだけ、ホバーできない端末では常に線で出す。画像ボタンの外(兄弟の要素)なので、押してもタイルは
 *   開かずドラッグも始まらない。選択モードの間はタイルの選択を優先して出さない。
 *   選択モードの印は右下(選択モードの間は操作を出さないので重ならない)。
 * - 左下の隅は種別のリボン(`AssetKindRibbon`。生成した画像には出ない)。下端の操作の1行より下の層に置き、
 *   リボンがあるときは操作の1行の左にリボンの幅だけ空ける(右寄せの操作が多くて狭いときに重ねない)。
 */
import type { AssetSummary } from '../../api/client'
import { AddToInputIcon, CrossIcon, HeartIcon, TrashIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import { setAssetDragData } from '../run-form/dragDropAssets'
import { AssetThumb } from '../content-rating/AssetThumb'
import { AssetKindRibbon } from '../asset-kind/AssetKindRibbon'
import { kindRibbon } from '../asset-kind/kindRibbon'
import { focalStyle } from '../../lib/focalPoint'
import { responsiveThumbProps } from '../../lib/derivedSrcSet'
import { stockTileSizes } from './stockTileSizes'
import { stockTileActions, stockTilePixelSize } from './stockTileActions'
import styles from './StockTile.module.css'

interface StockTileProps {
  asset: AssetSummary
  selectionMode: boolean
  isSelected: boolean
  /** スタジオにいる間は「結果エリアに表示」、それ以外は「ビューアで開く」の文言にする。 */
  inStudio: boolean
  onOpen: (asset: AssetSummary) => void
  onToggleSelect: (asset: AssetSummary) => void
  onUseAsInput: (asset: AssetSummary) => void
  onDelete: (asset: AssetSummary) => void
  /** お気に入りの付け外し(ADR-0055)。 */
  onToggleFavorite: (assetId: string, favorite: boolean) => void
  /** グループの節でだけ渡す。渡すと「このグループから外す」(×)を出す。 */
  onRemoveFromGroup?: (asset: AssetSummary) => void
}

export function StockTile({
  asset,
  selectionMode,
  isSelected,
  inStudio,
  onOpen,
  onToggleSelect,
  onUseAsInput,
  onDelete,
  onToggleFavorite,
  onRemoveFromGroup,
}: StockTileProps) {
  const { t } = useI18n()
  // mask は入力画像として追加できないので、ドラッグ元にもしない(「入力に使う」を出さないのと同じ条件)。
  // 選択モード中もドラッグしない。
  // 画像を残さなかった Asset(ADR-0051 4章)は入力に使えないので、「入力に使う」もドラッグも出さない。
  const withheld = asset.content_withheld === true
  const draggable = !selectionMode && asset.kind !== 'mask' && !withheld
  const actions = stockTileActions({
    kind: asset.kind,
    withheld,
    selectionMode,
    inGroup: onRemoveFromGroup !== undefined,
  })
  const pixelSize = stockTilePixelSize(asset.width, asset.height)
  const favorite = asset.favorite === true
  const imported = asset.imported === true
  const hasRibbon = kindRibbon(asset.kind, imported) !== null

  return (
    <div className={styles.tile} data-selected={selectionMode && isSelected} data-ribbon={hasRibbon}>
      <button
        type="button"
        className={styles.tileImageButton}
        aria-label={
          selectionMode ? t.stock.selection.toggleTile : inStudio ? t.stock.showInResultArea : t.stock.openInViewer
        }
        aria-pressed={selectionMode ? isSelected : undefined}
        onClick={() => (selectionMode ? onToggleSelect(asset) : onOpen(asset))}
      >
        <AssetThumb
          assetId={asset.id}
          rating={asset.content_rating}
          badge
          badgePosition="top-left"
          withheld={withheld}
          className={`${styles.tileImage} checkerboard`}
          style={focalStyle(asset.focal_point)}
          {...responsiveThumbProps(asset.id, asset, stockTileSizes(asset))}
          alt=""
          draggable={draggable}
          onDragStart={
            draggable
              ? (e) => {
                  setAssetDragData(e.dataTransfer, asset.id)
                }
              : undefined
          }
        />
        {selectionMode && isSelected && (
          <span className={styles.checkBadge} aria-hidden="true">
            ✓
          </span>
        )}
      </button>

      <AssetKindRibbon kind={asset.kind} imported={imported} />

      {!selectionMode && (
        <button
          type="button"
          className={styles.favoriteButton}
          data-favorite={favorite}
          aria-pressed={favorite}
          aria-label={t.history.card.favorite}
          title={t.history.card.favorite}
          onClick={(e) => {
            e.stopPropagation()
            onToggleFavorite(asset.id, !favorite)
          }}
        >
          <HeartIcon size={16} filled={favorite} />
        </button>
      )}

      {actions.length > 0 && (
        <div className={styles.overlay}>
          {(asset.title || pixelSize) && (
            <div className={styles.info}>
              {/* タイトル(ADR-0024)。一覧の応答には自動か人かの区別が無いので、見た目は分けない。 */}
              {asset.title && (
                <span className={styles.title} title={asset.title}>
                  {asset.title}
                </span>
              )}
              {pixelSize && <span className={styles.pixelSize}>{pixelSize}</span>}
            </div>
          )}
          <div className={styles.actions}>
            {actions.map((action) => {
              switch (action) {
                case 'useAsInput':
                  return (
                    <button
                      key={action}
                      type="button"
                      className={styles.actionButton}
                      aria-label={t.stock.useAsInput}
                      title={t.stock.useAsInput}
                      onClick={() => onUseAsInput(asset)}
                    >
                      <AddToInputIcon size={14} />
                    </button>
                  )
                case 'removeFromGroup':
                  return (
                    <button
                      key={action}
                      type="button"
                      className={styles.actionButton}
                      aria-label={t.stock.groups.removeFromThis}
                      title={t.stock.groups.removeFromThis}
                      onClick={() => onRemoveFromGroup?.(asset)}
                    >
                      <CrossIcon size={13} />
                    </button>
                  )
                case 'delete':
                  return (
                    <button
                      key={action}
                      type="button"
                      className={`${styles.actionButton} ${styles.danger}`}
                      aria-label={t.stock.delete}
                      title={t.stock.delete}
                      onClick={() => onDelete(asset)}
                    >
                      <TrashIcon size={14} />
                    </button>
                  )
              }
            })}
          </div>
        </div>
      )}
    </div>
  )
}
