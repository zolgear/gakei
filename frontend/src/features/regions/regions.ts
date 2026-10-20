/**
 * 領域の指示(ADR-0053)の純粋関数。四角の値の形・色・検証・幾何・編集の操作・再実行での復元を持つ。
 * 画面(`RegionEditor`)とフォーム(`useRunFormLogic`)と Run 詳細から使う。
 *
 * - 四角は主たる入力の画像に対する割合(0〜1)で、左上を原点にした `[x0, y0, x1, y1]`(3章)。
 *   画面の拡大率に依らず、同じ値のまま描ける。
 * - 色は番号の順に `REGION_PALETTE` を使い、9 個目からは繰り返す。サーバーが四角を描く画像
 *   (`backend/app/domain/regions.py` の `PALETTE` と `ordered()`)と同じ順・同じ色にする。
 * - 上限と文字数は `backend/app/domain/schemas.py`(`REGIONS_MAX` など)と同じ値。
 */
import type { OperationCapabilities, RegionBox, RegionKind, RunRegions } from '../../api/client'

export const REGIONS_MAX = 16
export const REGION_INSTRUCTION_MAX_LENGTH = 2000
export const REGION_TEXT_MAX_LENGTH = 200

export const REGION_KINDS: readonly RegionKind[] = ['change', 'add', 'keep', 'remove', 'move']

/** 四角の色。`backend/app/domain/regions.py` の `PALETTE` と同じ順・同じ値にする。 */
export const REGION_PALETTE: readonly { key: string; rgb: readonly [number, number, number] }[] = [
  { key: 'red', rgb: [230, 25, 75] },
  { key: 'blue', rgb: [0, 130, 200] },
  { key: 'green', rgb: [60, 180, 75] },
  { key: 'yellow', rgb: [255, 225, 25] },
  { key: 'magenta', rgb: [240, 50, 230] },
  { key: 'cyan', rgb: [70, 240, 240] },
  { key: 'orange', rgb: [245, 130, 48] },
  { key: 'purple', rgb: [145, 30, 180] },
]

/** 並び順(番号の順。0 から)の四角の色(CSS の色)。サーバーの `color_for` と同じ。 */
export function regionColor(index: number): string {
  const [r, g, b] = REGION_PALETTE[index % REGION_PALETTE.length].rgb
  return `rgb(${r}, ${g}, ${b})`
}

/** バッジの数字の色(明るい色には黒、暗い色には白)。サーバーの `_text_color` と同じ式。 */
export function regionBadgeTextColor(index: number): string {
  const [r, g, b] = REGION_PALETTE[index % REGION_PALETTE.length].rgb
  return 0.299 * r + 0.587 * g + 0.114 * b > 150 ? '#000000' : '#ffffff'
}

/** 番号の順に並べる(色・文・画像の番号はこの順で決まる。サーバーの `ordered()` と同じ)。 */
export function orderedRegions<T extends { number: number }>(regions: readonly T[]): T[] {
  return [...regions].sort((a, b) => a.number - b.number)
}

/** 今のプロバイダー・モデルの Edit が四角を受けるか(受けなければ「領域」を出さない。5章)。 */
export function regionsSupported(editOpCaps: OperationCapabilities | undefined): boolean {
  return editOpCaps?.supports_regions === true
}

// -- 幾何 -----------------------------------------------------------------------------

export type Box = [number, number, number, number]
export interface Point {
  x: number
  y: number
}

/** 描いた四角とみなす最小の幅・高さ(割合)。これより小さいドラッグは描かなかったことにする。 */
export const MIN_BOX_SIZE = 0.01

export function clamp01(v: number): number {
  return Math.min(1, Math.max(0, v))
}

/** 2点(ドラッグの始点と今の点)から四角を作る(範囲に収める)。 */
export function boxFromPoints(a: Point, b: Point): Box {
  const x0 = clamp01(Math.min(a.x, b.x))
  const y0 = clamp01(Math.min(a.y, b.y))
  const x1 = clamp01(Math.max(a.x, b.x))
  const y1 = clamp01(Math.max(a.y, b.y))
  return [x0, y0, x1, y1]
}

export function isBoxBigEnough(box: Box): boolean {
  return box[2] - box[0] >= MIN_BOX_SIZE && box[3] - box[1] >= MIN_BOX_SIZE
}

