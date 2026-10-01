/**
 * `/settings/comfyui/workflows`(ワークフロー一覧)の先頭に出す、接続状態の1行だけの要約。
 * 接続/URL変更/接続テスト/切り離しの実際の操作は `/settings/comfyui` の
 * `ComfyUIConnectionCard` に集約したので、ここでは状態とそこへのリンクだけを持つ。
 * `GET /api/comfyui/status` のキャッシュ(`['comfyui-status']`)はパネル側と共有していて、
 * どちらかで接続・切り離しをすればもう片方の表示にも再読み込みなしで反映される。
 */
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { getComfyUIStatus } from '../../api/client'
import { useI18n } from '../../i18n'
import { connectionSummary } from './comfyuiConnectionForm'
import panelStyles from './ComfyUIStatus.module.css'
import styles from './ComfyUIConnectionSummary.module.css'

export function ComfyUIConnectionSummary() {
  const { t } = useI18n()
  const statusQuery = useQuery({ queryKey: ['comfyui-status'], queryFn: getComfyUIStatus })

  if (statusQuery.isLoading) {
    return <p className={panelStyles.placeholder}>{t.comfyui.connection.checking}</p>
  }
  if (statusQuery.isError || !statusQuery.data) {
    return <p className={panelStyles.placeholder}>{t.comfyui.connection.loadError}</p>
  }

  const summary = connectionSummary(statusQuery.data)

  return (
    <div className={styles.row}>
      <span className={panelStyles.dot} data-state={summary.state} aria-hidden="true" />
      <span className={styles.label}>{summary.label}</span>
      <Link to="/settings/comfyui" className={styles.link}>
        {t.comfyui.connection.settingsLink}
      </Link>
    </div>
  )
}
