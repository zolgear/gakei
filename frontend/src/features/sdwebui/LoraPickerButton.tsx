/**
 * プロンプト欄の操作列の「LoRA」(ADR-0038 8章)。SD WebUI のモデルを選んでいるときだけ出す。
 *
 * 押すと `Modal`(狭い幅では全画面のシート)に接続先の LoRA の一覧を出す。選ぶと重みを決めて
 * `<lora:name:重み>` をプロンプトの末尾に足す(`insertPrompt` の `append-tags`。テキストモードでも
 * タグモードでも同じ)。トリガーワードの候補はチップで出し、タップで足す。
 *
 * ベースモデル(SDXL / SD1.5 / 不明)は目印として出すだけで、チェックポイントと合わない LoRA を
 * 隠したり警告したりはしない(チェックポイントのベースモデルは API から分からないため)。
 */
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, listSdWebuiLoras, refreshSdWebui, type SdWebuiLora } from '../../api/client'
import { Modal } from '../../components/Modal'
import { fmt, useI18n } from '../../i18n'
import { isAdmin, useAuth } from '../auth/authState'
import type { PromptInsertMode } from '../run-form/promptInsertion'
import {
  LORA_WEIGHT_DEFAULT,
  LORA_WEIGHT_MAX,
  LORA_WEIGHT_MIN,
  LORA_WEIGHT_STEP,
  filterLoras,
  loraPromptTag,
  normalizeLoraWeight,
  promptHasLora,
  promptTagKeys,
  triggerTagToPrompt,
} from './loraPrompt'
import { tagCompareKey } from '../prompt-tags/promptTags'
import styles from './LoraPickerButton.module.css'

const SDWEBUI_LORAS_QUERY_KEY = ['sdwebui-loras'] as const

interface LoraPickerButtonProps {
  /** 今のプロンプト(既に入っている LoRA とトリガーワードの目印に使う)。 */
  prompt: string
  /** 括弧をエスケープするか(タグモードの候補と同じ規則)。 */
  escapeParens: boolean
  onInsert: (text: string, mode: PromptInsertMode, cursorPos: number | null) => void
}

export function LoraPickerButton({ prompt, escapeParens, onInsert }: LoraPickerButtonProps) {
  const { t } = useI18n()
  const l = t.sdwebui.lora
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<SdWebuiLora | null>(null)
  const [weight, setWeight] = useState(LORA_WEIGHT_DEFAULT)

  function close() {
    setOpen(false)
    setSelected(null)
  }

  return (
    <>
      <button
        type="button"
        className={styles.trigger}
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        title={l.triggerTitle}
      >
        {l.trigger}
      </button>
      <Modal open={open} title={selected ? selected.name : l.dialogTitle} onClose={close} size="large">
        {selected ? (
          <LoraDetail
            lora={selected}
            weight={weight}
            onWeightChange={setWeight}
            prompt={prompt}
            escapeParens={escapeParens}
            onInsert={onInsert}
            onBack={() => setSelected(null)}
          />
        ) : (
          <LoraList
            query={query}
            onQueryChange={setQuery}
            onSelect={(lora) => {
              setSelected(lora)
              setWeight(LORA_WEIGHT_DEFAULT)
            }}
            prompt={prompt}
            open={open}
          />
        )}
      </Modal>
    </>
  )
}

function BaseModelBadge({ baseModel }: { baseModel: SdWebuiLora['base_model'] }) {
  const { t } = useI18n()
  const b = t.sdwebui.lora.baseModels
  const label = baseModel === 'sdxl' ? b.sdxl : baseModel === 'sd1' ? b.sd1 : b.unknown
  return (
    <span className={styles.badge} data-base={baseModel ?? 'unknown'}>
      {label}
    </span>
  )
}

interface LoraListProps {
  query: string
  onQueryChange: (value: string) => void
  onSelect: (lora: SdWebuiLora) => void
  prompt: string
  open: boolean
}

