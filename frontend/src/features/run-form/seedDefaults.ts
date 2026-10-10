/**
 * seed 専用入力欄(`ParamDef.widget === 'seed'`。ADR-0013 フォローアップ)の初期値埋め。
 * 純粋関数のみ(副作用なし)。モード(`seedModePrefs.ts` でブラウザに記憶する、ランダム/固定)が
 * 「固定」で、まだ値が無いとき(新規フォーム、seed を持つモデル/ワークフローへの切り替え直後、
 * seed の無い下書きの復元)に限って `randomFn()` で具体的な数値を埋める。
 * 値が既にある場合(「同じ設定で再実行」(`paramsForRerun`)が固定した seed、seed 入りの
 * 下書きの復元)はモードに関わらず変更しない(ただし seed の欄の上限を超える値は、固定のとき
 * 範囲内の乱数に置き換える)。モードが「ランダム」のときは何もしない
 * (既存の raw の値をそのまま残す。既定では空文字列 = ランダム)。
 */
import type { ParamDef } from '../../api/client'
import type { RawParamValues } from './paramsBuilder'
import { isSeedRandom, randomSeedValue, seedMaximum } from './seedRandom'
import type { SeedMode } from './seedModePrefs'

export function fillSeedDefaults(
  defs: ParamDef[],
  raw: RawParamValues,
  mode: SeedMode,
  /** 0〜`maximum` の乱数を返す(既定は crypto の乱数)。`maximum` は seed の欄の上限。 */
  randomFn: (maximum: number) => number = (maximum) => randomSeedValue(undefined, maximum),
): RawParamValues {
  if (mode !== 'fixed') return raw

  let changed = false
  const next: RawParamValues = { ...raw }
  for (const def of defs) {
    if (def.widget !== 'seed') continue
    const current = next[def.name] ?? ''
    const maximum = seedMaximum(def)
    // 値が無いときに加えて、別のプロバイダーから持ち込んだ上限を超える値(ComfyUI の 2^53 未満の
    // seed を、2^32 未満の SD WebUI へ持ち込んだなど)も、範囲内の乱数に置き換える。
    if (isSeedRandom(current) || Number(current) > maximum) {
      next[def.name] = String(randomFn(maximum))
      changed = true
    }
  }
  return changed ? next : raw
}
