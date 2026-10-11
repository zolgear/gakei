/**
 * マップ(ADR-0033 8章)の canvas。マップの機能はここ 1 か所に持ち、「ネットワーク(平面)」と
 * 「地図(立体)」の違いは投影(`projection.ts`)の実装の差だけにしている。
 *
 * - 点の座標(`positions`)は、投影の `stride`(平面 2、立体 3)刻み。画面の座標・奥行き・縮尺に
 *   投影し、**奥から手前へ**描く(painter's algorithm)。平面は奥行きが全点で同じなので、並べ替えも
 *   フォグ(奥を背景色に溶かす)も効かず、今までの 2D と同じ見た目になる。
 * - 拡大してサムネイルがある程度の大きさになったら、見えている分だけサムネイルを読んで描く
 *   (それまでは点)。立体では手前のものから枚数を抑える。
 * - 操作: 平面はドラッグで移動。立体はドラッグで回転(離すと慣性で少し回り続ける)、中ボタン・
 *   右ボタン・Shift+ドラッグ・2本指で平行移動(掴んだ点が指に付いてくる倍率)。どちらもホイールと
 *   ピンチで拡大縮小、クリック(タップ)で選ぶ。何も無い所を押すと選択を外す。Esc でも外す。
 * - 選んだ画像は、その近傍(`neighborIndices` の行)を強調し、ほかを薄くする(「似た画像」)。
 * - 選んだ画像は左下のカードに出し、「↗」で大きく見るパネル(`MapPreviewPanel`)に広げられる。
 *   開閉は呼び出し側がブラウザに覚え(`mapPrefs.ts` の `previewOpen`)、広げたあとは別の画像を
 *   選んでも、次に開いたときもパネルで出す。「↙」と Esc で畳むと畳んだことを覚える。選択を外す
 *   (「×」・何も無い所を押す)のは開閉を変えない。Esc は、パネルを開いていればまずパネルを畳み、
 *   次に選択を外す。カードとパネルの画像を押すとビューアを開く。
 * - マップ内の意味検索(`search`)をしている間は、一致したノードを縁取りと大きさで目立たせ
 *   (1位はさらに太い縁)、一致しないものを薄くする。一致したノード全体が収まるように表示を
 *   合わせ(立体は寄り)、「次の一致へ」(ボタンと `n` / `p`)で高い順に1つずつ選んで中心に出す。
 * - 類似度の辺・系列の辺・選んだ画像から近傍への線は、投影した画面座標で描く。座標が 3 次元でも
 *   同じ道筋で描ける(立体では、カメラの後ろや画面の外に出た端点を持つ辺は描かない)。
 * - 立体のときだけ: 開いたときの 1 回だけの回転(`introSpin`)と、座標のラインの重ね表示
 *   (`axes`)。回転は利用者がカメラを動かしたとき・画面の設定が変わったときにその場で止まり、
 *   右上のボタンを押したときだけもう一度回す。
 * - 利用者が動かすまでは、座標が変わるたびに全体が収まるように合わせる(地図の計算の途中経過や、
 *   ネットワークの力学モデルの動きを追う)。
 * - 色は CSS 変数(配色)から読む。requestAnimationFrame は、動きが落ち着いたとき・画面から
 *   外れたとき・タブが隠れたときに止める。
 */
import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { Link } from 'react-router'
import { assetUrl } from '../../api/assetUrl'
import type { EmbeddingGraphNode } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { focalStyle } from '../../lib/focalPoint'
import { buildSimilarSearchPath } from '../search/searchQuerySync'
import { axisGridLines } from './axisGrid'
import { computeBounds3, introSpinStep } from './camera3d'
import { neighborsOf, type SimilarityEdge } from './edges'
import { stepMatch, type MapSearchHighlight } from './mapSearchHighlight'
import { MapPreviewPanel } from './MapPreviewPanel'
import {
  DOT_RADIUS,
  THUMB_MIN_PX,
  arrowSizePx,
  drawTilePx,
  lineageSegment,
  nodeExtentPx,
  type LineageSegment,
} from './nodeSize'
import {
  createProjectedPoints,
  createProjection,
  ensureProjectedCapacity,
  type MapProjection,
  type ProjectionKind,
} from './projection'
import { thumbCache, type ThumbRequest } from './thumbCache'
import styles from './MapCanvas.module.css'