function LoraList({ query, onQueryChange, onSelect, prompt, open }: LoraListProps) {
  const { t } = useI18n()
  const l = t.sdwebui.lora
  const admin = isAdmin(useAuth())
  const queryClient = useQueryClient()
  const lorasQuery = useQuery({
    queryKey: SDWEBUI_LORAS_QUERY_KEY,
    queryFn: listSdWebuiLoras,
    enabled: open,
    staleTime: 60_000,
    retry: false,
  })
  const refreshMutation = useMutation({
    mutationFn: refreshSdWebui,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: SDWEBUI_LORAS_QUERY_KEY })
      void queryClient.invalidateQueries({ queryKey: ['capabilities'] })
    },
  })

  const items = useMemo(() => lorasQuery.data?.items ?? [], [lorasQuery.data])
  const filtered = useMemo(() => filterLoras(items, query), [items, query])

  const refreshButton = admin ? (
    <div className={styles.refreshRow}>
      <button
        type="button"
        className={styles.secondary}
        onClick={() => refreshMutation.mutate()}
        disabled={refreshMutation.isPending}
      >
        {refreshMutation.isPending ? l.refreshing : l.refresh}
      </button>
      {refreshMutation.isError && (
        <span className={styles.error} role="alert">
          {refreshMutation.error instanceof ApiError ? refreshMutation.error.message : l.refreshFailed}
        </span>
      )}
    </div>
  ) : null

  if (lorasQuery.isPending) {
    return <p className={styles.muted}>{l.loading}</p>
  }
  if (lorasQuery.isError) {
    const message = lorasQuery.error instanceof ApiError ? lorasQuery.error.message : l.loadFailed
    return (
      <div className={styles.emptyState}>
        <p className={styles.error} role="alert">
          {fmt(l.loadFailedWithReason, { reason: message })}
        </p>
        <button type="button" className={styles.secondary} onClick={() => void lorasQuery.refetch()}>
          {l.retry}
        </button>
      </div>
    )
  }
  if (items.length === 0) {
    return (
      <div className={styles.emptyState}>
        <p className={styles.emptyHeadline}>{l.empty}</p>
        <p className={styles.muted}>{admin ? l.emptyHelpAdmin : l.emptyHelpUser}</p>
        {refreshButton}
      </div>
    )
  }

  return (
    <div className={styles.listWrap}>
      <input
        type="search"
        className={styles.search}
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        placeholder={l.searchPlaceholder}
        aria-label={l.searchLabel}
        autoComplete="off"
        spellCheck={false}
      />
      <p className={styles.count}>
        {fmt(l.count, { shown: filtered.length, total: items.length })}
      </p>
      {filtered.length === 0 ? (
        <p className={styles.muted}>{l.noMatch}</p>
      ) : (
        <ul className={styles.list}>
          {filtered.map((lora) => (
            <li key={lora.name}>
              <button type="button" className={styles.row} onClick={() => onSelect(lora)}>
                <span className={styles.rowNames}>
                  <span className={styles.rowName}>{lora.name}</span>
                  {lora.alias && <span className={styles.rowAlias}>{lora.alias}</span>}
                </span>
                {promptHasLora(prompt, lora.name) && <span className={styles.inPrompt}>{l.inPrompt}</span>}
                <BaseModelBadge baseModel={lora.base_model} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {refreshButton && <div className={styles.listFooter}>{refreshButton}</div>}
    </div>
  )
}

interface LoraDetailProps {
  lora: SdWebuiLora
  weight: number
  onWeightChange: (value: number) => void
  prompt: string
  escapeParens: boolean
  onInsert: (text: string, mode: PromptInsertMode, cursorPos: number | null) => void
  onBack: () => void
}

function LoraDetail({ lora, weight, onWeightChange, prompt, escapeParens, onInsert, onBack }: LoraDetailProps) {
  const { t } = useI18n()
  const l = t.sdwebui.lora
  const tag = loraPromptTag(lora.name, weight)
  const present = promptTagKeys(prompt)
  const tagInPrompt = present.has(tagCompareKey(tag))
  const triggerTags = lora.trigger_tags ?? []

  return (
    <div className={styles.detail}>
      <button type="button" className={styles.backButton} onClick={onBack}>
        {l.back}
      </button>
      <div className={styles.detailHead}>
        {lora.alias && <span className={styles.rowAlias}>{fmt(l.aliasLabel, { alias: lora.alias })}</span>}
        <BaseModelBadge baseModel={lora.base_model} />
      </div>

      <div className={styles.weightRow}>
        <label htmlFor="lora-weight" className={styles.fieldLabel}>
          {l.weight}
        </label>
        <input
          type="range"
          className={styles.weightRange}
          min={LORA_WEIGHT_MIN}
          max={LORA_WEIGHT_MAX}
          step={LORA_WEIGHT_STEP}
          value={weight}
          onChange={(e) => onWeightChange(normalizeLoraWeight(Number(e.target.value)))}
          aria-label={l.weight}
        />
        <input
          id="lora-weight"
          type="number"
          className={styles.weightNumber}
          min={LORA_WEIGHT_MIN}
          max={LORA_WEIGHT_MAX}
          step={LORA_WEIGHT_STEP}
          value={weight}
          onChange={(e) => {
            if (e.target.value === '') return
            onWeightChange(normalizeLoraWeight(Number(e.target.value)))
          }}
        />
      </div>

      <div className={styles.insertRow}>
        <code className={styles.preview}>{tag}</code>
        <button
          type="button"
          className={styles.primary}
          disabled={tagInPrompt}
          onClick={() => onInsert(tag, 'append-tags', null)}
        >
          {tagInPrompt ? l.inserted : l.insert}
        </button>
      </div>

      <section className={styles.triggers}>
        <h3 className={styles.sectionTitle}>{l.triggerTags}</h3>
        {triggerTags.length === 0 ? (
          <p className={styles.muted}>{l.noTriggerTags}</p>
        ) : (
          <>
            <p className={styles.muted}>{l.triggerTagsHelp}</p>
            <ul className={styles.chips}>
              {triggerTags.map((trigger) => {
                const text = triggerTagToPrompt(trigger, escapeParens)
                const added = present.has(tagCompareKey(text))
                return (
                  <li key={trigger}>
                    <button
                      type="button"
                      className={styles.chip}
                      data-added={added || undefined}
                      disabled={added}
                      aria-label={fmt(added ? l.triggerAdded : l.addTrigger, { tag: trigger })}
                      onClick={() => onInsert(text, 'append-tags', null)}
                    >
                      {added ? '✓ ' : '+ '}
                      {trigger}
                    </button>
                  </li>
                )
              })}
            </ul>
          </>
        )}
      </section>
    </div>
  )
}
