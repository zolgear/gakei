/**
 * サイドバー配置(ADR-0009 1章・2026-09-26 追記)の入力画像。下段の `InputChipsRow` と同じ
 * props(`InputImagesProps`)・同じ `EditInputsLogic` を受け、正方形のサムネイルタイルを
 * 折り返して並べる(「+ 追加」「スケッチ」もタイルにする)。番号(1 が主たる親)・マスクの
 * 表示・「マスクを描く」「マスクを外す」「描き込む」「外す」・プレビュー表示・並べ替えは
 * チップ行と同じ操作をタイルの下端の操作バンド(hover / focus-within で表示)に置く。
 * ドラッグ&ドロップの強調もチップ行と同じ考え方(`isRelevantDragTypes`)。
 * 「+ 追加」タイルは `onOpenAddDialog` で `AddInputImagesDialog` を開く(チップ行と同じ。
 * ADR-0009 1章・2026-09-26 追加)。
 */
import { useState, type DragEvent } from 'react'
import { assetUrl } from '../../api/assetUrl'
import { fmt, useI18n } from '../../i18n'
import { isRelevantDragTypes } from '../run-form/dragDropAssets'
import { AddIcon, PencilIcon, RemoveIcon } from './inputIcons'
import type { InputImagesProps } from './InputChipsRow'
import styles from './InputImageTiles.module.css'

