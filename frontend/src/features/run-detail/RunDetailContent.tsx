/**
 * Run 詳細の中身(状態バッジ、プロンプト、詳細設定、時刻・実行状況、エラー、入力/出力
 * サムネイル、操作ボタン)。`RunDetailPage`(フルページ)と系列グラフのインスペクター
 * パネル(`compact`)の両方から使う。
 *
 * 削除は履歴一覧からだけ行う(系列や詳細から消すのは、画像が見えず勘違いしやすいため)。
 * 「同じ設定で新規作成」は常にスタジオへ遷移する。
 */
import { useEffect, type ReactNode } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { Link, useLocation, useNavigate } from 'react-router'
import { getAsset, getCapabilities, getRun, type AssetDetail } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { UserAvatar } from '../../components/UserAvatar'
import { useRunFormContext } from '../../context/useRunFormContext'
import { useLineageOrigin } from '../../context/useLineageOrigin'
import { describeError } from '../run-status/errorMessages'
import { SaveToPromptSetButton } from '../prompt-sets/SaveToPromptSetButton'
import { buildParamLabelMap } from '../run-form/paramLabels'
import { omitComfyUiParams, paramsForRerun } from '../run-form/paramsBuilder'
import { inputsFromRunInputs } from '../run-form/editInputs'
import {
  comfyuiPromptDownloadFilename,
  extractComfyUiPrompt,
  comfyUiSeedTooltip,
  describeComfyUiSeed,
  extractComfyUiSeed,
  extractComfyUiWorkflowInfo,
  shortSha256,
} from './comfyuiPromptDisplay'
import { formatDateTime, formatDuration, statusLabel } from '../../lib/format'
import { formatUsd } from '../workspace/priceEstimateText'
import { buildStudioPath } from '../workspace/assetQueryParam'
import { nodeTargetPath } from '../lineage/nodeTargetPath'
import { fmt, useI18n } from '../../i18n'
import { StudioPromptActions } from '../workspace/StudioPromptActions'
import { FinalPromptSection } from './FinalPromptSection'
import { findFinalPrompt } from './finalPrompt'
import styles from './RunDetailContent.module.css'

export interface RunDetailContentProps {
  runId: string
  /** true でパネル用に余白・見出しを詰める(系列グラフのインスペクター向け)。 */
  compact?: boolean
  /**
   * プロンプト全文の直下に差し込む操作(スタジオの系列インスペクターの「プロンプトに挿入/
   * 置き換え」用)。プロンプト文字列はここで取得するまで呼び出し側には分からないため、
   * ReactNode ではなく関数(render prop)で受け取る。
   */
  promptActions?: (prompt: string) => ReactNode
}

