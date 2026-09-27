/**
 * トリミング共通部品(`CropEditor`/`ImageCropDialog`、ADR-0020 5章)の座標計算。
 * DOM に依存しない純粋関数だけを置く(`CropEditor.tsx` から呼ばれる)。
 *
 * `CropRect` は常に「元画像のピクセル座標」(整数)。表示用のピクセル座標は `toDisplay` で
 * 変換した別の `CropRect` として扱い、両者を型では区別しない(呼び出し側の文脈で判断する)。
 */
import type { Size } from '../../lib/geometry'

export interface CropRect {
  x: number
  y: number
  width: number
  height: number
}

/** 四隅のハンドル。`n`/`s` が上/下、`w`/`e` が左/右。 */
export type CropHandle = 'nw' | 'ne' | 'sw' | 'se'

/** 拡縮・移動で許す最小の一辺(元画像のピクセル)。これ未満には縮められない。 */
export const MIN_CROP_SIZE = 16

function clamp(value: number, min: number, max: number): number {
  if (max < min) return min
  return Math.min(Math.max(value, min), max)
}

/** 全フィールドを整数に丸める(負の幅・高さにはしない)。 */
export function roundRect(rect: CropRect): CropRect {
  return {
    x: Math.round(rect.x),
    y: Math.round(rect.y),
    width: Math.max(0, Math.round(rect.width)),
    height: Math.max(0, Math.round(rect.height)),
  }
}

/**
 * container に natural を収める表示倍率(アスペクト比を保つ contain fit)。
 * ビューア(`viewerScale.ts`/`lib/geometry.ts::computeFitScale`)と違い、トリミングは細かい操作の
 * しやすさを優先するので、小さい画像を container まで拡大することも許す(1 を上限にしない)。
 * どちらかのサイズが 0 以下なら 1(等倍)を返す。
 */
export function fitScale(natural: Size, container: Size): number {
  if (natural.width <= 0 || natural.height <= 0 || container.width <= 0 || container.height <= 0) {
    return 1
  }
  return Math.min(container.width / natural.width, container.height / natural.height)
}

/** 元画像のピクセル座標 → 表示ピクセル座標。 */
export function toDisplay(rect: CropRect, scale: number): CropRect {
  return { x: rect.x * scale, y: rect.y * scale, width: rect.width * scale, height: rect.height * scale }
}

/** 表示ピクセル座標 → 元画像のピクセル座標(整数に丸める)。 */
export function toNatural(rect: CropRect, scale: number): CropRect {
  if (scale === 0) return roundRect(rect)
  return roundRect({
    x: rect.x / scale,
    y: rect.y / scale,
    width: rect.width / scale,
    height: rect.height / scale,
  })
}

/** 矩形を bounds([0,0]-[width,height])の内側に収める。サイズは bounds を超えない範囲でそのまま保つ。 */
export function clampRect(rect: CropRect, bounds: Size): CropRect {
  const width = clamp(rect.width, 0, Math.max(0, bounds.width))
  const height = clamp(rect.height, 0, Math.max(0, bounds.height))
  const x = clamp(rect.x, 0, Math.max(0, bounds.width - width))
  const y = clamp(rect.y, 0, Math.max(0, bounds.height - height))
  return roundRect({ x, y, width, height })
}

/** 矩形を (dx, dy) だけ平行移動する(サイズは変えず、bounds の内側にクランプする)。 */
export function moveRect(rect: CropRect, dx: number, dy: number, bounds: Size): CropRect {
  const maxX = Math.max(0, bounds.width - rect.width)
  const maxY = Math.max(0, bounds.height - rect.height)
  const x = clamp(rect.x + dx, 0, maxX)
  const y = clamp(rect.y + dy, 0, maxY)
  return roundRect({ x, y, width: rect.width, height: rect.height })
}

interface Point {
  x: number
  y: number
}

/** ハンドルの位置(その角の座標)。 */
function cornerOf(rect: CropRect, handle: CropHandle): Point {
  return {
    x: handle.includes('e') ? rect.x + rect.width : rect.x,
    y: handle.includes('s') ? rect.y + rect.height : rect.y,
  }
}