/** 選んだ画像を大きく見るパネルの開閉。 */
export interface MapPreviewState {
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** 開いたときに一度だけ回すかどうか(立体のときだけ。呼び出し側が決める)。 */
export interface MapIntroSpinState {
  /**
   * このビューを開いて配置が初めて出たときに、一度だけ回すか。覚えた値が「回す」で、かつ
   * 画面の設定をまだ変えていないとき(設定を変えたあとは回さない)。
   */
  enabled: boolean
  /**
   * 画面の設定を変えた回数。増えたら回転をその場で止める(マウス操作と同じ扱い)。
   * `enabled` だけでは、ボタンで回し直したあとの設定変更を拾えないので別に数える。
   */
  settingsVersion: number
}

/** 座標のラインの重ね表示(立体のときだけ)。 */
export interface MapAxesState {
  shown: boolean
  onShownChange: (shown: boolean) => void
}

export interface MapCanvasHandle {
  /** 座標を書き換えたあとに呼ぶ(次のフレームで描き直す)。 */
  redraw: () => void
}

interface MapCanvasProps {
  nodes: EmbeddingGraphNode[]
  /** 投影の刻み幅ごとの座標(平面は x0, y0, x1, ...、立体は x0, y0, z0, ...)。 */
  positions: Float32Array | null
  /** 別のデータに替わったら変える。表示を全体に合わせ直す。 */
  dataKey: string
  neighborIndices: number[][]
  similarityEdges?: SimilarityEdge[] | null
  lineageEdges?: [number, number][] | null
  selected: number | null
  onSelect: (index: number | null) => void
  controllerRef?: RefObject<MapCanvasHandle | null>
  ariaLabel: string
  /** 操作の説明(canvas の title)。 */
  hint?: string
  /** canvas の上に重ねる(進み具合など)。 */
  overlay?: ReactNode
  /**
   * サムネイルの大きさの倍率。ネットワークでは辺が見えるよう小さめにする。
   */
  tileScale?: number
  preview: MapPreviewState
  /** マップ内の意味検索の一致(null なら検索していない)。 */
  search?: MapSearchHighlight | null
  /** 見せ方(平面か立体か)。 */
  projection: ProjectionKind
  /** 開いたときに一度だけ回すか(立体のときだけ効く)。 */
  introSpin?: MapIntroSpinState
  /** 座標のラインの重ね表示(立体のときだけ効く)。 */
  axes?: MapAxesState
}

/** クリックとみなす移動量の上限。 */
const CLICK_SLOP = 6
/** 当たり判定の最小の半径(指でも押せるように)。 */
const MIN_HIT_RADIUS = 10
/** 1フレームで読みに行くサムネイルの数の上限。 */
const MAX_WANTED = 240
/** 検索しているとき、一致しないノードの濃さ。 */
const SEARCH_DIM = 0.22
/** 一致したノードを大きくする割合(一致の強さ 1 のとき)。 */
const SEARCH_GROW = 0.25
/** 奥がどこまで背景に溶けるか(1 で完全に背景色)。奥行きの幅が 0 の平面では効かない。 */
const MAX_FOG = 0.82
/** フォグの段数(点をまとめて描くために量子化する)。 */
const FOG_STEPS = 6
/** 座標のラインの濃さ(軸と格子)。 */
const AXIS_ALPHA = 0.85
const GRID_ALPHA = 0.4

interface Colors {
  dot: string
  dotDim: string
  tileBg: string
  accent: string
  text: string
  lineage: string
  bg: string
  border: string
}

function readColors(el: Element): Colors {
  const css = getComputedStyle(el)
  const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback
  return {
    dot: v('--color-text-muted', '#9aa0aa'),
    dotDim: v('--color-text-faint', '#6b7178'),
    tileBg: v('--color-header-band', '#2b2f36'),
    accent: v('--color-accent', '#e3b341'),
    text: v('--color-text', '#e6e7ea'),
    lineage: v('--color-edit', '#56c2d6'),
    bg: v('--color-bg', '#17181b'),
    border: v('--color-border-strong', '#3a3f47'),
  }
}

interface PointerInfo {
  x: number
  y: number
}

/** 点をまとめて描くための束(同じ濃さ・同じ色の点は 1 回の fill で済む)。 */
interface DotBucket {
  alpha: number
  color: string
  list: number[]
}

export function MapCanvas({
  nodes,
  positions,
  dataKey,
  neighborIndices,
  similarityEdges,
  lineageEdges,
  selected,
  onSelect,
  controllerRef,
  ariaLabel,
  hint,
  overlay,
  tileScale = 1,
  preview,
  search = null,
  projection,
  introSpin,
  axes,
}: MapCanvasProps) {
  const { t } = useI18n()
  const m = t.map
  const containerRef = useRef<HTMLDivElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const sizeRef = useRef({ width: 0, height: 0 })
  // 見せ方(投影)。種類が替わったときだけ作り直す。
  const projRef = useRef<MapProjection | null>(null)
  if (!projRef.current || projRef.current.kind !== projection) {
    projRef.current = createProjection(projection)
  }
  const proj = projRef.current
  /** 利用者が動かすまでは、座標が変わるたびに全体に合わせ直す。 */
  const userMovedRef = useRef(false)
  /** 検索の一致に表示を合わせたいか(座標が揃った最初のフレームで合わせる)。 */
  const pendingSearchFitRef = useRef(false)
  /** 画面に見えているか(見えていない間はフレームを回さない)。 */
  const onScreenRef = useRef(true)
  const frameRef = useRef<number | null>(null)
  const lastTsRef = useRef<number | null>(null)
  /** サムネイル1枚の、配置の座標での一辺(直前のフレームの値。当たり判定と寄る距離に使う)。 */
  const tileWorldRef = useRef(1)
  /** このビューで、一度だけの回転をもう始めたか(配置が変わっても回し直さない)。 */
  const introSpunRef = useRef(false)
  /**
   * 1フレームの計算に使う作業用の配列。動いている間は毎フレーム描くので、確保し直さず使い回す
   * (数千点の Float32Array を毎フレーム捨てると GC が増える)。
   */
  const scratchRef = useRef({
    points: createProjectedPoints(),
    axisPoints: createProjectedPoints(),
    capacity: 0,
    tilePx: new Float32Array(0),
    useThumb: new Uint8Array(0),
    drawn: new Uint8Array(0),
  })
  /** 配色は毎フレーム読むと重い(`getComputedStyle`)ので、描き直しの要求ごとに 1 回だけ読む。 */
  const colorsRef = useRef<Colors | null>(null)
  const [hover, setHover] = useState<{ index: number; x: number; y: number } | null>(null)
  // 選んだ画像を大きく見るパネルを開いているか(呼び出し側が覚える)。
  const previewOpen = preview.open
  const expandButtonRef = useRef<HTMLButtonElement | null>(null)
  // 「↗」で広げたときだけパネルへ焦点を移す(覚えた開閉で開いたときは canvas の焦点を奪わない)。
  const [focusPanel, setFocusPanel] = useState(false)

  // 描画は requestAnimationFrame から呼ぶので、最新の props を ref で読む。
  const propsRef = useRef({
    nodes,
    positions,
    neighborIndices,
    similarityEdges,
    lineageEdges,
    selected,
    tileScale,
    search,
    showAxes: false,
    hover: -1,
  })
  propsRef.current = {
    nodes,
    positions,
    neighborIndices,
    similarityEdges,
    lineageEdges,
    selected,
    tileScale,
    search,
    showAxes: proj.hasDepth && (axes?.shown ?? false),
    hover: hover?.index ?? -1,
  }

  // ---- 描画 ----
  const drawRef = useRef<() => void>(() => {})
  drawRef.current = () => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const { width, height } = sizeRef.current
    const dpr = window.devicePixelRatio || 1
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, width, height)
    const p = propsRef.current
    const pos = p.positions
    const stride = proj.stride
    const n = Math.min(p.nodes.length, pos ? Math.floor(pos.length / stride) : 0)
    if (!pos || n === 0 || width === 0 || height === 0) return
    const size = { width, height }

