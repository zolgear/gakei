/**
 * 生成のフォームの「設定を読み込む」(ADR-0040 4章)。`Modal`(狭い幅では全画面のシート)で
 * 検索つきの一覧を出し、選ぶとフォームに読み込む。今のプロンプトが空でなく、セットにプロンプトが
 * あるときは、ダイアログの中で確かめてから置き換える。開閉は呼び出し側(プロンプト欄の設定のメニュー)。
 */
import { useCallback, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listParameterSets } from '../../api/client'
import { Modal } from '../../components/Modal'
import { useI18n } from '../../i18n'
import { ParameterSetConfirmBody } from './ParameterSetConfirmBody'
import { ParameterSetList } from './ParameterSetList'
import { PARAMETER_SETS_QUERY_KEY } from './parameterSets'
import { useParameterSetLoader } from './useParameterSetLoader'
import styles from './ParameterSets.module.css'

interface LoadParameterSetDialogProps {
  open: boolean
  onClose: () => void
}

export function LoadParameterSetDialog({ open, onClose }: LoadParameterSetDialogProps) {
  const { t } = useI18n()
  const l = t.parameterSets.load
  const [error, setError] = useState<string | null>(null)
  const close = useCallback(() => {
    onClose()
    setError(null)
  }, [onClose])
  const loader = useParameterSetLoader(close)
  const setsQuery = useQuery({ queryKey: PARAMETER_SETS_QUERY_KEY, queryFn: listParameterSets, enabled: open })

  function handleClose() {
    loader.cancel()
    close()
  }

  return (
    <Modal
      open={open}
      title={loader.pending ? l.confirmTitle : l.dialogTitle}
      onClose={handleClose}
      size={loader.pending ? 'default' : 'large'}
    >
      {loader.pending ? (
        <ParameterSetConfirmBody set={loader.pending} onConfirm={loader.confirm} onCancel={loader.cancel} />
      ) : (
        <>
          {error && (
            <p className={styles.errorText} role="alert">
              {error}
            </p>
          )}
          <ParameterSetList
            sets={setsQuery.data?.items ?? []}
            caps={loader.caps}
            isLoading={setsQuery.isLoading}
            isError={setsQuery.isError}
            autoFocusSearch
            renderActions={(set, reason) => (
              <button
                type="button"
                className={`${styles.smallButton} ${styles.smallPrimary}`}
                disabled={reason !== null}
                onClick={() => setError(loader.load(set))}
              >
                {l.apply}
              </button>
            )}
          />
        </>
      )}
    </Modal>
  )
}
