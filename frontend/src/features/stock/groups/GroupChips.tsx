/**
 * ストックパネルのグループ絞り込み行(ADR-0022 4章)。見出し「グループ」+ 選択中グループの
 * 名前と「⋯」メニューは横スクロールしないヘッダー行に置き(パネル幅を超えてメニューが
 * 見えなくなるのを防ぐ)、「すべて」+ 各グループのチップ(横スクロール)+ 新規作成の「+」だけを
 * その下の帯に置く。「⋯」から名前の変更・削除のメニューを開く(`UserMenu` と同じ、外側クリック/
 * Esc で閉じるポップオーバー)。削除は既存の Asset 削除と同じ `ConfirmDialog` を通す。
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, createAssetGroup, deleteAssetGroup, updateAssetGroup } from '../../../api/client'
import { ConfirmDialog } from '../../../components/ConfirmDialog'
import { fmt, useI18n } from '../../../i18n'
import { invalidateAssetGroupQueries, useAssetGroups } from './assetGroupQueries'
import styles from './GroupChips.module.css'

interface GroupChipsProps {
  activeGroupId: string | null
  onSelectGroup: (groupId: string | null) => void
}

export function GroupChips({ activeGroupId, onSelectGroup }: GroupChipsProps) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const groupsQuery = useAssetGroups()
  const groups = groupsQuery.data?.items ?? []
  const activeGroup = groups.find((g) => g.id === activeGroupId) ?? null

  const [creating, setCreating] = useState(false)
  const [draftName, setDraftName] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [renameDraft, setRenameDraft] = useState('')
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)
  // 横スクロールする帯(チップ)の外側ラッパー。右端フェードの表示・非表示を
  // React の state ではなく DOM の data 属性で持つ(見た目だけの情報なので再レンダー不要)。
  const stripWrapperRef = useRef<HTMLDivElement | null>(null)
  const stripRef = useRef<HTMLDivElement | null>(null)

  // 選択中のグループが変わったら、開きっぱなしのメニュー・改名の途中状態を畳む
  // (レンダー中に前回値と比較して更新する。React 公式が勧める「prop の変化に応じて
  // state をリセットする」パターンで、useEffect を使わない)。
  const [lastActiveGroupId, setLastActiveGroupId] = useState(activeGroupId)
  if (activeGroupId !== lastActiveGroupId) {
    setLastActiveGroupId(activeGroupId)
    setMenuOpen(false)
    setRenaming(false)
    setFormError(null)
  }

  useEffect(() => {
    if (!menuOpen) return
    function handlePointerDown(e: PointerEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setMenuOpen(false)
    }
    window.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [menuOpen])

  // 帯がパネル幅より広い(=横スクロールできる)間だけ右端にフェードを出す(finding 3)。
  // ResizeObserver は DOM 側の実測値を扱う外部システムなので、ここは effect が適切。
  // 属性は ref 経由で直接書き換え、state は使わない(不要な再レンダーを避ける)。
  useLayoutEffect(() => {
    const strip = stripRef.current
    const wrapper = stripWrapperRef.current
    if (!strip || !wrapper) return

    function updateOverflow() {
      if (!strip || !wrapper) return
      wrapper.dataset.overflow = strip.scrollWidth > strip.clientWidth + 1 ? 'true' : 'false'
    }

    updateOverflow()
    const observer = new ResizeObserver(updateOverflow)
    observer.observe(strip)
    observer.observe(wrapper)
    return () => observer.disconnect()
  }, [groups.length, creating])

  const createMutation = useMutation({
    mutationFn: (name: string) => createAssetGroup(name),
    onSuccess: () => {
      setCreating(false)
      setDraftName('')
      setFormError(null)
      invalidateAssetGroupQueries(queryClient)
    },
    onError: (err: unknown) => setFormError(err instanceof ApiError ? err.message : t.stock.groups.createFailed),
  })

  const renameMutation = useMutation({
    mutationFn: (name: string) => updateAssetGroup(activeGroupId as string, name),
    onSuccess: () => {
      setRenaming(false)
      setFormError(null)
      invalidateAssetGroupQueries(queryClient)
    },
    onError: (err: unknown) => setFormError(err instanceof ApiError ? err.message : t.stock.groups.renameFailed),
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteAssetGroup(activeGroupId as string),
    onSuccess: () => {
      setDeleteConfirmOpen(false)
      invalidateAssetGroupQueries(queryClient)
      onSelectGroup(null)
    },
    onError: (err: unknown) => {
      setDeleteConfirmOpen(false)
      setFormError(err instanceof ApiError ? err.message : t.stock.groups.deleteFailed)
    },
  })

  function submitCreate() {
    const name = draftName.trim()
    if (!name) return
    createMutation.mutate(name)
  }

  function submitRename() {
    const name = renameDraft.trim()
    if (!name) return
    renameMutation.mutate(name)
  }

  return (
    <div className={styles.wrap}>
      {/* 見出しと、選択中グループの名前・「⋯」メニューは横スクロール帯の外に置く
          (finding 1, 2)。メニューはここを起点に開くので、帯の overflow-x に切り取られない。 */}
      <div className={styles.groupsHeader}>
        <span className={styles.groupsHeadingText} id="stock-groups-heading">
          {t.stock.groups.heading}
        </span>
        {activeGroup && (
          <div className={styles.activeGroupControls}>
            <span className={styles.activeGroupName}>{activeGroup.name}</span>
            <button
              type="button"
              className={styles.menuTrigger}
              aria-label={t.stock.groups.menu}
              aria-haspopup="true"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
            >
              ⋯
            </button>
            {menuOpen && (
              <div className={styles.menu} role="menu" ref={menuRef}>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setRenameDraft(activeGroup.name)
                    setRenaming(true)
                    setMenuOpen(false)
                  }}
                >
                  {t.stock.groups.rename}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className={styles.dangerItem}
                  onClick={() => {
                    setDeleteConfirmOpen(true)
                    setMenuOpen(false)
                  }}
                >
                  {t.stock.groups.delete}
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      <div className={styles.stripWrapper} ref={stripWrapperRef}>
        <div className={styles.row} role="group" aria-labelledby="stock-groups-heading" ref={stripRef}>
          <button
            type="button"
            className={styles.chip}
            data-active={activeGroupId === null}
            onClick={() => onSelectGroup(null)}
          >
            {t.stock.groups.all}
          </button>

          {groups.map((group) => (
            <button
              key={group.id}
              type="button"
              className={styles.chip}
              data-active={activeGroupId === group.id}
              onClick={() => onSelectGroup(group.id)}
            >
              {group.name}
              <span className={styles.countBadge}>{group.member_count}</span>
            </button>
          ))}

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
                  setFormError(null)
                }
              }}
            />
          ) : (
            <button
              type="button"
              className={styles.addChip}
              aria-label={t.stock.groups.new}
              onClick={() => setCreating(true)}
            >
              +
            </button>
          )}
        </div>
      </div>

      {renaming && activeGroup && (
        <div className={styles.renameRow}>
          <input
            autoFocus
            className={styles.nameInput}
            value={renameDraft}
            onChange={(e) => setRenameDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submitRename()
              if (e.key === 'Escape') setRenaming(false)
            }}
          />
        </div>
      )}

      {formError && <p className={styles.error}>{formError}</p>}

      <ConfirmDialog
        open={deleteConfirmOpen}
        message={activeGroup ? fmt(t.stock.groups.deleteConfirm, { name: activeGroup.name }) : ''}
        confirmLabel={t.stock.groups.delete}
        onConfirm={() => deleteMutation.mutate()}
        onCancel={() => setDeleteConfirmOpen(false)}
      />
    </div>
  )
}
