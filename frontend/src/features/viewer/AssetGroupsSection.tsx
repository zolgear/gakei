/**
 * ビューアの「グループ」節(ADR-0022 4章)。所属グループをチップで並べ、各チップの × で
 * (この Asset だけを)外す。「追加」で `AddToGroupPopover` を開く(既に入っているグループは
 * 選べないよう disabled にする)。
 */
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, removeAssetsFromGroup, type AssetGroupRef } from '../../api/client'
import { AddToGroupPopover } from '../stock/groups/AddToGroupPopover'
import { invalidateAssetGroupQueries } from '../stock/groups/assetGroupQueries'
import { fmt, useI18n } from '../../i18n'
import styles from './AssetGroupsSection.module.css'

export interface AssetGroupsSectionProps {
  assetId: string
  groups: AssetGroupRef[]
}

export function AssetGroupsSection({ assetId, groups }: AssetGroupsSectionProps) {
  const { t } = useI18n()
  const queryClient = useQueryClient()

  const removeMutation = useMutation({
    mutationFn: (groupId: string) => removeAssetsFromGroup(groupId, [assetId]),
    onSuccess: () => invalidateAssetGroupQueries(queryClient, [assetId]),
  })

  const disabledGroupIds = new Set(groups.map((g) => g.id))

  return (
    <div className={styles.section}>
      <h3 className={styles.heading}>{t.viewer.groups.heading}</h3>

      {groups.length === 0 ? (
        <p className={styles.empty}>{t.viewer.groups.empty}</p>
      ) : (
        <div className={styles.chips}>
          {groups.map((group) => (
            <span key={group.id} className={styles.chip}>
              {group.name}
              <button
                type="button"
                className={styles.removeButton}
                aria-label={fmt(t.viewer.groups.remove, { name: group.name })}
                disabled={removeMutation.isPending}
                onClick={() => removeMutation.mutate(group.id)}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      {removeMutation.isError && (
        <p className={styles.error}>
          {removeMutation.error instanceof ApiError ? removeMutation.error.message : t.stock.groups.removeFailed}
        </p>
      )}

      <AddToGroupPopover
        assetIds={[assetId]}
        disabledGroupIds={disabledGroupIds}
        triggerLabel={t.viewer.groups.add}
        triggerClassName={styles.addButton}
        placement="down"
      />
    </div>
  )
}