    const tileWorld = proj.tileWorld(pos, n, p.neighborIndices) * p.tileScale
    tileWorldRef.current = tileWorld
    /** 倍率ごとの、ノードの中心から描いた端(選択の枠を含む)までの距離。 */
    const extentAt = (scale: number) => nodeExtentPx(tileWorld, scale, n)
    // 端の画像(半分の大きさと選択の枠)も画面に収める。立体では向きは変えない。
    if (!userMovedRef.current) proj.fitAll(pos, n, size, extentAt)
    // 検索の一致が決まったら、一致したノード全体が収まるように合わせる(立体は滑らかに寄る)。
    if (pendingSearchFitRef.current) {
      const ranked = p.search?.ranked ?? []
      if (ranked.length === 0) {
        pendingSearchFitRef.current = false
      } else if (proj.revealIndices(pos, ranked, size, extentAt)) {
        userMovedRef.current = true
        proj.stopIntroSpin()
        pendingSearchFitRef.current = false
        scheduleRef.current()
      }
    }

    if (!colorsRef.current) colorsRef.current = readColors(container)
    const colors = colorsRef.current

    // 作業用の配列(足りなければ広げ、足りていれば使い回す)。
    const scratch = scratchRef.current
    if (scratch.capacity < n) {
      scratch.capacity = n
      scratch.tilePx = new Float32Array(n)
      scratch.useThumb = new Uint8Array(n)
      scratch.drawn = new Uint8Array(n)
    }
    const { tilePx, useThumb, drawn } = scratch
    const pts = ensureProjectedCapacity(scratch.points, n)
    proj.project(pos, n, size, pts)
    const { sx, sy, depth } = pts

    const sel = p.selected !== null && p.selected < n ? p.selected : null
    const emphasized = sel !== null ? new Set(neighborsOf(p.neighborIndices, sel)) : null
    const searchHit = p.search ?? null
    const searching = searchHit !== null

    // 描くノードを集め、同時に奥行きの範囲を測る。
    const order: number[] = []
    drawn.fill(0, 0, n)
    let minDepth = Infinity
    let maxDepth = -Infinity
    for (let i = 0; i < n; i++) {
      if (!pts.front[i]) continue
      const px = drawTilePx(tileWorld * pts.scale[i], n)
      tilePx[i] = px
      const margin = (px >= THUMB_MIN_PX ? px / 2 : DOT_RADIUS) + 4
      if (!(sx[i] >= -margin && sx[i] <= width + margin && sy[i] >= -margin && sy[i] <= height + margin)) {
        continue
      }
      order.push(i)
      drawn[i] = 1
      if (depth[i] < minDepth) minDepth = depth[i]
      if (depth[i] > maxDepth) maxDepth = depth[i]
    }
    if (order.length === 0) return
    // 奥から手前へ。奥行きが同じ(= 平面)ときは、強調したノードを後に回して上に重ねる。
    const emphasisRank = (i: number) => (emphasized?.has(i) ? 1 : 0)
    order.sort((a, b) => depth[b] - depth[a] || emphasisRank(a) - emphasisRank(b) || a - b)

    /** 奥行きから、背景に溶ける割合(0〜`MAX_FOG`)。段に丸めて、点をまとめて描けるようにする。 */
    const span = maxDepth - minDepth
    const fogStepOf = (value: number) =>
      span > 1e-9 ? Math.round(((value - minDepth) / span) * (FOG_STEPS - 1)) : 0
    const fogAlpha = (step: number) => 1 - (step / (FOG_STEPS - 1)) * MAX_FOG

    // サムネイルで描くもの(手前から、上限の枚数まで)。
    useThumb.fill(0, 0, n)
    let picked = 0
    for (let k = order.length - 1; k >= 0 && picked < proj.maxThumbs; k--) {
      const i = order[k]
      if (tilePx[i] >= THUMB_MIN_PX) {
        useThumb[i] = 1
        picked++
      }
    }

    /** 辺の端点として使えるか(立体では、カメラの後ろ・画面の外の点を避ける)。 */
    const edgeEnd = (i: number) => i < n && (proj.cullsOffscreenEdges ? drawn[i] === 1 : pts.front[i] === 1)

    // 座標のライン(立体のときだけ)。点やサムネイルより奥に、控えめな濃さで描く。
    if (p.showAxes) drawAxisGrid(ctx)

    // 類似度の辺(ネットワーク)。
    if (p.similarityEdges && p.similarityEdges.length > 0) {
      ctx.save()
      ctx.strokeStyle = colors.dot
      ctx.globalAlpha = sel !== null ? 0.12 : 0.35
      ctx.lineWidth = 1
      ctx.beginPath()
      for (const e of p.similarityEdges) {
        if (!edgeEnd(e.source) || !edgeEnd(e.target)) continue
        ctx.moveTo(sx[e.source], sy[e.source])
        ctx.lineTo(sx[e.target], sy[e.target])
      }
      ctx.stroke()
      ctx.restore()
    }

    // 選んだ画像から近傍への線。
    if (sel !== null && emphasized && edgeEnd(sel)) {
      ctx.save()
      ctx.strokeStyle = colors.accent
      ctx.globalAlpha = 0.7
      ctx.lineWidth = 1.25
      ctx.beginPath()
      for (const j of emphasized) {
        if (!edgeEnd(j)) continue
        ctx.moveTo(sx[sel], sy[sel])
        ctx.lineTo(sx[j], sy[j])
      }
      ctx.stroke()
      ctx.restore()
    }

    const wanted: { id: string; focal: EmbeddingGraphNode['focal_point']; depth: number }[] = []
    const dotRadius = (i: number) => proj.dotRadiusPx(tilePx[i])
    /**
     * 点は束にまとめ、サムネイルを描き終わってからまとめて描く(同じ濃さの点が重なっても
     * 濃くならないよう、束ごとに 1 回の fill にする)。束の鍵は「フォグの段 × 濃さの種類」。
     */
    const dotBuckets = new Map<number, DotBucket>()
    const pushDot = (i: number, key: number, alpha: number, color: string) => {
      let bucket = dotBuckets.get(key)
      if (!bucket) {
        bucket = { alpha, color, list: [] }
        dotBuckets.set(key, bucket)
      }
      bucket.list.push(i)
    }

