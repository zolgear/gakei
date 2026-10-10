/**
 * 下段(入力エリア)の1行目「入力」。入力画像をチップ(サムネイル30px、1 からの番号、寸法、
 * マスクを描く、描き込む、外す)で横並びに表示する。サムネイルを押すと、その画像を上段のプレビューに
 * 出す(編集元を見ながらプロンプトを書けるように。ビューアへは移動しない)。番号 1 が主たる親(position 0)で、マスクが
 * あればそのチップ内に表示する。行末に枚数 `n/16` を出す。「+ 追加」の隣の「スケッチ」ボタンは
 * 白紙に描いて入力画像を作る(ADR-0010)。
 * ドロップはこの行と下段全体の両方で受ける(呼び出し側が `onDrop`/`onDragOver` を
 * このコンポーネントとコンテナの両方に配線する)。
 * 「+ 追加」は OS のファイル選択を直接開かず、`onOpenAddDialog` で `AddInputImagesDialog`
 * (ファイルを選ぶ/ストックから選ぶ)を開く(ADR-0009 1章・2026-09-26 追加)。
 */
import { useState, type DragEvent } from 'react'
import { assetUrl } from '../../api/assetUrl'
import { fmt, useI18n } from '../../i18n'
import type { EditInputsLogic } from '../run-form/useEditInputsLogic'
import { isRelevantDragTypes } from '../run-form/dragDropAssets'
import { AddIcon, PencilIcon, RemoveIcon } from './inputIcons'
import styles from './InputChipsRow.module.css'
import { focalStyle } from '../../lib/focalPoint'

/**
 * 入力画像の一覧を渡す props。下段の `InputChipsRow` とサイドバー配置の `InputImageTiles`
 * (予定)が同じ props 型を共有する(見た目だけが違う)。
 */
export interface InputImagesProps {
  logic: EditInputsLogic
  maxInputImages: number
  /**
   * 選んだモデルの edit が「ちょうどN枚」を要求するときの N(ADR-0013 の画像の枠。
   * min_input_images === max_input_images > 1)。それ以外は null で、従来どおり
   * 「枚数/上限」の表示のままにする。
   */
  exactInputImagesRequired: number | null
  /** 選んだモデルの edit がマスクの差し込み先を持つか(supports_mask)。false のワークフロー
   * (ComfyUI の一部)では「マスクを描く」を出さず、既にマスクがあれば非対応と分かる表示にする。 */
  maskSupported: boolean
  onOpenMaskEditor: () => void
  /** 「スケッチ」ボタン(白紙)で使う Canvas サイズ。出力サイズから解いた値を呼び出し側が渡す。 */
  blankSketchSize: { width: number; height: number }
  /** チップのサムネイルを押したとき、その Asset を上段のプレビューに表示する。 */
  onPreviewAsset: (assetId: string) => void
  /** 「+ 追加」ボタンが押されたとき、`AddInputImagesDialog` を開く(呼び出し側が状態を持つ)。 */
  onOpenAddDialog: () => void
}

