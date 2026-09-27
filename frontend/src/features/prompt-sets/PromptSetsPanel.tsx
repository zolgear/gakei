/**
 * サイドバーの「プロンプトセット」パネル。一覧はアコーディオン形式(`PromptSetCard`)。
 * 項目を選ぶと、常にプロンプトの末尾に追加する(置き換えないので確認は不要。2026-09-23 変更)。
 * 今のルートが /studio ならその場で反映し、それ以外(履歴など)なら /studio に遷移する。
 * 反映自体は RunFormContext の `requestPromptInsert` を介して行う(スタジオがマウントされて
 * いなくてもリクエストは残り、マウント後に消費される)。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router'
import { ApiError, createPromptSet, listPromptSets } from '../../api/client'
import { useRunFormContext } from '../../context/useRunFormContext'
import { PromptSetCard } from './PromptSetCard'
import { AutoGrowTextarea } from './AutoGrowTextarea'
import { suggestSetNameFromPrompt } from './promptSetNaming'
import { validateSetName } from './promptSetValidation'
import { useI18n } from '../../i18n'
import styles from './PromptSetsPanel.module.css'

export function PromptSetsPanel() {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const location = useLocation()
  const navigate = useNavigate()
  const { requestPromptInsert } = useRunFormContext()

  const [isCreating, setIsCreating] = useState(false)
  const [newSetName, setNewSetName] = useState('')
  const [newSetPrompt, setNewSetPrompt] = useState('')
  const [createError, setCreateError] = useState<string | null>(null)

  const query = useQuery({ queryKey: ['prompt-sets'], queryFn: listPromptSets })
  const sets = query.data?.items ?? []

  const createMutation = useMutation({
    mutationFn: (name: string) =>
      createPromptSet({
        name,
        items: newSetPrompt.trim() ? [{ label: null, text: newSetPrompt.trim() }] : [],
      }),
    onSuccess: () => {
      setIsCreating(false)
      setNewSetName('')
      setNewSetPrompt('')
      setCreateError(null)
      queryClient.invalidateQueries({ queryKey: ['prompt-sets'] })
    },
    onError: (err: unknown) => {
      setCreateError(err instanceof ApiError ? err.message : t.promptSets.panel.createFailed)
    },
  })

  function handleCreateSubmit() {
    // 名前が空でもプロンプトがあれば、その先頭部分を名前の候補として使う。
    const name = newSetName.trim() || suggestSetNameFromPrompt(newSetPrompt)
    const check = validateSetName(name)
    if (!check.valid) {
      setCreateError(check.error ?? null)
      return
    }
    createMutation.mutate(name)
  }

  function handleLoadPrompt(text: string) {
    requestPromptInsert(text)
    if (location.pathname !== '/studio') {
      navigate('/studio')
    }
  }

  return (
    <div className={styles.panel}>
      <div className={styles.headerRow}>
        <h2 className={styles.heading}>{t.promptSets.panel.heading}</h2>
        {!isCreating && (
          <button type="button" className={styles.headerButton} onClick={() => setIsCreating(true)}>
            {t.promptSets.panel.add}
          </button>
        )}
      </div>

      {isCreating && (
        <div className={styles.createForm}>
          <label htmlFor="new-set-name" className={styles.fieldLabel}>
            {t.promptSets.panel.setNameLabel}
          </label>
          <input
            id="new-set-name"
            value={newSetName}
            onChange={(e) => setNewSetName(e.target.value)}
            placeholder={t.promptSets.panel.setNamePlaceholder}
            className={styles.createInput}
          />

          <label htmlFor="new-set-prompt" className={styles.fieldLabel}>
            {t.promptSets.panel.firstPromptLabel}
          </label>
          <AutoGrowTextarea
            id="new-set-prompt"
            value={newSetPrompt}
            onChange={setNewSetPrompt}
            placeholder={t.promptSets.panel.firstPromptPlaceholder}
            minRows={4}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                e.preventDefault()
                handleCreateSubmit()
              }
            }}
          />

          {createError && <p className={styles.errorText}>{createError}</p>}
          <div className={styles.createActions}>
            <button type="button" onClick={handleCreateSubmit} disabled={createMutation.isPending}>
              {t.promptSets.panel.create}
            </button>
            <button
              type="button"
              onClick={() => {
                setIsCreating(false)
                setCreateError(null)
                setNewSetName('')
                setNewSetPrompt('')
              }}
            >
              {t.promptSets.panel.cancel}
            </button>
          </div>
        </div>
      )}

      {query.isLoading && <p className={styles.placeholder}>{t.promptSets.panel.loading}</p>}
      {query.isError && <p className={styles.placeholder}>{t.promptSets.panel.loadFailed}</p>}
      {!query.isLoading && sets.length === 0 && (
        <p className={styles.placeholder}>{t.promptSets.panel.empty}</p>
      )}

      <div className={styles.list}>
        {sets.map((set) => (
          <PromptSetCard key={set.id} set={set} onLoadPrompt={handleLoadPrompt} />
        ))}
      </div>

      <p className={styles.hint}>{t.promptSets.panel.hint}</p>
    </div>
  )
}
