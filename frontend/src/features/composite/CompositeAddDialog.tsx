/**
 * 配置の「素材を足す」ダイアログ。生成のフォームの入力にある画像と、ストック
 * (`StockPickerGrid`。入力画像の追加と同じ部品)から複数選んで足す。
 */
import { useState } from 'react'
import { useQueries } from '@tanstack/react-query'
import { getAsset, type AssetSummary } from '../../api/client'
import { Modal } from '../../components/Modal'
import { fmt, useI18n } from '../../i18n'
import { AssetThumb } from '../content-rating/AssetThumb'
import { StockPickerGrid } from '../stock/StockPickerGrid'
import dialogStyles from '../workspace/AddInputImagesDialog.module.css'
import styles from './CompositeEditor.module.css'

interface CompositeAddDialogProps {
  open: boolean
  /** 生成のフォームの入力にある画像(先頭から)。 */
  inputAssetIds: readonly string[]
  /** あと何枚足せるか。 */
  remaining: number
  onAdd: (assetIds: string[]) => void
  onClose: () => void
}

export function CompositeAddDialog({ open, inputAssetIds, remaining, onAdd, onClose }: CompositeAddDialogProps) {
  const { t } = useI18n()
  const d = t.composite.addDialog
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const uniqueInputIds = [...new Set(inputAssetIds)]
  const inputQueries = useQueries({
    queries: uniqueInputIds.map((id) => ({ queryKey: ['asset', id], queryFn: () => getAsset(id), enabled: open })),
  })
  const canSelectMore = selectedIds.length < remaining

  function handleClose() {
    setSelectedIds([])
    onClose()
  }

  function toggle(id: string) {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : canSelectMore ? [...prev, id] : prev,
    )
  }

  function handleAdd() {
    if (selectedIds.length === 0) return
    onAdd(selectedIds)
    setSelectedIds([])
  }

  return (
    <Modal open={open} title={d.title} onClose={handleClose} size="large">
      <div className={dialogStyles.body}>
        {uniqueInputIds.length > 0 && (
          <div className={styles.addInputsSection}>
            <h3 className={dialogStyles.stockHeading}>{d.fromInputs}</h3>
            <div className={styles.addInputsRow}>
              {uniqueInputIds.map((id, index) => {
                const detail = inputQueries[index]?.data
                const selected = selectedIds.includes(id)
                return (
                  <button
                    key={id}
                    type="button"
                    className={styles.addInputTile}
                    data-selected={selected}
                    aria-pressed={selected}
                    aria-label={fmt(d.inputAria, { n: index + 1 })}
                    title={fmt(d.inputAria, { n: index + 1 })}
                    disabled={!selected && !canSelectMore}
                    onClick={() => toggle(id)}
                  >
                    <span className={styles.thumbFrame}>
                      <AssetThumb
                        assetId={id}
                        rating={detail ? (detail.content_rating ?? null) : undefined}
                        className={styles.thumbImage}
                        alt=""
                        draggable={false}
                      />
                    </span>
                  </button>
                )
              })}
            </div>
          </div>
        )}

        <div className={dialogStyles.stockSection}>
          <div className={dialogStyles.stockHeader}>
            <h3 className={dialogStyles.stockHeading}>{d.fromStock}</h3>
            <span className={dialogStyles.remaining}>{fmt(d.remaining, { count: remaining })}</span>
          </div>
          <StockPickerGrid
            open={open}
            selection="multiple"
            selectedIds={selectedIds}
            onToggle={(asset: AssetSummary) => toggle(asset.id)}
            canSelectMore={canSelectMore}
            emptyText={d.empty}
            loadFailedText={d.loadFailed}
            loadMoreText={d.loadMore}
          />
        </div>
      </div>

      <div className={dialogStyles.footer}>
        <span className={dialogStyles.selectedCount}>{fmt(d.selectedCount, { count: selectedIds.length })}</span>
        <div className={dialogStyles.footerButtons}>
          <button type="button" className={dialogStyles.closeButton} onClick={handleClose}>
            {t.common.close}
          </button>
          <button
            type="button"
            className={dialogStyles.addSelectedButton}
            disabled={selectedIds.length === 0}
            onClick={handleAdd}
          >
            {d.addSelected}
          </button>
        </div>
      </div>
    </Modal>
  )
}