/** ハンドルの対角(固定される角)の座標。 */
function anchorOf(rect: CropRect, handle: CropHandle): Point {
  return {
    x: handle.includes('e') ? rect.x : rect.x + rect.width,
    y: handle.includes('s') ? rect.y : rect.y + rect.height,
  }
}

/**
 * 四隅のハンドルを (dx, dy) だけ動かして拡縮する。対角(anchor)は固定したまま、動かした角
 * (handle)が (dx, dy) の位置に来るように width/height を決める。
 * `aspect` が数値なら縦横比を保つ(対角線方向のどちらか大きい方に合わせて広がる)。
 * `null` なら自由な形。最小サイズ(`MIN_CROP_SIZE`)を下回らず、bounds の外にはみ出さない。
 */
export function resizeWithAspect(
  rect: CropRect,
  handle: CropHandle,
  dx: number,
  dy: number,
  aspect: number | null,
  bounds: Size,
): CropRect {
  const anchor = anchorOf(rect, handle)
  const corner = cornerOf(rect, handle)
  const dirX = handle.includes('e') ? 1 : -1
  const dirY = handle.includes('s') ? 1 : -1
  // 動かした先の角を、bounds の内側かつ anchor を越えない範囲(越えると矩形が反転してしまう)に
  // クランプしてから幅・高さを求める。
  const movedX = dirX === 1 ? clamp(corner.x + dx, anchor.x, bounds.width) : clamp(corner.x + dx, 0, anchor.x)
  const movedY = dirY === 1 ? clamp(corner.y + dy, anchor.y, bounds.height) : clamp(corner.y + dy, 0, anchor.y)

  const maxWidth = Math.max(0, dirX === 1 ? bounds.width - anchor.x : anchor.x)
  const maxHeight = Math.max(0, dirY === 1 ? bounds.height - anchor.y : anchor.y)

  let width = Math.abs(movedX - anchor.x)
  let height = Math.abs(movedY - anchor.y)

  if (aspect !== null && aspect > 0) {
    // 縦横比固定: 2方向のうち、より大きく広がろうとした方に合わせる。
    if (width / aspect >= height) {
      height = width / aspect
    } else {
      width = height * aspect
    }
    // bounds を超えないよう、縦横比を保ったまま縮める。
    if (width > maxWidth) {
      width = maxWidth
      height = width / aspect
    }
    if (height > maxHeight) {
      height = maxHeight
      width = height * aspect
    }
    // 最小サイズ(bounds が MIN_CROP_SIZE 未満ならその場で許される最大まで)。
    const minWidth = Math.min(MIN_CROP_SIZE, maxWidth)
    const minHeight = Math.min(MIN_CROP_SIZE, maxHeight)
    if (width < minWidth) {
      width = minWidth
      height = width / aspect
    }
    if (height < minHeight) {
      height = minHeight
      width = height * aspect
    }
  } else {
    width = clamp(width, Math.min(MIN_CROP_SIZE, maxWidth), maxWidth)
    height = clamp(height, Math.min(MIN_CROP_SIZE, maxHeight), maxHeight)
  }

  const x = dirX === 1 ? anchor.x : anchor.x - width
  const y = dirY === 1 ? anchor.y : anchor.y - height
  return roundRect({ x, y, width, height })
}

/** 縦横比に合う、画像中央に置いた最大の矩形(`aspect` が `null` なら画像全体)。 */
export function initialRect(natural: Size, aspect: number | null): CropRect {
  if (aspect === null || aspect <= 0) {
    return roundRect({ x: 0, y: 0, width: natural.width, height: natural.height })
  }
  let width = natural.width
  let height = width / aspect
  if (height > natural.height) {
    height = natural.height
    width = height * aspect
  }
  const x = (natural.width - width) / 2
  const y = (natural.height - height) / 2
  return roundRect({ x, y, width, height })
}