    // ノード(奥から手前へ)。選んだ画像と検索の一致は、あとで目立たせて描く。
    ctx.save()
    for (const i of order) {
      if (i === sel || searchHit?.matched.has(i)) continue
      const isEmphasized = emphasized?.has(i) ?? false
      const selMiss = sel !== null && !isEmphasized
      const dim = (selMiss ? 0.3 : 1) * (searching ? SEARCH_DIM : 1)
      const step = fogStepOf(depth[i])
      if (!useThumb[i]) {
        // 近傍の点はアクセント色、ほかは薄い色(検索しているときはさらに薄く)。
        if (isEmphasized) pushDot(i, -1, 1, colors.accent)
        else {
          const key = step * 4 + (selMiss ? 1 : 0) + (searching ? 2 : 0)
          pushDot(i, key, fogAlpha(step) * dim, searching ? colors.dotDim : colors.dot)
        }
        continue
      }
      const id = p.nodes[i].id
      const tile = thumbCache.get(id)
      const half = tilePx[i] / 2
      ctx.globalAlpha = fogAlpha(step) * dim
      if (tile) {
        ctx.drawImage(tile, sx[i] - half, sy[i] - half, tilePx[i], tilePx[i])
      } else {
        ctx.fillStyle = colors.tileBg
        ctx.fillRect(sx[i] - half, sy[i] - half, tilePx[i], tilePx[i])
        wanted.push({ id, focal: p.nodes[i].focal_point, depth: depth[i] })
      }
      if (isEmphasized) {
        ctx.strokeStyle = colors.accent
        ctx.lineWidth = 1.5
        ctx.strokeRect(sx[i] - half, sy[i] - half, tilePx[i], tilePx[i])
      }
    }
    ctx.restore()

    // 点(束ごとに 1 回の fill)。近傍のアクセント色は最後に描く。
    ctx.save()
    const buckets = [...dotBuckets.values()].sort(
      (a, b) => Number(a.color === colors.accent) - Number(b.color === colors.accent),
    )
    for (const bucket of buckets) {
      ctx.globalAlpha = bucket.alpha
      ctx.fillStyle = bucket.color
      ctx.beginPath()
      for (const i of bucket.list) {
        const r = dotRadius(i)
        ctx.moveTo(sx[i] + r, sy[i])
        ctx.arc(sx[i], sy[i], r, 0, Math.PI * 2)
      }
      ctx.fill()
    }
    ctx.restore()

    // 系列の辺(破線)。サムネイルに隠れないよう、ノードの上に描く。
    if (p.lineageEdges && p.lineageEdges.length > 0) {
      ctx.save()
      ctx.strokeStyle = colors.lineage
      ctx.fillStyle = colors.lineage
      ctx.globalAlpha = 0.95
      ctx.lineWidth = 2
      /**
       * 組ごとに、サムネイルで描いているか(両端)で引き方を変える。
       *
       * - サムネイル: 親の縁から子の縁まで引き、子の側に塗った矢じりを付ける(サムネイルに
       *   隠れないように縁で止める)。両端の大きさが違う(立体)ときは、中ほどの大きさで縁を決める。
       * - 点: 中心どうしを破線で結び、子の側に小さな丸を付けて向きを示す。
       * - サムネイルが重なって線を引けない組: 中心どうしを実線で結ぶ(向きは示せない)。
       */
      const segments: { seg: LineageSegment; wing: number }[] = []
      const dots: [number, number][] = []
      const overlapped: [number, number][] = []
      // サムネイルで描く大きさかは、画面の外に出た端点でも決まるように `tilePx` で見る
      // (平面では全点で同じ大きさなので、今までどおり「全部サムネイル」か「全部点」になる)。
      const asTile = (i: number) => tilePx[i] >= THUMB_MIN_PX
      for (const [a, b] of p.lineageEdges) {
        if (!edgeEnd(a) || !edgeEnd(b)) continue
        if (!asTile(a) || !asTile(b)) {
          dots.push([a, b])
          continue
        }
        const half = (tilePx[a] + tilePx[b]) / 4
        const arrow = arrowSizePx(half * 2)
        const seg = lineageSegment(sx[a], sy[a], sx[b], sy[b], half, arrow)
        if (seg) segments.push({ seg, wing: arrow * 0.5 })
        else overlapped.push([a, b])
      }
      ctx.setLineDash([5, 4])
      ctx.beginPath()
      for (const { seg } of segments) {
        ctx.moveTo(seg.x0, seg.y0)
        ctx.lineTo(seg.x1, seg.y1)
      }
      for (const [a, b] of dots) {
        ctx.moveTo(sx[a], sy[a])
        ctx.lineTo(sx[b], sy[b])
      }
      ctx.stroke()
      ctx.setLineDash([])
      // 矢じり。
      ctx.beginPath()
      for (const { seg: s, wing } of segments) {
        ctx.moveTo(s.tipX, s.tipY)
        ctx.lineTo(s.x1 - s.uy * wing, s.y1 + s.ux * wing)
        ctx.lineTo(s.x1 + s.uy * wing, s.y1 - s.ux * wing)
        ctx.closePath()
      }
      ctx.fill()
      // 点の組は、子の側に小さな丸で向きを示す。
      ctx.beginPath()
      for (const [a, b] of dots) {
        const dx = sx[a] - sx[b]
        const dy = sy[a] - sy[b]
        const len = Math.hypot(dx, dy)
        if (len < 1) continue
        const off = Math.min(len / 2, DOT_RADIUS + 4)
        const cx = sx[b] + (dx / len) * off
        const cy = sy[b] + (dy / len) * off
        ctx.moveTo(cx + 2.5, cy)
        ctx.arc(cx, cy, 2.5, 0, Math.PI * 2)
      }
      ctx.fill()
      if (overlapped.length > 0) {
        ctx.beginPath()
        for (const [a, b] of overlapped) {
          ctx.moveTo(sx[a], sy[a])
          ctx.lineTo(sx[b], sy[b])
        }
        ctx.stroke()
      }
      ctx.restore()
    }

