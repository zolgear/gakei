/**
 * 系列グラフの埋め込み(未検証)ノード(ADR-0014 6章、`LineageNode.embedded`)のインスペクター
 * 内容。この環境の DB 行ではなく、祖先の PNG に埋め込まれていたグラフ(自己申告)から
 * 組み立てたノードなので、`getAsset`/`getRun` は呼ばず、渡された `node`(系列グラフの
 * クエリ結果に既に含まれる `embedded_detail`)だけで描画する。値はすべて未検証の自己申告
 * (誰でも書き換えられる)であるため、型を確認してから使う(`originRecipe.ts` と同じ方針)。
 *
 * Run ノードの「この設定をフォームに読み込む」は `RecipeLoadButton`
 * (`OriginRecipeSection` と共有)を使う。
 */
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import type { LineageNode, RunStatus } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { msg, useI18n } from '../../i18n'
import { shortSha256 } from '../run-detail/comfyuiPromptDisplay'
import { assetKindLabel, formatDateTime, statusLabel } from '../../lib/format'
import { extractRunInfoFromEmbeddedNode } from './originRecipe'
import { RecipeLoadButton } from './RecipeLoadButton'
import styles from './EmbeddedNodeInspector.module.css'

export interface EmbeddedNodeInspectorProps {
  node: LineageNode
  /** スタジオの系列インスペクターの「プロンプトに挿入/置き換え」(Run のみ)。 */
  renderPromptActions?: (prompt: string) => ReactNode
}

const RUN_STATUSES = new Set<RunStatus>(['queued', 'running', 'succeeded', 'failed', 'canceled'])

function str(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** 未検証の自己申告 status を、既知の値だけラベル化する(壊れていれば生の文字列か「不明」)。 */
function statusOrRaw(value: unknown): string {
  const s = str(value)
  if (s === null) return msg().lineage.unknownValue
  return RUN_STATUSES.has(s as RunStatus) ? statusLabel(s as RunStatus) : s
}

function operationOrRaw(value: unknown): string {
  const s = str(value)
  if (s === null) return msg().lineage.unknownValue
  const labels = msg().comfyui.operationLabels as Record<string, string>
  return labels[s] ?? s
}

export function EmbeddedNodeInspector({ node, renderPromptActions }: EmbeddedNodeInspectorProps) {
  const { t } = useI18n()
  const detail = (node.embedded_detail ?? {}) as Record<string, unknown>

  return (
    <div className={styles.content}>
      <p className={styles.banner}>{t.lineage.originRecipe.heading}</p>
      <dl className={styles.metaList}>
        <dt>{t.lineage.embeddedNodeTypeLabel}</dt>
        <dd>{node.type === 'run' ? 'Generated' : 'Asset'}</dd>
        <dt>id</dt>
        <dd className={styles.mono}>{node.id}</dd>
        <dt>{t.lineage.embeddedInstanceLabel}</dt>
        <dd className={styles.mono}>{node.instance ?? t.lineage.unknownValue}</dd>
      </dl>

      {node.type === 'asset' ? (
        <AssetSection node={node} detail={detail} />
      ) : (
        <RunSection
          detail={detail}
          modelLabel={node.run?.model_label}
          renderPromptActions={renderPromptActions}
        />
      )}

      <p className={styles.note}>{t.lineage.originRecipe.inputsNotRestored}</p>
    </div>
  )
}

function AssetSection({ node, detail }: { node: LineageNode; detail: Record<string, unknown> }) {
  const { t } = useI18n()
  const kind = assetKindLabel(str(detail.kind))
  const mime = str(detail.mime)
  const width = num(detail.width)
  const height = num(detail.height)
  const sha256 = str(detail.sha256)
  const createdAt = str(detail.created_at)
  const resolvedAssetId = node.resolved_asset_id ?? null

  return (
    <>
      {resolvedAssetId ? (
        <img
          className={`${styles.preview} checkerboard`}
          src={assetUrl(resolvedAssetId, 'preview')}
          alt=""
          draggable={false}
        />
      ) : (
        <p className={styles.placeholder}>{t.lineage.notIngestedPlaceholder}</p>
      )}
      <dl className={styles.metaList}>
        {kind && (
          <>
            <dt>{t.lineage.kindLabel}</dt>
            <dd>{kind}</dd>
          </>
        )}
        {width !== null && height !== null && (
          <>
            <dt>{t.lineage.dimensions}</dt>
            <dd>
              {width} × {height} px
            </dd>
          </>
        )}
        {mime && (
          <>
            <dt>{t.lineage.format}</dt>
            <dd>{mime}</dd>
          </>
        )}
        {sha256 && (
          <>
            <dt>sha256</dt>
            <dd className={styles.mono}>{shortSha256(sha256)}…</dd>
          </>
        )}
        {createdAt && (
          <>
            <dt>{t.lineage.created}</dt>
            <dd>{formatDateTime(createdAt)}</dd>
          </>
        )}
      </dl>
      {resolvedAssetId && (
        <Link to={`/assets/${resolvedAssetId}`} className={styles.actionButton}>
          {t.lineage.openIngestedAsset}
        </Link>
      )}
    </>
  )
}

function RunSection({
  detail,
  modelLabel,
  renderPromptActions,
}: {
  detail: Record<string, unknown>
  /** ComfyUI のワークフロー名など、表示用のモデル名(ADR-0013)。無ければ model を出す。 */
  modelLabel?: string | null
  renderPromptActions?: (prompt: string) => ReactNode
}) {
  const { t } = useI18n()
  const runInfo = extractRunInfoFromEmbeddedNode(detail)
  const operation = operationOrRaw(detail.operation)
  const status = statusOrRaw(detail.status)
  const finishedAt = formatDateTime(str(detail.finished_at))

  return (
    <>
      <dl className={styles.metaList}>
        <dt>provider / model</dt>
        <dd className={styles.mono}>
          {runInfo?.provider ?? t.lineage.unknownValue} /{' '}
          {modelLabel ?? runInfo?.model ?? t.lineage.unknownValue}
        </dd>
        <dt>operation</dt>
        <dd>{operation}</dd>
        <dt>{t.lineage.statusLabel}</dt>
        <dd>{status}</dd>
        <dt>{t.lineage.finishedAtLabel}</dt>
        <dd>{finishedAt}</dd>
      </dl>

      {runInfo?.prompt && (
        <>
          <h3 className={styles.subheading}>{t.lineage.promptHeading}</h3>
          {runInfo.prompt.length > 80 ? (
            <details className={styles.promptDetails}>
              <summary>{runInfo.prompt.slice(0, 80)}…</summary>
              <p className={styles.prompt}>{runInfo.prompt}</p>
            </details>
          ) : (
            <p className={styles.prompt}>{runInfo.prompt}</p>
          )}
          {renderPromptActions?.(runInfo.prompt)}
        </>
      )}

      {runInfo && Object.keys(runInfo.params).length > 0 && (
        <>
          <h3 className={styles.subheading}>{t.lineage.paramsHeading}</h3>
          <ul className={styles.paramsList}>
            {Object.entries(runInfo.params).map(([key, value]) => (
              <li key={key}>
                <span className={styles.paramName}>{key}</span>
                <span className={styles.paramValue}>{String(value)}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      <RecipeLoadButton runInfo={runInfo} />
    </>
  )
}
