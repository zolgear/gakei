/**
 * 設定グリッドの1マス目「モデル」。`<optgroup>` でプロバイダーごとに分け(ADR-0013)、
 * ComfyUI でワークフロー未登録のプロバイダーには登録ページへのリンクを添える。
 * 下段配置(`InputPaneBottomLayout`)とサイドバー配置(予定)の両方から同じものを使う。
 */
import { Link } from 'react-router'
import type { CapabilitiesResponse } from '../../api/client'
import { useI18n } from '../../i18n'
import { modelOptionValue, parseModelOptionValue } from '../../lib/capabilities'
import fieldStyles from '../run-form/ParamField.module.css'
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
  return (
    <div className={`${fieldStyles.field} ${styles.spanTwo}`}>
      <label htmlFor="model" title="model">
        {ip.modelLabel}
      </label>
      <select
        id="model"
        value={modelOptionValue(provider, model)}
        title={selectedModelDescription || undefined}
        onChange={(e) => {
          const next = parseModelOptionValue(e.target.value)
          if (next) onSelect(next.provider, next.model)
        }}
      >
        {(caps.providers ?? []).map((p) => (
          <optgroup key={p.provider} label={p.available ? p.label : `${p.label}${ip.providerUnavailableSuffix}`}>
            {p.models.length > 0 ? (
              p.models.map((m) => (
                <option key={m.model} value={modelOptionValue(p.provider, m.model)}>
                  {m.label}
                </option>
              ))
            ) : (
              <option value="" disabled>
                {ip.noWorkflowRegistered}
              </option>
            )}
          </optgroup>
        ))}
      </select>
      {(caps.providers ?? []).some((p) => p.provider === 'comfyui' && p.available && p.models.length === 0) && (
        <Link to="/settings/comfyui/workflows" className={styles.comfyWorkflowLink}>
          {ip.registerComfyWorkflow}
        </Link>
      )}
    </div>
  )
}