/** サーバーの検証(`_check_region_box`)と同じ: 0〜1 の範囲で、x0 < x1 かつ y0 < y1。 */
export function isValidBox(box: readonly number[] | null | undefined): box is Box {
  if (!box || box.length !== 4) return false
  if (!box.every((v) => Number.isFinite(v) && v >= 0 && v <= 1)) return false
  return box[0] < box[2] && box[1] < box[3]
}

/** 大きさを保ったまま動かす(画像の外へは出さない)。 */
export function moveBox(box: Box, dx: number, dy: number): Box {
  const w = box[2] - box[0]
  const h = box[3] - box[1]
  const x0 = Math.min(1 - w, Math.max(0, box[0] + dx))
  const y0 = Math.min(1 - h, Math.max(0, box[1] + dy))
  return [x0, y0, x0 + w, y0 + h]
}

export type ResizeHandle = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w'
export const RESIZE_HANDLES: readonly ResizeHandle[] = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']

/** ハンドルの位置(割合)。 */
export function handlePoint(box: Box, handle: ResizeHandle): Point {
  const cx = (box[0] + box[2]) / 2
  const cy = (box[1] + box[3]) / 2
  const x = handle.includes('w') ? box[0] : handle.includes('e') ? box[2] : cx
  const y = handle.includes('n') ? box[1] : handle.includes('s') ? box[3] : cy
  return { x, y }
}

/** ハンドルを `point` へ動かしたときの四角。反対の辺を越えないよう、最小の大きさで止める。 */
export function resizeBox(box: Box, handle: ResizeHandle, point: Point): Box {
  let [x0, y0, x1, y1] = box
  const px = clamp01(point.x)
  const py = clamp01(point.y)
  if (handle.includes('w')) x0 = Math.min(px, x1 - MIN_BOX_SIZE)
  if (handle.includes('e')) x1 = Math.max(px, x0 + MIN_BOX_SIZE)
  if (handle.includes('n')) y0 = Math.min(py, y1 - MIN_BOX_SIZE)
  if (handle.includes('s')) y1 = Math.max(py, y0 + MIN_BOX_SIZE)
  return [clamp01(x0), clamp01(y0), clamp01(x1), clamp01(y1)]
}

/**
 * 「動かす」に切り替えたときの移す先の初期位置。元の四角を同じ大きさのまま横にずらす
 * (右に入らなければ左、それも入らなければ下へ)。
 */
export function defaultMoveTarget(source: Box): Box {
  const w = source[2] - source[0]
  const h = source[3] - source[1]
  const shiftX = Math.max(w * 0.6, 0.08)
  if (source[2] + shiftX <= 1) return moveBox(source, shiftX, 0)
  if (source[0] - shiftX >= 0) return moveBox(source, -shiftX, 0)
  const shiftY = Math.max(h * 0.6, 0.08)
  if (source[3] + shiftY <= 1) return moveBox(source, 0, shiftY)
  return moveBox(source, 0, -shiftY)
}

export function boxCenter(box: Box): Point {
  return { x: (box[0] + box[2]) / 2, y: (box[1] + box[3]) / 2 }
}

// -- 編集画面の状態 ---------------------------------------------------------------------

/** 編集画面の四角1つ。並び順が番号(1 から)で、色もこの順で決まる。 */
export interface EditorRegion {
  /** 画面の中だけのキー(並べ替えても変わらない)。 */
  key: string
  kind: RegionKind
  /** 四角(「動かす」では移す先)。 */
  box: Box
  /** 元の四角(「動かす」だけ)。 */
  sourceBox: Box | null
  instruction: string
  text: string
  referenceAssetId: string | null
  /**
   * 検出の候補から作ったときの名前。四角の見出しと指示の欄の目安に出す。画面の中(とフォームの状態)
   * だけのもので、サーバーには送らない(`RegionBox` に名前の欄は無い)。
   */
  label?: string
}

let keySeq = 0
export function newRegionKey(): string {
  keySeq += 1
  return `r${Date.now().toString(36)}-${keySeq}`
}

/**
 * 四角の名前(検出の候補の名前)。キーは四角の番号。フォームの状態に四角と組で持ち、サーバーには
 * 送らない。
 */
export type RegionLabels = Record<string, string>

