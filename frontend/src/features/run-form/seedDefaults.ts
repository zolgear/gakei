/**
 * seed 専用入力欄(`ParamDef.widget === 'seed'`。ADR-0013 フォローアップ)の初期値埋め。
 * 純粋関数のみ(副作用なし)。モード(`seedModePrefs.ts` でブラウザに記憶する、ランダム/固定)が
 * 「固定」で、まだ値が無いとき(新規フォーム、seed を持つモデル/ワークフローへの切り替え直後、
 * seed の無い下書きの復元)に限って `randomFn()` で具体的な数値を埋める。
 * 値が既にある場合(「同じ設定で再実行」(`paramsForRerun`)が固定した seed、seed 入りの
 * 下書きの復元)はモードに関わらず変更しない。モードが「ランダム」のときは何もしない
 * (既存の raw の値をそのまま残す。既定では空文字列 = ランダム)。
 */
import type { ParamDef } from '../../api/client'
import type { RawParamValues } from './paramsBuilder'
import { isSeedRandom, randomSeedValue } from './seedRandom'
import type { SeedMode } from './seedModePrefs'

export function fillSeedDefaults(
  defs: ParamDef[],
  raw: RawParamValues,
  mode: SeedMode,
  randomFn: () => number = randomSeedValue,
): RawParamValues {
  if (mode !== 'fixed') return raw

  let changed = false
  const next: RawParamValues = { ...raw }
  for (const def of defs) {
    if (def.widget !== 'seed') continue
    if (isSeedRandom(next[def.name] ?? '')) {
      next[def.name] = String(randomFn())
      changed = true
    }
  }
  return changed ? next : raw
}
