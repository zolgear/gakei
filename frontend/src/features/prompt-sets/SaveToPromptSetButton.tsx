/**
 * 「現在のプロンプトをセットに保存」。生成/編集フォームと Run 詳細から使う。押すと `Modal`
 * (画面中央)に保存先の選択(既存セット、または新規セット名)とラベル欄を出す。
 * 以前はボタンの真下に絶対配置のポップオーバーを出していたが、生成画面の下段配置では
 * 画面外(`.pane` の overflow: hidden の外)に出て見えず、サイドバー配置ではスクロール列の
 * 中に出て切れるため、配置に依存しないモーダルに変えた(2026-09-26)。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, addPromptSetItem, createPromptSet, listPromptSets } from '../../api/client'
import { normalizeLabel, validateItemText, validateSetName } from './promptSetValidation'
import { AutoGrowTextarea } from './AutoGrowTextarea'
import { Modal } from '../../components/Modal'
import { useI18n } from '../../i18n'
import styles from './SaveToPromptSetButton.module.css'

interface SaveToPromptSetButtonProps {
  text: string
}

export function SaveToPromptSetButton({ text }: SaveToPromptSetButtonProps) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<'existing' | 'new'>('existing')
  const [selectedSetId, setSelectedSetId] = useState('')
  const [newSetName, setNewSetName] = useState('')
  const [label, setLabel] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [savedMessage, setSavedMessage] = useState<string | null>(null)

  const setsQuery = useQuery({ queryKey: ['prompt-sets'], queryFn: listPromptSets, enabled: open })
  const sets = setsQuery.data?.items ?? []

  const saveMutation = useMutation({
    mutationFn: async () => {
      const textCheck = validateItemText(text.trim())
      if (!textCheck.valid) throw new Error(textCheck.error ?? t.promptSets.save.emptyPromptError)

      let targetSetId = selectedSetId
      if (mode === 'new') {
        const nameCheck = validateSetName(newSetName)
        if (!nameCheck.valid) throw new Error(nameCheck.error ?? t.promptSets.save.nameRequiredError)
        const created = await createPromptSet({ name: newSetName.trim(), items: [] })
        targetSetId = created.id
      }
      if (!targetSetId) throw new Error(t.promptSets.save.targetRequiredError)

      return addPromptSetItem(targetSetId, { label: normalizeLabel(label), text: text.trim() })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['prompt-sets'] })
      setSavedMessage(t.promptSets.save.savedMessage)
      setOpen(false)
      setLabel('')
      setNewSetName('')
      setSelectedSetId('')
      setError(null)
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError || err instanceof Error ? err.message : t.promptSets.save.saveFailed)
    },
  })

  const canSave = text.trim().length > 0

  return (
    <div className={styles.wrap}>
      <button
        type="button"
        className={styles.trigger}
        disabled={!canSave}
        onClick={() => {
          setOpen((v) => !v)
          setSavedMessage(null)
        }}
      >
        {t.promptSets.save.trigger}
      </button>

      {savedMessage && <span className={styles.savedMessage}>{savedMessage}</span>}

      <Modal open={open} title={t.promptSets.save.trigger} onClose={() => setOpen(false)}>
        <div className={styles.form}>
          <p className={styles.fieldLabel}>{t.promptSets.save.fieldLabel}</p>
          <AutoGrowTextarea value={text} onChange={() => {}} minRows={4} maxHeightVh={30} readOnly />

          <div className={styles.modeRow}>
            <label>
              <input
                type="radio"
                checked={mode === 'existing'}
                onChange={() => setMode('existing')}
              />
              {t.promptSets.save.existingSet}
            </label>
            <label>
              <input type="radio" checked={mode === 'new'} onChange={() => setMode('new')} />
              {t.promptSets.save.newSet}
            </label>
          </div>

          {mode === 'existing' ? (
            <select
              value={selectedSetId}
              onChange={(e) => setSelectedSetId(e.target.value)}
              className={styles.select}
            >
              <option value="">{t.promptSets.save.selectPlaceholder}</option>
              {sets.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          ) : (
            <input
              value={newSetName}
              onChange={(e) => setNewSetName(e.target.value)}
              placeholder={t.promptSets.save.newSetNamePlaceholder}
              className={styles.input}
            />
          )}

          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder={t.promptSets.save.labelPlaceholder}
            className={styles.input}
          />

          {error && <p className={styles.errorText}>{error}</p>}

          <div className={styles.actions}>
            <button type="button" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
              {t.promptSets.save.save}
            </button>
            <button type="button" onClick={() => setOpen(false)}>
              {t.promptSets.save.close}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
