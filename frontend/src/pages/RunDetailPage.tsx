/** Run 詳細ページ。中身は `RunDetailContent`(系列グラフのインスペクターとも共用)。 */
import { useParams } from 'react-router'
import { useBackNavigate } from '../lib/useBackNavigate'
import { RunDetailContent } from '../features/run-detail/RunDetailContent'
import { useI18n } from '../i18n'
import styles from './RunDetailPage.module.css'

export function RunDetailPage() {
  const { t } = useI18n()
  const { id } = useParams<{ id: string }>()
  const goBack = useBackNavigate('/')

  if (!id) return null

  return (
    <div className={styles.page}>
      <button type="button" className={styles.backLink} onClick={goBack}>
        {t.common.back}
      </button>
      <RunDetailContent runId={id} />
    </div>
  )
}
