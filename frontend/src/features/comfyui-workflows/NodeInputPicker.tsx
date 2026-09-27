/**
 * 差し込み先(node, input)を1つ選ぶ小さな部品。「ノード」と「入力名」の2段階の <select> で
 * 指定する(ADR-0013)。選択肢は analyze の `nodes` から作る。
 */
import { findNode, inputOptionLabel, nodeOptionLabel, type InputRefValue } from './nodeOptions'
import type { ComfyNodeInfo } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import styles from './NodeInputPicker.module.css'

interface NodeInputPickerProps {
  label: string
  nodes: ComfyNodeInfo[]
  value: InputRefValue | null
  onChange: (ref: InputRefValue | null) => void
  /** 「なし」を選べるか(既定は選べる)。 */
  allowNone?: boolean
  disabled?: boolean
}

export function NodeInputPicker({
  label,
  nodes,
  value,
  onChange,
  allowNone = true,
  disabled = false,
}: NodeInputPickerProps) {
  const { t } = useI18n()
  const selectedNodeId = value?.node ?? ''
  const selectedInput = value?.input ?? ''
  const selectedNode = findNode(nodes, selectedNodeId || null)
  const inputs = selectedNode?.inputs ?? []

  function handleNodeChange(nodeId: string) {
    if (!nodeId) {
      onChange(null)
      return
    }
    const node = findNode(nodes, nodeId)
    const firstInput = node?.inputs?.[0]?.name
    onChange(firstInput ? { node: nodeId, input: firstInput } : { node: nodeId, input: '' })
  }

  function handleInputChange(inputName: string) {
    if (!selectedNodeId || !inputName) return
    onChange({ node: selectedNodeId, input: inputName })
  }

  return (
    <div className={styles.picker}>
      <span className={styles.label}>{label}</span>
      <div className={styles.row}>
        <select
          aria-label={fmt(t.comfyui.nodePicker.nodeAriaLabel, { label })}
          className={styles.select}
          value={selectedNodeId}
          disabled={disabled}
          onChange={(e) => handleNodeChange(e.target.value)}
        >
          {allowNone && <option value="">{t.comfyui.nodePicker.none}</option>}
          {nodes.map((node) => (
            <option key={node.id} value={node.id}>
              {nodeOptionLabel(node)}
            </option>
          ))}
        </select>
        <select
          aria-label={fmt(t.comfyui.nodePicker.inputAriaLabel, { label })}
          className={styles.select}
          value={selectedInput}
          disabled={disabled || !selectedNodeId}
          onChange={(e) => handleInputChange(e.target.value)}
        >
          {inputs.length === 0 && <option value="">-</option>}
          {inputs.map((input) => (
            <option key={input.name} value={input.name}>
              {inputOptionLabel(input)}
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}