/** フォームの値(API の形)から編集画面の状態を作る。番号の順に並べる。 */
export function editorRegionsFromBoxes(boxes: readonly RegionBox[], labels: RegionLabels = {}): EditorRegion[] {
  return orderedRegions(boxes).map((b) => ({
    ...(labels[String(b.number)] ? { label: labels[String(b.number)] } : {}),
    key: newRegionKey(),
    kind: b.kind,
    box: [...b.box] as Box,
    sourceBox: b.kind === 'move' && b.source_box ? ([...b.source_box] as Box) : null,
    instruction: b.instruction ?? '',
    text: b.text ?? '',
    referenceAssetId: b.reference_asset_id ?? null,
  }))
}

/** 編集画面の状態を API の形にする。番号は並び順で 1 から振り直す。 */
export function boxesFromEditorRegions(regions: readonly EditorRegion[]): RegionBox[] {
  return regions.map((r, index) => ({
    number: index + 1,
    kind: r.kind,
    box: [...r.box],
    source_box: r.kind === 'move' ? (r.sourceBox ? [...r.sourceBox] : null) : null,
    instruction: r.instruction.trim(),
    // 描く文字は前後の空白も含めてそのまま渡す(サーバーと同じ)。空なら無し。
    text: r.text === '' ? null : r.text,
    reference_asset_id: r.referenceAssetId,
  }))
}

/** 編集画面の状態から、四角の名前(番号 → 名前)を作る。番号は並び順で 1 から。 */
export function labelsFromEditorRegions(regions: readonly EditorRegion[]): RegionLabels {
  const labels: RegionLabels = {}
  regions.forEach((r, index) => {
    if (r.label) labels[String(index + 1)] = r.label
  })
  return labels
}

export function addRegion(regions: readonly EditorRegion[], box: Box, extra: Partial<EditorRegion> = {}): EditorRegion[] {
  if (regions.length >= REGIONS_MAX) return [...regions]
  return [
    ...regions,
    { key: newRegionKey(), kind: 'change', box, sourceBox: null, instruction: '', text: '', referenceAssetId: null, ...extra },
  ]
}

export function removeRegion(regions: readonly EditorRegion[], key: string): EditorRegion[] {
  return regions.filter((r) => r.key !== key)
}

export function updateRegion(
  regions: readonly EditorRegion[],
  key: string,
  patch: Partial<Omit<EditorRegion, 'key'>>,
): EditorRegion[] {
  return regions.map((r) => (r.key === key ? { ...r, ...patch } : r))
}

/** 番号を1つ前(`up`)・後ろ(`down`)へ入れ替える。 */
export function moveRegionOrder(regions: readonly EditorRegion[], key: string, direction: 'up' | 'down'): EditorRegion[] {
  const index = regions.findIndex((r) => r.key === key)
  const target = direction === 'up' ? index - 1 : index + 1
  if (index < 0 || target < 0 || target >= regions.length) return [...regions]
  const next = [...regions]
  ;[next[index], next[target]] = [next[target], next[index]]
  return next
}

/**
 * 種類を変える。「動かす」にすると、今の四角を元の位置にし、移す先を横にずらして作る。
 * 「動かす」からほかの種類にすると、元の位置の四角を残す(物がある場所なので)。
 */
export function setRegionKind(regions: readonly EditorRegion[], key: string, kind: RegionKind): EditorRegion[] {
  return regions.map((r) => {
    if (r.key !== key || r.kind === kind) return r
    if (kind === 'move') {
      const source = r.box
      return { ...r, kind, sourceBox: source, box: defaultMoveTarget(source) }
    }
    if (r.kind === 'move') return { ...r, kind, box: r.sourceBox ?? r.box, sourceBox: null }
    return { ...r, kind }
  })
}

// -- 検証(サーバーの検証に合わせる) ------------------------------------------------------

export type RegionProblem =
  | 'instructionRequired'
  | 'instructionTooLong'
  | 'textTooLong'
  | 'invalidBox'
  | 'invalidSourceBox'

