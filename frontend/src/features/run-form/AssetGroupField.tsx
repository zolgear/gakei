/**
 * 生成フォームの「グループ」(ADR-0022 4章「生成時の指定」)。選択肢は「なし」、各グループ
 * (一覧の並び=`updated_at` の新しい順)、「新しいグループ…」。「新しいグループ…」を選ぶと
 * 下に名前の入力欄を出し、Enter で作成してそのグループを選ぶ。Esc で取りやめて元の選択に戻す
 * (`AddToGroupPopover` のインライン作成と同じ流れ)。値そのものは `useRunFormLogic` が持つ。
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, createAssetGroup, type AssetGroupListResponse, type AssetGroupRow } from '../../api/client'
import { useI18n } from '../../i18n'
import { ASSET_GROUPS_QUERY_KEY } from '../stock/groups/assetGroupQueries'
import { NEW_ASSET_GROUP_OPTION, prependAssetGroup } from './assetGroupSelection'
import fieldStyles from './ParamField.module.css'
import styles from './AssetGroupField.module.css'

interface AssetGroupFieldProps {
  /** 解決済みのグループ id(null は「なし」)。 */
  value: string | null
  groups: AssetGroupRow[]
  onChange: (id: string | null) => void
}

export function AssetGroupField({ value, groups, onChange }: AssetGroupFieldProps) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)
  const [draftName, setDraftName] = useState('')
  const [error, setError] = useState<string | null>(null)

  function cancelCreate() {
    setCreating(false)
    setDraftName('')
    setError(null)
  }

  const createMutation = useMutation({
    mutationFn: (name: string) => createAssetGroup(name),
    onSuccess: (group) => {
      // 再取得を待たずに選択を解決できるよう、一覧のキャッシュへ先に差し込んでから作り直す。
      queryClient.setQueryData<AssetGroupListResponse>(ASSET_GROUPS_QUERY_KEY, (old) =>
        old ? { ...old, items: prependAssetGroup(old.items ?? [], group) } : old,
      )
      queryClient.invalidateQueries({ queryKey: ASSET_GROUPS_QUERY_KEY })
      onChange(group.id)
      cancelCreate()
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : t.stock.groups.createFailed),
  })

  function submitCreate() {
    const name = draftName.trim()
    if (!name || createMutation.isPending) return
    createMutation.mutate(name)
  }

  return (
    <div className={`${fieldStyles.field} ${styles.root}`}>
      <label htmlFor="asset-group">{t.runForm.group.label}</label>
      <select
        id="asset-group"
        className={styles.select}
        value={creating ? NEW_ASSET_GROUP_OPTION : (value ?? '')}
        onChange={(e) => {
          const next = e.target.value
          if (next === NEW_ASSET_GROUP_OPTION) {
            setCreating(true)
            return
          }
          cancelCreate()
          onChange(next === '' ? null : next)
        }}
      >
        <option value="">{t.runForm.group.none}</option>
        {groups.map((group) => (
          <option key={group.id} value={group.id}>
            {group.name}
          </option>
        ))}
        <option value={NEW_ASSET_GROUP_OPTION}>{t.stock.groups.new}</option>
      </select>
      {creating && (
        <input
          autoFocus
          className={styles.nameInput}
          value={draftName}
          maxLength={100}
          placeholder={t.stock.groups.newPlaceholder}
          aria-label={t.stock.groups.newPlaceholder}
          disabled={createMutation.isPending}
          onChange={(e) => setDraftName(e.target.value)}
          onKeyDown={(e) => {
            // IME の変換確定の Enter では作成しない。
            if (e.nativeEvent.isComposing) return
            if (e.key === 'Enter') {
              // 下段全体のキー操作(Ctrl+Enter の送信など)に渡さない。
              e.preventDefault()
              e.stopPropagation()
              submitCreate()
            } else if (e.key === 'Escape') {
              e.preventDefault()
              e.stopPropagation()
              cancelCreate()
            }
          }}
        />
      )}
      {error && <p className={styles.error}>{error}</p>}
    </div>
  )
}
