/**
 * ストックパネルのタイル 1 枚(ADR-0022 4章で節ごとに使い回すため `StockPanel` から切り出した)。
 * 画像ボタン(クリックでビューア/結果エリア、選択モードでは選択の切り替え)、ホバーで出る
 * 「+」(入力に追加。マスクは不可)と削除、グループの節でだけ出す「このグループから外す」(×)。
 * 768px 未満では削除だけ画像から外してキャプション行の右端に置く(「+」と重ならないように。issue #10)。
 * 画像はドラッグ元になり、入力欄やグループの節の見出しに落とせる(マスクと選択モード中は除く)。
 */
import { assetUrl } from '../../api/assetUrl'
import type { AssetSummary } from '../../api/client'
import { useI18n } from '../../i18n'
import { GAKEI_ASSET_ID_DATA_TYPE } from '../run-form/dragDropAssets'
import styles from './StockTile.module.css'

function TrashIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M3 4.5h10M6.5 4.5V3a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v1.5M4.5 4.5v8a1 1 0 0 0 1 1h5a1 1 0 0 0 1-1v-8M6.5 7.5v3.5M9.5 7.5v3.5"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function CrossIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

function extFromMime(mime: string): string {
  return mime.split('/')[1] ?? mime
}

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
  onRemoveFromGroup,
}: StockTileProps) {
  const { t } = useI18n()
  // mask は入力画像として追加できないので、ドラッグ元にもしない(「+」を出さないのと同じ条件)。
  // 選択モード中もドラッグしない。
  const draggable = !selectionMode && asset.kind !== 'mask'

  return (
    <div className={styles.tile} data-selected={selectionMode && isSelected}>
      <button
        type="button"
        className={styles.tileImageButton}
        aria-label={
          selectionMode ? t.stock.selection.toggleTile : inStudio ? t.stock.showInResultArea : t.stock.openInViewer
        }
        aria-pressed={selectionMode ? isSelected : undefined}
        onClick={() => (selectionMode ? onToggleSelect(asset) : onOpen(asset))}
      >
        <img
          className={`${styles.tileImage} checkerboard`}
          src={assetUrl(asset.id, 'thumb')}
          alt=""
          draggable={draggable}
          onDragStart={
            draggable
              ? (e) => {
                  e.dataTransfer.setData(GAKEI_ASSET_ID_DATA_TYPE, asset.id)
                  e.dataTransfer.effectAllowed = 'copy'
                }
              : undefined
          }
        />
        {selectionMode && isSelected && <span className={styles.checkBadge}>✓</span>}
      </button>
      {!selectionMode && onRemoveFromGroup && (
        <button
          type="button"
          className={`${styles.overlayButton} ${styles.removeButton}`}
          aria-label={t.stock.groups.removeFromThis}
          title={t.stock.groups.removeFromThis}
          onClick={() => onRemoveFromGroup(asset)}
        >
          <CrossIcon />
        </button>
      )}
      {!selectionMode && asset.kind !== 'mask' && (
        <button
          type="button"
          className={`${styles.overlayButton} ${styles.addButton}`}
          aria-label={t.stock.useAsInput}
          onClick={() => onUseAsInput(asset)}
        >
          +
        </button>
      )}
      {!selectionMode && (
        <button
          type="button"
          className={`${styles.overlayButton} ${styles.deleteButton}`}
          aria-label={t.stock.delete}
          onClick={() => onDelete(asset)}
        >
          <TrashIcon />
        </button>
      )}
      <span className={styles.caption}>
        {asset.width}×{asset.height} · {extFromMime(asset.mime)}
      </span>
    </div>
  )
}
