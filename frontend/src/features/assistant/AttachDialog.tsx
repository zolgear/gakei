/**
 * 送信欄の「画像を添付」で開く、ストックから選ぶダイアログ(ADR-0048 6章)。入力画像の追加と
 * 同じ `StockPickerGrid` を複数選択で使う。添付できるのはストックの画像だけ(新しいファイルの
 * アップロードはここではしない。アップロードはストックか入力画像の欄から行う)。
 */
import { useState } from 'react'
import type { AssetSummary } from '../../api/client'
import { Modal } from '../../components/Modal'
import { fmt, useI18n } from '../../i18n'
import { StockPickerGrid } from '../stock/StockPickerGrid'
import { ASSISTANT_ATTACHMENTS_MAX } from './assistantForm'
import styles from './AssistantPanel.module.css'

interface AttachDialogProps {
  open: boolean
  onClose: () => void
  /** 既に添付している Asset。 */
  current: readonly string[]
  onAttach: (assetIds: string[]) => void
}

export function AttachDialog({ open, onClose, current, onAttach }: AttachDialogProps) {
  const { t } = useI18n()
  const d = t.assistant.attachDialog
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const remaining = Math.max(0, ASSISTANT_ATTACHMENTS_MAX - current.length)
  const canSelectMore = selectedIds.length < remaining

  function close() {
    setSelectedIds([])
    onClose()
  }

  function toggle(asset: AssetSummary) {
    setSelectedIds((prev) =>
      prev.includes(asset.id) ? prev.filter((id) => id !== asset.id) : canSelectMore ? [...prev, asset.id] : prev,
    )
  }

  return (
    <Modal open={open} title={d.title} onClose={close} size="large">
      <div className={styles.attachDialogBody}>
        <p className={styles.attachDialogHint}>{fmt(d.remaining, { count: remaining, max: ASSISTANT_ATTACHMENTS_MAX })}</p>
        <StockPickerGrid
          open={open}
          selection="multiple"
          selectedIds={selectedIds}
          onToggle={toggle}
          disabledAssetIds={new Set(current)}
          disabledBadgeText={d.alreadyAttached}
          canSelectMore={canSelectMore}
          emptyText={d.empty}
          loadFailedText={d.loadFailed}
          loadMoreText={d.loadMore}
        />
      </div>
      <div className={styles.attachDialogFooter}>
        <span className={styles.messageMeta}>{fmt(d.selectedCount, { count: selectedIds.length })}</span>
        <div className={styles.cardActions}>
          <button type="button" className={styles.smallButton} onClick={close}>
            {t.common.close}
          </button>
          <button
            type="button"
            className={`${styles.smallButton} ${styles.smallPrimary}`}
            disabled={selectedIds.length === 0}
            onClick={() => {
              onAttach(selectedIds)
              close()
            }}
          >
            {d.attach}
          </button>
        </div>
      </div>
    </Modal>
  )
}
