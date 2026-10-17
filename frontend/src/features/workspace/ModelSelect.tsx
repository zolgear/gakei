/**
 * 設定グリッドの1マス目「モデル」。`<optgroup>` でプロバイダーごとに分け(ADR-0013)、
 * ComfyUI でワークフロー未登録のプロバイダーには登録ページへのリンクを添える。
 * SD WebUI(ADR-0038)が有効なのに接続できずモデルが無いときは、管理者に接続の設定ページへの
 * リンクを添える(SD WebUI のモデルを選んでいるとき、または選べるモデルが無いときだけ。ComfyUI のワークフローなど
 * 別のプロバイダーのモデルを選んでいる間は出さない)。ComfyUI のワークフローを選んでいるときは「ワークフローを見る」
 * を出し、結果エリアに「ワークフロー」の表示を開く(ADR-0052 7章)。モデルが多いとき(SD WebUI のチェックポイントなど)は、名前で絞り込む欄を出す
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
import { filterModelOptions, modelFieldLinks, shouldShowModelFilter } from './modelOptions'
import { requestWorkflowView } from './workflowViewRequest'
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
  const links = modelFieldLinks(providers, { provider, model }, admin)
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
      {links.registerComfyWorkflow && (
        <Link to="/settings/comfyui" className={styles.comfyWorkflowLink}>
          {ip.registerComfyWorkflow}
        </Link>
      )}
      {links.viewComfyWorkflow && (
        <button type="button" className={styles.comfyWorkflowLinkButton} onClick={() => requestWorkflowView()}>
          {ip.viewComfyWorkflow}
        </button>
      )}
      {links.checkSdWebuiConnection && (
        <Link to="/settings/sdwebui" className={styles.comfyWorkflowLink}>
          {ip.checkSdWebuiConnection}
        </Link>
      )}
    </div>
  )
}