/** 四角1つの問題(無ければ空)。「変える」「足す」は、指示・描く文字・参照画像のどれかが要る。 */
export function regionProblems(region: EditorRegion): RegionProblem[] {
  const problems: RegionProblem[] = []
  const instruction = region.instruction.trim()
  if (
    (region.kind === 'change' || region.kind === 'add') &&
    instruction === '' &&
    region.text === '' &&
    region.referenceAssetId === null
  ) {
    problems.push('instructionRequired')
  }
  if (instruction.length > REGION_INSTRUCTION_MAX_LENGTH) problems.push('instructionTooLong')
  if (region.text.length > REGION_TEXT_MAX_LENGTH) problems.push('textTooLong')
  if (!isValidBox(region.box)) problems.push('invalidBox')
  if (region.kind === 'move' && !isValidBox(region.sourceBox)) problems.push('invalidSourceBox')
  return problems
}

/** 保存できるか(四角の数と、どの四角にも問題が無いこと)。 */
export function canSaveRegions(regions: readonly EditorRegion[]): boolean {
  return regions.length <= REGIONS_MAX && regions.every((r) => regionProblems(r).length === 0)
}

/** フォームが覚えている四角(API の形)をそのまま送れるか。 */
export function areRegionBoxesValid(boxes: readonly RegionBox[]): boolean {
  if (boxes.length > REGIONS_MAX) return false
  const numbers = new Set(boxes.map((b) => b.number))
  if (numbers.size !== boxes.length) return false
  return editorRegionsFromBoxes(boxes).every((r) => regionProblems(r).length === 0)
}

// -- 入力の枚数(5章) --------------------------------------------------------------------

/**
 * 四角を付けたとき、サーバーが入力に足す画像の数。四角を描いた画像 1 枚と、入力に無い参照画像
 * (同じ画像は1枚)。四角が無ければ 0。
 */
export function regionExtraInputCount(boxes: readonly RegionBox[], imageAssetIds: readonly string[]): number {
  if (boxes.length === 0) return 0
  const inputs = new Set(imageAssetIds)
  const refs = new Set<string>()
  for (const b of boxes) {
    if (b.reference_asset_id && !inputs.has(b.reference_asset_id)) refs.add(b.reference_asset_id)
  }
  return 1 + refs.size
}

// -- 再実行での復元(4章・5章) -------------------------------------------------------------

/** `run.prompt` の末尾がサーバーの足した文なら、それを除いて利用者が書いたプロンプトに戻す。 */
export function stripAppendedText(prompt: string, appended: string | null | undefined): string {
  if (!appended) return prompt
  return prompt.endsWith(appended) ? prompt.slice(0, prompt.length - appended.length) : prompt
}

interface RunInputLike {
  asset_id: string
  role: 'image' | 'mask' | 'reference'
  position: number
}

export interface RestoredRunRegions<T extends RunInputLike> {
  prompt: string
  inputs: T[]
  regions: RegionBox[]
  /** 四角が付いている主たる入力の Asset(四角が無ければ null)。 */
  regionsAssetId: string | null
}

/**
 * 「同じ設定で新規作成」・再実行で、Run から四角を戻す。サーバーが足した入力(四角を描いた画像と、
 * 四角の参照画像として利用者の入力の後ろに足したもの)を外し、プロンプトの末尾の足した文を除く。
 * 四角の無い Run はそのまま返す。
 */
export function restoreRunRegions<T extends RunInputLike>(run: {
  prompt: string
  inputs: readonly T[]
  regions?: RunRegions | null
}): RestoredRunRegions<T> {
  const record = run.regions
  if (!record) return { prompt: run.prompt, inputs: [...run.inputs], regions: [], regionsAssetId: null }

  const overlayId = record.overlay_asset_id ?? null
  const overlay = overlayId
    ? run.inputs.find((i) => i.role === 'image' && i.asset_id === overlayId)
    : undefined
  const referenceIds = new Set(record.regions.map((r) => r.reference_asset_id).filter((id): id is string => !!id))
  const inputs = overlay
    ? run.inputs.filter(
        (i) =>
          !(
            i.role === 'image' &&
            i.position >= overlay.position &&
            (i.asset_id === overlayId || referenceIds.has(i.asset_id))
          ),
      )
    : [...run.inputs]
  const primary = inputs.find((i) => i.role === 'image' && i.position === 0)
  const regions = primary ? orderedRegions(record.regions).map((r) => ({ ...r })) : []
  return {
    prompt: stripAppendedText(run.prompt, record.appended_text),
    inputs,
    regions,
    regionsAssetId: primary && regions.length > 0 ? primary.asset_id : null,
  }
}
