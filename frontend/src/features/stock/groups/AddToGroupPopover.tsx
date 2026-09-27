/**
 * グループへの追加ポップオーバー(ADR-0022 4章)。ストックパネルの選択モードの下部
 * (`placement="up"`)と、ビューアの「グループ」節(`placement="down"`)の両方から使う。
 * グループ一覧をクリックすると即座に追加して閉じる。「新しいグループ…」はインライン入力を
 * 出し、Enter で作成してからそのグループに追加する。外側クリックと Esc で閉じる
 * (`UserMenu` と同じ)。
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, addAssetsToGroup, createAssetGroup } from '../../../api/client'
import { useI18n } from '../../../i18n'
import { isGroupAlreadyContaining } from './addToGroupPopoverLogic'
import { invalidateAssetGroupQueries, useAssetGroups } from './assetGroupQueries'
import styles from './AddToGroupPopover.module.css'

interface AddToGroupPopoverProps {
  /** 追加対象の Asset id(1件でも複数件でも良い)。 */
  assetIds: string[]
  /** すでにこの Asset が入っているグループ(ビューアの単体操作でだけ渡す)。 */
  disabledGroupIds?: ReadonlySet<string>
  triggerLabel: string
  triggerClassName?: string
  disabled?: boolean
  /** ポップオーバーを開く向き。フッターなど下端に近いトリガーは 'up' を渡す。 */
  placement?: 'up' | 'down'
  /** 追加(作成 + 追加を含む)に成功したときに呼ぶ(呼び出し側の選択解除などに使う)。 */
  onAdded?: () => void
}

export function AddToGroupPopover({
  assetIds,
  disabledGroupIds,
  triggerLabel,
  triggerClassName,
  disabled = false,
  placement = 'down',
  onAdded,
}: AddToGroupPopoverProps) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const rootRef = useRef<HTMLDivElement | null>(null)

  const [open, setOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [draftName, setDraftName] = useState('')
  const [error, setError] = useState<string | null>(null)

  const groupsQuery = useAssetGroups()
  const groups = groupsQuery.data?.items ?? []

  function close() {
    setOpen(false)
    setCreating(false)
    setDraftName('')
    setError(null)
  }

  useEffect(() => {
    if (!open) return
    function handlePointerDown(e: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) close()
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') close()
    }
    window.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
    // close は state の setter だけを呼ぶ安定した関数なので依存配列には含めない。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const addMutation = useMutation({
    mutationFn: (groupId: string) => addAssetsToGroup(groupId, assetIds),
    onSuccess: () => {
      invalidateAssetGroupQueries(queryClient, assetIds)
      close()
      onAdded?.()
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : t.stock.groups.addFailed),
  })

  const createAndAddMutation = useMutation({
    mutationFn: async (name: string) => {
      const group = await createAssetGroup(name)
      return addAssetsToGroup(group.id, assetIds)
    },
    onSuccess: () => {
      invalidateAssetGroupQueries(queryClient, assetIds)
      close()
      onAdded?.()
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : t.stock.groups.createFailed),
  })

  function submitCreate() {
    const name = draftName.trim()
    if (!name) return
    createAndAddMutation.mutate(name)
  }

  return (
    <div className={styles.root} ref={rootRef}>
      <button
        type="button"
        className={triggerClassName ?? styles.trigger}
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="true"
        aria-expanded={open}
      >
        {triggerLabel}
      </button>

      {open && (
        <div className={styles.popover} data-placement={placement} role="menu">
          {error && <p className={styles.error}>{error}</p>}
          {groups.length === 0 && !creating && <p className={styles.emptyText}>{t.stock.groups.noGroups}</p>}
          {groups.length > 0 && (
            <ul className={styles.list}>
              {groups.map((group) => {
                const isDisabled = isGroupAlreadyContaining(group.id, disabledGroupIds)
                return (
                  <li key={group.id}>
                    <button
                      type="button"
                      role="menuitem"
                      className={styles.groupItem}
                      disabled={isDisabled || addMutation.isPending}
                      onClick={() => addMutation.mutate(group.id)}
                    >
                      {group.name}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}

          {creating ? (
            <input
              autoFocus
              className={styles.nameInput}
              value={draftName}
              placeholder={t.stock.groups.newPlaceholder}
              onChange={(e) => setDraftName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submitCreate()
                if (e.key === 'Escape') {
                  setCreating(false)
                  setDraftName('')
                }
              }}
            />
          ) : (
            <button type="button" className={styles.newGroupButton} onClick={() => setCreating(true)}>
              {t.stock.groups.new}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
