/**
 * サイズ入力の検証。純粋関数のみ(副作用なし)にして vitest で単体テストする。
 * 制約は capabilities(`SizeConstraints`)から取り、ここに定数として埋め込まない。
 */
import { fmt, msg } from '../../i18n'
import type { SizeConstraints } from '../../api/client'

export type SizeMode = 'unspecified' | 'auto' | 'custom'

export interface SizeState {
  mode: SizeMode
  width: number
  height: number
}

/** プリセットの「値」部分(API に送る内容と対応する)。表示名(label)とは分けて持つ。 */
export interface SizePresetValue {
  mode: SizeMode
  width?: number
  height?: number
}

export interface SizePreset {
  value: SizePresetValue
  label: string
}

/**
 * フォームに並べるプリセット一覧。表示名の文字列はここに集約し(他の場所で組み立てない)、
 * 値(mode/width/height。API に送る `WIDTHxHEIGHT` の元)とは別のプロパティに分けて持つ。
 * 「任意の幅×高さ」はプリセットを選ばず custom を直接編集する(SizeInput.tsx側の固定オプション。
 * ラベルは CUSTOM_SIZE_LABEL)。auto/未指定は選べるままにするが、フォームの初期値にはしない
 * (安定しないため。初期値は capabilities の default_size から決める)。
 *
 * サイズの並び・推奨表記は OpenAI の Image gen prompting guide(2026-09-22 確認)に基づく。
 * 2560×1440(総画素 3,686,400)が「安定して使える上限」、それを超える 4K 系は実験的。
 */
export function sizePresets(): SizePreset[] {
  // JSON からの値は mode が string 型に広がるため、SizePreset[] へキャストする(値自体は変えない)。
  return msg().runForm.sizeInput.presets as SizePreset[]
}

/**
 * プロバイダーの制約で選べるプリセットだけを返す。`allow_auto` でなければ auto を外し、
 * 幅と高さが制約(長辺・総画素・縦横比など)を満たさないプリセットも外す
 * (OpenAI 向けの 2K・4K のプリセットを、長辺 2048px の SD WebUI で出さないため)。
 */
export function sizePresetsFor(constraints: SizeConstraints): SizePreset[] {
  return sizePresets().filter((preset) => {
    const value = preset.value
    if (value.mode === 'auto') return constraints.allow_auto
    if (value.mode !== 'custom') return true
    return validateSize(constraints, value.width ?? 0, value.height ?? 0).valid
  })
}

/**
 * プロバイダーを切り替えたときのサイズ。今のサイズが新しいプロバイダーの制約で使えなければ
 * (auto を受け付けない、長辺の上限を超えるなど)、そのプロバイダーの既定のサイズに戻す。
 * 丸めれば済む(倍数でないだけの)サイズはそのまま残す(送信時に丸める)。
 */
export function sizeStateForProvider(
  constraints: SizeConstraints,
  defaultSize: string | null | undefined,
  state: SizeState,
): SizeState {
  if (validateSizeState(constraints, roundSizeStateToMultiple(constraints, state)).valid) return state
  return paramToSizeState(defaultSize ?? undefined)
}

/** select の「任意の幅×高さ」オプションの表示名。値は SIZE_PRESETS に含めず custom として扱う。 */
export function customSizeLabel(): string {
  return msg().runForm.sizeInput.customLabel
}

export function defaultSizeState(): SizeState {
  return { mode: 'unspecified', width: 1024, height: 1024 }
}

export interface SizeValidationResult {
  valid: boolean
  errors: string[]
}

/**
 * width/height が制約を満たすか検証する。mode が custom のときだけ呼ぶ想定
 * (unspecified/auto は常に valid)。
 */
export function validateSize(
  constraints: SizeConstraints,
  width: number,
  height: number,
): SizeValidationResult {
  const errors: string[] = []

  const v = msg().runForm.sizeValidation

  if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0) {
    return { valid: false, errors: [v.positiveInteger] }
  }

  if (width % constraints.multiple_of !== 0 || height % constraints.multiple_of !== 0) {
    errors.push(fmt(v.multipleOf, { n: constraints.multiple_of }))
  }

  if (Math.max(width, height) > constraints.max_long_edge) {
    errors.push(fmt(v.maxLongEdge, { n: constraints.max_long_edge }))
  }

  const totalPixels = width * height
  if (totalPixels < constraints.min_total_pixels || totalPixels > constraints.max_total_pixels) {
    errors.push(fmt(v.totalPixelsRange, { min: constraints.min_total_pixels, max: constraints.max_total_pixels }))
  }

  const aspectRatio = width / height
  if (aspectRatio < constraints.min_aspect_ratio || aspectRatio > constraints.max_aspect_ratio) {
    errors.push(
      fmt(v.aspectRatio, {
        min: formatAspectRatio(constraints.min_aspect_ratio),
        max: formatAspectRatio(constraints.max_aspect_ratio),
      }),
    )
  }

  return { valid: errors.length === 0, errors }
}