    // 検索の一致。縁取りと大きさで目立たせ、1位はさらに太い縁にする。
    if (searchHit) {
      ctx.save()
      for (const i of order) {
        if (i === sel || !searchHit.matched.has(i)) continue
        const strength = searchHit.strength[i] ?? 0
        const isTop = i === searchHit.top
        if (useThumb[i]) {
          const tileSize = tilePx[i] * (1 + SEARCH_GROW * strength)
          const h = tileSize / 2
          const id = p.nodes[i].id
          const tile = thumbCache.get(id)
          ctx.globalAlpha = 1
          if (tile) {
            ctx.drawImage(tile, sx[i] - h, sy[i] - h, tileSize, tileSize)
          } else {
            ctx.fillStyle = colors.tileBg
            ctx.fillRect(sx[i] - h, sy[i] - h, tileSize, tileSize)
            wanted.push({ id, focal: p.nodes[i].focal_point, depth: depth[i] })
          }
          ctx.globalAlpha = 0.6 + 0.4 * strength
          ctx.strokeStyle = colors.accent
          ctx.lineWidth = isTop ? 3 : 1.5
          ctx.strokeRect(sx[i] - h, sy[i] - h, tileSize, tileSize)
        } else {
          const r = Math.max(dotRadius(i), 2) * (1 + SEARCH_GROW * strength) + 1
          ctx.globalAlpha = 0.6 + 0.4 * strength
          ctx.fillStyle = colors.accent
          ctx.beginPath()
          ctx.arc(sx[i], sy[i], r, 0, Math.PI * 2)
          ctx.fill()
          if (isTop) {
            ctx.globalAlpha = 1
            ctx.strokeStyle = colors.text
            ctx.lineWidth = 1.5
            ctx.beginPath()
            ctx.arc(sx[i], sy[i], r + 3, 0, Math.PI * 2)
            ctx.stroke()
          }
        }
      }
      ctx.restore()
    }

    // なぞっている画像と選んだ画像を最後に、枠付きで描く(フォグは掛けない。見失わないように)。
    const ring = (i: number, color: string, lineWidth: number) => {
      ctx.save()
      ctx.globalAlpha = 1
      if (tilePx[i] >= THUMB_MIN_PX && (useThumb[i] === 1 || i === sel)) {
        const half = tilePx[i] / 2
        const id = p.nodes[i].id
        const tile = thumbCache.get(id)
        if (tile) ctx.drawImage(tile, sx[i] - half, sy[i] - half, tilePx[i], tilePx[i])
        else {
          ctx.fillStyle = colors.tileBg
          ctx.fillRect(sx[i] - half, sy[i] - half, tilePx[i], tilePx[i])
          wanted.push({ id, focal: p.nodes[i].focal_point, depth: depth[i] })
        }
        const r = half + 2
        ctx.strokeStyle = colors.bg
        ctx.lineWidth = lineWidth + 2
        ctx.strokeRect(sx[i] - r, sy[i] - r, r * 2, r * 2)
        ctx.strokeStyle = color
        ctx.lineWidth = lineWidth
        ctx.strokeRect(sx[i] - r, sy[i] - r, r * 2, r * 2)
      } else {
        const r = Math.max(dotRadius(i), DOT_RADIUS) + 4
        ctx.fillStyle = color
        ctx.beginPath()
        ctx.arc(sx[i], sy[i], DOT_RADIUS + 1.5, 0, Math.PI * 2)
        ctx.fill()
        ctx.strokeStyle = color
        ctx.lineWidth = lineWidth
        ctx.beginPath()
        ctx.arc(sx[i], sy[i], r, 0, Math.PI * 2)
        ctx.stroke()
      }
      ctx.restore()
    }
    if (p.hover >= 0 && p.hover < n && p.hover !== sel && drawn[p.hover]) ring(p.hover, colors.text, 1.5)
    if (sel !== null && drawn[sel]) ring(sel, colors.accent, 2)

    // 手前のものから読む(平面では奥行きが同じなので、並べ替えても元の順のまま)。
    if (wanted.length > 0) {
      wanted.sort((a, b) => a.depth - b.depth)
      const requests: ThumbRequest[] = wanted.slice(0, MAX_WANTED).map(({ id, focal }) => ({ id, focal }))
      thumbCache.want(requests)
    }

