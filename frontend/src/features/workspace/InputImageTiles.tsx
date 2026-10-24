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
import { AssetImage } from '../mods/AssetImage'
import { fmt, useI18n } from '../../i18n'
import { isRelevantDragTypes } from '../run-form/dragDropAssets'
import { AddIcon, PencilIcon, RemoveIcon } from './inputIcons'
import { ArrangeIcon } from '../composite/compositeIcons'
import { EyeIcon, RegionsIcon } from '../regions/regionIcons'
import { LayoutSketchPreview } from '../regions/LayoutSketchPreview'
import type { InputImagesProps } from './InputChipsRow'
import { AssetThumb } from '../content-rating/AssetThumb'
import styles from './InputImageTiles.module.css'
import { focalStyle } from '../../lib/focalPoint'

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
  regions,
}: InputImagesProps) {
  const { t } = useI18n()
  const c = t.workspace.inputChips
  const it = t.workspace.inputTiles
  const [isDragOver, setIsDragOver] = useState(false)
  const { images, maskInput, detailsByAssetId, notFoundAssetIds, deletedAssetIds, blockedAssetIds } = logic

  // 1枚目の上に領域を描けるか(タイルの操作のバンドと同じ条件)。
  const primary = images[0]
  const primaryRegionsUsable =
    regions.supported &&
    primary !== undefined &&
    detailsByAssetId.get(primary.assetId)?.content_withheld !== true &&
    !notFoundAssetIds.has(primary.assetId) &&
    !deletedAssetIds.has(primary.assetId) &&
    !blockedAssetIds.has(primary.assetId)

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
          // Mod の判定で入力に使えない(ADR-0051 4章)。画像が無ければ描き込み・配置・マスクも出さない。
          const isBlocked = blockedAssetIds.has(item.assetId)
          const isWithheld = detail?.content_withheld === true
          // チップ行(InputChipsRow)と同じ条件: マスクの入力があるか、選んだモデルが
          // マスクに対応していれば「マスクを描く」を出す。入力があるのに非対応になった場合は
          // タイトルだけ差し替えて、外す操作は残す。
          const showMaskDraw = isPrimary && !isWithheld && (maskSupported || Boolean(maskInput))
          const maskDrawTitle = maskInput ? (maskSupported ? c.drawMask : c.maskNotSupportedTitle) : c.drawMask

          return (
            <div
              key={item.inputId}
              className={styles.tile}
              data-primary={isPrimary}
              data-not-found={isNotFound}
              data-deleted={isDeleted || isBlocked}
              data-redrawing={logic.compositeRedrawingInputId === item.inputId}
            >
              <button
                type="button"
                className={styles.thumbButton}
                aria-label={c.showInPreview}
                title={c.showInPreview}
                onClick={() => onPreviewAsset(item.assetId)}
              >
                <AssetThumb
                  assetId={item.assetId}
                  // 詳細を取得するまでは段階が分からない(undefined)のでマスクしない。
                  rating={detail ? (detail.content_rating ?? null) : undefined}
                  withheld={isWithheld}
                  withheldLabel={detail?.content_withheld_verdict?.label}
                  className={styles.thumb}
                  style={focalStyle(detail?.focal_point)}
                  alt=""
                  draggable={false}
                />
              </button>

              <span className={styles.indexBadge} title={isPrimary ? c.primaryTitle : undefined}>
                {index + 1}
              </span>

              {isNotFound ? (
                <span className={styles.statusLabel}>{c.notFound}</span>
              ) : isDeleted ? (
                <span className={styles.statusLabel}>{c.deleted}</span>
              ) : isWithheld ? null : logic.compositeRedrawingInputId === item.inputId ? (
                <span className={styles.busyLabel} role="status" title={t.composite.redrawing}>
                  {t.composite.redrawingShort}
                </span>
              ) : isBlocked ? (
                // 画像なしはタイルの置き換えの表示が理由を示すので、ここは deny の判定だけのとき。
                <span className={styles.statusLabel} title={t.mods.inputBlockedTitle}>
                  {t.mods.inputBlocked}
                </span>
              ) : (
                detail && <span className={styles.dims}>{`${detail.width}×${detail.height}`}</span>
              )}

              {/* 白紙の土台の四角は1枚目に付かない(数は白紙のタイルに出す)。 */}
              {isPrimary && regions.supported && regions.count > 0 && !regions.blank && !isWithheld && (
                <span className={styles.regionsBadge}>
                  <button
                    type="button"
                    className={styles.regionsBadgeButton}
                    title={t.regions.form.chipTitle}
                    onClick={regions.onOpen}
                  >
                    {fmt(t.regions.form.chip, { count: regions.count })}
                  </button>
                  <button
                    type="button"
                    className={styles.regionsBadgeButton}
                    aria-label={t.regions.form.clear}
                    title={t.regions.form.clear}
                    onClick={regions.onClear}
                  >
                    <RemoveIcon />
                  </button>
                </span>
              )}

              {isPrimary && maskInput && (
                <span className={styles.maskBadge}>
                  <AssetImage
                    className={styles.maskThumb}
                    src={assetUrl(maskInput.assetId, 'thumb')}
                    withheld={detailsByAssetId.get(maskInput.assetId)?.content_withheld === true}
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
                      onClick={() => logic.handleMove(item.inputId, 'up')}
                      disabled={index === 0}
                      aria-label={it.movePrev}
                      title={it.movePrev}
                    >
                      <PrevIcon />
                    </button>
                    <button
                      type="button"
                      className={styles.actionButton}
                      onClick={() => logic.handleMove(item.inputId, 'down')}
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

                {isPrimary && regions.supported && !isWithheld && !isNotFound && !isDeleted && !isBlocked && (
                  <button
                    type="button"
                    className={styles.actionButton}
                    aria-label={regions.count > 0 ? t.regions.form.chipTitle : t.regions.button}
                    title={regions.count > 0 ? t.regions.form.chipTitle : t.regions.buttonTitle}
                    onClick={regions.onOpen}
                  >
                    <RegionsIcon size={12} />
                  </button>
                )}

                {isPrimary && regions.supported && regions.hasOverlay && !isWithheld && (
                  <button
                    type="button"
                    className={styles.actionButton}
                    aria-label={regions.overlayShown ? t.regions.assist.hide : t.regions.assist.show}
                    aria-pressed={regions.overlayShown}
                    title={regions.overlayShown ? t.regions.assist.hide : t.regions.assist.show}
                    onClick={regions.onToggleOverlay}
                  >
                    <EyeIcon off={!regions.overlayShown} size={12} />
                  </button>
                )}

                {detail?.kind === 'composite' && !isWithheld && (
                  <button
                    type="button"
                    className={styles.actionButton}
                    aria-label={t.composite.edit}
                    title={t.composite.edit}
                    onClick={() =>
                      logic.openCompositeEditor({ mode: 'reopen', inputId: item.inputId, assetId: item.assetId })
                    }
                  >
                    <ArrangeIcon size={12} />
                  </button>
                )}

                {!isWithheld && (
                  <button
                    type="button"
                    className={styles.actionButton}
                    aria-label={c.drawOver}
                    title={c.drawOver}
                    onClick={() =>
                      logic.openSketchEditor({ mode: 'over', inputId: item.inputId, assetId: item.assetId })
                    }
                  >
                    <PencilIcon />
                  </button>
                )}

                <button
                  type="button"
                  className={styles.actionButton}
                  aria-label={c.removeInput}
                  title={c.removeInput}
                  onClick={() => logic.handleRemove(item.inputId)}
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

        {/* 入力画像があるときの領域(ADR-0053)。1枚目の上の四角を描く。タイルの操作のバンドはホバーで
            しか見えない(タッチでは出ない)ので、スケッチ・配置と同じ並びにも常に出す。白紙の土台の四角が
            あるときは、下の白紙のタイルを出す。 */}
        {primaryRegionsUsable && !(regions.blank && regions.count > 0) && (
          <button
            type="button"
            className={styles.addTile}
            aria-label={regions.count > 0 ? t.regions.form.chipTitle : t.regions.button}
            title={regions.count > 0 ? t.regions.form.chipTitle : t.regions.buttonTitle}
            onClick={regions.onOpen}
          >
            <RegionsIcon />
            <span>{regions.count > 0 ? fmt(t.regions.form.chip, { count: regions.count }) : t.regions.button}</span>
          </button>
        )}

        {/* 白紙の領域(ADR-0053 6章・1章)。四角を付けたら、入力画像のタイルと同じ形で白紙の配置図の
            プレビューを出し、数と外すボタンを重ねる(入力画像があっても)。四角が無ければ、入力画像の
            無いときだけ「領域」を出す(あるときは上の1枚目の「領域」から開く)。 */}
        {regions.blank &&
          (regions.count > 0 || images.length === 0) &&
          (regions.count > 0 && regions.boxes && regions.blankCanvas ? (
            <div className={styles.tile}>
              <button
                type="button"
                className={styles.thumbButton}
                aria-label={t.regions.form.chipTitle}
                title={t.regions.form.chipTitle}
                onClick={regions.onOpen}
              >
                <LayoutSketchPreview
                  className={styles.thumb}
                  width={regions.blankCanvas.width}
                  height={regions.blankCanvas.height}
                  regions={regions.boxes}
                />
              </button>
              <span className={styles.regionsBadge}>
                <button
                  type="button"
                  className={styles.regionsBadgeButton}
                  title={t.regions.form.chipTitle}
                  onClick={regions.onOpen}
                >
                  {fmt(t.regions.form.chip, { count: regions.count })}
                </button>
                <button
                  type="button"
                  className={styles.regionsBadgeButton}
                  aria-label={t.regions.form.clear}
                  title={t.regions.form.clear}
                  onClick={regions.onClear}
                >
                  <RemoveIcon />
                </button>
              </span>
            </div>
          ) : (
            <button
              type="button"
              className={styles.addTile}
              aria-label={t.regions.button}
              title={t.regions.blankButtonTitle}
              onClick={regions.onOpen}
            >
              <RegionsIcon />
              <span>{t.regions.button}</span>
            </button>
          ))}

        <button
          type="button"
          className={styles.addTile}
          aria-label={t.composite.button}
          title={t.composite.buttonTitle}
          onClick={() => logic.startComposite()}
        >
          <ArrangeIcon />
          <span>{t.composite.button}</span>
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
