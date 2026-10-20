/**
 * フォームの状態(model/params/prompt/inputs)を localStorage に保持する。画面移動での保持は
 * RunFormContext がメモリ上で行うが、これはリロードや iOS Safari のタブ復帰(バックグラウンドで
 * 破棄される)をまたいだ保持のため。`src/shell/panelStorage.ts` と同じく try/catch で囲み、
 * 使えない環境では黙ってメモリだけで動く。
 *
 * capabilities に対する検証(存在しないモデル・選択肢を落とす)はここでは行わない。
 * このモジュールはあくまで「保存されていた値をそのまま返す/そのまま書く」だけで、
 * 検証は capabilities を知っている RunForm.tsx 側(sanitizeRawValues 等)が行う。
 */
import { ensureInputIds } from '../features/run-form/editInputs'
import type { RunFormState, RunInputItem } from '../features/run-form/types'
import type { RegionBox } from '../api/client'

const STORAGE_KEY = 'gakei.runForm.v1'
const STORAGE_VERSION = 1

interface StoredRunFormPayloadV1 {
  version: 1
  /**
   * ADR-0013 で追加。旧バージョンの保存値には無いので optional にする(無ければ呼び出し側
   * (useRunFormLogic)が capabilities の `default_provider` に解決する)。バージョンは上げない
   * (model/prompt/params/inputs まで巻き添えで破棄したくないため)。
   */
  provider?: string
  model: string
  prompt: string
  params: Record<string, string | number | boolean>
  inputs: RunInputItem[]
  /**
   * ADR-0022 で追加。provider と同じ理由でバージョンは上げず optional にする(無い・文字列で
   * なければ null=グループなし。存在確認は呼び出し側(useRunFormLogic)がグループ一覧で行う)。
   */
  assetGroupId?: string | null
  /**
   * ADR-0053 で追加。provider と同じ理由でバージョンは上げず optional にする(無い・形が壊れて
   * いれば四角なし。四角だけを捨て、ほかの値は残す)。
   */
  regions?: RegionBox[]
  regionsAssetId?: string | null
  /**
   * 古い保存値だけが持つ、四角の名前(番号 → 名前)。今は四角の `label` に入れる。読むときに
   * `label` へ移し、書くときは出さない。
   */
  regionLabels?: Record<string, string>
}

/** 保存値の入力1件。inputId は issue #12・#13 の修正で追加したため、旧形式の保存値には無い。 */
type StoredRunInputItem = Omit<RunInputItem, 'inputId'> & { inputId?: string }

function isStoredRunInputItem(value: unknown): value is StoredRunInputItem {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  return (
    (v.inputId === undefined || typeof v.inputId === 'string') &&
    typeof v.assetId === 'string' &&
    (v.role === 'image' || v.role === 'mask' || v.role === 'reference') &&
    typeof v.position === 'number'
  )
}

function isNumberArray(value: unknown, length: number): value is number[] {
  return Array.isArray(value) && value.length === length && value.every((v) => typeof v === 'number')
}

/** 保存値の四角1つの形を確かめる(値の範囲の検証は送る前に `areRegionBoxesValid` が行う)。 */
function isStoredRegionBox(value: unknown): value is RegionBox {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  return (
    typeof v.number === 'number' &&
    (v.kind === 'change' || v.kind === 'add' || v.kind === 'keep' || v.kind === 'remove' || v.kind === 'move') &&
    isNumberArray(v.box, 4) &&
    (v.source_box === undefined || v.source_box === null || isNumberArray(v.source_box, 4)) &&
    typeof v.instruction === 'string' &&
    (v.text === undefined || v.text === null || typeof v.text === 'string') &&
    (v.reference_asset_id === undefined || v.reference_asset_id === null || typeof v.reference_asset_id === 'string') &&
    (v.label === undefined || v.label === null || typeof v.label === 'string')
  )
}

/** 古い保存値の四角の名前(番号 → 名前)。文字列でない値は捨てる。 */
function parseStoredRegionLabels(value: unknown): Record<string, string> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return {}
  const labels: Record<string, string> = {}
  for (const [key, label] of Object.entries(value as Record<string, unknown>)) {
    if (typeof label === 'string' && label) labels[key] = label
  }
  return labels
}

