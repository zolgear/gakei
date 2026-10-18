/**
 * 公開パラメーターの3段(ADR-0052 2章・4章)。推奨は最初から公開、その他は畳んで未チェック、
 * 公開しないものは理由つきで並べる(チェックは置かない)。段はサーバーの `param_tiers` が決める。
 */
import type { ComfyNodeInfo, ComfyParamTierInfo } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { tierExposedRows, type TieredRow } from './bindingCards'
import type { BindingsFormState } from './bindingsForm'
import type { ExposedParamRow } from './exposedParamsForm'
import { ExposedParamsTable } from './ExposedParamsTable'
import { NodeName } from './NodeName'
import styles from './BindingCards.module.css'

interface ExposedParamsTiersProps {
  rows: ExposedParamRow[]
  tiers: readonly ComfyParamTierInfo[] | null | undefined
  bindings: BindingsFormState
  nodes: ComfyNodeInfo[]
  onChange: (index: number, row: ExposedParamRow) => void
}

export function ExposedParamsTiers({ rows, tiers, bindings, nodes, onChange }: ExposedParamsTiersProps) {
  const { t } = useI18n()
  const c = t.comfyui.cards
  if (rows.length === 0) return <p className={styles.why}>{t.comfyui.exposedParamsTable.empty}</p>
  const tiered = tierExposedRows(rows, tiers, bindings)

  const table = (subset: TieredRow[]) => (
    <ExposedParamsTable
      rows={subset.map((item) => item.row)}
      nodes={nodes}
      onChange={(i, row) => onChange(subset[i].index, row)}
    />
  )

  return (
    <div className={styles.tiers}>
      <div className={styles.tier}>
        <h3 className={styles.tierTitle}>{c.tierRecommended}</h3>
        <p className={styles.why}>{c.tierRecommendedHelp}</p>
        {tiered.recommended.length > 0 ? table(tiered.recommended) : <p className={styles.why}>{c.tierEmpty}</p>}
      </div>
      {tiered.other.length > 0 && (
        <details className={styles.mini}>
          <summary>{fmt(c.tierOther, { count: tiered.other.length })}</summary>
          <p className={styles.why}>{c.tierOtherHelp}</p>
          {table(tiered.other)}
        </details>
      )}
      {tiered.hidden.length > 0 && (
        <details className={styles.mini}>
          <summary>{fmt(c.tierHidden, { count: tiered.hidden.length })}</summary>
          <ul className={styles.hiddenList}>
            {tiered.hidden.map(({ row, reason }) => (
              <li key={`${row.node}\u0000${row.input}`}>
                <NodeName id={row.node} node={nodes.find((n) => n.id === row.node)} input={row.input} />
                <span className={styles.why}>{reason ? c.hiddenReasons[reason] : c.hiddenReasonUnknown}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
