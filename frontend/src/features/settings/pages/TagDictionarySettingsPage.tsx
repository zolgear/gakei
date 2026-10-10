/**
 * `/settings/tag-dictionary`(管理者。ADR-0041 1章、ADR-0031)。タグ辞書の登録・一覧・有効/無効・削除。
 *
 * このページの項目はどれも「操作」(押すとその場でサーバーに送る)なので、保存のボタンは出さない。
 * 有効/無効のスイッチとカテゴリーの体系も、一覧の行ごとの操作としてすぐ送る(行ごとに状態を持つ一覧で、
 * 保存でまとめる値ではないため。トークンの失効や ONNX モデルの削除と同じ扱い)。削除は確認してから。
 *
 * - 登録: ファイルを選ぶ(かドロップする)とすぐアップロードする。体系は先に選んでおく。
 * - 取り込みはサーバーの中で続くので、取り込み中の辞書がある間は数秒ごとに一覧を取り直す。
 * - 辞書を変えたら、候補と訳のキャッシュを捨てる(次に引くときに新しい辞書で答える)。
 * - 辞書の配布元は画面に書かない(配布の条件は辞書ごとに違う。`docs/tag-dictionary.md`)。
 */
import { useEffect, useRef, useState, type DragEvent as ReactDragEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  deleteTagDictionary,
  listTagDictionaries,
  updateTagDictionary,
  uploadTagDictionary,
  type TagCategoryScheme,
  type TagDictionaryItem,
  type TagDictionaryListResponse,
} from '../../../api/client'
import { ConfirmDialog } from '../../../components/ConfirmDialog'
import { fmt, useI18n } from '../../../i18n'
import { formatDateTime } from '../../../lib/format'
import { clearTagTranslationCache } from '../../tag-dictionary/useTagTranslations'
import { SettingsPageFrame } from '../SettingsPageFrame'
import { QueryStatus, SettingsRow, SettingsSection, SettingsSwitch } from '../SettingsParts'
import { useSettingsShell } from '../settingsShell'
import common from '../settings.module.css'
import styles from './TagDictionarySettingsPage.module.css'

const QUERY_KEY = ['settings', 'tag-dictionaries'] as const
/** 取り込み中の辞書がある間、一覧を取り直す間隔。 */
const POLL_MS = 2000
const SCHEMES: readonly TagCategoryScheme[] = ['danbooru', 'other']

function isScheme(value: string): value is TagCategoryScheme {
  return (SCHEMES as readonly string[]).includes(value)
}

function errorText(error: unknown, fallback: string): string | null {
  if (!error) return null
  return error instanceof ApiError && error.message ? error.message : fallback
}

export function TagDictionarySettingsPage() {
  const { t } = useI18n()
  const s = t.settings.tagDictionary
  const { toast } = useSettingsShell()
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [scheme, setScheme] = useState<TagCategoryScheme>('danbooru')
  const [dragOver, setDragOver] = useState(false)
  const [toDelete, setToDelete] = useState<TagDictionaryItem | null>(null)

  const query = useQuery({
    queryKey: QUERY_KEY,
    queryFn: listTagDictionaries,
    refetchInterval: (q) => (q.state.data?.items?.some((item) => item.status === 'importing') ? POLL_MS : false),
  })
  const items = query.data?.items ?? []

  // 取り込みが終わった(取り込み中の数が減った)ら、候補と訳のキャッシュを捨てる。
  const importingCount = items.filter((item) => item.status === 'importing').length
  const lastImportingCountRef = useRef(importingCount)
  useEffect(() => {
    const finished = importingCount < lastImportingCountRef.current
    lastImportingCountRef.current = importingCount
    if (finished) {
      clearTagTranslationCache()
      void queryClient.invalidateQueries({ queryKey: ['tags'] })
    }
  }, [importingCount, queryClient])

  function invalidateTagCaches() {
    clearTagTranslationCache()
    void queryClient.invalidateQueries({ queryKey: ['tags'] })
  }

  function mergeItem(item: TagDictionaryItem) {
    queryClient.setQueryData<TagDictionaryListResponse>(QUERY_KEY, (prev) =>
      prev ? { ...prev, items: (prev.items ?? []).map((row) => (row.id === item.id ? item : row)) } : prev,
    )
  }

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadTagDictionary(file, scheme),
    onSuccess: (response) => {
      const created = response.items ?? []
      toast.show({ message: fmt(s.uploaded, { count: created.length }) })
      void queryClient.invalidateQueries({ queryKey: QUERY_KEY })
    },
  })
  const updateMutation = useMutation({
    mutationFn: ({ id, ...body }: { id: string; enabled?: boolean; category_scheme?: TagCategoryScheme }) =>
      updateTagDictionary(id, body),
    onSuccess: (item) => {
      mergeItem(item)
      invalidateTagCaches()
    },
  })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteTagDictionary(id),
    onSuccess: () => {
      toast.show({ message: s.deleted })
      invalidateTagCaches()
      void queryClient.invalidateQueries({ queryKey: QUERY_KEY })
    },
  })

  function upload(file: File | null | undefined) {
    if (!file || uploadMutation.isPending) return
    uploadMutation.mutate(file)
  }

  function handleDrop(e: ReactDragEvent<HTMLDivElement>) {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
    upload(e.dataTransfer.files?.[0])
  }

  function handleDragOver(e: ReactDragEvent<HTMLDivElement>) {
    if (!Array.from(e.dataTransfer.types).includes('Files')) return
    e.preventDefault()
    e.stopPropagation()
    setDragOver(true)
  }

  const uploadError = errorText(uploadMutation.error, s.uploadFailed)
  const rowError = errorText(updateMutation.error ?? deleteMutation.error, s.updateFailed)

  return (
    <SettingsPageFrame pageId="tagDictionary" title={t.settings.pages.tagDictionary} intro={s.intro}>
      <SettingsSection heading={s.uploadHeading}>
        <SettingsRow label={s.schemeLabel} htmlFor="gakei-tag-dictionary-scheme" description={s.schemeHelp}>
          <select
            id="gakei-tag-dictionary-scheme"
            className={common.select}
            value={scheme}
            onChange={(e) => {
              if (isScheme(e.target.value)) setScheme(e.target.value)
            }}
          >
            {SCHEMES.map((value) => (
              <option key={value} value={value}>
                {s.schemes[value]}
              </option>
            ))}
          </select>
        </SettingsRow>
        <div
          className={styles.dropZone}
          data-active={dragOver || undefined}
          aria-busy={uploadMutation.isPending}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={() => setDragOver(false)}
        >
          <button
            type="button"
            className={common.primaryButton}
            disabled={uploadMutation.isPending}
            onClick={() => fileInputRef.current?.click()}
          >
            {uploadMutation.isPending ? s.uploading : s.dropLabel}
          </button>
          <p className={styles.dropHint}>{s.dropHint}</p>
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.zip,text/csv,application/zip"
            className={styles.hiddenInput}
            onChange={(e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              upload(file)
            }}
          />
        </div>
        {uploadError && (
          <p className={common.errorText} role="alert">
            {uploadError}
          </p>
        )}
      </SettingsSection>

      <SettingsSection heading={s.listHeading}>
        <QueryStatus
          isLoading={query.isLoading}
          isError={query.isError}
          loadingText={s.loading}
          errorText={s.loadFailed}
          retryText={s.retry}
          onRetry={() => void query.refetch()}
        />
        {query.data && items.length === 0 && <p className={common.placeholder}>{s.empty}</p>}
        {items.length > 0 && (
          <ul className={common.list}>
            {items.map((item) => (
              <DictionaryRow
                key={item.id}
                item={item}
                busy={updateMutation.isPending || deleteMutation.isPending}
                onToggle={(enabled) => updateMutation.mutate({ id: item.id, enabled })}
                onScheme={(value) => updateMutation.mutate({ id: item.id, category_scheme: value })}
                onDelete={() => setToDelete(item)}
              />
            ))}
          </ul>
        )}
        {rowError && (
          <p className={common.errorText} role="alert">
            {rowError}
          </p>
        )}
      </SettingsSection>

      <ConfirmDialog
        open={toDelete !== null}
        message={toDelete ? fmt(s.confirmDelete, { name: toDelete.filename }) : ''}
        confirmLabel={s.confirmDeleteLabel}
        onConfirm={() => {
          if (toDelete) deleteMutation.mutate(toDelete.id)
          setToDelete(null)
        }}
        onCancel={() => setToDelete(null)}
      />
    </SettingsPageFrame>
  )
}

