/**
 * ノードそのもの(入力ではなく)を複数選ぶための部品。出力ノード(SaveImage 等)の指定に使う。
 */
import { nodeOptionLabel } from './nodeOptions'
import type { ComfyNodeInfo } from '../../api/client'
import { useI18n } from '../../i18n'
import styles from './NodeMultiPicker.module.css'

interface NodeMultiPickerProps {
  label: string
  nodes: ComfyNodeInfo[]
  value: string[]
  onChange: (nodeIds: string[]) => void
}

export function NodeMultiPicker({ label, nodes, value, onChange }: NodeMultiPickerProps) {
  const { t } = useI18n()
  function toggle(nodeId: string, checked: boolean) {
    if (checked) {
      if (!value.includes(nodeId)) onChange([...value, nodeId])
    } else {
      onChange(value.filter((id) => id !== nodeId))
    }
  }

  return (
    <div className={styles.picker}>
      <span className={styles.label}>{label}</span>
      <div className={styles.list} role="group" aria-label={label}>
        {nodes.length === 0 && <p className={styles.empty}>{t.comfyui.nodePicker.noNodes}</p>}
        {nodes.map((node) => (
          <label key={node.id} className={styles.option}>
            <input
              type="checkbox"
              checked={value.includes(node.id)}
              onChange={(e) => toggle(node.id, e.target.checked)}
            />
            <span>{nodeOptionLabel(node)}</span>
          </label>
        ))}
      </div>
    </div>
  )
}
