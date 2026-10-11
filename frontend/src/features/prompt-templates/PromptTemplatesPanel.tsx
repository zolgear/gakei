/**
 * サイドバーの「プロンプトテンプレート」パネル(実験。パラメーターセットと並べる)。
 * 一覧(名前・スロット数・候補数・組み合わせ数)から、生成フォームで「使う」・編集・削除ができる。
 * 新規作成は名前だけを受けて空のテンプレートを作り、続けて編集のダイアログを開く。
 *
 * 「使う」は、パラメーターセットと同じく `requestFormLoad` にリクエストを積み、スタジオの
 * フォーム(`useRunFormLogic`)が消費してテンプレートモードに入る。スタジオの外からなら
 * /studio へ移る。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router'
import {
  ApiError,
  createPromptTemplate,
  deletePromptTemplate,
  getPromptTemplate,
  listPromptTemplates,
  type PromptTemplateSummary,
} from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { useRunFormContext } from '../../context/useRunFormContext'
import { fmt, useI18n } from '../../i18n'
import { PromptTemplateEditor } from './PromptTemplateEditor'
import {
  PROMPT_TEMPLATES_QUERY_KEY,
  emptyTemplateDraft,
  draftToWriteRequest,
  filterPromptTemplates,
} from './promptTemplates'
import styles from './PromptTemplates.module.css'

export function PromptTemplatesPanel() {
  const { t } = useI18n()
  const p = t.promptTemplates.panel
  const queryClient = useQueryClient()
  const { requestFormLoad } = useRunFormContext()
  const location = useLocation()
  const navigate = useNavigate()
  const query = useQuery({ queryKey: PROMPT_TEMPLATES_QUERY_KEY, queryFn: listPromptTemplates })
  const [search, setSearch] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [newName, setNewName] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<PromptTemplateSummary | null>(null)
  // どのテンプレートを生成フォームで使っているか(画面の見た目だけ。正はフォーム側の状態)。
  const [usingId, setUsingId] = useState<string | null>(null)

  const invalidate = () => queryClient.invalidateQueries({ queryKey: PROMPT_TEMPLATES_QUERY_KEY })

  const createMutation = useMutation({
    mutationFn: (name: string) => {
      if (name.trim() === '') throw new Error(p.nameRequired)
      return createPromptTemplate(draftToWriteRequest(emptyTemplateDraft(name)))
    },
    onSuccess: (created) => {
      setNewName(null)
      setError(null)
      invalidate()
      // 作った直後は中身が空なので、続けて編集を開く。
      setEditingId(created.id)
    },
    onError: (err: unknown) =>
      setError(err instanceof ApiError || err instanceof Error ? err.message : p.createFailed),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deletePromptTemplate(id),
    onSuccess: (_result, id) => {
      setError(null)
      // 使っていたテンプレートを消したら、フォームのテンプレートモードも解除する。
      if (usingId === id) {
        setUsingId(null)
        requestFormLoad({ kind: 'promptTemplate', template: null })
      }
      invalidate()
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : p.deleteFailed),
  })

  // 一覧は数だけを持つので、使うときに全体(スロット・候補・組み合わせ)を取り直す。
  const useMutationHandle = useMutation({
    mutationFn: (id: string) => getPromptTemplate(id),
    onSuccess: (template) => {
      setError(null)
      setUsingId(template.id)
      requestFormLoad({ kind: 'promptTemplate', template })
      if (location.pathname !== '/studio') navigate('/studio')
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : p.loadFailed),
  })

  function stopUsing() {
    setUsingId(null)
    requestFormLoad({ kind: 'promptTemplate', template: null })
  }

  const items = query.data?.items ?? []
  const filtered = filterPromptTemplates(items, search)

  return (
    <div className={styles.panel}>
      <h2 className={styles.panelHeading}>{p.heading}</h2>
      {error && (
        <p className={styles.errorText} role="alert">
          {error}
        </p>
      )}

      {newName === null ? (
        <button type="button" className={`${styles.smallButton} ${styles.smallPrimary}`} onClick={() => setNewName('')}>
          {p.newButton}
        </button>
      ) : (
        <form
          className={styles.itemActions}
          onSubmit={(e) => {
            e.preventDefault()
            createMutation.mutate(newName)
          }}
        >
          <input
            className={styles.input}
            value={newName}
            maxLength={100}
            placeholder={p.newNamePlaceholder}
            aria-label={p.newNamePlaceholder}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setNewName(null)
            }}
            // 「新しいテンプレート」を押した直後に打てるように。
            // eslint-disable-next-line jsx-a11y/no-autofocus
            autoFocus
          />
          <button
            type="submit"
            className={`${styles.smallButton} ${styles.smallPrimary}`}
            disabled={createMutation.isPending}
          >
            {p.create}
          </button>
          <button type="button" className={styles.smallButton} onClick={() => setNewName(null)}>
            {p.cancel}
          </button>
        </form>
      )}

      <div className={styles.listWrap}>
        <input
          type="search"
          className={styles.search}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={p.searchPlaceholder}
          aria-label={p.searchLabel}
        />
        {query.isLoading && <p className={styles.placeholder}>{p.loading}</p>}
        {query.isError && <p className={styles.placeholder}>{p.loadFailed}</p>}
        {!query.isLoading && !query.isError && items.length === 0 && <p className={styles.placeholder}>{p.empty}</p>}
        {items.length > 0 && filtered.length === 0 && <p className={styles.placeholder}>{p.noMatch}</p>}
        {filtered.length > 0 && (
          <ul className={styles.list}>
            {filtered.map((item) => (
              <li key={item.id} className={styles.item}>
                <span className={styles.itemName}>{item.name}</span>
                <span className={styles.itemMeta}>
                  {fmt(p.counts, {
                    slots: item.slot_count,
                    variants: item.variant_count,
                    combos: item.combo_count,
                  })}
                  {usingId === item.id ? ` · ${p.inUse}` : ''}
                </span>
                <div className={styles.itemActions}>
                  {usingId === item.id ? (
                    <button type="button" className={styles.smallButton} onClick={stopUsing}>
                      {p.stop}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className={`${styles.smallButton} ${styles.smallPrimary}`}
                      disabled={useMutationHandle.isPending}
                      onClick={() => useMutationHandle.mutate(item.id)}
                    >
                      {p.use}
                    </button>
                  )}
                  <button type="button" className={styles.smallButton} onClick={() => setEditingId(item.id)}>
                    {p.edit}
                  </button>
                  <button
                    type="button"
                    className={`${styles.smallButton} ${styles.smallDanger}`}
                    onClick={() => setDeleting(item)}
                  >
                    {p.delete}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
      <p className={styles.hint}>{p.hint}</p>

      <PromptTemplateEditor
        templateId={editingId}
        onClose={() => setEditingId(null)}
        onSaved={(template) => {
          invalidate()
          // 使っている最中に保存したら、フォームの選択も新しい中身で作り直す。
          if (usingId === template.id) requestFormLoad({ kind: 'promptTemplate', template })
        }}
      />
      <ConfirmDialog
        open={deleting !== null}
        message={deleting ? fmt(p.deleteConfirm, { name: deleting.name }) : ''}
        confirmLabel={p.delete}
        onConfirm={() => {
          if (deleting) deleteMutation.mutate(deleting.id)
          setDeleting(null)
        }}
        onCancel={() => setDeleting(null)}
      />
    </div>
  )
}
