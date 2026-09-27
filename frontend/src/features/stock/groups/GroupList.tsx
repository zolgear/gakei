/**
 * ストックパネルのグループ絞り込み(ADR-0022 4章)。kind のチップ行と見分けがつくよう、
 * チップではなく折り畳める縦のリストにする。
 *
 * - 見出し行(常に表示): 開閉ボタン(「グループ」+ 現在の選択「すべて」/ グループ名と件数)と、
 *   グループ選択中だけ右端に「⋯」(名前の変更・削除)。「⋯」は折り畳み部分の外に置く。
 * - 本体(開いているときだけ): 「すべて」、各グループ(名前と件数)、末尾に「新しいグループ」
 *   (押すとその場で名前の入力)。縦スクロールのみで、横スクロールはさせない。
 *
 * 開閉状態は localStorage に持つ(`groupsOpenStorage.ts`)。グループを選んでも畳まない。
 * 「⋯」のメニューは `UserMenu` と同じ、外側クリック/Esc で閉じるポップオーバー。
 * 削除は既存の Asset 削除と同じ `ConfirmDialog` を通す。
 */
import { useEffect, useId, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, createAssetGroup, deleteAssetGroup, updateAssetGroup } from '../../../api/client'
import { ConfirmDialog } from '../../../components/ConfirmDialog'
import { fmt, useI18n } from '../../../i18n'
import { invalidateAssetGroupQueries, useAssetGroups } from './assetGroupQueries'
import { loadGroupsOpen, saveGroupsOpen } from './groupsOpenStorage'
import styles from './GroupList.module.css'

interface GroupListProps {
  activeGroupId: string | null
  onSelectGroup: (groupId: string | null) => void
}

export function GroupList({ activeGroupId, onSelectGroup }: GroupListProps) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const groupsQuery = useAssetGroups()
  const groups = groupsQuery.data?.items ?? []
  const activeGroup = groups.find((g) => g.id === activeGroupId) ?? null
  const bodyId = useId()

  // 初期値は localStorage から読む(effect で読み直さない)。
  const [open, setOpen] = useState(loadGroupsOpen)
  const [creating, setCreating] = useState(false)
  const [draftName, setDraftName] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [renameDraft, setRenameDraft] = useState('')
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)

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

  function toggleOpen() {
    const next = !open
    setOpen(next)
    saveGroupsOpen(next)
  }

  function cancelCreate() {
    setCreating(false)
    setDraftName('')
    setFormError(null)
  }

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
      <div className={styles.header}>
        <button
          type="button"
          className={styles.disclosure}
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={toggleOpen}
        >
          <span className={styles.chevron} aria-hidden="true">
            {open ? '▾' : '▸'}
          </span>
          <span className={styles.headingText}>{t.stock.groups.heading}</span>
          {activeGroup ? (
            <>
              <span className={styles.selectionName}>{activeGroup.name}</span>
              <span
                className={styles.countBadge}
                title={fmt(t.stock.groups.memberCount, { count: activeGroup.member_count })}
              >
                {activeGroup.member_count}
              </span>
            </>
          ) : (
            <span className={styles.selectionName}>{t.stock.groups.all}</span>
          )}
        </button>

        {/* 「⋯」は折り畳み部分の外(見出し行の右端)に置き、畳んでいても操作できるようにする。 */}
        {activeGroup && (
          <div className={styles.menuAnchor}>
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

      {renaming && activeGroup && (
        <input
          autoFocus
          className={styles.nameInput}
          value={renameDraft}
          aria-label={t.stock.groups.rename}
          onChange={(e) => setRenameDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submitRename()
            if (e.key === 'Escape') setRenaming(false)
          }}
        />
      )}

      {open && (
        <div className={styles.list} id={bodyId}>
          <button
            type="button"
            className={styles.row}
            aria-pressed={activeGroupId === null}
            data-active={activeGroupId === null}
            onClick={() => onSelectGroup(null)}
          >
            <span className={styles.rowName}>{t.stock.groups.all}</span>
          </button>

          {groups.map((group) => (
            <button
              key={group.id}
              type="button"
              className={styles.row}
              aria-pressed={activeGroupId === group.id}
              data-active={activeGroupId === group.id}
              onClick={() => onSelectGroup(group.id)}
            >
              <span className={styles.rowName}>{group.name}</span>
              <span
                className={styles.countBadge}
                title={fmt(t.stock.groups.memberCount, { count: group.member_count })}
              >
                {group.member_count}
              </span>
            </button>
          ))}

          {creating ? (
            <div className={styles.createRow}>
              <input
                autoFocus
                className={styles.nameInput}
                value={draftName}
                placeholder={t.stock.groups.newPlaceholder}
                aria-label={t.stock.groups.newPlaceholder}
                onChange={(e) => setDraftName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') submitCreate()
                  if (e.key === 'Escape') cancelCreate()
                }}
              />
            </div>
          ) : (
            <button type="button" className={styles.newRow} onClick={() => setCreating(true)}>
              <span className={styles.plus} aria-hidden="true">
                +
              </span>
              <span className={styles.rowName}>{t.stock.groups.new}</span>
            </button>
          )}
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
