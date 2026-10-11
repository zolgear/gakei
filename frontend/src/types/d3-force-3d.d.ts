/**
 * `d3-force-3d`(MIT)の最小限の型。パッケージに型定義が同梱されていないので、マップの
 * ネットワーク(`features/map/NetworkView.tsx`)で使う範囲だけを書く。
 *
 * API は `d3-force` とほぼ同じで、`forceSimulation(nodes, 次元数)` の第 2 引数と `forceZ`、
 * ノードの `z` / `vz` / `fz` が増えている。使っていない力(`forceCenter`、`forceCollide`、
 * `forceRadial`)は、必要になったときに足す。
 */
declare module 'd3-force-3d' {
  /** 力学モデルが読み書きするノード。座標と速度はモデルが書き込む。 */
  export interface SimulationNodeDatum3D {
    index?: number
    x?: number
    y?: number
    z?: number
    vx?: number
    vy?: number
    vz?: number
    /** 位置を固定したいときに入れる。 */
    fx?: number | null
    fy?: number | null
    fz?: number | null
  }

  /** 辺。`source` / `target` は `id()` で引く値(初期化後はノードに置き換わる)。 */
  export interface SimulationLinkDatum3D<N extends SimulationNodeDatum3D> {
    source: N | string | number
    target: N | string | number
    index?: number
  }

  /** ノードに作用する力。モデルが初期化と毎回の計算に呼ぶ。 */
  export interface Force3D<N extends SimulationNodeDatum3D> {
    (alpha: number): void
    initialize?(nodes: N[], random: () => number, numDimensions: number): void
  }

  export interface Simulation3D<N extends SimulationNodeDatum3D> {
    nodes(): N[]
    /** 名前を付けて力を足す(null で外す)。 */
    force(name: string, force: Force3D<N> | null): this
    force(name: string): Force3D<N> | undefined
    /** 今の「熱さ」。`alphaMin()` を下回ったら落ち着いたとみなす。 */
    alpha(): number
    alpha(value: number): this
    alphaMin(): number
    alphaMin(value: number): this
    alphaDecay(): number
    alphaDecay(value: number): this
    velocityDecay(): number
    velocityDecay(value: number): this
    /** 揺らぎに使う乱数(種を与えると同じ配置になる)。 */
    randomSource(source: () => number): this
    /** 1 回分進める(自前のループから呼ぶ)。 */
    tick(iterations?: number): this
    /** 内部のタイマーを止める。 */
    stop(): this
    restart(): this
  }

  export interface LinkForce3D<N extends SimulationNodeDatum3D, L> extends Force3D<N> {
    links(): L[]
    links(links: L[]): this
    id(accessor: (node: N, index: number, nodes: N[]) => string | number): this
    distance(distance: number | ((link: L, index: number, links: L[]) => number)): this
    strength(strength: number | ((link: L, index: number, links: L[]) => number)): this
    iterations(count: number): this
  }

  export interface ManyBodyForce3D<N extends SimulationNodeDatum3D> extends Force3D<N> {
    strength(strength: number | ((node: N, index: number, nodes: N[]) => number)): this
    theta(theta: number): this
    distanceMin(distance: number): this
    distanceMax(distance: number): this
  }

  /** 1 つの軸へ引く力(`forceX` / `forceY` / `forceZ`)。 */
  export interface AxisForce3D<N extends SimulationNodeDatum3D> extends Force3D<N> {
    strength(strength: number | ((node: N, index: number, nodes: N[]) => number)): this
    x?(x: number | ((node: N, index: number, nodes: N[]) => number)): this
    y?(y: number | ((node: N, index: number, nodes: N[]) => number)): this
    z?(z: number | ((node: N, index: number, nodes: N[]) => number)): this
  }

  /** `numDimensions` は 1〜3(省くと 2)。 */
  export function forceSimulation<N extends SimulationNodeDatum3D>(
    nodes?: N[],
    numDimensions?: number,
  ): Simulation3D<N>

  export function forceLink<N extends SimulationNodeDatum3D, L extends SimulationLinkDatum3D<N>>(
    links?: L[],
  ): LinkForce3D<N, L>

  export function forceManyBody<N extends SimulationNodeDatum3D>(): ManyBodyForce3D<N>

  export function forceX<N extends SimulationNodeDatum3D>(
    x?: number | ((node: N, index: number, nodes: N[]) => number),
  ): AxisForce3D<N>

  export function forceY<N extends SimulationNodeDatum3D>(
    y?: number | ((node: N, index: number, nodes: N[]) => number),
  ): AxisForce3D<N>

  export function forceZ<N extends SimulationNodeDatum3D>(
    z?: number | ((node: N, index: number, nodes: N[]) => number),
  ): AxisForce3D<N>
}
