/**
 * ノードそのもの(入力ではなく)を1つだけ選ぶ部品。最終プロンプト(PE の出力)のノードの指定に
 * 使う(ADR-0030 1章)。選択肢はテンプレートのすべてのノードと「なし」で、種類では絞らない。
 * 見た目は `NodeInputPicker` の1段目と揃える。
 */
import { nodeOptionLabel } from './nodeOptions'
import type { ComfyNodeInfo } from '../../api/client'
import { useI18n } from '../../i18n'
import styles from './NodeInputPicker.module.css'

interface NodeSelectPickerProps {
  label: string
  nodes: ComfyNodeInfo[]
  value: string | null
  onChange: (nodeId: string | null) => void
}

export function NodeSelectPicker({ label, nodes, value, onChange }: NodeSelectPickerProps) {
  const { t } = useI18n()
  return (
    <div className={styles.picker}>
      <span className={styles.label}>{label}</span>
      <div className={styles.row}>
        <select
          aria-label={label}
          className={styles.select}
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value || null)}
        >
          <option value="">{t.comfyui.nodePicker.none}</option>
          {nodes.map((node) => (
            <option key={node.id} value={node.id}>
              {nodeOptionLabel(node)}
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}
