/**
 * 「+ 追加」(入力画像)を押したときに開くモーダル(ADR-0009 1章「入力画像の追加は
 * ダイアログから」2026-09-26 追加)。「ファイルを選ぶ」(従来どおり OS のファイル選択へ)と
 * 「ストックから選ぶ」(kind で絞り込めるサムネイル一覧、複数選択して「追加」)の2通りを出す。
 * ドラッグ&ドロップと貼り付けは `InputChipsRow` / `InputImageTiles` / `InputPane` 側で
 * これまでどおり直接追加するので、このダイアログは触らない。
 * ストックの一覧(kind チップ・検索・無限スクロール)は `StockPickerGrid`(ADR-0020 で
 * 設定画面のプロフィール向けに共通部品として切り出した)を複数選択モードで使う。モーダルは
 * `size="large"` で画面の大半を使う。
 */
import { useRef, useState } from 'react'
import { type AssetSummary } from '../../api/client'
import { Modal } from '../../components/Modal'
import { fmt, useI18n } from '../../i18n'
import type { EditInputsLogic } from '../run-form/useEditInputsLogic'
import { StockPickerGrid } from '../stock/StockPickerGrid'
import styles from './AddInputImagesDialog.module.css'

interface AddInputImagesDialogProps {
  open: boolean
  onClose: () => void
  /** `handleFiles` でのアップロード、`addAssets` でのストック選択追加の両方をここから呼ぶ。 */
  logic: EditInputsLogic
  maxInputImages: number
}

export function AddInputImagesDialog({ open, onClose, logic, maxInputImages }: AddInputImagesDialogProps) {
  const { t } = useI18n()
  const d = t.workspace.addInputsDialog
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [selectedIds, setSelectedIds] = useState<string[]>([])

  // 閉じるとき(Esc・背景クリック・×・フッターの各ボタン、いずれも最終的にここを通る)に
  // 選択を初期状態へ戻す。次に開いたときに前回の選択が残らないように、`onClose` を
  // ここでラップする(kind・検索欄は `StockPickerGrid` 自身の state なので、`Modal` が
  // 閉じて子ごと描画をやめれば自然にリセットされる)。
  function handleClose() {
    setSelectedIds([])
    onClose()
  }

  const existingIds = new Set(logic.images.map((i) => i.assetId))
  const remaining = Math.max(0, maxInputImages - logic.images.length)
  const canSelectMore = selectedIds.length < remaining

  function toggle(asset: AssetSummary) {
    setSelectedIds((prev) =>
      prev.includes(asset.id) ? prev.filter((id) => id !== asset.id) : canSelectMore ? [...prev, asset.id] : prev,
    )
  }

  function handleFileInputChange(files: FileList | null) {
    logic.handleFiles(files)
    if (fileInputRef.current) fileInputRef.current.value = ''
    handleClose()
  }

  function handleAddSelected() {
    if (selectedIds.length === 0) return
    logic.addAssets(selectedIds)
    handleClose()
  }

  return (
    <Modal open={open} title={d.title} onClose={handleClose} size="large">
      <div className={styles.body}>
        <div className={styles.chooseRow}>
          <button
            type="button"
            className={styles.chooseFileButton}
            disabled={logic.isUploading}
            onClick={() => fileInputRef.current?.click()}
          >
            {logic.isUploading ? t.workspace.inputChips.uploading : d.chooseFile}
          </button>
          <span className={styles.dropHint}>{d.dropHint}</span>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            multiple
            className={styles.hiddenFileInput}
            onChange={(e) => handleFileInputChange(e.target.files)}
          />
        </div>

        <div className={styles.stockSection}>
          <div className={styles.stockHeader}>
            <h3 className={styles.stockHeading}>{d.fromStock}</h3>
            <span className={styles.remaining}>{fmt(d.remaining, { count: remaining })}</span>
          </div>

          <StockPickerGrid
            open={open}
            selection="multiple"
            selectedIds={selectedIds}
            onToggle={toggle}
            disabledAssetIds={existingIds}
            disabledBadgeText={d.alreadyInInputs}
            canSelectMore={canSelectMore}
            emptyText={d.empty}
            loadFailedText={d.loadFailed}
            loadMoreText={d.loadMore}
          />
        </div>
      </div>

      <div className={styles.footer}>
        <span className={styles.selectedCount}>{fmt(d.selectedCount, { count: selectedIds.length })}</span>
        <div className={styles.footerButtons}>
          <button type="button" className={styles.closeButton} onClick={handleClose}>
            {t.common.close}
          </button>
          <button
            type="button"
            className={styles.addSelectedButton}
            disabled={selectedIds.length === 0}
            onClick={handleAddSelected}
          >
            {d.addSelected}
          </button>
        </div>
      </div>
    </Modal>
  )
}
