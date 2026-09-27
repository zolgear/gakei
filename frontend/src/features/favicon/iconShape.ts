/**
 * favicon と App バーのロゴの描画を1つの情報源にする(ADR-0009 8章)。
 * `iconShapes()` が「今どういうコマを描くか」(IconState)から「図形のリスト」(FaviconShape[])を
 * 返す純粋関数で、SVG 文字列(faviconSvg.ts)も React JSX(GakeiMark)も、この図形リストを
 * 描くだけにして色・形の決定ロジックを重複させない。
 *
 * 参照実装(docs/assets/favicon-progress/src/gakei-favicon.js)のうち、実際に使うプリセットだけを
 * 移植している: idle(通常ロゴ)・queued(蓋なしの空箱)・fill の tile モード・sealed・spin の
 * tile スタイル・done の通知ドット・error。orbit/trail/liquid/progress(点灯)は移植しない。
 */

export const COLORS = {
  violet: '#9B8CFF',
  violetPink: '#BC82E2',
  pinkViolet: '#DE79C5',
  pink: '#FF6FA8',
  error: '#F0525A',
  lineOnDark: '#E8EAED',
  lineOnLight: '#2A2D33',
} as const

/** 通常ロゴの非アクティブタイルの不透明度。 */
const DIM_IDLE = 0.4
/** 進捗表示中(fill/spin)の消灯タイルの不透明度。 */
const DIM_OFF = 0.22
/** fill で「次に埋まるマス」が明滅するときの明るいほうの不透明度(暗いほうは DIM_OFF)。 */
const DIM_PULSE = 0.45

/** タイルの並び: 0=左上, 1=右上, 2=左下, 3=右下(GakeiMark の既存の並びと同じ)。 */
export type TileIndex = 0 | 1 | 2 | 3
const ALL_TILE_INDEXES: readonly TileIndex[] = [0, 1, 2, 3]

/** 起点(Violet, 左上) → 派生(Pink, 右下) のランプ。読み順(左上→右上→左下→右下)で点灯する。 */
const RAMP: readonly string[] = [COLORS.violet, COLORS.violetPink, COLORS.pinkViolet, COLORS.pink]
/** fill(tile モード)の塗り順: 左下 → 右下 → 左上 → 右上。 */
const FILL_ORDER: readonly TileIndex[] = [2, 3, 0, 1]
/** spin(tile スタイル)の回転順序(時計回り): 左上 → 右上 → 右下 → 左下。 */
const CLOCKWISE: readonly TileIndex[] = [0, 1, 3, 2]

/** タイルの座標(viewBox 0 0 64 64)。参照実装の TILE_XY と同じ。 */
export const TILE_XY: Readonly<Record<TileIndex, readonly [number, number]>> = {
  0: [18, 18],
  1: [33, 18],
  2: [18, 33],
  3: [33, 33],
}
export const TILE_SIZE = 13
export const TILE_RADIUS = 2.5

/** 蓋(上辺)の path。 */
export const LID_PATH = 'M4 4H60V9H4Z'
/** 箱の外周(蓋を除く)の path。 */
export const FRAME_PATH = 'M4 17.5H9V55H55V17.5H60V60H4Z'
/** 通知ドットの位置と半径。 */
export const BADGE_CX = 54
export const BADGE_CY = 10
export const BADGE_RADIUS = 8
/** 通知ドットの周囲を抜くマスクの半径(favicon の SVG 文字列だけで使う)。 */
export const BADGE_MASK_RADIUS = 11

/** タイル1枚の塗り方。`color` が null は「枠線色」(currentColor 相当)を表す。 */
export interface TileShape {
  kind: 'tile'
  index: TileIndex
  color: string | null
  opacity: number
  /**
   * true は何も描かない(空のマス)。fill/liquid・progress(点灯の smooth)は移植しないため、
   * タイルは「塗るか空か」の二択のみを表す(下からの塗り率・clip は使わない)。
   */
  empty: boolean
}

export type FaviconShape =
  /** 蓋(上辺)。このシェイプが無いときは蓋を外した状態を表す。 */
  | { kind: 'lid' }
  /** 箱の外周。常に描く。 */
  | { kind: 'frame' }
  | TileShape
  /** 右上の通知ドット。 */
  | { kind: 'badge'; color: string }