/** 縦横比(幅 / 高さ)を `1:3`・`4:1` の形にする。小数は2桁まで。 */
export function formatAspectRatio(ratio: number): string {
  const short = (n: number) => String(Math.round(n * 100) / 100)
  return ratio >= 1 ? `${short(ratio)}:1` : `1:${short(1 / ratio)}`
}

/**
 * SizeState 全体を検証する。unspecified は常に有効。auto は `allow_auto` のプロバイダーだけ有効
 * (SD WebUI など、auto を受け付けないプロバイダーではサーバーが 422 にするため)。
 */
export function validateSizeState(
  constraints: SizeConstraints,
  state: SizeState,
): SizeValidationResult {
  if (state.mode === 'auto' && !constraints.allow_auto) {
    return { valid: false, errors: [msg().runForm.sizeValidation.autoNotSupported] }
  }
  if (state.mode !== 'custom') {
    return { valid: true, errors: [] }
  }
  return validateSize(constraints, state.width, state.height)
}

/**
 * `multipleOf` の倍数に丸める(最も近い倍数。四捨五入)。
 * 丸めた結果が0以下になる場合は multipleOf 自体を返す。
 */
export function roundToMultiple(value: number, multipleOf: number): number {
  if (multipleOf <= 0) return value
  const rounded = Math.round(value / multipleOf) * multipleOf
  return Math.max(multipleOf, rounded)
}

/**
 * custom モードの SizeState を、width/height を multiple_of の倍数に丸めたものへ変換する。
 * blur 時・送信時の自動丸めに使う(unspecified/auto はそのまま返す)。
 */
export function roundSizeStateToMultiple(constraints: SizeConstraints, state: SizeState): SizeState {
  if (state.mode !== 'custom') return state
  return {
    ...state,
    width: roundToMultiple(state.width, constraints.multiple_of),
    height: roundToMultiple(state.height, constraints.multiple_of),
  }
}

/**
 * 2560×1440(2K、総画素 3,686,400)が OpenAI の Image gen prompting guide で言う
 * 「安定して使える上限」。これを超えるサイズ(4K 系・任意入力)は実験的扱いとして注記を出す。
 */
export const EXPERIMENTAL_TOTAL_PIXELS_THRESHOLD = 2560 * 1440

/** 総画素数が推奨上限(2560×1440)を超えているか(エラーではなく注記の判定に使う)。 */
export function isExperimentalSize(width: number, height: number): boolean {
  return width * height > EXPERIMENTAL_TOTAL_PIXELS_THRESHOLD
}

/**
 * 「実験的」の注記を出す制約か。注記は OpenAI のガイドに基づくもので、長辺が 2560px を超える
 * サイズ(4K 系)を受け付けるプロバイダーにだけ当てはまる(長辺 2048px の SD WebUI には出さない)。
 */
export function hasExperimentalSizes(constraints: SizeConstraints): boolean {
  return constraints.max_long_edge > 2560
}

/** SizeState を `params.size` に入れる値へ変換する。unspecified は undefined(=キーを作らない)。 */
export function sizeToParam(state: SizeState): string | undefined {
  if (state.mode === 'unspecified') return undefined
  if (state.mode === 'auto') return 'auto'
  return `${state.width}x${state.height}`
}

/** sizeToParam の逆変換。「同じ設定で再実行」でのプリフィルに使う。 */
export function paramToSizeState(sizeParam: unknown): SizeState {
  if (sizeParam === undefined || sizeParam === null) return defaultSizeState()
  if (sizeParam === 'auto') return { mode: 'auto', width: 1024, height: 1024 }

  const [widthStr, heightStr] = String(sizeParam).toLowerCase().split('x')
  const width = Number(widthStr)
  const height = Number(heightStr)
  if (Number.isFinite(width) && Number.isFinite(height)) {
    return { mode: 'custom', width, height }
  }
  return defaultSizeState()
}
