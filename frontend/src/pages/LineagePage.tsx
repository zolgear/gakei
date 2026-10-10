/**
 * 系列グラフの大きい表示(`/lineage/:assetId?node=<id>`)。系列が主役のページなので、
 * ここではノードをクリックしてもページ遷移せず、右側(モバイルは下)のインスペクターで
 * その場の内容(Run 詳細 / Asset プレビュー)を見る。選択中ノードは `?node=` に同期し、
 * 戻る/進むで再現できる。サイドバーに系列パネルが開いていると同じグラフが二重に出るため、
 * このページを開いている間はサイドバー側を自動で畳む(離れたら元に戻す)。
 */
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useLocation, useNavigate, useParams } from 'react-router'
import { getAssetLineage } from '../api/client'
import { useBackNavigate } from '../lib/useBackNavigate'
import { useResourcePanel } from '../context/useResourcePanel'
import { LineageGraph } from '../features/lineage/LineageGraph'
import { LineageInspectorPanel } from '../features/lineage/LineageInspectorPanel'
import { resolveInspectorContent, resolveOpenRunTarget } from '../features/lineage/inspectorContent'
import { parseNodeIdFromSearch, buildLineagePath } from '../features/lineage/nodeQueryParam'
import { useI18n } from '../i18n'
import styles from './LineagePage.module.css'

export function LineagePage() {
  const { t } = useI18n()
  const { assetId } = useParams<{ assetId: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const goBack = useBackNavigate('/')
  const { selectedPanel, collapsePanel, openPanel } = useResourcePanel()

  // サイドバーの系列パネルが開いていたら、このページを見ている間だけ畳む(二重表示回避)。
  // 離れたら(アンマウント時)元に戻す。
  useEffect(() => {
    if (selectedPanel === 'graph') {
      collapsePanel()
      return () => openPanel('graph')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const nodeId = parseNodeIdFromSearch(location.search)

  const lineageQuery = useQuery({
    queryKey: ['lineage', assetId],
    queryFn: () => getAssetLineage(assetId as string),
    enabled: assetId !== undefined,
  })

  if (!assetId) return null

  const content = resolveInspectorContent(nodeId, lineageQuery.data?.nodes ?? [])

  function selectNode(id: string | null) {
    navigate(buildLineagePath(assetId as string, id))
  }

  // Asset のインスペクターの「Generated の詳細を開く」: グラフにある Run ならその場で切り替える。
  function openRun(runId: string) {
    const target = resolveOpenRunTarget(runId, lineageQuery.data?.nodes ?? [])
    if (target.kind === 'select') selectNode(target.nodeId)
    else navigate(target.path)
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <button type="button" className={styles.backButton} onClick={goBack}>
          {t.common.back}
        </button>
        <h1 className={styles.title}>{t.common.lineagePage.title}</h1>
        <Link to={`/assets/${assetId}`} className={styles.rootLink}>
          {t.common.lineagePage.viewRootInViewer}
        </Link>
      </div>
      <div className={styles.body}>
        <div className={styles.canvasArea}>
          <LineageGraph
            assetId={assetId}
            height="100%"
            highlightedNodeId={nodeId}
            onNodeClick={(node) => selectNode(node.id)}
          />
        </div>
        <LineageInspectorPanel content={content} onClose={() => selectNode(null)} onOpenRun={openRun} />
      </div>
    </div>
  )
}