/** 四角と、四角を付けた主たる入力。どちらかが壊れていれば四角なしにする。 */
function parseStoredRegions(
  payload: Record<string, unknown>,
): Pick<RunFormState, 'regions' | 'regionsAssetId'> {
  const regions = payload.regions
  const assetId = payload.regionsAssetId
  if (!Array.isArray(regions) || regions.length === 0 || typeof assetId !== 'string') {
    return { regions: [], regionsAssetId: null }
  }
  if (!regions.every(isStoredRegionBox)) return { regions: [], regionsAssetId: null }
  // 古い保存値の `regionLabels` は、四角に名前が無ければその四角の `label` に移す。
  const labels = parseStoredRegionLabels(payload.regionLabels)
  const merged = regions.map((r) => {
    const old = labels[String(r.number)]
    return !r.label && old ? { ...r, label: old } : r
  })
  return { regions: merged, regionsAssetId: assetId }
}

function isParamValue(value: unknown): value is string | number | boolean {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
}

function isParamsRecord(value: unknown): value is Record<string, string | number | boolean> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  return Object.values(value as Record<string, unknown>).every(isParamValue)
}

/**
 * JSON 文字列(localStorage から読んだ生の値)を検証しつつ RunFormState へ変換する。
 * バージョン不一致・形が壊れている(JSON エラー含む)場合は null を返す(既定にする)。
 * 純粋関数(副作用なし)。
 */
export function parseStoredRunFormState(raw: string | null): RunFormState | null {
  if (raw === null) return null

  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return null
  }

  if (typeof data !== 'object' || data === null) return null
  const payload = data as Record<string, unknown>

  if (payload.version !== STORAGE_VERSION) return null
  if (payload.provider !== undefined && typeof payload.provider !== 'string') return null
  if (typeof payload.model !== 'string') return null
  if (typeof payload.prompt !== 'string') return null
  if (!isParamsRecord(payload.params)) return null
  if (!Array.isArray(payload.inputs) || !payload.inputs.every(isStoredRunInputItem)) return null

  return {
    // 旧バージョンの保存値には provider が無い。空文字にしておき、capabilities 読み込み後に
    // useRunFormLogic 側で default_provider へ解決させる。
    provider: typeof payload.provider === 'string' ? payload.provider : '',
    model: payload.model,
    prompt: payload.prompt,
    params: payload.params,
    // 旧形式(inputId なし)や重複した inputId は、読み込み時に新しい inputId を振り直す。
    // バージョンは上げない(provider と同じく、他の値まで巻き添えで破棄したくないため)。
    inputs: ensureInputIds(payload.inputs),
    assetGroupId: typeof payload.assetGroupId === 'string' ? payload.assetGroupId : null,
    ...parseStoredRegions(payload),
  }
}

/** RunFormState を保存用の JSON 文字列にする。純粋関数。 */
export function serializeRunFormState(state: RunFormState): string {
  const payload: StoredRunFormPayloadV1 = {
    version: STORAGE_VERSION,
    provider: state.provider,
    model: state.model,
    prompt: state.prompt,
    params: state.params,
    inputs: state.inputs,
    assetGroupId: state.assetGroupId,
    regions: state.regions,
    regionsAssetId: state.regionsAssetId,
  }
  return JSON.stringify(payload)
}

/** 保存されているフォーム状態を読む。無い・壊れていれば null。 */
export function loadRunFormState(): RunFormState | null {
  try {
    return parseStoredRunFormState(localStorage.getItem(STORAGE_KEY))
  } catch {
    return null
  }
}

/** フォーム状態を保存する。失敗しても(容量超過・プライベートブラウジング等)黙って無視する。 */
export function saveRunFormState(state: RunFormState): void {
  try {
    localStorage.setItem(STORAGE_KEY, serializeRunFormState(state))
  } catch {
    // 保存できなくても致命的ではないので黙って無視する(メモリ上の Context は動き続ける)。
  }
}