export function RunDetailContent({ runId, compact = false, promptActions }: RunDetailContentProps) {
  const { t } = useI18n()
  const navigate = useNavigate()
  // スタジオにいる間(結果エリアやインスペクターに埋め込まれている間)は、入出力の
  // サムネイルもページを離れず結果エリアに表示する(nodeTargetPath.ts)。
  const location = useLocation()
  const { setFormState } = useRunFormContext()
  const { setOriginAssetId } = useLineageOrigin()

  const query = useQuery({
    queryKey: ['run', runId],
    queryFn: () => getRun(runId),
  })
  // params に表示名を添えるためだけの補助情報(証跡そのものはJSONのまま残す)。
  const capsQuery = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities })

  const lineageOriginAssetId =
    query.data?.outputs?.[0]?.asset_id ?? query.data?.primary_parent_asset_id ?? null

  // 系列グラフの起点(b): 先頭の出力。無ければ主たる親。インスペクター(compact)は
  // 別のページ(系列が主役)の中の一部なので、起点を横取りしない。
  useEffect(() => {
    if (!compact && lineageOriginAssetId) setOriginAssetId(lineageOriginAssetId)
  }, [compact, lineageOriginAssetId, setOriginAssetId])

  // 入力・出力それぞれの Asset が(Run 本体とは別に)個別に削除されていることがあるので、
  // サムネイルに「削除済み」タグを出すために取得する(RunOutputRef/RunInput は deleted_at
  // を持たないため)。hooks は早期 return より前で呼ぶ必要があるので、query.data が
  // まだ無い間は空配列にしておく。
  const thumbAssetIds = [
    ...(query.data?.inputs ?? []).map((i) => i.asset_id),
    ...(query.data?.outputs ?? []).map((o) => o.asset_id),
  ]
  const thumbAssetQueries = useQueries({
    queries: thumbAssetIds.map((assetId) => ({
      queryKey: ['asset', assetId],
      queryFn: () => getAsset(assetId),
    })),
  })

  if (query.isLoading) {
    return <div className={styles.content} data-compact={compact || undefined}>{t.runDetail.loading}</div>
  }
  if (query.isError || !query.data) {
    return (
      <div className={styles.content} data-compact={compact || undefined}>
        {t.runDetail.loadFailed}
      </div>
    )
  }

  const run = query.data
  const inputs = run.inputs ?? []
  const outputs = run.outputs ?? []
  const rawParams = (run.params ?? {}) as Record<string, unknown>
  // comfyui_* (ADR-0013) は通常のパラメータ一覧・生JSONブロックには出さず、下の専用の
  // 表示(ワークフロー名 + sha・折りたたみJSON)にまとめる。
  const params = omitComfyUiParams(rawParams) as Record<string, string | number | boolean>
  const paramLabels = buildParamLabelMap(capsQuery.data, run.model)
  const comfyuiWorkflow = extractComfyUiWorkflowInfo(rawParams)
  const comfyuiPrompt = extractComfyUiPrompt(rawParams)
  const comfyuiSeed = extractComfyUiSeed(rawParams)
  const finalPrompt = findFinalPrompt(run.text_outputs)

  const thumbAssetDetails = new Map<string, AssetDetail>()
  thumbAssetIds.forEach((assetId, index) => {
    const data = thumbAssetQueries[index]?.data
    if (data) thumbAssetDetails.set(assetId, data)
  })

  function handleDownloadComfyuiPrompt() {
    if (!comfyuiPrompt) return
    const blob = new Blob([JSON.stringify(comfyuiPrompt, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = comfyuiPromptDownloadFilename(run.id)
    a.click()
    URL.revokeObjectURL(url)
  }

  function handleRerun() {
    // サーバーは comfyui_* をクライアントからの入力として受け付けない(422)ので、
    // 「同じ設定で新規作成」ではフォームへ戻す前に取り除く(seed 等の公開パラメーターは残る)。
    setFormState({
      provider: run.provider,
      model: run.model,
      prompt: run.prompt,
      params: paramsForRerun(rawParams) as Record<string, string | number | boolean>,
      inputs: inputsFromRunInputs(inputs),
      // 削除済みのグループなら null(=「なし」)で返ってくる。
      assetGroupId: run.asset_group?.id ?? null,
    })
    navigate('/studio')
  }

  return (
    <div className={styles.content} data-compact={compact || undefined}>
      {run.deleted_at && (
        <div className={styles.deletedBanner}>
          {fmt(t.runDetail.deletedBanner, { deletedAt: formatDateTime(run.deleted_at) })}
        </div>
      )}

      <div className={styles.headerRow}>
        <span className={styles.statusBadge} data-status={run.status}>
          {statusLabel(run.status)}
        </span>
        <span className={styles.modelTag}>{run.model_label ?? run.model}</span>
        <button type="button" className={styles.rerunButton} onClick={handleRerun}>
          {t.runDetail.rerun}
        </button>
        {(run.status === 'queued' || run.status === 'running') && (
          <Link to={buildStudioPath(null, null, run.id)} className={styles.lineageLink}>
            {t.runDetail.watchProgress}
          </Link>
        )}
        {!compact && lineageOriginAssetId && (
          <Link to={`/lineage/${lineageOriginAssetId}`} className={styles.lineageLink}>
            {t.runDetail.viewLineageGraph}
          </Link>
        )}
        {compact && (
          <Link to={`/runs/${runId}`} className={styles.lineageLink}>
            {t.runDetail.openDetailPage}
          </Link>
        )}
      </div>

      <section className={styles.section}>
        <div className={styles.sectionHeaderRow}>
          <h2 className={styles.heading}>{t.runDetail.promptHeading}</h2>
          <SaveToPromptSetButton text={run.prompt} />
        </div>
        <p className={styles.prompt}>{run.prompt}</p>
        {promptActions?.(run.prompt)}
      </section>

      {/* 最終プロンプト(ADR-0030 3章)。スタジオ内では呼び出し側の挿入・置き換え(promptActions)を
          そのまま使い、スタジオの外ではリクエストを積んでスタジオへ移る(StudioPromptActions)。 */}
      {finalPrompt && (
        <section className={styles.section}>
          <FinalPromptSection
            textOutputs={run.text_outputs}
            headingLevel="h2"
            headingClassName={styles.heading}
            renderActions={(text) => (promptActions ? promptActions(text) : <StudioPromptActions prompt={text} />)}
          />
        </section>
      )}

      {/* 出力はパラメーター等より前に置く。ユーザーは「サムネイルを押したら画像が見たい」
          と期待するため、スクロールせずに辿れる位置にする。 */}
      {outputs.length > 0 && (
        <section className={styles.section}>
          <div className={styles.sectionHeaderRow}>
            <h2 className={styles.heading}>{t.runDetail.outputsHeading}</h2>
            {run.primary_parent_asset_id && (
              <Link
                to={`/runs/${runId}/compare?before=${run.primary_parent_asset_id}&after=${outputs[0].asset_id}`}
                className={styles.lineageLink}
              >
                {t.runDetail.compareWithOriginal}
              </Link>
            )}
          </div>
          <div className={styles.thumbGrid}>
            {outputs.map((output) => (
              <Link
                key={output.asset_id}
                to={nodeTargetPath({ id: output.asset_id, type: 'asset' }, location.pathname)}
                className={styles.thumbCard}
              >
                <img
                  className={`${styles.thumb} checkerboard`}
                  src={assetUrl(output.asset_id, 'thumb')}
                  alt=""
                  draggable={false}
                />
                <span className={styles.thumbLabel}>
                  #{output.output_index}
                  {thumbAssetDetails.get(output.asset_id)?.deleted_at && (
                    <span className={styles.deletedTag}>{t.runDetail.deletedTag}</span>
                  )}
                </span>
              </Link>
            ))}
          </div>
        </section>
      )}

      <section className={styles.section}>
        <h2 className={styles.heading}>{t.runDetail.detailSettingsHeading}</h2>
        {comfyuiWorkflow && (
          <p className={styles.comfyuiInfo} title={comfyUiSeedTooltip(outputs.length)}>
            {fmt(t.runDetail.workflowLine, {
              name: comfyuiWorkflow.name,
              sha: shortSha256(comfyuiWorkflow.template_sha256),
            })}
            {comfyuiSeed !== null && ` / ${describeComfyUiSeed(comfyuiSeed, outputs.length)}`}
          </p>
        )}
        {Object.keys(params).length > 0 && (
          <ul className={styles.paramsAnnotated}>
            {Object.entries(params).map(([key, value]) => (
              <li key={key}>
                <span className={styles.paramLabel}>{paramLabels.get(key) ?? key}</span>
                <span className={styles.paramName}>{key}</span>
                <span className={styles.paramValue}>{String(value)}</span>
              </li>
            ))}
          </ul>
        )}
        <pre className={styles.paramsBlock}>{JSON.stringify(params, null, 2)}</pre>
        {comfyuiPrompt && (
          <details className={styles.comfyuiDetails}>
            <summary>
              {t.runDetail.comfyuiPromptSummary}
              <button
                type="button"
                className={styles.comfyuiDownloadLink}
                onClick={(e) => {
                  // <summary> の既定動作(開閉のトグル)を親に持たせない。
                  e.preventDefault()
                  e.stopPropagation()
                  handleDownloadComfyuiPrompt()
                }}
              >
                {t.runDetail.downloadJson}
              </button>
            </summary>
            <pre className={styles.paramsBlock}>{JSON.stringify(comfyuiPrompt, null, 2)}</pre>
          </details>
        )}
      </section>

      <section className={styles.section}>
        <h2 className={styles.heading}>{t.runDetail.timingHeading}</h2>
        <dl className={styles.metaList}>
          {run.created_by && (
            <>
              <dt>{t.runDetail.createdBy}</dt>
              <dd className={styles.createdByValue}>
                {run.created_by.avatar_url && (
                  <UserAvatar
                    name={run.created_by.name ?? run.created_by.email}
                    avatarUrl={run.created_by.avatar_url}
                    size={18}
                  />
                )}
                <span>{run.created_by.name ?? run.created_by.email}</span>
              </dd>
            </>
          )}
          {run.origin === 'mcp' && (
            <>
              <dt>{t.runDetail.origin}</dt>
              <dd>{t.runDetail.originMcp}</dd>
            </>
          )}
          <dt>API</dt>
          <dd className={styles.mono}>{run.operation}</dd>
          <dt>queued_at</dt>
          <dd>{formatDateTime(run.queued_at)}</dd>
          <dt>started_at</dt>
          <dd>{formatDateTime(run.started_at)}</dd>
          <dt>finished_at</dt>
          <dd>{formatDateTime(run.finished_at)}</dd>
          <dt>{t.runDetail.duration}</dt>
          <dd>{formatDuration(run.started_at, run.finished_at)}</dd>
          <dt>provider_request_id</dt>
          <dd className={styles.mono}>{run.provider_request_id ?? '-'}</dd>
        </dl>
        {run.usage && (
          <>
            <h3 className={styles.subheading}>{t.runDetail.usageHeading}</h3>
            {run.cost_usd !== null && run.cost_usd !== undefined && (
              <p className={styles.costLine}>{fmt(t.runDetail.estimatedCost, { amount: formatUsd(run.cost_usd) })}</p>
            )}
            <pre className={styles.paramsBlock}>{JSON.stringify(run.usage, null, 2)}</pre>
          </>
        )}
      </section>

      {run.status === 'failed' && (
        <section className={styles.section}>
          <h2 className={styles.heading}>{t.runDetail.errorHeading}</h2>
          <div className={styles.errorBox}>
            <p>{describeError(run.error_code, run.error_message)}</p>
            {run.error_code && <p className={styles.mono}>{fmt(t.runDetail.errorCodeLine, { code: run.error_code })}</p>}
          </div>
        </section>
      )}

      {inputs.length > 0 && (
        <section className={styles.section}>
          <h2 className={styles.heading}>{t.runDetail.inputsHeading}</h2>
          <div className={styles.thumbGrid}>
            {inputs.map((input) => (
              <Link
                key={`${input.asset_id}-${input.position}`}
                to={nodeTargetPath({ id: input.asset_id, type: 'asset' }, location.pathname)}
                className={styles.thumbCard}
              >
                <img
                  className={`${styles.thumb} checkerboard`}
                  src={assetUrl(input.asset_id, 'thumb')}
                  alt=""
                  draggable={false}
                />
                <span className={styles.thumbLabel}>
                  {input.role} #{input.position}
                  {input.role === 'image' && input.position === 0 && (
                    <span className={styles.primaryBadge}>{t.runDetail.primaryParent}</span>
                  )}
                  {thumbAssetDetails.get(input.asset_id)?.deleted_at && (
                    <span className={styles.deletedTag}>{t.runDetail.deletedTag}</span>
                  )}
                </span>
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
