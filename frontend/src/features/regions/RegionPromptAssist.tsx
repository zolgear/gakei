/**
 * プロンプトの欄の上に出す、領域の入力の補助(ADR-0053)。1枚目の入力に、付けた四角(番号と色)と
 * 検出の候補(点線と名前)を重ねた小さなプレビューと、名前のチップを並べる。チップを押すとプロンプトの
 * カーソル位置へ入れ、チップに触れると対応する四角を強調する。プレビューを押すと大きく見られる。
 *
 * 書きながら見られるよう、プロンプトのすぐ上に置く(狭い幅では結果エリアが上にあり、入力の画像は
 * 小さなチップでしか見えないため)。1枚目が無い、四角を受けないモデル、四角も候補も無いときは出さない。
 * 開閉はブラウザごとに覚える(`regionAssistStore`)。
 */
import { useState } from 'react'
import { assetUrl } from '../../api/assetUrl'
import type { AssetDetail, RegionBox } from '../../api/client'
import { Modal } from '../../components/Modal'
import { fmt, useI18n } from '../../i18n'
import { RegionOverlay, type RegionHighlight } from './RegionOverlay'
import { RegionLabelChips } from './RegionLabelChips'
import { labelChipItems, regionChipItems } from './regionChips'
import { setRegionAssistOpen, useRegionAssistOpen } from './regionAssistStore'
import type { RegionLabels } from './regions'
import { candidatesOf, useDetectionHistory } from './useRegionDetection'
import styles from './RegionPromptAssist.module.css'

interface RegionPromptAssistProps {
  /** 1枚目の入力(未取得・無ければ undefined)。 */
  asset: AssetDetail | undefined
  /** 選んだモデルが四角を受けるか。 */
  enabled: boolean
  regions: readonly RegionBox[]
  labels: RegionLabels
  /** チップを出すか(プロンプトテンプレートのときは書く欄が無いので出さない)。 */
  showChips: boolean
  onInsert: (text: string) => void
}

export function RegionPromptAssist({ asset, enabled, regions, labels, showChips, onInsert }: RegionPromptAssistProps) {
  const { t } = useI18n()
  const a = t.regions.assist
  const open = useRegionAssistOpen()
  const [highlight, setHighlight] = useState<RegionHighlight>(null)
  const [enlarged, setEnlarged] = useState(false)
  const usable = enabled && asset !== undefined && asset.content_withheld !== true
  const { selected } = useDetectionHistory(asset?.id ?? null, usable)
  if (!usable || !asset) return null
  const candidates = selected ? candidatesOf(selected) : []
  if (regions.length === 0 && candidates.length === 0) return null

  const items = [...regionChipItems(t, regions, labels), ...labelChipItems(candidates)]
  const summary = [
    regions.length > 0 ? fmt(a.boxCount, { count: regions.length }) : null,
    candidates.length > 0 ? fmt(a.candidateCount, { count: candidates.length }) : null,
  ]
    .filter(Boolean)
    .join(' · ')

  const overlay = (scale: number) => (
    <RegionOverlay
      width={asset.width}
      height={asset.height}
      regions={regions}
      labels={labels}
      candidates={candidates}
      highlight={highlight}
      textScale={scale}
    />
  )

  return (
    <div className={styles.assist}>
      <button
        type="button"
        className={styles.toggle}
        aria-expanded={open}
        onClick={() => setRegionAssistOpen(!open)}
        title={open ? a.hide : a.show}
      >
        <span className={styles.toggleArrow} aria-hidden="true">
          {open ? '▾' : '▸'}
        </span>
        <span>{a.heading}</span>
        <span className={styles.summary}>{summary}</span>
      </button>

      {open && (
        <div className={styles.body}>
          <button
            type="button"
            className={styles.preview}
            style={
              { aspectRatio: `${asset.width} / ${asset.height}`, '--ar': asset.width / asset.height } as React.CSSProperties
            }
            onClick={() => setEnlarged(true)}
            aria-label={a.enlarge}
            title={a.enlarge}
          >
            <img className={styles.previewImage} src={assetUrl(asset.id, 'preview')} alt="" draggable={false} />
            {overlay(1.6)}
          </button>
          {showChips && (
            <div className={styles.chipsColumn}>
              <span className={styles.chipsHint}>{a.chipsHint}</span>
              <RegionLabelChips items={items} onInsert={onInsert} onHighlight={setHighlight} ariaLabel={a.chipsLabel} />
            </div>
          )}
        </div>
      )}

      <Modal open={enlarged} title={a.heading} onClose={() => setEnlarged(false)} size="large">
        <div className={styles.enlargedBody}>
          <div className={styles.enlargedFrame} style={{ aspectRatio: `${asset.width} / ${asset.height}` }}>
            <img className={styles.previewImage} src={assetUrl(asset.id, 'preview')} alt="" draggable={false} />
            {overlay(1)}
          </div>
          {showChips && (
            <RegionLabelChips
              items={items}
              onInsert={(text) => {
                onInsert(text)
                setEnlarged(false)
              }}
              onHighlight={setHighlight}
              ariaLabel={a.chipsLabel}
            />
          )}
        </div>
      </Modal>
    </div>
  )
}
