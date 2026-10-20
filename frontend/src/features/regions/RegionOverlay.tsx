/**
 * 画像の上に、領域の四角(番号と色。編集画面・サーバーの画像と同じ色)と検出の候補(点線と名前)を
 * 重ねて見せるだけの部品(ADR-0053)。操作は受けない。生成のフォームの入力の補助
 * (`RegionPromptAssist`)で使う。
 *
 * SVG の座標は画像の画素(`viewBox` が画像の大きさ)にし、線の太さは表示の大きさに依らず一定にする
 * (`vector-effect: non-scaling-stroke`)。文字の大きさは画像の短辺に対する割合で決める。
 */
import type { RegionBox } from '../../api/client'
import { estimateTextWidth, placeCaptions, type CaptionRect, type CaptionRequest } from './captionLayout'
import { orderedRegions, regionBadgeTextColor, regionColor, regionLabel } from './regions'
import type { RegionCandidate } from './useRegionDetection'
import styles from './RegionPromptAssist.module.css'

/** 強調する物。四角は番号、候補は名前(同じ名前の候補をまとめて強調する)。 */
export type RegionHighlight = { type: 'region'; number: number } | { type: 'label'; label: string } | null

interface RegionOverlayProps {
  width: number
  height: number
  regions: readonly RegionBox[]
  candidates: readonly RegionCandidate[]
  highlight: RegionHighlight
  /** 文字の大きさの倍率(小さなプレビューでは大きめにする)。 */
  textScale?: number
}

export function RegionOverlay({ width, height, regions, candidates, highlight, textScale = 1 }: RegionOverlayProps) {
  const short = Math.min(width, height)
  const font = short * 0.045 * textScale
  const badge = short * 0.07 * textScale
  const ordered = orderedRegions(regions)
  const dim = (on: boolean) => (highlight === null || on ? 1 : 0.35)

  // 名前の位置。近い四角の名前が重ならないよう、重なるときは上下にずらす(四角の名前を先に置く)。
  const captionHeight = font * 1.3
  const requests: CaptionRequest[] = []
  const badges: CaptionRect[] = []
  for (const region of ordered) {
    const x = region.box[0] * width
    const y = region.box[1] * height
    badges.push({ x, y, width: badge, height: badge })
    const label = regionLabel(region)
    if (label) {
      requests.push({
        key: `r:${region.number}`,
        x: x + badge * 1.15,
        y: y + (badge - captionHeight) / 2,
        width: estimateTextWidth(label, font) + font * 0.3,
        height: captionHeight,
      })
    }
  }
  for (const c of candidates) {
    requests.push({
      key: `c:${c.key}`,
      x: c.box[0] * width + font * 0.3,
      y: c.box[1] * height + font * 0.2,
      width: estimateTextWidth(c.label, font) + font * 0.3,
      height: captionHeight,
    })
  }
  const captions = placeCaptions(requests, { width, height }, badges)
  return (
    <svg className={styles.overlay} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
      {candidates.map((c) => {
        const on = highlight?.type === 'label' && highlight.label === c.label
        const x = c.box[0] * width
        const y = c.box[1] * height
        const w = (c.box[2] - c.box[0]) * width
        const h = (c.box[3] - c.box[1]) * height
        const pos = captions.get(`c:${c.key}`) ?? { x: x + font * 0.3, y: y + font * 0.2 }
        return (
          <g key={c.key} opacity={dim(on)}>
            <rect x={x} y={y} width={w} height={h} fill={on ? 'rgba(255,255,255,0.18)' : 'none'} stroke="rgba(0,0,0,0.8)" strokeWidth={on ? 4 : 3} strokeDasharray="5 4" vectorEffect="non-scaling-stroke" />
            <rect x={x} y={y} width={w} height={h} fill="none" stroke="#ffffff" strokeWidth={on ? 2.5 : 1.5} strokeDasharray="5 4" vectorEffect="non-scaling-stroke" />
            <text x={pos.x} y={pos.y + font * 0.1} fontSize={font} dominantBaseline="hanging" className={styles.overlayLabel}>
              {c.label}
            </text>
          </g>
        )
      })}
      {ordered.map((region, index) => {
        const on = highlight?.type === 'region' && highlight.number === region.number
        const color = regionColor(index)
        const [x0, y0, x1, y1] = region.box
        const x = x0 * width
        const y = y0 * height
        const label = regionLabel(region)
        const labelPos = captions.get(`r:${region.number}`)
        return (
          <g key={region.number} opacity={dim(on)}>
            {region.kind === 'move' && region.source_box && (
              <rect
                x={region.source_box[0] * width}
                y={region.source_box[1] * height}
                width={(region.source_box[2] - region.source_box[0]) * width}
                height={(region.source_box[3] - region.source_box[1]) * height}
                fill="none"
                stroke={color}
                strokeWidth={2}
                strokeDasharray="7 5"
                vectorEffect="non-scaling-stroke"
              />
            )}
            <rect x={x} y={y} width={(x1 - x0) * width} height={(y1 - y0) * height} fill="none" stroke="rgba(255,255,255,0.9)" strokeWidth={on ? 6 : 4} vectorEffect="non-scaling-stroke" />
            <rect x={x} y={y} width={(x1 - x0) * width} height={(y1 - y0) * height} fill={on ? 'rgba(255,255,255,0.12)' : 'none'} stroke={color} strokeWidth={on ? 4 : 2} vectorEffect="non-scaling-stroke" />
            <rect x={x} y={y} width={badge} height={badge} fill={color} />
            <text x={x + badge / 2} y={y + badge / 2} fontSize={badge * 0.7} textAnchor="middle" dominantBaseline="central" fill={regionBadgeTextColor(index)} className={styles.overlayBadge}>
              {region.number}
            </text>
            {label && labelPos && (
              <text x={labelPos.x} y={labelPos.y + font * 0.1} fontSize={font} dominantBaseline="hanging" className={styles.overlayLabel}>
                {label}
              </text>
            )}
          </g>
        )
      })}
    </svg>
  )
}