export function InputChipsRow({
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
  const [isDragOver, setIsDragOver] = useState(false)
  const { images, maskInput, detailsByAssetId, notFoundAssetIds, deletedAssetIds } = logic

  function handleDragOver(e: DragEvent<HTMLElement>) {
    if (!isRelevantDragTypes(Array.from(e.dataTransfer.types))) return
    e.preventDefault()
    setIsDragOver(true)
  }

  function handleDrop(e: DragEvent<HTMLElement>) {
    if (!isRelevantDragTypes(Array.from(e.dataTransfer.types))) return
    // 下段全体(InputPane)も保険としてドロップを受けるので、ここで処理したら伝播を止める。
    // 止めないと同じドロップが二重に処理され、同じ画像が2回アップロードされて入力も2つ増える。
    e.stopPropagation()
    setIsDragOver(false)
    logic.handleDrop(e)
  }

  return (
    <div
      className={styles.row}
      onDragOver={handleDragOver}
      onDragLeave={() => setIsDragOver(false)}
      onDrop={handleDrop}
    >
      <span className={styles.label}>{c.label}</span>
      {exactInputImagesRequired !== null && (
        <span className={styles.requirement}>{fmt(c.requirement, { count: images.length, total: exactInputImagesRequired })}</span>
      )}

      {images.length === 0 ? (
        <span className={styles.hint}>{c.emptyHint}</span>
      ) : (
        <>
          <div className={styles.chips}>
            {images.map((item, index) => {
              const detail = detailsByAssetId.get(item.assetId)
              const isPrimary = index === 0
              const isNotFound = notFoundAssetIds.has(item.assetId)
              const isDeleted = deletedAssetIds.has(item.assetId)
              return (
                <div
                  key={item.inputId}
                  className={styles.chip}
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
                    <img
                      className={styles.thumb}
                      src={assetUrl(item.assetId, 'thumb')}
                      style={focalStyle(detail?.focal_point)}
                      alt=""
                      draggable={false}
                    />
                  </button>
                  <span className={styles.indexTag} title={isPrimary ? c.primaryTitle : undefined}>
                    {index + 1}
                  </span>
                  {isNotFound ? (
                    <span className={styles.statusTag}>{c.notFound}</span>
                  ) : isDeleted ? (
                    <span className={styles.statusTag}>{c.deleted}</span>
                  ) : (
                    <span className={styles.dims}>
                      {detail ? `${detail.width}×${detail.height}` : c.dimsLoading}
                    </span>
                  )}

                  {images.length > 1 && (
                    <div className={styles.reorderButtons}>
                      <button
                        type="button"
                        onClick={() => logic.handleMove(item.inputId, 'up')}
                        disabled={index === 0}
                        aria-label={c.moveUp}
                      >
                        ▲
                      </button>
                      <button
                        type="button"
                        onClick={() => logic.handleMove(item.inputId, 'down')}
                        disabled={index === images.length - 1}
                        aria-label={c.moveDown}
                      >
                        ▼
                      </button>
                    </div>
                  )}

                  {isPrimary &&
                    (maskInput ? (
                      <>
                        <img
                          className={styles.maskThumb}
                          src={assetUrl(maskInput.assetId, 'thumb')}
                          alt={c.maskAlt}
                          draggable={false}
                        />
                        {/* 非対応モデルへ切り替えた後もマスクの入力自体は残る(サーバー側で
                            422 になる)ので、外せる操作は残しつつ非対応であることを示す。 */}
                        {!maskSupported && <span className={styles.statusTag}>{c.maskNotSupportedTag}</span>}
                        <button
                          type="button"
                          className={styles.iconButton}
                          aria-label={c.drawMask}
                          title={maskSupported ? c.drawMask : c.maskNotSupportedTitle}
                          onClick={onOpenMaskEditor}
                        >
                          <PencilIcon />
                        </button>
                        <button
                          type="button"
                          className={styles.iconButton}
                          aria-label={c.removeMask}
                          title={c.removeMask}
                          onClick={logic.handleRemoveMask}
                        >
                          <RemoveIcon />
                        </button>
                      </>
                    ) : (
                      // マスクの差し込み先が無いモデル(supports_mask=false)では、描いても
                      // 送信できない(サーバーが 422 を返す)ので、そもそもボタンを出さない。
                      maskSupported && (
                        <button
                          type="button"
                          className={styles.iconButton}
                          aria-label={c.drawMask}
                          title={c.drawMask}
                          onClick={onOpenMaskEditor}
                        >
                          <PencilIcon />
                        </button>
                      )
                    ))}

                  <button
                    type="button"
                    className={styles.iconButton}
                    aria-label={c.drawOver}
                    title={c.drawOver}
                    onClick={() =>
                      logic.openSketchEditor({ mode: 'over', inputId: item.inputId, assetId: item.assetId })
                    }
                  >
                    <PencilIcon />
                  </button>

                  <button
                    type="button"
                    className={styles.iconButton}
                    aria-label={c.removeInput}
                    title={c.removeInput}
                    onClick={() => logic.handleRemove(item.inputId)}
                  >
                    <RemoveIcon />
                  </button>
                </div>
              )
            })}
          </div>
          {exactInputImagesRequired === null && (
            <span className={styles.count} title={fmt(c.countTitle, { max: maxInputImages })}>
              {images.length}/{maxInputImages}
            </span>
          )}
        </>
      )}

      <button
        type="button"
        className={styles.addButton}
        data-drag-over={isDragOver}
        aria-label={c.addAria}
        title={fmt(c.addTitle, { max: maxInputImages })}
        disabled={logic.isUploading || images.length >= maxInputImages}
        onClick={onOpenAddDialog}
      >
        <AddIcon />
        {logic.isUploading ? c.uploading : c.add}
      </button>
      <button
        type="button"
        className={styles.addButton}
        aria-label={c.sketch}
        title={fmt(c.sketchTitle, { width: blankSketchSize.width, height: blankSketchSize.height })}
        disabled={images.length >= maxInputImages}
        onClick={() => logic.openSketchEditor({ mode: 'blank' })}
      >
        <PencilIcon />
        {c.sketch}
      </button>

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
