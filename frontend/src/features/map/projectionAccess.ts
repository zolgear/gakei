/**
 * 見せ方(投影)を変える操作を 1 か所に通すための、小さな入れ物(純粋な関数のみ)。
 *
 * マップの canvas は、見せ方(`projection.ts` の平面・立体)を ref に持ち、種類が替わったときに
 * **作り直す**。そのため、長く生きるイベントの購読(`addEventListener` のホイールなど)が
 * レンダーのときの実体を掴んでしまうと、見せ方を切り替えたあとに**古い投影**を動かし続けて、
 * 画面は何も変わらない(2026-10-11 の不具合。ホイールが効かなくなった)。
 *
 * `changeProjection` は、呼ばれるたびに ref から**その時点の**実体を読み、変えたあとに必ず
 * 描き直しを頼む。「状態を変えたら必ず描き直しが走る」を 1 か所で守るための関数。
 */
import type { MapProjection } from './projection'

/** 今の投影を持つ入れ物(React の `useRef` をそのまま渡せる形)。 */
export interface ProjectionRef {
  current: MapProjection | null
}

/**
 * 今の投影に `change` を適用し、描き直しを頼む。まだ投影が無ければ何もせず false。
 *
 * `change` が false を返したときも描き直しは頼む(状態を変えたかどうかは呼び出し側より
 * 投影のほうが知っているが、余分な 1 フレームは害が無く、取りこぼしのほうが痛いため)。
 */
export function changeProjection(
  ref: ProjectionRef,
  requestDraw: () => void,
  change: (projection: MapProjection) => void,
): boolean {
  const projection = ref.current
  if (!projection) return false
  change(projection)
  requestDraw()
  return true
}