    /** 原点を通る X 軸・Y 軸と、その平面の格子。 */
    function drawAxisGrid(target: CanvasRenderingContext2D) {
      const grid = axisGridLines(computeBounds3(pos!, n))
      if (!grid) return
      const drawSegments = (segments: Float32Array, alpha: number, lineWidth: number, color: string) => {
        const count = Math.floor(segments.length / 3)
        if (count === 0) return
        const out = ensureProjectedCapacity(scratch.axisPoints, count)
        proj.project(segments, count, size, out, 3)
        target.save()
        target.strokeStyle = color
        target.lineWidth = lineWidth
        for (let k = 0; k + 1 < count; k += 2) {
          if (!out.front[k] || !out.front[k + 1]) continue
          // 奥の線は背景に溶かす(点やサムネイルと同じフォグ)。
          const fog = fogAlpha(fogStepOf((out.depth[k] + out.depth[k + 1]) / 2))
          target.globalAlpha = alpha * fog
          target.beginPath()
          target.moveTo(out.sx[k], out.sy[k])
          target.lineTo(out.sx[k + 1], out.sy[k + 1])
          target.stroke()
        }
        target.restore()
      }
      drawSegments(grid.grid, GRID_ALPHA, 1, colors.border)
      // 軸は格子より明るい色で、原点の向きが分かるようにする。
      drawSegments(grid.axes, AXIS_ALPHA, 1.5, colors.dot)
    }
  }

  // ---- フレーム ----
  const scheduleRef = useRef<() => void>(() => {})
  const frameHandlerRef = useRef<(ts: number) => void>(() => {})
  frameHandlerRef.current = (ts: number) => {
    frameRef.current = null
    const last = lastTsRef.current
    lastTsRef.current = ts
    // タブが戻ってきた直後などに大きく進まないよう、1フレーム分の上限を置く。
    const dt = last === null ? 16 : Math.min(64, Math.max(0, ts - last))
    // 平面は動かない(常に false)ので、1 回の要求で 1 回だけ描く。
    const animating = proj.step(dt)
    drawRef.current()
    if (animating && onScreenRef.current) scheduleRef.current()
    else lastTsRef.current = null
  }
  scheduleRef.current = () => {
    if (frameRef.current !== null) return
    frameRef.current = requestAnimationFrame((ts) => frameHandlerRef.current(ts))
  }
  const requestDraw = () => scheduleRef.current()

  // 呼び出し側へ描き直しの口を渡す。
  useEffect(() => {
    if (!controllerRef) return
    controllerRef.current = { redraw: requestDraw }
    return () => {
      controllerRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controllerRef])

  // データが替わったら全体に合わせ直す。
  const prevSelectedRef = useRef<number | null | undefined>(undefined)
  useEffect(() => {
    userMovedRef.current = false
    proj.cancelMotion()
    // 配置が替わっても回し直さない(絞り込みを変えたときなので、止めたままにする)。
    proj.stopIntroSpin()
    prevSelectedRef.current = undefined
    setHover(null)
    requestDraw()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataKey])

  // 選んだ画像へ寄る(立体だけ。開いた直後に URL で選ばれているときは寄せない)。
  useEffect(() => {
    const prev = prevSelectedRef.current
    prevSelectedRef.current = selected
    if (!proj.followsSelection) return
    if (prev === undefined || selected === null || prev === selected) return
    const pos = propsRef.current.positions
    if (!pos) return
    if (!proj.revealIndex(pos, selected, sizeRef.current, tileWorldRef.current)) return
    userMovedRef.current = true
    proj.stopIntroSpin()
    requestDraw()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected])

  // 検索の一致が変わったら、一致したノードに表示を合わせる(検索語を消したら全体に戻す)。
  const searchKey = search ? `${search.query}\u0000${search.ranked.join(',')}` : ''
  useEffect(() => {
    proj.stopIntroSpin()
    if (searchKey === '') {
      userMovedRef.current = false
      proj.cancelMotion()
      pendingSearchFitRef.current = false
    } else {
      pendingSearchFitRef.current = true
    }
    requestDraw()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchKey])

  // props が変わったら、配色を読み直して描き直す(配色の切り替えにもすぐ追いつく)。
  useEffect(() => {
    colorsRef.current = null
    requestDraw()
  })

  // サムネイルが届いたら描き直す。
  useEffect(() => thumbCache.subscribe(requestDraw), [])

  /**
   * このビューを開いて配置が**初めて出たとき**に、一度だけ回し始める(立体だけ)。初めて開いた
   * ときは配置の計算が走るので、座標は後から届く。届いた時点で回し始め、以後は途中経過で何度
   * 届いても回し直さない(`introSpinStep` の `decided` で印を立てる)。
   */
  useEffect(() => {
    if (!proj.supportsIntroSpin || !introSpin || introSpunRef.current) return
    const hasPositions = positions !== null && positions.length >= 3 && nodes.length > 0
    const { start, decided } = introSpinStep({
      enabled: introSpin.enabled,
      alreadySpun: false,
      hasPositions,
      userMoved: userMovedRef.current,
      hasSelection: selected !== null,
    })
    if (decided) introSpunRef.current = true
    if (start) startIntroSpin()
  })

  /**
   * 画面の設定(絞り込み・検索など)を変えたら、その場で止める(以後は回し直さない)。
   * 版が**変わったとき**だけ止める(マウント直後の 1 回目で止めてしまうと、配置が届いてからの
   * 最初の回転が起きない)。
   */
  const settingsVersion = introSpin?.settingsVersion ?? 0
  const settingsVersionRef = useRef(settingsVersion)
  useEffect(() => {
    if (settingsVersionRef.current === settingsVersion) return
    settingsVersionRef.current = settingsVersion
    proj.stopIntroSpin()
    introSpunRef.current = true
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settingsVersion])

  // 大きさの変化(と、デバイスピクセル比)。
  useEffect(() => {
    const container = containerRef.current
    const canvas = canvasRef.current
    if (!container || !canvas) return
    const resize = () => {
      const rect = container.getBoundingClientRect()
      const dpr = window.devicePixelRatio || 1
      sizeRef.current = { width: rect.width, height: rect.height }
      canvas.width = Math.max(1, Math.round(rect.width * dpr))
      canvas.height = Math.max(1, Math.round(rect.height * dpr))
      canvas.style.width = `${rect.width}px`
      canvas.style.height = `${rect.height}px`
      requestDraw()
    }
    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(container)
    return () => {
      observer.disconnect()
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current)
      frameRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 見えていないとき(タブが隠れた・画面の外へ出た)はフレームを止める。
  useEffect(() => {
    const container = containerRef.current
    const update = (onScreen: boolean) => {
      const visible = onScreen && document.visibilityState !== 'hidden'
      if (visible === onScreenRef.current) return
      onScreenRef.current = visible
      if (visible) {
        lastTsRef.current = null
        requestDraw()
      }
    }
    let intersecting = true
    const onVisibility = () => update(intersecting)
    document.addEventListener('visibilitychange', onVisibility)
    let observer: IntersectionObserver | null = null
    if (container && typeof IntersectionObserver === 'function') {
      observer = new IntersectionObserver((entries) => {
        intersecting = entries.some((entry) => entry.isIntersecting)
        update(intersecting)
      })
      observer.observe(container)
    }
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      observer?.disconnect()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ---- 操作 ----
  const pointersRef = useRef(new Map<number, PointerInfo>())
  const gestureRef = useRef({ moved: 0, multi: false, pan: false, select: true, lastAt: 0 })

  function localPoint(e: { clientX: number; clientY: number }): [number, number] {
    const rect = canvasRef.current!.getBoundingClientRect()
    return [e.clientX - rect.left, e.clientY - rect.top]
  }

  /** 画面の点にあるノード(重なったら手前を優先)。 */
  function pick(x: number, y: number): number {
    const p = propsRef.current
    const pos = p.positions
    if (!pos) return -1
    const n = Math.min(p.nodes.length, Math.floor(pos.length / proj.stride))
    const tileWorld = tileWorldRef.current
    return proj.hitTest(pos, n, sizeRef.current, x, y, (scale) => {
      const size = drawTilePx(tileWorld * scale, n)
      return Math.max(MIN_HIT_RADIUS, size >= THUMB_MIN_PX ? size / 2 : 0)
    })
  }

  function onPointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    const [x, y] = localPoint(e)
    // 中ボタン・右ボタンのドラッグは平行移動。ブラウザの自動スクロールを出さない。
    const panButton = e.button === 1 || e.button === 2
    if (panButton) e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    pointersRef.current.set(e.pointerId, { x, y })
    if (pointersRef.current.size === 1) {
      const pan = panButton || e.shiftKey
      gestureRef.current = { moved: 0, multi: false, pan, select: e.button === 0, lastAt: e.timeStamp }
      proj.beginDrag(pan)
      // 触った時点で、一度だけの回転は止める(クリックでの選択も含む)。
      proj.stopIntroSpin()
    } else {
      gestureRef.current.multi = true
    }
  }

  function onPointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    const [x, y] = localPoint(e)
    const pointers = pointersRef.current
    const prev = pointers.get(e.pointerId)
    if (!prev) {
      // ボタンを押していないマウスの移動: なぞった画像を示す。
      if (e.pointerType === 'mouse') {
        const hit = pick(x, y)
        if (hit < 0) {
          if (hover !== null) setHover(null)
        } else if (hover?.index !== hit) {
          setHover({ index: hit, x, y })
        }
      }
      return
    }
    if (pointers.size === 1) {
      const dx = x - prev.x
      const dy = y - prev.y
      const g = gestureRef.current
      g.moved += Math.abs(dx) + Math.abs(dy)
      if (g.moved > CLICK_SLOP) {
        userMovedRef.current = true
        const dtMs = Math.max(1, e.timeStamp - g.lastAt)
        proj.drag(dx, dy, sizeRef.current, { pan: g.pan || e.shiftKey, dtMs })
        g.lastAt = e.timeStamp
        if (hover !== null) setHover(null)
        requestDraw()
      }
      pointers.set(e.pointerId, { x, y })
      return
    }
    if (pointers.size >= 2) {
      // 2本指: 中点の動きで平行移動、指の間隔で拡大縮小(同時に効く)。
      const [a, b] = [...pointers.entries()].slice(0, 2)
      const before = { a: a[1], b: b[1] }
      pointers.set(e.pointerId, { x, y })
      const after = { a: pointers.get(a[0])!, b: pointers.get(b[0])! }
      const d0 = Math.hypot(before.a.x - before.b.x, before.a.y - before.b.y)
      const d1 = Math.hypot(after.a.x - after.b.x, after.a.y - after.b.y)
      const mx0 = (before.a.x + before.b.x) / 2
      const my0 = (before.a.y + before.b.y) / 2
      const mx1 = (after.a.x + after.b.x) / 2
      const my1 = (after.a.y + after.b.y) / 2
      proj.cancelMotion()
      proj.pan(mx1 - mx0, my1 - my0, sizeRef.current)
      if (d0 > 0 && d1 > 0) proj.zoomBy(d1 / d0, mx1, my1, sizeRef.current)
      userMovedRef.current = true
      gestureRef.current.moved += CLICK_SLOP + 1
      requestDraw()
    }
  }

  function onPointerUp(e: React.PointerEvent<HTMLCanvasElement>) {
    const pointers = pointersRef.current
    if (!pointers.has(e.pointerId)) return
    pointers.delete(e.pointerId)
    const g = gestureRef.current
    if (pointers.size === 0) {
      proj.endDrag({ pan: g.pan })
      // 中ボタン・右ボタンでは選ばない。
      if (!g.multi && g.select && g.moved <= CLICK_SLOP && e.type === 'pointerup') {
        const [x, y] = localPoint(e)
        const hit = pick(x, y)
        onSelect(hit >= 0 ? hit : null)
        setHover(null)
      }
      requestDraw()
    }
  }

  // ホイールは passive: false で受けないとページがスクロールしてしまう。
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = canvas.getBoundingClientRect()
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? rect.height : 1
      const factor = Math.exp(-e.deltaY * unit * 0.0015)
      proj.zoomBy(factor, e.clientX - rect.left, e.clientY - rect.top, sizeRef.current)
      userMovedRef.current = true
      proj.stopIntroSpin()
      proj.cancelMotion()
      setHover(null)
      requestDraw()
    }
    // 中ボタンの自動スクロール(丸いカーソル)は mousedown で始まるので、ここでも止める
    // (pointerdown の preventDefault だけに頼らない)。左ボタンは焦点が移るよう触らない。
    const onMouseDown = (e: MouseEvent) => {
      if (e.button === 1) e.preventDefault()
    }
    canvas.addEventListener('wheel', onWheel, { passive: false })
    canvas.addEventListener('mousedown', onMouseDown)
    return () => {
      canvas.removeEventListener('wheel', onWheel)
      canvas.removeEventListener('mousedown', onMouseDown)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function zoomButton(factor: number) {
    const { width, height } = sizeRef.current
    proj.zoomBy(factor, width / 2, height / 2, sizeRef.current)
    userMovedRef.current = true
    proj.stopIntroSpin()
    proj.cancelMotion()
    requestDraw()
  }

  function fitAll() {
    proj.stopIntroSpin()
    proj.cancelMotion()
    userMovedRef.current = false
    pendingSearchFitRef.current = false
    requestDraw()
  }

  /** 検索の一致を、スコアの高い順に1つずつ選んで中心に出す(立体はその点へ寄る)。 */
  function focusMatch(step: number) {
    const p = propsRef.current
    const next = stepMatch(p.search?.ranked ?? [], selected, step)
    if (next === null) return
    proj.stopIntroSpin()
    pendingSearchFitRef.current = false
    if (p.positions && proj.revealIndex(p.positions, next, sizeRef.current, tileWorldRef.current)) {
      userMovedRef.current = true
    }
    onSelect(next)
    requestDraw()
  }

  function rotateButton(dAzimuth: number, dElevation: number) {
    if (!proj.orbit) return
    proj.stopIntroSpin()
    proj.orbit(dAzimuth, dElevation)
    userMovedRef.current = true
    requestDraw()
  }

  function startIntroSpin() {
    proj.startIntroSpin()
    requestDraw()
  }

  function expandPreview() {
    setFocusPanel(true)
    preview.onOpenChange(true)
  }

  // 「↙」と Esc は同じ扱い(畳んだことを覚える)。
  function collapsePreview() {
    setFocusPanel(false)
    preview.onOpenChange(false)
    // 畳んだら、広げたボタンに焦点を戻す(カードが描かれてから)。
    requestAnimationFrame(() => expandButtonRef.current?.focus({ preventScroll: true }))
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape' && previewOpen && selected !== null) {
      e.preventDefault()
      setFocusPanel(false)
      preview.onOpenChange(false)
    } else if (e.key === 'Escape' && selected !== null) {
      e.preventDefault()
      onSelect(null)
    } else if (e.key === '+' || e.key === '=') {
      e.preventDefault()
      zoomButton(1.4)
    } else if (e.key === '-') {
      e.preventDefault()
      zoomButton(1 / 1.4)
    } else if (e.key === '0') {
      e.preventDefault()
      fitAll()
    } else if (e.key === 'ArrowLeft' && proj.orbit) {
      e.preventDefault()
      rotateButton(-0.12, 0)
    } else if (e.key === 'ArrowRight' && proj.orbit) {
      e.preventDefault()
      rotateButton(0.12, 0)
    } else if (e.key === 'ArrowUp' && proj.orbit) {
      e.preventDefault()
      rotateButton(0, 0.08)
    } else if (e.key === 'ArrowDown' && proj.orbit) {
      e.preventDefault()
      rotateButton(0, -0.08)
    } else if ((e.key === 'n' || e.key === 'N') && search) {
      // 検索しているときだけ、次の一致・前の一致へ(矢印は回転なので別のキーにする)。
      e.preventDefault()
      focusMatch(1)
    } else if ((e.key === 'p' || e.key === 'P') && search) {
      e.preventDefault()
      focusMatch(-1)
    }
  }

  const hoverNode = hover && hover.index < nodes.length && hover.index !== selected ? nodes[hover.index] : null
  const selectedNode = selected !== null && selected < nodes.length ? nodes[selected] : null
  const { width: cw } = sizeRef.current
  const axesShown = axes?.shown ?? false

  return (
    <div
      ref={containerRef}
      className={styles.root}
      data-preview-open={(previewOpen && selectedNode !== null) || undefined}
    >
      <canvas
        ref={canvasRef}
        className={styles.canvas}
        role="img"
        aria-label={ariaLabel}
        title={hint}
        tabIndex={0}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={(e) => {
          if (e.pointerType === 'mouse' && !pointersRef.current.has(e.pointerId)) setHover(null)
        }}
        // 中ボタンの自動スクロールと、右ボタンのメニューを出さない(どちらも平行移動に使う)。
        onAuxClick={(e) => e.preventDefault()}
        onContextMenu={(e) => e.preventDefault()}
        onKeyDown={onKeyDown}
      />

      {overlay && <div className={styles.overlay}>{overlay}</div>}

      <div className={styles.zoomControls} role="group" aria-label={m.zoomGroup}>
        {search && search.ranked.length > 0 && (
          <>
            <button
              type="button"
              className={styles.zoomButton}
              aria-label={m.prevMatch}
              title={m.prevMatch}
              onClick={() => focusMatch(-1)}
            >
              ‹
            </button>
            <button
              type="button"
              className={styles.zoomButton}
              aria-label={m.nextMatch}
              title={m.nextMatch}
              onClick={() => focusMatch(1)}
            >
              ›
            </button>
          </>
        )}
        {proj.supportsIntroSpin && introSpin && (
          <button
            type="button"
            className={styles.zoomButton}
            aria-label={m.rotateOnce}
            title={m.rotateOnce}
            onClick={startIntroSpin}
          >
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M13.5 8a5.5 5.5 0 1 1-1.9-4.2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              <path
                d="M12 1.2V4h-2.8"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        )}
        {proj.hasDepth && axes && (
          <button
            type="button"
            className={styles.zoomButton}
            aria-label={m.toggleAxes}
            title={m.toggleAxes}
            aria-pressed={axesShown}
            data-active={axesShown || undefined}
            onClick={() => axes.onShownChange(!axesShown)}
          >
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M2 14V2M2 14h12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              <path d="M6 14V5M10 14V5M2 10h12M2 6h12" stroke="currentColor" strokeWidth="0.8" opacity="0.6" />
            </svg>
          </button>
        )}
        <button type="button" className={styles.zoomButton} aria-label={m.zoomIn} title={m.zoomIn} onClick={() => zoomButton(1.4)}>
          +
        </button>
        <button type="button" className={styles.zoomButton} aria-label={m.zoomOut} title={m.zoomOut} onClick={() => zoomButton(1 / 1.4)}>
          −
        </button>
        <button type="button" className={styles.zoomButton} aria-label={m.fit} title={m.fit} onClick={fitAll}>
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4" stroke="currentColor" strokeWidth="1.5" />
          </svg>
        </button>
      </div>

      {hoverNode && hover && (
        <div
          className={styles.hoverCard}
          style={{
            left: Math.min(hover.x + 14, Math.max(0, cw - 200)),
            top: Math.max(8, hover.y - 70),
          }}
          aria-hidden="true"
        >
          <img
            src={assetUrl(hoverNode.id, 'thumb')}
            style={focalStyle(hoverNode.focal_point)}
            alt=""
            className={styles.hoverThumb}
          />
          <span className={styles.hoverTitle}>{hoverNode.title || m.untitled}</span>
        </div>
      )}

      {selectedNode && previewOpen && (
        <MapPreviewPanel
          assetId={selectedNode.id}
          fallbackTitle={selectedNode.title}
          autoFocus={focusPanel}
          onCollapse={collapsePreview}
        />
      )}

      {selectedNode && !previewOpen && (
        <div
          className={styles.selectionCard}
          role="region"
          aria-label={m.selectionLabel}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.preventDefault()
              onSelect(null)
              canvasRef.current?.focus({ preventScroll: true })
            }
          }}
        >
          <Link
            to={`/assets/${selectedNode.id}`}
            className={styles.selectionThumbLink}
            aria-label={m.openInViewer}
            title={m.openInViewer}
          >
            <img
              src={assetUrl(selectedNode.id, 'thumb')}
              style={focalStyle(selectedNode.focal_point)}
              alt=""
              className={styles.selectionThumb}
            />
          </Link>
          <div className={styles.selectionBody}>
            <span className={styles.selectionTitle}>{selectedNode.title || m.untitled}</span>
            <span className={styles.selectionMeta}>
              {fmt(m.neighborsHighlighted, { count: neighborsOf(neighborIndices, selected!).length })}
            </span>
            <div className={styles.selectionLinks}>
              <Link to={`/lineage/${selectedNode.id}`}>{m.lineageGraph}</Link>
              <Link to={buildSimilarSearchPath(selectedNode.id)}>{m.similarInSearch}</Link>
            </div>
          </div>
          <div className={styles.selectionButtons}>
            <button
              ref={expandButtonRef}
              type="button"
              className={styles.selectionButton}
              aria-label={m.expandPreview}
              title={m.expandPreview}
              onClick={expandPreview}
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path
                  d="M3 13L12 4M6 4h6v6"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
            <button
              type="button"
              className={styles.selectionButton}
              aria-label={t.common.close}
              title={t.common.close}
              onClick={() => onSelect(null)}
            >
              ×
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
