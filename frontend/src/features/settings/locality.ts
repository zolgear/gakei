/**
 * 接続先(`locality`。ADR-0051 3章)の表示の純粋関数。値は「外部」(`external`)と「ローカル」
 * (`local`)。ComfyUI のワークフローだけは「接続の設定に従う」(`unset`)も選べる。
 * GAKEI は自動で判定せず、この値で動作も変えない。`ai.before_send` に載せて、判断は Mod に任せる。
 * 接続先の欄は、Mod を1つも有効にしていなくても出す。
 */
import type { Locality, WorkflowLocality } from '../../api/client'
import { fmt, msg } from '../../i18n'

/** 接続の値。並びは選択肢の順(外部 → ローカル)。 */
export const LOCALITY_VALUES: readonly Locality[] = ['external', 'local']
/** ワークフローの値。「接続の設定に従う」を先頭に出す(既定)。 */
export const WORKFLOW_LOCALITY_VALUES: readonly WorkflowLocality[] = ['unset', 'external', 'local']

export function isLocality(value: unknown): value is Locality {
  return typeof value === 'string' && (LOCALITY_VALUES as readonly string[]).includes(value)
}

export function isWorkflowLocality(value: unknown): value is WorkflowLocality {
  return typeof value === 'string' && (WORKFLOW_LOCALITY_VALUES as readonly string[]).includes(value)
}

/**
 * 選択肢・表示の名前。`unset`(ワークフローだけ)は「接続の設定に従う」。
 * `defaultValue` を渡すと、その値に「(既定)」を添える。
 */
export function localityLabel(value: WorkflowLocality, options: { defaultValue?: WorkflowLocality } = {}): string {
  const l = msg().settings.locality
  const base = value === 'unset' ? l.inherit : l[value]
  return options.defaultValue === value ? fmt(l.defaultSuffix, { label: base }) : base
}
