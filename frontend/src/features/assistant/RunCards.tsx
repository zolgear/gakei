/**
 * `generate_image` の結果のカード(ADR-0048 2章・9章)。作った Run ごとに、進捗と出力の
 * サムネイルを見せる。サムネイルを選ぶと、スタジオの結果エリアに出す(`/studio?asset=`)。
 *
 * 状態は Run 詳細を数秒ごとに取り直して追い、実行中の間だけ Run の SSE(`useRunEvents`)で
 * 進捗を見る。1回の発言で 20 件の Run を作れるので、待機中の Run ごとに SSE をつなぐと
 * ブラウザの同時接続の上限に当たる。そのため SSE は実行中の Run だけにする。
 */
import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { getRun, type AssistantMessageResponse } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { focalStyle } from '../../lib/focalPoint'
import { GAKEI_ASSET_ID_DATA_TYPE } from '../run-form/dragDropAssets'
import { progressPercent } from '../run-status/runProgress'
import { useRunEvents } from '../run-status/useRunEvents'
import { buildStudioPath } from '../workspace/assetQueryParam'
import { effectiveRunStatus, isTerminalRunStatus, shouldWatchRunEvents } from './runCardState'
import { AssetThumb } from '../content-rating/AssetThumb'
import styles from './AssistantPanel.module.css'

const POLL_MS = 3000

export function RunCards({ message }: { message: AssistantMessageResponse }) {
  const { t } = useI18n()
  const r = t.assistant.runCard
  const result = message.result ?? {}
  const prompt = typeof result.prompt === 'string' ? result.prompt : ''
  const cost = typeof result.estimated_cost_usd === 'number' ? result.estimated_cost_usd : null
  const model = typeof result.model === 'string' ? result.model : ''
  const runIds = message.run_ids ?? []

  return (
    <div className={styles.card} data-kind="runs">
      <div className={styles.cardHeader}>
        <span className={styles.cardKind}>{fmt(r.heading, { count: runIds.length })}</span>
        {model && <span className={styles.cardTitle}>{model}</span>}
      </div>
      {prompt && <p className={styles.runPrompt}>{prompt}</p>}
      {cost !== null && <p className={styles.cardNote}>{fmt(r.cost, { cost: cost.toFixed(3) })}</p>}
      <ul className={styles.runList}>
        {runIds.map((runId) => (
          <RunCard key={runId} runId={runId} />
        ))}
      </ul>
    </div>
  )
}

function RunCard({ runId }: { runId: string }) {
  const { t } = useI18n()
  const r = t.assistant.runCard
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['run', runId],
    queryFn: () => getRun(runId),
    refetchInterval: (q) => (isTerminalRunStatus(q.state.data?.status) ? false : POLL_MS),
  })
  const run = query.data
  const watch = run ? shouldWatchRunEvents(run.status) : false
  const events = useRunEvents(watch ? runId : null)
  const sseStatus = events.latestStatus?.status ?? null
  const status = effectiveRunStatus(run?.status, sseStatus)

  // SSE で終わりを知ったら、出力を得るために詳細を取り直す。
  useEffect(() => {
    if (isTerminalRunStatus(sseStatus) && !isTerminalRunStatus(run?.status)) {
      void queryClient.invalidateQueries({ queryKey: ['run', runId] })
    }
  }, [sseStatus, run?.status, runId, queryClient])

  const outputs = run?.outputs ?? []
  const percent = events.progress ? progressPercent(events.progress) : null

  return (
    <li className={styles.runItem} data-status={status}>
      <div className={styles.runItemHeader}>
        <span className={styles.runStatus} data-status={status}>
          {t.common.statusLabels[status]}
        </span>
        {isTerminalRunStatus(status) && (
          <button type="button" className={styles.linkButton} onClick={() => navigate(`/runs/${runId}`)}>
            {r.detail}
          </button>
        )}
      </div>
      {!isTerminalRunStatus(status) && (
        // 終わるまでは、押すとスタジオの結果エリアで進捗を見られる枠を出す。
        <button
          type="button"
          className={styles.runPending}
          title={r.watch}
          aria-label={r.watch}
          onClick={() => navigate(buildStudioPath(null, null, runId))}
        >
          {status === 'running' && (
            <span className={styles.progressTrack} aria-hidden="true">
              <span
                className={styles.progressBar}
                data-indeterminate={percent === null ? true : undefined}
                style={percent !== null ? { width: `${percent}%` } : undefined}
              />
            </span>
          )}
        </button>
      )}
      {status === 'failed' && run?.error_message && <p className={styles.runError}>{run.error_message}</p>}
      {query.isError && <p className={styles.runError}>{r.loadFailed}</p>}
      {outputs.map((output) => (
        <button
          key={output.asset_id}
          type="button"
          className={styles.thumbButton}
          aria-label={r.showOutput}
          title={r.showOutput}
          onClick={() => navigate(buildStudioPath(output.asset_id))}
        >
          <AssetThumb
            assetId={output.asset_id}
            rating={output.content_rating}
            badge
            className={`${styles.thumb} checkerboard`}
            style={focalStyle(output.focal_point)}
            alt=""
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData(GAKEI_ASSET_ID_DATA_TYPE, output.asset_id)
              e.dataTransfer.effectAllowed = 'copy'
            }}
          />
        </button>
      ))}
    </li>
  )
}
