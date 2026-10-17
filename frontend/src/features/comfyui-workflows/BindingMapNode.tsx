/**
 * 差し込み先の図の箱(ADR-0052 4章)。単独のノードはタイトル・クラス名・短い id、束は代表の名前と
 * 「×N」と中のクラス名。下に役割の札を並べる。入力の行(ポート)は描かない(入力は横のパネルで選ぶ)。
 *
 * 状態(出力につながる / 表示だけ / 使われない)は枠の線と濃さで描き分ける。
 */
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { MapNodeData } from './bindingMapView'
import styles from './BindingMap.module.css'

export function BindingMapNode({ data }: NodeProps) {
  const d = data as MapNodeData
  return (
    <div
      className={styles.box}
      data-state={d.state}
      data-on-path={d.onPath || undefined}
      data-evidence={d.evidence || undefined}
      data-selected={d.selected || undefined}
      data-pickable={d.pickable === null ? undefined : String(d.pickable)}
      title={d.tooltip}
    >
      <Handle type="target" position={Position.Left} className={styles.handle} isConnectable={false} />
      <div className={styles.boxHead}>
        <span className={styles.boxTitle}>{d.title}</span>
        {d.count > 1 && <span className={styles.boxCount}>{d.countLabel}</span>}
      </div>
      <div className={styles.boxSub}>{d.sub}</div>
      {d.badges.length > 0 && (
        <div className={styles.badges}>
          {d.badges.map((badge, i) => (
            <span key={`${badge.label}-${i}`} className={styles.roleBadge} data-tone={badge.tone}>
              {badge.label}
            </span>
          ))}
        </div>
      )}
      <Handle type="source" position={Position.Right} className={styles.handle} isConnectable={false} />
    </div>
  )
}