/** 描画中のコマ(参照実装の `IconState` に相当。アニメーションの1コマを表す)。 */
export type IconState =
  | { kind: 'idle' }
  | { kind: 'queued' }
  /**
   * 1マスずつ溜まる進捗。`frame` はコマ番号(呼ぶたびに +1。省略時 0)。ステップが多いと
   * マスが長く増えないので、埋まったマスの色をランプに沿って流し、次に埋まるマスを薄く
   * 明滅させて「動いている」と分かるようにする(25% 刻みのマス数は変えない)。
   */
  | { kind: 'fill'; tiles: 0 | 1 | 2 | 3 | 4; frame?: number }
  | { kind: 'sealed' }
  | { kind: 'spin'; frame: number }
  | { kind: 'done'; notify: boolean }
  | { kind: 'error' }

function tile(index: TileIndex, partial: Partial<Omit<TileShape, 'kind' | 'index'>> = {}): TileShape {
  return { kind: 'tile', index, color: null, opacity: 1, empty: false, ...partial }
}
const offTile = (index: TileIndex): TileShape => tile(index, { opacity: DIM_OFF })
const emptyTile = (index: TileIndex): TileShape => tile(index, { empty: true })

/** 通常ロゴの図形(idle と done はこれに通知ドットが乗るだけ)。 */
function idleTiles(): TileShape[] {
  return [
    tile(0, { color: COLORS.violet }),
    tile(1, { opacity: DIM_IDLE }),
    tile(2, { opacity: DIM_IDLE }),
    tile(3, { color: COLORS.pink }),
  ]
}

/** 状態(IconState)から図形のリストを返す。SVG 文字列・JSX はどちらもこれを描くだけにする。 */
export function iconShapes(state: IconState): FaviconShape[] {
  switch (state.kind) {
    case 'idle':
      return [{ kind: 'lid' }, { kind: 'frame' }, ...idleTiles()]

    case 'done':
      return [
        { kind: 'lid' },
        { kind: 'frame' },
        ...idleTiles(),
        ...(state.notify ? [{ kind: 'badge', color: COLORS.pink } as const] : []),
      ]

    case 'queued':
      return [{ kind: 'frame' }, ...ALL_TILE_INDEXES.map(emptyTile)]

    case 'fill': {
      const frame = ((Math.trunc(state.frame ?? 0) % 4) + 4) % 4
      // 埋まったマス k(塗り順の何番目か)の色は RAMP[(k - frame) mod 4]。コマが進むと色が
      // 塗り順の方向へ1マスずつ流れる。1マスだけのときは4色を順に巡る。
      const colorOfFilled = new Map<TileIndex, string>()
      FILL_ORDER.slice(0, state.tiles).forEach((i, k) => colorOfFilled.set(i, RAMP[(k - frame + 4) % 4]))
      const next: TileIndex | null = state.tiles < 4 ? FILL_ORDER[state.tiles] : null
      return [
        { kind: 'frame' },
        ...ALL_TILE_INDEXES.map((i) => {
          const color = colorOfFilled.get(i)
          if (color !== undefined) return tile(i, { color })
          if (i === next) return tile(i, { opacity: frame % 2 === 0 ? DIM_OFF : DIM_PULSE })
          return emptyTile(i)
        }),
      ]
    }

    case 'sealed':
      return [{ kind: 'lid' }, { kind: 'frame' }, ...ALL_TILE_INDEXES.map((i) => tile(i, { color: RAMP[i] }))]

    case 'spin': {
      const k = ((state.frame % 4) + 4) % 4
      const current = CLOCKWISE[k]
      return [
        { kind: 'frame' },
        ...ALL_TILE_INDEXES.map((i) => (i === current ? tile(i, { color: RAMP[i] }) : offTile(i))),
      ]
    }

    case 'error':
      return [{ kind: 'lid' }, { kind: 'frame' }, offTile(0), offTile(1), offTile(2), tile(3, { color: COLORS.error })]
  }
}
