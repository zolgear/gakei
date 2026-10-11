/**
 * サイドバーの「パラメーターセット」パネル(ADR-0040 4章。プロンプトセットと並べる)。
 * 一覧(検索つき)から読み込み・名前の変更・削除ができる。作成はフォームの「設定を保存」と
 * Run の詳細から行う(パネルでは作らない。中身は今のフォームか Run から取るため)。
 * 読み込みは今のルートが /studio でなければスタジオへ移ってから反映する。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  deleteParameterSet,
  listParameterSets,
  updateParameterSet,
  type ParameterSetResponse,
} from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Modal } from '../../components/Modal'
import { fmt, useI18n } from '../../i18n'
import { validateSetName } from '../prompt-sets/promptSetValidation'
import { ParameterSetConfirmBody } from './ParameterSetConfirmBody'
import { ParameterSetList } from './ParameterSetList'
import { PARAMETER_SETS_QUERY_KEY } from './parameterSets'
import { useParameterSetLoader } from './useParameterSetLoader'
import styles from './ParameterSets.module.css'

export function ParameterSetsPanel() {
  const { t } = useI18n()
  const p = t.parameterSets.panel
  const l = t.parameterSets.load
  const queryClient = useQueryClient()
  const loader = useParameterSetLoader()
  const query = useQuery({ queryKey: PARAMETER_SETS_QUERY_KEY, queryFn: listParameterSets })
  const [error, setError] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null)
  const [deleting, setDeleting] = useState<ParameterSetResponse | null>(null)

  const invalidate = () => queryClient.invalidateQueries({ queryKey: PARAMETER_SETS_QUERY_KEY })

  const renameMutation = useMutation({
    mutationFn: (target: { id: string; name: string }) => {
      const check = validateSetName(target.name)
      if (!check.valid) throw new Error(check.error)
      return updateParameterSet(target.id, { name: target.name.trim() })
    },
    onSuccess: () => {
      setRenaming(null)
      setError(null)
      invalidate()
    },
    onError: (err: unknown) => setError(err instanceof ApiError || err instanceof Error ? err.message : p.renameFailed),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteParameterSet(id),
    onSuccess: () => {
      setError(null)
      invalidate()
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : p.deleteFailed),
  })

  return (
    <div className={styles.panel}>
      <h2 className={styles.panelHeading}>{p.heading}</h2>
      {error && (
        <p className={styles.errorText} role="alert">
          {error}
        </p>
      )}
      <ParameterSetList
        sets={query.data?.items ?? []}
        caps={loader.caps}
        isLoading={query.isLoading}
        isError={query.isError}
        renderName={(set) =>
          renaming?.id === set.id ? (
            <form
              className={styles.renameRow}
              onSubmit={(e) => {
                e.preventDefault()
                renameMutation.mutate(renaming)
              }}
            >
              <input
                className={styles.input}
                value={renaming.name}
                maxLength={100}
                aria-label={p.rename}
                onChange={(e) => setRenaming({ id: set.id, name: e.target.value })}
                onKeyDown={(e) => {
                  if (e.key === 'Escape') setRenaming(null)
                }}
                // 名前変更を押した直後に打てるように。
                // eslint-disable-next-line jsx-a11y/no-autofocus
                autoFocus
              />
            </form>
          ) : null
        }
        renderActions={(set, reason) =>
          renaming?.id === set.id ? (
            <>
              <button
                type="button"
                className={`${styles.smallButton} ${styles.smallPrimary}`}
                onClick={() => renameMutation.mutate(renaming)}
                disabled={renameMutation.isPending}
              >
                {p.save}
              </button>
              <button type="button" className={styles.smallButton} onClick={() => setRenaming(null)}>
                {p.cancel}
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className={`${styles.smallButton} ${styles.smallPrimary}`}
                disabled={reason !== null}
                onClick={() => setError(loader.load(set))}
              >
                {l.apply}
              </button>
              <button
                type="button"
                className={styles.smallButton}
                onClick={() => {
                  setError(null)
                  setRenaming({ id: set.id, name: set.name })
                }}
              >
                {p.rename}
              </button>
              <button
                type="button"
                className={`${styles.smallButton} ${styles.smallDanger}`}
                onClick={() => setDeleting(set)}
              >
                {p.delete}
              </button>
            </>
          )
        }
      />
      <p className={styles.hint}>{p.hint}</p>

      <Modal open={loader.pending !== null} title={l.confirmTitle} onClose={loader.cancel}>
        {loader.pending && (
          <ParameterSetConfirmBody set={loader.pending} onConfirm={loader.confirm} onCancel={loader.cancel} />
        )}
      </Modal>
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
