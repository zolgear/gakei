/**
 * ビューア・系列インスペクターの生成情報(A1111 形式)の「SD WebUI のフォームに読み込む」
 * (ADR-0038 9章)。SD WebUI が有効なときだけ出す。
 *
 * サーバーがストックの画像の生成情報を SD WebUI のフォームの値に対応付けて返す。スタジオの今の
 * プロンプト(context の `formState.prompt`)が空でなければ確かめてから、`requestFormLoad` に積んで
 * スタジオへ移る(スタジオが消費して反映する)。スタジオの中(系列インスペクター)ではページを
 * 移らない(`?asset=` などを消さないため)。
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router'
import { ApiError, getCapabilities, importSdWebuiParams, type SdWebuiImportParamsResponse } from '../../api/client'
import { Modal } from '../../components/Modal'
import { useRunFormContext } from '../../context/useRunFormContext'
import { useI18n } from '../../i18n'
import { shouldConfirmReplace } from '../run-form/promptInsertion'
import { ImportConfirmBody } from './ImportConfirmBody'
import { isSdWebuiEnabled } from './importParams'
import styles from './ImportParams.module.css'

export function ImportFromAssetButton({ assetId }: { assetId: string }) {
  const { t } = useI18n()
  const ip = t.sdwebui.importParams
  const capsQuery = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities })
  const { formState, requestFormLoad } = useRunFormContext()
  const navigate = useNavigate()
  const location = useLocation()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState<SdWebuiImportParamsResponse | null>(null)

  if (!isSdWebuiEnabled(capsQuery.data)) return null

  function apply(response: SdWebuiImportParamsResponse) {
    setPending(null)
    requestFormLoad(response)
    if (location.pathname !== '/studio') navigate('/studio')
  }

  async function handleClick() {
    setLoading(true)
    setError(null)
    try {
      const response = await importSdWebuiParams({ assetId })
      if (shouldConfirmReplace(formState.prompt)) setPending(response)
      else apply(response)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : ip.failed)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className={styles.assetAction}>
      <button
        type="button"
        className={styles.assetButton}
        onClick={() => void handleClick()}
        disabled={loading}
        title={ip.fromAssetTitle}
      >
        {loading ? ip.fromAssetReading : ip.fromAsset}
      </button>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      <Modal open={pending !== null} title={ip.confirmTitle} onClose={() => setPending(null)}>
        {pending && (
          <ImportConfirmBody response={pending} onConfirm={() => apply(pending)} onCancel={() => setPending(null)} />
        )}
      </Modal>
    </div>
  )
}
