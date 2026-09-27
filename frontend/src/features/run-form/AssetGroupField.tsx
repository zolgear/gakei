/**
 * 生成フォームの「グループ」(ADR-0022 4章「生成時の指定」)。モデルの直下に置く。
 * 選択肢は「なし」と各グループ(一覧の並び=`updated_at` の新しい順)。新規作成は選択肢にせず、
 * 選択欄の右の「+」で行う(ストックの「グループなし」の見出しの「+」と同じ部品
 * `NewGroupInline`)。押すと下に名前の入力を出し、Enter で作成してそのグループを選ぶ。
 * Escape か「+」をもう一度押すと取り消す。値そのものは `useRunFormLogic` が持つ。
 */
import { useQueryClient } from '@tanstack/react-query'
import type { AssetGroupListResponse, AssetGroupRow } from '../../api/client'
import { useI18n } from '../../i18n'
import { ASSET_GROUPS_QUERY_KEY } from '../stock/groups/assetGroupQueries'
import { NewGroupButton, NewGroupNameInput } from '../stock/groups/NewGroupInline'
import { useNewGroupInline } from '../stock/groups/useNewGroupInline'
import { prependAssetGroup } from './assetGroupSelection'
import fieldStyles from './ParamField.module.css'
import styles from './AssetGroupField.module.css'

interface AssetGroupFieldProps {
  /** 解決済みのグループ id(null は「なし」)。 */
  value: string | null
  groups: AssetGroupRow[]
  onChange: (id: string | null) => void
  /** 配置側のグリッドでの幅(下段配置ではモデルと同じく 2 列にまたがる)。 */
  className?: string
}

export function AssetGroupField({ value, groups, onChange, className }: AssetGroupFieldProps) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const newGroup = useNewGroupInline((group) => {
    // 再取得を待たずに選択を解決できるよう、一覧のキャッシュへ先に差し込む(作り直しは共通部品が行う)。
    queryClient.setQueryData<AssetGroupListResponse>(ASSET_GROUPS_QUERY_KEY, (old) =>
      old ? { ...old, items: prependAssetGroup(old.items ?? [], group) } : old,
    )
    onChange(group.id)
  })

  return (
    <div className={className ? `${styles.root} ${className}` : styles.root}>
      <div className={fieldStyles.field}>
        <label htmlFor="asset-group">{t.runForm.group.label}</label>
        <div className={styles.row}>
          <select
            id="asset-group"
            className={styles.select}
            value={value ?? ''}
            onChange={(e) => onChange(e.target.value === '' ? null : e.target.value)}
          >
            <option value="">{t.runForm.group.none}</option>
            {groups.map((group) => (
              <option key={group.id} value={group.id}>
                {group.name}
              </option>
            ))}
          </select>
          <NewGroupButton state={newGroup} className={styles.addButton} />
        </div>
      </div>
      {/* `.field input` の見た目を受けないよう、名前の入力は `.field` の外に置く(ストックと同じ見た目)。 */}
      <NewGroupNameInput state={newGroup} inputClassName={styles.nameInput} stopKeyPropagation />
    </div>
  )
}
