/**
 * 白紙の配置図のプレビュー(ADR-0053 5章・6章)。サーバーが送る配置図と同じ配置を SVG で描く
 * (`layoutSketch`)。入力の行のタイル・チップに、入力画像のサムネイルの代わりに置く。
 */
import { useMemo } from 'react'
import type { RegionBox } from '../../api/client'
import { layoutSketch } from './layoutSketch'

interface Props {
  width: number
  height: number
  regions: readonly RegionBox[]
  className?: string
}

export function LayoutSketchPreview({ width, height, regions, className }: Props) {
  const sketch = useMemo(() => layoutSketch(width, height, regions), [width, height, regions])
  return (
    <svg
      className={className}
      viewBox={`0 0 ${sketch.width} ${sketch.height}`}
      preserveAspectRatio="xMidYMid meet"
      aria-hidden="true"
    >
      <rect x={0} y={0} width={sketch.width} height={sketch.height} fill="#ffffff" />
      {sketch.marks.map((m) => (
        <g key={m.number}>
          <rect x={m.edge.x} y={m.edge.y} width={m.edge.w} height={m.edge.h} fill="none" stroke="#ffffff" strokeWidth={m.edge.strokeWidth} />
          <rect x={m.frame.x} y={m.frame.y} width={m.frame.w} height={m.frame.h} fill="none" stroke={m.color} strokeWidth={m.frame.strokeWidth} />
          <rect
            x={m.badge.x}
            y={m.badge.y}
            width={m.badge.w}
            height={m.badge.h}
            fill={m.color}
            stroke="#ffffff"
            strokeWidth={m.badge.strokeWidth}
          />
          <text
            x={m.badge.x + m.badge.w / 2}
            y={m.badge.y + m.badge.h / 2}
            fill={m.textColor}
            fontSize={m.fontSize}
            fontFamily="sans-serif"
            textAnchor="middle"
            dominantBaseline="central"
          >
            {m.number}
          </text>
        </g>
      ))}
    </svg>
  )
}
