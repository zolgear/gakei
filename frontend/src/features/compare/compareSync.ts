/**
 * 比較ビュー(`CompareCanvas`)のパン/ズーム同期・レイアウトにまつわる純粋関数。
 * DOM やライブラリの型には触れず、数値だけを扱う(vitest でテストする)。
 */
import type { Size } from '../../lib/geometry'

export interface TransformState {
  positionX: number
  positionY: number
  scale: number
}

/**
 * ほぼ同じ transform かどうか。同期の無限ループ防止と、無駄な setTransform 呼び出しの
 * 抑止に使う(react-zoom-pan-pinch の onTransform は浮動小数点を返すため、完全一致では
 * なく許容誤差で比較する)。
 */
export function transformsEqual(a: TransformState, b: TransformState, epsilon = 0.01): boolean {
  return (
    Math.abs(a.positionX - b.positionX) <= epsilon &&
    Math.abs(a.positionY - b.positionY) <= epsilon &&
    Math.abs(a.scale - b.scale) <= epsilon
  )
}

/**
 * 入力(before)と出力(after)を同じ表示枠に収めるための枠サイズ。出力の縦横比を基準にする
 * (ADR-0009「入力と出力で縦横の寸法が違う場合は…出力の縦横比の枠に収める」)。出力が無ければ
 * 入力を基準にする。どちらも無効なサイズなら 1x1 を返す。
 */
export function computeFrameSize(after: Size | null, before: Size | null): Size {
  const base = after && after.width > 0 && after.height > 0 ? after : before
  if (!base || base.width <= 0 || base.height <= 0) return { width: 1, height: 1 }
  return { width: base.width, height: base.height }
}

/**
 * 2枚を同じ大きさで見比べるための枠サイズ(重複の候補の比較。ADR-0033 8章)。面積の大きい方の
 * 寸法を枠にし、小さい方はその枠いっぱいまで拡大して重ねる(縦横比は保つ)。縦横比が違うときは
 * 両方を同じ枠に contain で収めるので、どちらも同じ中心に揃って余白が付く。面積が同じなら a を使う。
 * どちらも無効なサイズなら 1x1 を返す。
 */
export function computeSharedFrameSize(a: Size | null, b: Size | null): Size {
  const area = (s: Size | null) => (s && s.width > 0 && s.height > 0 ? s.width * s.height : 0)
  const base = area(b) > area(a) ? b : a
  if (!base || area(base) === 0) return { width: 1, height: 1 }
  return { width: base.width, height: base.height }
}

export interface ContainRect {
  /** 表示幅(枠基準の px) */
  width: number
  /** 表示高さ(枠基準の px) */
  height: number
  /** 枠内でのオフセット(px) */
  left: number
  top: number
  /** 画像の実寸に対する contain 倍率。ズーム倍率と掛け合わせて実際の表示倍率を求める。 */
  fitScale: number
}

/** 枠(frame)の中に画像(image)を contain で収めたときの矩形(枠の左上を原点とする px)。 */
export function computeContainRect(frame: Size, image: Size): ContainRect {
  if (frame.width <= 0 || frame.height <= 0 || image.width <= 0 || image.height <= 0) {
    return { width: 0, height: 0, left: 0, top: 0, fitScale: 0 }
  }
  const fitScale = Math.min(frame.width / image.width, frame.height / image.height)
  const width = image.width * fitScale
  const height = image.height * fitScale
  return { width, height, left: (frame.width - width) / 2, top: (frame.height - height) / 2, fitScale }
}

/** 分割バー位置(%)。0〜100 にクランプする。 */
export function clampSplitPercent(value: number): number {
  if (Number.isNaN(value)) return 50
  return Math.min(100, Math.max(0, value))
}

/** ポインター位置(clientX)とコンテナの矩形から分割バー位置(%)を求める。 */
export function splitPercentFromPointer(clientX: number, containerLeft: number, containerWidth: number): number {
  if (containerWidth <= 0) return 50
  return clampSplitPercent(((clientX - containerLeft) / containerWidth) * 100)
}

/**
 * 分割バー位置から、上に重ねた出力(after)側レイヤーを切り取る clip-path。
 * 左側(splitPercent% 未満)を隠すことで、左に入力・右に出力を見せる。
 */
export function computeSplitClipPath(splitPercent: number): string {
  const clamped = clampSplitPercent(splitPercent)
  return `inset(0 0 0 ${clamped}%)`
}