/** 「前へ」(チップ行の ▲ に相当)。タイルでは矢印を左右向きの山形にする。 */
function PrevIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path d="M7.5 2.5L3 6l4.5 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/** 「後ろへ」(チップ行の ▼ に相当)。 */
function NextIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path d="M4.5 2.5L9 6l-4.5 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function InputImageTiles({
  logic,
  maxInputImages,
  exactInputImagesRequired,
  maskSupported,
  onOpenMaskEditor,
  blankSketchSize,
  onPreviewAsset,
  onOpenAddDialog,
}: InputImagesProps) {
  const { t } = useI18n()
  const c = t.workspace.inputChips
  const it = t.workspace.inputTiles
  const [isDragOver, setIsDragOver] = useState(false)
  const { images, maskInput, detailsByAssetId, notFoundAssetIds, deletedAssetIds } = logic

  function handleDragOver(e: DragEvent<HTMLElement>) {
    if (!isRelevantDragTypes(Array.from(e.dataTransfer.types))) return
    e.preventDefault()
    setIsDragOver(true)
  }

  function handleDrop(e: DragEvent<HTMLElement>) {
    if (!isRelevantDragTypes(Array.from(e.dataTransfer.types))) return
    // 入力欄全体(InputPane)も保険としてドロップを受けるので、ここで処理したら伝播を止める
    // (チップ行と同じ。止めないと同じ画像が二重にアップロードされる)。
    e.stopPropagation()
    setIsDragOver(false)
    logic.handleDrop(e)
  }

  return (
    <div
      className={styles.wrap}
      data-drag-over={isDragOver}
      onDragOver={handleDragOver}
      onDragLeave={() => setIsDragOver(false)}
      onDrop={handleDrop}
    >
      <div className={styles.headingRow}>
        <span className={styles.heading}>{it.heading}</span>
        {exactInputImagesRequired !== null ? (
          <span className={styles.requirement}>
            {fmt(c.requirement, { count: images.length, total: exactInputImagesRequired })}
          </span>
        ) : (
          <span className={styles.count} title={fmt(c.countTitle, { max: maxInputImages })}>
            {images.length}/{maxInputImages}
          </span>
        )}
      </div>

      {images.length === 0 && <span className={styles.hint}>{c.emptyHint}</span>}

      <div className={styles.grid}>
        {images.map((item, index) => {
          const detail = detailsByAssetId.get(item.assetId)
          const isPrimary = index === 0
          const isNotFound = notFoundAssetIds.has(item.assetId)
          const isDeleted = deletedAssetIds.has(item.assetId)
          // チップ行(InputChipsRow)と同じ条件: マスクの入力があるか、選んだモデルが
          // マスクに対応していれば「マスクを描く」を出す。入力があるのに非対応になった場合は
          // タイトルだけ差し替えて、外す操作は残す。
          const showMaskDraw = isPrimary && (maskSupported || Boolean(maskInput))
          const maskDrawTitle = maskInput ? (maskSupported ? c.drawMask : c.maskNotSupportedTitle) : c.drawMask

          return (
            <div
              key={item.assetId}
              className={styles.tile}
              data-primary={isPrimary}
              data-not-found={isNotFound}
              data-deleted={isDeleted}
            >
              <button
                type="button"
                className={styles.thumbButton}
                aria-label={c.showInPreview}
                title={c.showInPreview}
                onClick={() => onPreviewAsset(item.assetId)}
              >
                <img className={styles.thumb} src={assetUrl(item.assetId, 'thumb')} alt="" draggable={false} />
              </button>

              <span className={styles.indexBadge} title={isPrimary ? c.primaryTitle : undefined}>
                {index + 1}
              </span>

              {isNotFound ? (
                <span className={styles.statusLabel}>{c.notFound}</span>
              ) : isDeleted ? (
                <span className={styles.statusLabel}>{c.deleted}</span>
              ) : (
                detail && <span className={styles.dims}>{`${detail.width}×${detail.height}`}</span>
              )}

              {isPrimary && maskInput && (
                <span className={styles.maskBadge}>
                  <img
                    className={styles.maskThumb}
                    src={assetUrl(maskInput.assetId, 'thumb')}
                    alt={c.maskAlt}
                    draggable={false}
                  />
                  {!maskSupported && <span className={styles.maskNotSupportedTag}>{c.maskNotSupportedTag}</span>}
                </span>
              )}

              <div className={styles.actions}>
                {images.length > 1 && (
                  <>
                    <button
                      type="button"
                      className={styles.actionButton}
                      onClick={() => logic.handleMove(item.assetId, 'up')}
                      disabled={index === 0}
                      aria-label={it.movePrev}
                      title={it.movePrev}
                    >
                      <PrevIcon />
                    </button>
                    <button
                      type="button"
                      className={styles.actionButton}
                      onClick={() => logic.handleMove(item.assetId, 'down')}
                      disabled={index === images.length - 1}
                      aria-label={it.moveNext}
                      title={it.moveNext}
                    >
                      <NextIcon />
                    </button>
                  </>
                )}

                {showMaskDraw && (
                  <button
                    type="button"
                    className={styles.actionButton}
                    aria-label={c.drawMask}
                    title={maskDrawTitle}
                    onClick={onOpenMaskEditor}
                  >
                    <PencilIcon />
                  </button>
                )}

                {isPrimary && maskInput && (
                  <button
                    type="button"
                    className={styles.actionButton}
                    aria-label={c.removeMask}
                    title={c.removeMask}
                    onClick={logic.handleRemoveMask}
                  >
                    <RemoveIcon />
                  </button>
                )}

                <button
                  type="button"
                  className={styles.actionButton}
                  aria-label={c.drawOver}
                  title={c.drawOver}
                  onClick={() => logic.openSketchEditor({ mode: 'over', assetId: item.assetId })}
                >
                  <PencilIcon />
                </button>

                <button
                  type="button"
                  className={styles.actionButton}
                  aria-label={c.removeInput}
                  title={c.removeInput}
                  onClick={() => logic.handleRemove(item.assetId)}
                >
                  <RemoveIcon />
                </button>
              </div>
            </div>
          )
        })}

        <button
          type="button"
          className={styles.addTile}
          disabled={logic.isUploading || images.length >= maxInputImages}
          aria-label={c.addAria}
          title={fmt(c.addTitle, { max: maxInputImages })}
          onClick={onOpenAddDialog}
        >
          <AddIcon />
          <span>{logic.isUploading ? c.uploading : c.add}</span>
        </button>

        <button
          type="button"
          className={styles.addTile}
          disabled={images.length >= maxInputImages}
          aria-label={c.sketch}
          title={fmt(c.sketchTitle, { width: blankSketchSize.width, height: blankSketchSize.height })}
          onClick={() => logic.openSketchEditor({ mode: 'blank' })}
        >
          <PencilIcon />
          <span>{c.sketch}</span>
        </button>
      </div>

      {logic.uploadErrors.length > 0 && (
        <ul className={styles.errorList}>
          {logic.uploadErrors.map((err) => (
            <li key={err}>{err}</li>
          ))}
        </ul>
      )}
      {logic.uploadNotice && <p className={styles.uploadNotice}>{logic.uploadNotice}</p>}
    </div>
  )
}
