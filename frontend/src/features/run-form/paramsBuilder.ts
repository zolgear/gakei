/**
 * フォームの生の入力値(文字列)から `POST /api/runs` に送る params オブジェクトを組み立てる。
 * 純粋関数のみ(副作用なし)にして vitest で単体テストする。
 */
import type { ParamDef } from '../../api/client'

/** select で「未指定(API の既定に任せる)」を表す特別な値。 */
export const UNSPECIFIED = '__unspecified__'

/**
 * 型ごとの「未指定」の生値表現。int/float/text は空文字(number input・textarea が自然に
 * 空で表せるため)、それ以外(enum/bool。select で表す)は `UNSPECIFIED` を使う。
 */
export function unspecifiedRawValue(type: ParamDef['type']): string {
  return type === 'int' || type === 'float' || type === 'text' ? '' : UNSPECIFIED
}

/**
 * フォームが保持する、パラメータ名 → 生の文字列値。
 * - enum: 選択肢の文字列 or UNSPECIFIED
 * - int / float: 数字の文字列 or ''(未指定)
 * - bool: 'true' | 'false' | UNSPECIFIED
 * - text: 文字列 or ''(未指定。textarea なので select の UNSPECIFIED は使わない)
 */
export type RawParamValues = Record<string, string>

/**
 * defs に定義されたパラメータのうち、値が指定されているものだけを型変換して返す。
 * - 未指定(UNSPECIFIED / '' / undefined)の項目は結果に含めない。
 * - int / float は数値に、bool は真偽値に変換する(text/enum は文字列のまま)。
 * - defs に存在しないキーは無視する(モデル切り替え後の残骸を落とす)。
 */
export function buildParams(
  defs: ParamDef[],
  raw: RawParamValues,
): Record<string, string | number | boolean> {
  const result: Record<string, string | number | boolean> = {}

  for (const def of defs) {
    const value = raw[def.name]
    if (value === undefined || value === UNSPECIFIED || value === '') continue

    if (def.type === 'int' || def.type === 'float') {
      const n = Number(value)
      if (!Number.isFinite(n)) continue
      result[def.name] = n
    } else if (def.type === 'bool') {
      result[def.name] = value === 'true'
    } else {
      result[def.name] = value
    }
  }

  return result
}

/** サーバーだけが書く `run.params` のキーの接頭辞(ComfyUI は ADR-0013、SD WebUI は ADR-0038 3章)。 */
const SERVER_ONLY_PARAM_PREFIXES = ['comfyui_', 'sdwebui_'] as const

/** 実際に使った seed を持つキー。「同じ設定で再実行」では `seed` に戻す。 */
const SERVER_SEED_KEYS = ['comfyui_seed', 'sdwebui_seed'] as const

/**
 * Run が `run.params` に持つ、サーバー専有のキーを取り除く。
 * - ComfyUI: `comfyui_workflow`、`comfyui_seed`、`comfyui_uploads`、`comfyui_prompt`、
 *   `comfyui_outputs`、`comfyui_mask_mode`(ADR-0013)
 * - SD WebUI: `sdwebui_seed`、`sdwebui_task_id`、`sdwebui_request`(ADR-0038 3章)
 * サーバーはこれらをクライアントからの入力として受け付けない(422)ため、「同じ設定で再実行」で
 * フォームへ戻す前に必ず通す。`seed`(公開パラメーターとしての素の値)はそのまま残る。
 */
export function omitServerOnlyParams(params: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (SERVER_ONLY_PARAM_PREFIXES.some((prefix) => key.startsWith(prefix))) continue
    result[key] = value
  }
  return result
}

/**
 * 「同じ設定で再実行」用に、`run.params` からサーバー専有のキーを取り除いた上で、実際に使った
 * seed(`comfyui_seed` / `sdwebui_seed`)があれば `seed` として固定する(同じ画像を再現できるように。
 * ランダムに戻すのは seed の専用欄のトグル1つ。ADR-0013 フォローアップ)。
 */
export function paramsForRerun(params: Record<string, unknown>): Record<string, unknown> {
  const result = omitServerOnlyParams(params)
  for (const key of SERVER_SEED_KEYS) {
    const seed = params[key]
    if (typeof seed === 'number') {
      result.seed = seed
      break
    }
  }
  return result
}

/** buildParams の結果に `size` を足す(unspecified なら足さない)。 */
export function withSizeParam(
  params: Record<string, string | number | boolean>,
  sizeParam: string | undefined,
): Record<string, string | number | boolean> {
  if (sizeParam === undefined) return params
  return { ...params, size: sizeParam }
}

/**
 * 組み立て済みの params(型変換済み・未指定は欠落)から、フォーム制御用の
 * RawParamValues(文字列表現)へ戻す。「同じ設定で再実行」でのプリフィルに使う。
 */
export function toRawParamValues(
  defs: ParamDef[],
  params: Record<string, string | number | boolean>,
): RawParamValues {
  const raw: RawParamValues = {}
  for (const def of defs) {
    const value = params[def.name]
    if (value === undefined) {
      raw[def.name] = unspecifiedRawValue(def.type)
    } else {
      raw[def.name] = String(value)
    }
  }
  return raw
}

/**
 * モデル(または operation)切り替え後、新しい defs に存在しない、または
 * enum の選択肢に無い値を「未指定」に戻す(キーを落とす)。
 * 例: gpt-image-2.5 で quality=xhigh を選んだ後に gpt-image-2 へ切り替えた場合。
 */
export function sanitizeRawValues(defs: ParamDef[], raw: RawParamValues): RawParamValues {
  const result: RawParamValues = {}
  const defsByName = new Map(defs.map((d) => [d.name, d]))

  for (const [name, value] of Object.entries(raw)) {
    const def = defsByName.get(name)
    if (def === undefined) continue
    if (value === UNSPECIFIED || value === '') {
      result[name] = value
      continue
    }
    if (def.type === 'enum' && def.choices !== null && def.choices !== undefined) {
      if (!def.choices.includes(value)) continue
    }
    result[name] = value
  }

  return result
}

/**
 * sanitizeRawValues の前後を比べて、「指定されていたのに落ちた」パラメータ名を返す。
 * 入力画像の有無で operation が変わったとき、またはモデルを切り替えたときに capabilities が
 * 変わり、値が使えなくなることがある。そのとき利用者へ小さく知らせるために使う
 * (例: quality=xhigh を選んだ後、xhigh の無いモデルに切り替えると落ちる)。
 */
export function findDroppedParamNames(prev: RawParamValues, next: RawParamValues): string[] {
  return Object.keys(prev).filter((name) => {
    const prevValue = prev[name]
    const wasSpecified = prevValue !== undefined && prevValue !== UNSPECIFIED && prevValue !== ''
    return wasSpecified && next[name] === undefined
  })
}
