/**
 * `iconShapes()` の図形リストから favicon 用の SVG 文字列を組み立てる。
 * theme='auto' なら参照実装どおり `<style>` の `prefers-color-scheme` で枠線色を切り替える
 * (`frontend/public/favicon.svg` と同じ形)。theme='dark'|'light' は枠線色を直接色コードで
 * 埋め込む(Safari 向けに canvas で PNG化する前など、ブラウザの配色に合わせて描くときに使う)。
 */
import {
  BADGE_CX,
  BADGE_CY,
  BADGE_MASK_RADIUS,
  BADGE_RADIUS,
  COLORS,
  FRAME_PATH,
  LID_PATH,
  TILE_RADIUS,
  TILE_SIZE,
  TILE_XY,
  iconShapes,
  type FaviconShape,
  type IconState,
} from './iconShape'

export type FaviconTheme = 'auto' | 'dark' | 'light'

/** `.4` のように先頭の `0` を省く(`frontend/public/favicon.svg` と同じ書式)。 */
function formatOpacity(opacity: number): string {
  const text = String(opacity)
  return text.startsWith('0.') ? text.slice(1) : text
}

function renderShape(shape: FaviconShape, lineAttr: string): string {
  switch (shape.kind) {
    case 'lid':
      return `<path d="${LID_PATH}" ${lineAttr}/>`
    case 'frame':
      return `<path d="${FRAME_PATH}" ${lineAttr}/>`
    case 'badge':
      return '' // マスクの外に出すため、末尾でまとめて描く(下の renderFaviconSvg を参照)。
    case 'tile': {
      if (shape.empty) return ''
      const [x, y] = TILE_XY[shape.index]
      const colorAttr = shape.color ? `fill="${shape.color}"` : lineAttr
      const opacityAttr = shape.opacity < 1 ? ` opacity="${formatOpacity(shape.opacity)}"` : ''
      return `<rect x="${x}" y="${y}" width="${TILE_SIZE}" height="${TILE_SIZE}" rx="${TILE_RADIUS}" ${colorAttr}${opacityAttr}/>`
    }
  }
}

export function renderFaviconSvg(state: IconState, theme: FaviconTheme = 'auto'): string {
  const shapes = iconShapes(state)
  const lineAttr =
    theme === 'auto' ? 'class="g"' : `fill="${theme === 'light' ? COLORS.lineOnLight : COLORS.lineOnDark}"`
  const badge = shapes.find((shape): shape is Extract<FaviconShape, { kind: 'badge' }> => shape.kind === 'badge')

  let svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
  if (theme === 'auto') {
    svg += `<style>.g{fill:${COLORS.lineOnDark}}@media (prefers-color-scheme:light){.g{fill:${COLORS.lineOnLight}}}</style>`
  }
  // 通知ドットがあるときは周囲を抜いて重なりを避ける(参照実装と同じ)。
  if (badge) {
    svg += `<mask id="m"><rect width="64" height="64" fill="#fff"/><circle cx="${BADGE_CX}" cy="${BADGE_CY}" r="${BADGE_MASK_RADIUS}" fill="#000"/></mask><g mask="url(#m)">`
  }
  for (const shape of shapes) svg += renderShape(shape, lineAttr)
  if (badge) {
    svg += `</g><circle cx="${BADGE_CX}" cy="${BADGE_CY}" r="${BADGE_RADIUS}" fill="${badge.color}"/>`
  }
  return svg + '</svg>'
}

/** `<link rel="icon">` に差し込める `data:` URL(SVG のまま)。 */
export function svgToDataUrl(svg: string): string {
  return 'data:image/svg+xml,' + encodeURIComponent(svg)
}
