/**
 * 公開パラメーターの表(ADR-0013)。行は analyze の `candidate_params` から作る
 * (`buildExposedParamRows`)。既定では公開しない。狭い幅では1カラムに積む(CLAUDE.md)。
 */
import {
  formatChoicesInput,
  formatDefaultInput,
  parseChoicesInput,
  parseDefaultInput,
  parseNumberInput,
  validateExposedParamName,
  type ExposedParamRow,
} from './exposedParamsForm'
import { nodeOptionLabel } from './nodeOptions'
import type { ComfyNodeInfo, ComfyParamType } from '../../api/client'
import { fmt, useI18n, type Messages } from '../../i18n'
import styles from './ExposedParamsTable.module.css'

const TYPE_OPTIONS: ComfyParamType[] = ['text', 'int', 'float', 'bool', 'enum']

function typeLabels(t: Messages): Record<ComfyParamType, string> {
  return t.comfyui.exposedParamsTable.typeLabels
}

interface ExposedParamsTableProps {
  rows: ExposedParamRow[]
  nodes: ComfyNodeInfo[]
  onChange: (index: number, row: ExposedParamRow) => void
}

export function ExposedParamsTable({ rows, nodes, onChange }: ExposedParamsTableProps) {
  const { t } = useI18n()
  if (rows.length === 0) {
    return <p className={styles.empty}>{t.comfyui.exposedParamsTable.empty}</p>
  }

  function update(index: number, patch: Partial<ExposedParamRow>) {
    onChange(index, { ...rows[index], ...patch })
  }

  return (
    <div className={styles.table}>
      {rows.map((row, index) => {
        const node = nodes.find((n) => n.id === row.node)
        const nameError = row.enabled ? validateExposedParamName(row.name) : null
        return (
          <div key={`${row.node}:${row.input}`} className={styles.row} data-enabled={row.enabled}>
            <div className={styles.rowHead}>
              <label className={styles.enableToggle}>
                <input
                  type="checkbox"
                  checked={row.enabled}
                  onChange={(e) => update(index, { enabled: e.target.checked })}
                />
                {t.comfyui.exposedParamsTable.enable}
              </label>
              <span className={styles.sourceRef} title={fmt(t.comfyui.exposedParamsTable.sourceRefTitle, { node: row.node, input: row.input })}>
                {node ? nodeOptionLabel(node) : row.node} / {row.input}
              </span>
            </div>

            {row.enabled && (
              <div className={styles.fields}>
                <div className={styles.field}>
                  <label>{t.comfyui.exposedParamsTable.nameLabel}</label>
                  <input
                    value={row.name}
                    onChange={(e) => update(index, { name: e.target.value })}
                  />
                  {nameError && <p className={styles.fieldError}>{nameError}</p>}
                </div>

                <div className={styles.field}>
                  <label>{t.comfyui.exposedParamsTable.displayLabelLabel}</label>
                  <input value={row.label} onChange={(e) => update(index, { label: e.target.value })} />
                </div>

                <div className={styles.field}>
                  <label>{t.comfyui.exposedParamsTable.typeLabel}</label>
                  <select
                    value={row.type}
                    onChange={(e) => update(index, { type: e.target.value as ComfyParamType })}
                  >
                    {TYPE_OPTIONS.map((type) => (
                      <option key={type} value={type}>
                        {typeLabels(t)[type]}
                      </option>
                    ))}
                  </select>
                </div>

                <div className={styles.field}>
                  <label>{t.comfyui.exposedParamsTable.defaultLabel}</label>
                  {row.type === 'bool' ? (
                    <select
                      value={row.default === true ? 'true' : row.default === false ? 'false' : ''}
                      onChange={(e) =>
                        update(index, { default: e.target.value === '' ? null : e.target.value === 'true' })
                      }
                    >
                      <option value="">{t.comfyui.exposedParamsTable.defaultPlaceholder}</option>
                      <option value="true">true</option>
                      <option value="false">false</option>
                    </select>
                  ) : (
                    <input
                      type={row.type === 'int' || row.type === 'float' ? 'number' : 'text'}
                      placeholder={t.comfyui.exposedParamsTable.defaultPlaceholder}
                      value={formatDefaultInput(row.default)}
                      onChange={(e) => update(index, { default: parseDefaultInput(row.type, e.target.value) })}
                    />
                  )}
                </div>

                {(row.type === 'int' || row.type === 'float') && (
                  <>
                    <div className={styles.field}>
                      <label>{t.comfyui.exposedParamsTable.minimumLabel}</label>
                      <input
                        type="number"
                        value={formatDefaultInput(row.minimum)}
                        onChange={(e) => update(index, { minimum: parseNumberInput(e.target.value) })}
                      />
                    </div>
                    <div className={styles.field}>
                      <label>{t.comfyui.exposedParamsTable.maximumLabel}</label>
                      <input
                        type="number"
                        value={formatDefaultInput(row.maximum)}
                        onChange={(e) => update(index, { maximum: parseNumberInput(e.target.value) })}
                      />
                    </div>
                    {row.type === 'float' && (
                      <div className={styles.field}>
                        <label>{t.comfyui.exposedParamsTable.stepLabel}</label>
                        <input
                          type="number"
                          value={formatDefaultInput(row.step)}
                          onChange={(e) => update(index, { step: parseNumberInput(e.target.value) })}
                        />
                      </div>
                    )}
                  </>
                )}

                {row.type === 'enum' && (
                  <div className={`${styles.field} ${styles.wide}`}>
                    <label>{t.comfyui.exposedParamsTable.choicesLabel}</label>
                    <input
                      value={formatChoicesInput(row.choices)}
                      onChange={(e) => update(index, { choices: parseChoicesInput(e.target.value) })}
                    />
                  </div>
                )}

                {row.type === 'text' && (
                  <div className={styles.field}>
                    <label>{t.comfyui.exposedParamsTable.maxLengthLabel}</label>
                    <input
                      type="number"
                      value={formatDefaultInput(row.maxLength)}
                      onChange={(e) => update(index, { maxLength: parseNumberInput(e.target.value) })}
                    />
                  </div>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
