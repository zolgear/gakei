/**
 * GAKEI のシンボル(ブランドキットの react/GakeiLogo.tsx をこのリポジトリの書式に合わせたもの)。
 * 「画」の字の「田」を4枚の画像に見立て、左上(Violet = 起点)から右下(Pink = 派生)への系列を表す。
 * 枠線は親要素の color を継承する。`title` を省くと装飾扱い(aria-hidden)になる。
 * 使用ルール: 最小 16px、縦横比の変更・回転・タイルの色の入れ替えはしない
 * (例外: `state` で Run の進捗を表すとき(ADR-0009 8章)だけ、タイルの色をランプ表示に使う)。
 */
import type { SVGProps } from 'react'
import {
  FRAME_PATH,
  LID_PATH,
  TILE_RADIUS,
  TILE_SIZE,
  TILE_XY,
  iconShapes,
  type FaviconShape,
  type IconState,
} from '../features/favicon/iconShape'

const IDLE: IconState = { kind: 'idle' }

interface GakeiMarkProps extends Omit<SVGProps<SVGSVGElement>, 'color'> {
  /** px。既定 32 */
  size?: number
  /** color: 枠は currentColor、タイルはブランド色 / mono: すべて currentColor */
  variant?: 'color' | 'mono'
  title?: string
  /**
   * Run の進捗(ADR-0009 8章)。省略時は従来どおりの通常ロゴ(idle)と同じ描画になる。
   * favicon(`features/favicon/faviconSvg.ts`)と同じ図形リスト(`iconShapes`)を描くだけにして、
   * 色・形の決定ロジックを重複させない。
   */
  state?: IconState
}

function renderShapeJsx(shape: FaviconShape, key: number, mono: boolean) {
  switch (shape.kind) {
    case 'lid':
      return <path key={key} d={LID_PATH} fill="currentColor" />
    case 'frame':
      return <path key={key} d={FRAME_PATH} fill="currentColor" />
    case 'badge':
      return <circle key={key} cx={54} cy={10} r={8} fill={mono ? 'currentColor' : shape.color} />
    case 'tile': {
      if (shape.empty) return null
      const [x, y] = TILE_XY[shape.index]
      const fill = mono || shape.color === null ? 'currentColor' : shape.color
      return (
        <rect
          key={key}
          x={x}
          y={y}
          width={TILE_SIZE}
          height={TILE_SIZE}
          rx={TILE_RADIUS}
          fill={fill}
          opacity={shape.opacity < 1 ? shape.opacity : undefined}
        />
      )
    }
  }
}

export function GakeiMark({ size = 32, variant = 'color', title, state, ...rest }: GakeiMarkProps) {
  const a11y = title ? { role: 'img', 'aria-label': title } : { 'aria-hidden': true }
  const mono = variant === 'mono'
  // 進捗が無いとき(既定)は通常ロゴ(idle)。favicon と同じ図形リストから描く。
  const shapes = iconShapes(state ?? IDLE)
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} {...a11y} {...rest}>
      {title && <title>{title}</title>}
      {shapes.map((shape, i) => renderShapeJsx(shape, i, mono))}
    </svg>
  )
}
