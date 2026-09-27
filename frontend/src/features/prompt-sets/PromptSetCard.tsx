/** プロンプトセット1件(アコーディオン)。名前変更・削除、項目の追加・編集・削除・並べ替え。 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  addPromptSetItem,
  deletePromptSet,
  deletePromptSetItem,
  updatePromptSet,
  updatePromptSetItem,
  ApiError,
  type PromptSetItemResponse,
  type PromptSetResponse,
} from '../../api/client'
import { reorderTargetPosition } from './promptSetOrdering'
import { normalizeLabel, validateItemLabel, validateItemText, validateSetName } from './promptSetValidation'
import { AutoGrowTextarea } from './AutoGrowTextarea'
import { Modal } from '../../components/Modal'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { fmt, useI18n } from '../../i18n'
import styles from './PromptSetCard.module.css'

type PendingDelete = { kind: 'set' } | { kind: 'item'; item: PromptSetItemResponse }

interface PromptSetCardProps {
  set: PromptSetResponse
  onLoadPrompt: (text: string) => void
}

function invalidatePromptSets(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ['prompt-sets'] })
}

/** サイドバー(288px)では編集がつらいので、モバイルでは項目の編集を常にモーダルにする。 */
function isMobileViewport(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches
}

export function PromptSetCard({ set, onLoadPrompt }: PromptSetCardProps) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const items = (set.items ?? []).slice().sort((a, b) => a.position - b.position)

  const [expanded, setExpanded] = useState(false)
  const [isRenaming, setIsRenaming] = useState(false)
  const [nameDraft, setNameDraft] = useState(set.name)
  const [isAddingItem, setIsAddingItem] = useState(false)
  const [newLabel, setNewLabel] = useState('')
  const [newText, setNewText] = useState('')
  const [editingItemId, setEditingItemId] = useState<string | null>(null)
  const [editLabel, setEditLabel] = useState('')
  const [editText, setEditText] = useState('')
  const [error, setError] = useState<string | null>(null)
  // 項目の追加・編集フォームを、サイドバー内にインラインで出すか、モーダルで大きく出すか。
  const [formModalOpen, setFormModalOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null)

  const renameMutation = useMutation({
    mutationFn: () => updatePromptSet(set.id, { name: nameDraft.trim() }),
    onSuccess: () => {
      setIsRenaming(false)
      setError(null)
      invalidatePromptSets(queryClient)
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : t.promptSets.card.renameFailed),
  })

  const deleteSetMutation = useMutation({
    mutationFn: () => deletePromptSet(set.id),
    onSuccess: () => {
      setPendingDelete(null)
      invalidatePromptSets(queryClient)
    },
    onError: (err: unknown) => {
      setPendingDelete(null)
      setError(err instanceof ApiError ? err.message : t.promptSets.card.deleteFailed)
    },
  })

  const addItemMutation = useMutation({
    mutationFn: () =>
      addPromptSetItem(set.id, { label: normalizeLabel(newLabel), text: newText.trim() }),
    onSuccess: () => {
      setIsAddingItem(false)
      setNewLabel('')
      setNewText('')
      setError(null)
      setFormModalOpen(false)
      invalidatePromptSets(queryClient)
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : t.promptSets.card.addItemFailed),
  })

  const updateItemMutation = useMutation({
    mutationFn: (vars: { itemId: string; label?: string | null; text?: string; position?: number }) =>
      updatePromptSetItem(set.id, vars.itemId, {
        ...(vars.label !== undefined ? { label: vars.label } : {}),
        ...(vars.text !== undefined ? { text: vars.text } : {}),
        ...(vars.position !== undefined ? { position: vars.position } : {}),
      }),
    onSuccess: () => {
      setEditingItemId(null)
      setError(null)
      setFormModalOpen(false)
      invalidatePromptSets(queryClient)
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : t.promptSets.card.updateFailed),
  })

  const deleteItemMutation = useMutation({
    mutationFn: (itemId: string) => deletePromptSetItem(set.id, itemId),
    onSuccess: () => {
      setPendingDelete(null)
      invalidatePromptSets(queryClient)
    },
    onError: (err: unknown) => {
      setPendingDelete(null)
      setError(err instanceof ApiError ? err.message : t.promptSets.card.deleteFailed)
    },
  })

  function handleRenameSubmit() {
    const check = validateSetName(nameDraft)
    if (!check.valid) {
      setError(check.error ?? null)
      return
    }
    renameMutation.mutate()
  }

  function handleAddItemSubmit() {
    const textCheck = validateItemText(newText.trim())
    const labelCheck = validateItemLabel(newLabel)
    if (!textCheck.valid) return setError(textCheck.error ?? null)
    if (!labelCheck.valid) return setError(labelCheck.error ?? null)
    addItemMutation.mutate()
  }

  function startAddItem() {
    setIsAddingItem(true)
    setEditingItemId(null)
    setNewLabel('')
    setNewText('')
    setError(null)
    setFormModalOpen(isMobileViewport())
  }

  function startEditItem(item: PromptSetItemResponse) {
    setEditingItemId(item.id)
    setIsAddingItem(false)
    setEditLabel(item.label ?? '')
    setEditText(item.text)
    setError(null)
    setFormModalOpen(isMobileViewport())
  }

  function closeItemForm() {
    setIsAddingItem(false)
    setEditingItemId(null)
    setFormModalOpen(false)
    setError(null)
  }

  function handleEditItemSubmit(itemId: string) {
    const textCheck = validateItemText(editText.trim())
    const labelCheck = validateItemLabel(editLabel)
    if (!textCheck.valid) return setError(textCheck.error ?? null)
    if (!labelCheck.valid) return setError(labelCheck.error ?? null)
    updateItemMutation.mutate({ itemId, label: normalizeLabel(editLabel), text: editText.trim() })
  }

  function handleMove(item: PromptSetItemResponse, direction: 'up' | 'down') {
    const index = items.findIndex((i) => i.id === item.id)
    const target = reorderTargetPosition(index, direction, items.length)
    if (target === null) return
    updateItemMutation.mutate({ itemId: item.id, position: target })
  }

  function confirmPendingDelete() {
    if (pendingDelete === null) return
    if (pendingDelete.kind === 'set') deleteSetMutation.mutate()
    else deleteItemMutation.mutate(pendingDelete.item.id)
  }

  const isEditingExisting = editingItemId !== null
  const formActive = isAddingItem || isEditingExisting

  /** 項目の追加・編集フォーム本体。インライン表示とモーダル表示の両方から呼ぶ。 */
  function renderItemForm() {
    const label = isEditingExisting ? editLabel : newLabel
    const text = isEditingExisting ? editText : newText
    const pending = isEditingExisting ? updateItemMutation.isPending : addItemMutation.isPending
    const fieldKey = isEditingExisting ? editingItemId : 'new'

    function handleLabelChange(v: string) {
      if (isEditingExisting) setEditLabel(v)
      else setNewLabel(v)
    }
    function handleTextChange(v: string) {
      if (isEditingExisting) setEditText(v)
      else setNewText(v)
    }
    function handleSubmit() {
      if (isEditingExisting && editingItemId) handleEditItemSubmit(editingItemId)
      else handleAddItemSubmit()
    }

    return (
      <div className={styles.editForm}>
        <label htmlFor={`item-label-${fieldKey}`} className={styles.fieldLabel}>
          {t.promptSets.card.labelField}
        </label>
        <input
          id={`item-label-${fieldKey}`}
          value={label}
          onChange={(e) => handleLabelChange(e.target.value)}
          placeholder={t.promptSets.card.labelPlaceholder}
          className={styles.labelInput}
        />

        <label htmlFor={`item-text-${fieldKey}`} className={styles.fieldLabel}>
          {t.promptSets.card.textField}
        </label>
        <AutoGrowTextarea
          id={`item-text-${fieldKey}`}
          value={text}
          onChange={handleTextChange}
          placeholder={t.promptSets.card.textPlaceholder}
          minRows={4}
          autoFocus={formModalOpen}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
              e.preventDefault()
              handleSubmit()
            }
          }}
        />

        {error && <p className={styles.errorText}>{error}</p>}

        <div className={styles.editActions}>
          <button type="button" onClick={handleSubmit} disabled={pending}>
            {isEditingExisting ? t.promptSets.card.save : t.promptSets.card.add}
          </button>
          <button type="button" onClick={closeItemForm}>
            {t.promptSets.card.cancel}
          </button>
          {!formModalOpen && (
            <button
              type="button"
              className={styles.expandModalButton}
              onClick={() => setFormModalOpen(true)}
            >
              {t.promptSets.card.expandToEdit}
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <button
          type="button"
          className={styles.expandButton}
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          <span className={styles.chevron} data-open={expanded}>
            ▸
          </span>
          {isRenaming ? (
            <span className={styles.renameRow} onClick={(e) => e.stopPropagation()}>
              <input
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                className={styles.renameInput}
              />
            </span>
          ) : (
            <span className={styles.setName}>{set.name}</span>
          )}
          <span className={styles.itemCount}>{items.length}</span>
        </button>
        <div className={styles.headerActions}>
          {isRenaming ? (
            <>
              <button type="button" onClick={handleRenameSubmit} disabled={renameMutation.isPending}>
                {t.promptSets.card.save}
              </button>
              <button type="button" onClick={() => setIsRenaming(false)}>
                {t.promptSets.card.cancel}
              </button>
            </>
          ) : (
            <>
              <button type="button" onClick={() => setIsRenaming(true)}>
                {t.promptSets.card.rename}
              </button>
              <button
                type="button"
                onClick={() => setPendingDelete({ kind: 'set' })}
                disabled={deleteSetMutation.isPending}
              >
                {t.promptSets.card.delete}
              </button>
            </>
          )}
        </div>
      </div>

      {expanded && (
        <div className={styles.body}>
          {error && <p className={styles.errorText}>{error}</p>}

          <ul className={styles.itemList}>
            {items.map((item, index) => (
              <li key={item.id} className={styles.item}>
                {editingItemId === item.id && !formModalOpen ? (
                  renderItemForm()
                ) : (
                  <>
                    <button
                      type="button"
                      className={styles.itemLoadButton}
                      title={t.promptSets.card.appendToEnd}
                      onClick={() => onLoadPrompt(item.text)}
                    >
                      <span className={styles.itemLabel}>{item.label || item.text.slice(0, 24)}</span>
                      <span className={styles.itemText}>{item.text}</span>
                    </button>
                    <div className={styles.itemActions}>
                      <button
                        type="button"
                        onClick={() => handleMove(item, 'up')}
                        disabled={index === 0}
                        aria-label={t.promptSets.card.moveUp}
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        onClick={() => handleMove(item, 'down')}
                        disabled={index === items.length - 1}
                        aria-label={t.promptSets.card.moveDown}
                      >
                        ↓
                      </button>
                      <button type="button" onClick={() => startEditItem(item)} aria-label={t.promptSets.card.edit}>
                        {t.promptSets.card.edit}
                      </button>
                      <button
                        type="button"
                        onClick={() => setPendingDelete({ kind: 'item', item })}
                        aria-label={t.promptSets.card.delete}
                      >
                        {t.promptSets.card.delete}
                      </button>
                    </div>
                  </>
                )}
              </li>
            ))}
          </ul>

          {isAddingItem && !formModalOpen && renderItemForm()}
          {!formActive && (
            <button type="button" className={styles.addItemButton} onClick={startAddItem}>
              {t.promptSets.card.addItem}
            </button>
          )}
        </div>
      )}

      <Modal
        open={formModalOpen && formActive}
        title={isEditingExisting ? t.promptSets.card.editItemTitle : t.promptSets.card.addItemTitle}
        onClose={closeItemForm}
      >
        {renderItemForm()}
      </Modal>

      <ConfirmDialog
        open={pendingDelete !== null}
        message={
          pendingDelete?.kind === 'set'
            ? fmt(t.promptSets.card.deleteSetConfirm, { name: set.name })
            : t.promptSets.card.deleteItemConfirm
        }
        onConfirm={confirmPendingDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  )
}