interface DictionaryRowProps {
  item: TagDictionaryItem
  busy: boolean
  onToggle: (enabled: boolean) => void
  onScheme: (scheme: TagCategoryScheme) => void
  onDelete: () => void
}

function DictionaryRow({ item, busy, onToggle, onScheme, onDelete }: DictionaryRowProps) {
  const { t } = useI18n()
  const s = t.settings.tagDictionary
  const importing = item.status === 'importing'
  const schemeId = `gakei-tag-dictionary-scheme-${item.id}`

  return (
    <li className={`${common.item} ${styles.item}`}>
      <div className={common.itemText}>
        <span className={common.itemName}>
          {item.filename}
          <span className={styles.kind}>{s.kinds[item.kind]}</span>
        </span>
        <dl className={common.itemMeta}>
          <dt>{s.statusLabel}</dt>
          <dd>
            <span className={styles.status} data-status={item.status} role={importing ? 'status' : undefined}>
              {s.status[item.status]}
            </span>
          </dd>
          <dt>{s.rowCountLabel}</dt>
          {/* 取り込み中の行数はまだ数えていない(0)ので出さない。 */}
          <dd>{importing ? '-' : fmt(s.rows, { count: item.row_count, rows: item.row_count.toLocaleString() })}</dd>
          <dt>{s.createdAtLabel}</dt>
          <dd>{formatDateTime(item.created_at)}</dd>
          {item.kind === 'tags' && (
            <>
              <dt>
                <label htmlFor={schemeId}>{s.schemeShortLabel}</label>
              </dt>
              <dd>
                <select
                  id={schemeId}
                  className={styles.schemeSelect}
                  value={item.category_scheme ?? 'danbooru'}
                  disabled={busy || importing}
                  onChange={(e) => {
                    if (isScheme(e.target.value)) onScheme(e.target.value)
                  }}
                >
                  {SCHEMES.map((value) => (
                    <option key={value} value={value}>
                      {s.schemes[value]}
                    </option>
                  ))}
                </select>
              </dd>
            </>
          )}
        </dl>
        {item.status === 'failed' && item.error_message && (
          <p className={common.errorText} role="alert">
            {item.error_message}
          </p>
        )}
      </div>
      <div className={common.itemActions}>
        <SettingsSwitch
          checked={item.enabled}
          disabled={busy || item.status === 'failed'}
          label={s.enabledShort}
          ariaLabel={fmt(s.enabledLabel, { name: item.filename })}
          title={fmt(s.enabledLabel, { name: item.filename })}
          onChange={onToggle}
        />
        <button
          type="button"
          className={common.dangerButton}
          disabled={busy || importing}
          title={importing ? s.deleteBlocked : fmt(s.deleteTitle, { name: item.filename })}
          aria-label={fmt(s.deleteTitle, { name: item.filename })}
          onClick={onDelete}
        >
          {s.delete}
        </button>
      </div>
    </li>
  )
}
