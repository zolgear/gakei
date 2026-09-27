/**
 * ビューアの「グループ」節(ADR-0022 4章)。1 つの Asset が属するグループは 1 つだけなので、
 * 所属グループを 1 つのチップで出し、× で外す。「変更」で `AddToGroupPopover` を開いて
 * 別のグループへ移す(今のグループは選べないよう disabled にする)。未所属なら
 * 「グループに入れる」で同じポップオーバーを開く。
 */
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, removeAssetsFromGroup, type AssetGroupRef } from '../../api/client'
import { AddToGroupPopover } from '../stock/groups/AddToGroupPopover'
import { invalidateAssetGroupQueries } from '../stock/groups/assetGroupQueries'
import { fmt, useI18n } from '../../i18n'
import styles from './AssetGroupsSection.module.css'

export interface AssetGroupsSectionProps {
  assetId: string
  group: AssetGroupRef | null
}

export function AssetGroupsSection({ assetId, group }: AssetGroupsSectionProps) {
  const { t } = useI18n()
  const queryClient = useQueryClient()

  const removeMutation = useMutation({
    mutationFn: (groupId: string) => removeAssetsFromGroup(groupId, [assetId]),
    onSuccess: () => invalidateAssetGroupQueries(queryClient, [assetId]),
  })

  const disabledGroupIds = group ? new Set([group.id]) : undefined

  return (
    <div className={styles.section}>
      <h3 className={styles.heading}>{t.viewer.groups.heading}</h3>

      <div className={styles.row}>
        {group ? (
          <span className={styles.chip}>
            {group.name}
            <button
              type="button"
              className={styles.removeButton}
              aria-label={fmt(t.viewer.groups.remove, { name: group.name })}
              title={fmt(t.viewer.groups.remove, { name: group.name })}
              disabled={removeMutation.isPending}
              onClick={() => removeMutation.mutate(group.id)}
            >
              ×
            </button>
          </span>
        ) : (
          <p className={styles.empty}>{t.viewer.groups.empty}</p>
        )}

        <AddToGroupPopover
          assetIds={[assetId]}
          disabledGroupIds={disabledGroupIds}
          triggerLabel={group ? t.viewer.groups.change : t.viewer.groups.assign}
          triggerClassName={styles.addButton}
          placement="down"
        />
      </div>

      {removeMutation.isError && (
        <p className={styles.error}>
          {removeMutation.error instanceof ApiError ? removeMutation.error.message : t.stock.groups.removeFailed}
        </p>
      )}
    </div>
  )
}
