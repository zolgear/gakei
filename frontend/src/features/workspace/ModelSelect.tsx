/**
 * 設定グリッドの1マス目「モデル」。`<optgroup>` でプロバイダーごとに分け(ADR-0013)、
 * ComfyUI でワークフロー未登録のプロバイダーには登録ページへのリンクを添える。
 * SD WebUI(ADR-0038)が有効なのに接続できずモデルが無いときは、管理者に接続の設定ページへの
 * リンクを添える。モデルが多いとき(SD WebUI のチェックポイントなど)は、名前で絞り込む欄を出す
 * (`modelOptions.ts`)。
 * 下段配置(`InputPaneBottomLayout`)とサイドバー配置(予定)の両方から同じものを使う。
 */
import { useState } from 'react'
import { Link } from 'react-router'
import type { CapabilitiesResponse } from '../../api/client'
import { useI18n } from '../../i18n'
import { modelOptionValue, parseModelOptionValue } from '../../lib/capabilities'
import { isAdmin, useAuth } from '../auth/authState'
import fieldStyles from '../run-form/ParamField.module.css'
import { filterModelOptions, shouldShowModelFilter } from './modelOptions'
import styles from './InputPane.module.css'

interface ModelSelectProps {
  caps: CapabilitiesResponse
  provider: string
  model: string
  /** 選ばれているモデルの説明文(`<select>` の title に出す)。 */
  selectedModelDescription: string | undefined
  onSelect: (provider: string, model: string) => void
}

export function ModelSelect({ caps, provider, model, selectedModelDescription, onSelect }: ModelSelectProps) {
  const { t } = useI18n()
  const ip = t.workspace.inputPane
  const admin = isAdmin(useAuth())
  const [filter, setFilter] = useState('')
  const providers = caps.providers ?? []
  const showFilter = shouldShowModelFilter(providers)
  const groups = filterModelOptions(providers, showFilter ? filter : '', { provider, model })
  return (
    <div className={`${fieldStyles.field} ${styles.spanTwo}`}>
      <label htmlFor="model" title="model">
        {ip.modelLabel}
      </label>
      {showFilter && (
        <input
          type="search"
          className={styles.modelFilter}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={ip.modelFilterPlaceholder}
          aria-label={ip.modelFilterAria}
          aria-controls="model"
          autoComplete="off"
          spellCheck={false}
        />
      )}
      <select
        id="model"
        value={modelOptionValue(provider, model)}
        title={selectedModelDescription || undefined}
        onChange={(e) => {
          const next = parseModelOptionValue(e.target.value)
          if (next) onSelect(next.provider, next.model)
        }}
      >
        {groups.map(({ provider: p, models }) => (
          <optgroup key={p.provider} label={p.available ? p.label : `${p.label}${ip.providerUnavailableSuffix}`}>
            {models.length > 0 ? (
              models.map((m) => (
                <option key={m.model} value={modelOptionValue(p.provider, m.model)}>
                  {m.label}
                </option>
              ))
            ) : (
              <option value="" disabled>
                {p.provider === 'comfyui' ? ip.noWorkflowRegistered : ip.noModelsAvailable}
              </option>
            )}
          </optgroup>
        ))}
      </select>
      {providers.some((p) => p.provider === 'comfyui' && p.available && p.models.length === 0) && (
        <Link to="/settings/comfyui" className={styles.comfyWorkflowLink}>
          {ip.registerComfyWorkflow}
        </Link>
      )}
      {admin && providers.some((p) => p.provider === 'sdwebui' && p.models.length === 0) && (
        <Link to="/settings/sdwebui" className={styles.comfyWorkflowLink}>
          {ip.checkSdWebuiConnection}
        </Link>
      )}
    </div>
  )
}
