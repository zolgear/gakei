/**
 * 四角の参照画像を選ぶダイアログ(ADR-0053 1章)。生成のフォームの入力にある画像と、ストック
 * (`StockPickerGrid`。入力画像の追加・配置の素材と同じ部品)から1枚選ぶ。入力に無い画像を選ぶと、
 * 送るときにサーバーが入力の後ろに足す。
 */
import { useState } from 'react'
import { useQueries } from '@tanstack/react-query'
import { getAsset, type AssetSummary } from '../../api/client'
import { Modal } from '../../components/Modal'
import { fmt, useI18n } from '../../i18n'
import { AssetThumb } from '../content-rating/AssetThumb'
import { StockPickerGrid } from '../stock/StockPickerGrid'
import dialogStyles from '../workspace/AddInputImagesDialog.module.css'
import styles from './RegionEditor.module.css'

interface RegionReferenceDialogProps {
  open: boolean
  /** 生成のフォームの入力にある画像(先頭から)。 */
  inputAssetIds: readonly string[]
  /** 今の参照画像(選んだ状態で開く)。 */
  currentAssetId: string | null
  onChoose: (assetId: string) => void
  onClose: () => void
}

export function RegionReferenceDialog({ open, inputAssetIds, currentAssetId, onChoose, onClose }: RegionReferenceDialogProps) {
  const { t } = useI18n()
  const d = t.regions.referenceDialog
  const [selectedId, setSelectedId] = useState<string | null>(currentAssetId)
  const uniqueInputIds = [...new Set(inputAssetIds)]
  const inputQueries = useQueries({
    queries: uniqueInputIds.map((id) => ({ queryKey: ['asset', id], queryFn: () => getAsset(id), enabled: open })),
  })

  return (
    <Modal open={open} title={d.title} onClose={onClose} size="large">
      <div className={dialogStyles.body}>
        {uniqueInputIds.length > 0 && (
          <div className={styles.refInputsSection}>
            <h3 className={dialogStyles.stockHeading}>{d.fromInputs}</h3>
            <div className={styles.refInputsRow}>
              {uniqueInputIds.map((id, index) => {
                const detail = inputQueries[index]?.data
                const selected = selectedId === id
                return (
                  <button
                    key={id}
                    type="button"
                    className={styles.refInputTile}
                    data-selected={selected}
                    aria-pressed={selected}
                    aria-label={fmt(d.inputAria, { n: index + 1 })}
                    title={fmt(d.inputAria, { n: index + 1 })}
                    // 画像を残さなかった入力(ADR-0051 4章)は参照にできない。
                    disabled={detail?.content_withheld === true}
                    onClick={() => setSelectedId(selected ? null : id)}
                  >
                    <span className={styles.refThumbFrame}>
                      <AssetThumb
                        assetId={id}
                        rating={detail ? (detail.content_rating ?? null) : undefined}
                        withheld={detail?.content_withheld === true}
                        className={styles.refThumbImage}
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
          </div>
          <StockPickerGrid
            open={open}
            selection="single"
            selectedIds={selectedId ? [selectedId] : []}
            onToggle={(asset: AssetSummary) => setSelectedId((prev) => (prev === asset.id ? null : asset.id))}
            emptyText={d.empty}
            loadFailedText={d.loadFailed}
            loadMoreText={d.loadMore}
          />
        </div>
      </div>

      <div className={dialogStyles.footer}>
        <span />
        <div className={dialogStyles.footerButtons}>
          <button type="button" className={dialogStyles.closeButton} onClick={onClose}>
            {t.common.close}
          </button>
          <button
            type="button"
            className={dialogStyles.addSelectedButton}
            disabled={selectedId === null}
            onClick={() => selectedId && onChoose(selectedId)}
          >
            {d.choose}
          </button>
        </div>
      </div>
    </Modal>
  )
}
