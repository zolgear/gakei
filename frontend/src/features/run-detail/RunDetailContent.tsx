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
import { UserAvatar } from '../../components/UserAvatar'
import { useRunFormContext } from '../../context/useRunFormContext'
import { useLineageOrigin } from '../../context/useLineageOrigin'
import { describeError } from '../run-status/errorMessages'
import { SaveToPromptSetButton } from '../prompt-sets/SaveToPromptSetButton'
import { SaveParameterSetButton } from '../parameter-sets/SaveParameterSetButton'
import { suggestSetNameFromPrompt } from '../prompt-sets/promptSetNaming'
import { allOperationDefs, buildRunSavePayload, runHasSeed } from '../parameter-sets/parameterSets'
import { buildParamLabelMap } from '../run-form/paramLabels'
import { omitServerOnlyParams, paramsForRerun } from '../run-form/paramsBuilder'
import { inputsFromRunInputs } from '../run-form/editInputs'
import { isLayoutSketchPrimaryRun, restoreRunRegions, stripAppendedText } from '../regions/regions'
import { RunRegionsSection } from '../regions/RunRegionsSection'
import {
  comfyuiPromptDownloadFilename,
  comfyuiPromptDownloadJson,
  extractComfyUiPrompt,
  comfyUiSeedTooltip,
  describeComfyUiSeed,
  extractComfyUiSeed,
  extractComfyUiWorkflowInfo,
  shortSha256,
} from './comfyuiPromptDisplay'
import {
  extractSdWebuiInfotext,
  extractSdWebuiRequest,
  extractSdWebuiSeed,
  sdwebuiRequestDownloadFilename,
  usageWithoutInfotext,
} from './sdwebuiRunDisplay'
import { runSeedDisplay } from './runSeedDisplay'
import { formatDateTime, formatDuration, statusLabel } from '../../lib/format'
import { formatUsd } from '../workspace/priceEstimateText'
import { buildStudioPath } from '../workspace/assetQueryParam'
import { nodeTargetPath } from '../lineage/nodeTargetPath'
import { fmt, useI18n } from '../../i18n'
import { StudioPromptActions } from '../workspace/StudioPromptActions'
import { FinalPromptSection } from './FinalPromptSection'
import { PromptBuildSection } from './PromptBuildSection'
import { ImportedRunSection } from '../lineage-transfer/ImportedRunSection'
import { baselineNegativePrompt, selectFinalPromptEntries } from './finalPrompt'
import { AssetThumb } from '../content-rating/AssetThumb'
import { RunModChecksSection } from '../mods/RunModChecksSection'
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
  const { setFormState, requestFormLoad } = useRunFormContext()
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
  // comfyui_* (ADR-0013) と sdwebui_* (ADR-0038) は通常のパラメータ一覧・生JSONブロックには出さず、
  // 下の専用の表示(ワークフロー名 + sha・seed・折りたたみJSON)にまとめる。
  const params = omitServerOnlyParams(rawParams) as Record<string, string | number | boolean>
  const paramLabels = buildParamLabelMap(capsQuery.data, run.model)
  const comfyuiWorkflow = extractComfyUiWorkflowInfo(rawParams)
  const comfyuiPrompt = extractComfyUiPrompt(rawParams)
  const comfyuiSeed = extractComfyUiSeed(rawParams)
  const sdwebuiRequest = extractSdWebuiRequest(rawParams)
  // SD WebUI の seed は1枚ごとに違うので、usage.all_seeds も見て1行にする(runSeedDisplay.ts)。
  const sdwebuiSeed = extractSdWebuiSeed(rawParams) !== null ? runSeedDisplay(run) : null
  const usage = (run.usage ?? null) as Record<string, unknown> | null
  const sdwebuiInfotext = extractSdWebuiInfotext(usage)
  // 最終プロンプト(ADR-0030)と、Run のプロンプトと違う出力ごとの展開後のプロンプト(ADR-0038 7章)。
  const finalPromptProps = {
    textOutputs: run.text_outputs,
    prompt: run.prompt,
    negativePrompt: baselineNegativePrompt(rawParams),
  }
  // 利用者が書いたプロンプト(領域の指示で足した文を除く。ADR-0053 5章)。
  const userPrompt = stripAppendedText(run.prompt, run.regions?.appended_text)
  const hasFinalPrompt = selectFinalPromptEntries(finalPromptProps).length > 0

  const thumbAssetDetails = new Map<string, AssetDetail>()
  thumbAssetIds.forEach((assetId, index) => {
    const data = thumbAssetQueries[index]?.data
    if (data) thumbAssetDetails.set(assetId, data)
  })

  function handleDownloadComfyuiPrompt() {
    const json = comfyuiPromptDownloadJson(rawParams)
    if (!json) return
    const blob = new Blob([json], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = comfyuiPromptDownloadFilename(run.id)
    a.click()
    URL.revokeObjectURL(url)
  }

  function handleDownloadSdwebuiRequest() {
    if (!sdwebuiRequest) return
    const blob = new Blob([JSON.stringify(sdwebuiRequest, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = sdwebuiRequestDownloadFilename(run.id)
    a.click()
    URL.revokeObjectURL(url)
  }

  function handleRerun() {
    // サーバーは comfyui_* / sdwebui_* をクライアントからの入力として受け付けない(422)ので、
    // 「同じ設定で新規作成」ではフォームへ戻す前に取り除く(seed 等の公開パラメーターは残り、
    // 実際に使った seed があれば seed に戻す)。
    // 領域の指示(ADR-0053 4章)。四角を戻し、サーバーが足した入力(四角を描いた画像と参照画像)と
    // プロンプトの末尾の足した文を除く。
    const restored = restoreRunRegions({ prompt: run.prompt, inputs, regions: run.regions })
    const state = {
      provider: run.provider,
      model: run.model,
      prompt: restored.prompt,
      params: paramsForRerun(rawParams) as Record<string, string | number | boolean>,
      inputs: inputsFromRunInputs(restored.inputs),
      // 削除済みのグループなら null(=「なし」)で返ってくる。
      assetGroupId: run.asset_group?.id ?? null,
      regions: restored.regions,
      regionsAssetId: restored.regionsAssetId,
      regionsFrame: restored.regionsFrame,
    }
    // context へ書くのは入力画像と、スタジオがまだマウントされていないときの初期値のため。
    // スタジオのフォームは context をマウント時にしか読まないので、結果エリアの Run 詳細
    // (フォームはマウントされたまま)からでも入るよう、読み込みのリクエストにも積む。
    setFormState(state)
    requestFormLoad({ kind: 'run', state })
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
        {run.imported && <span className={styles.importedBadge}>{t.lineageTransfer.importedRun.badge}</span>}
        <button type="button" className={styles.rerunButton} onClick={handleRerun}>
          {t.runDetail.rerun}
        </button>
        {/* パラメーターセットに保存(ADR-0040 3章)。サーバーだけが書く項目は除き、seed は「含める」の
            選択に従って実際に使った seed から戻す。プロンプトは Run のもの(Dynamic Prompts はテンプレート)。 */}
        <SaveParameterSetButton
          label={t.parameterSets.save.runButton}
          title={t.parameterSets.save.runButtonTitle}
          triggerClassName={styles.paramSetButton}
          buildPayload={(include) =>
            buildRunSavePayload(
              // 領域の指示で足した文は除く(四角は画像ごとのもので、パラメーターセットに入れない。ADR-0053 8章)。
              { provider: run.provider, model: run.model, prompt: userPrompt, params: rawParams },
              include,
              allOperationDefs(capsQuery.data, run.provider, run.model),
            )
          }
          seedAvailable={runHasSeed(rawParams)}
          seedUnavailableMessage={t.parameterSets.save.seedUnavailableRun}
          defaultName={suggestSetNameFromPrompt(userPrompt)}
        />
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

      {/* 系列の ZIP から取り込んだ記録(ADR-0037)。元の実行者と日時、未検証であること。 */}
      {run.imported && <ImportedRunSection info={run.imported} />}

      <section className={styles.section}>
        <div className={styles.sectionHeaderRow}>
          <h2 className={styles.heading}>{t.runDetail.promptHeading}</h2>
          <SaveToPromptSetButton text={userPrompt} />
        </div>
        {/* 領域の指示で足した文は、利用者のプロンプトと分けて、畳んで出す(ADR-0053 5章)。 */}
        <p className={styles.prompt}>{userPrompt}</p>
        {run.regions?.appended_text && userPrompt !== run.prompt && (
          <details className={styles.appendedText}>
            <summary>{t.regions.runDetail.appendedToggle}</summary>
            <p className={styles.prompt}>{run.regions.appended_text.trim()}</p>
          </details>
        )}
        {promptActions?.(userPrompt)}
      </section>

      {/* 領域の指示(ADR-0053 4章)。四角を付けた Run だけ。 */}
      {run.regions && (
        <RunRegionsSection
          regions={run.regions}
          sectionClassName={styles.section}
          headingClassName={styles.heading}
          assetDetails={thumbAssetDetails}
        />
      )}

      {/* プロンプトテンプレート(実験)で組み立てた Run だけ。どのスロットに何が選ばれたか。 */}
      {run.prompt_build && (
        <section className={styles.section}>
          <PromptBuildSection promptBuild={run.prompt_build} headingClassName={styles.heading} />
        </section>
      )}

      {/* 最終プロンプト(ADR-0030 3章)。スタジオ内では呼び出し側の挿入・置き換え(promptActions)を
          そのまま使い、スタジオの外ではリクエストを積んでスタジオへ移る(StudioPromptActions)。 */}
      {hasFinalPrompt && (
        <section className={styles.section}>
          <FinalPromptSection
            {...finalPromptProps}
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
            {run.primary_parent_asset_id && !isLayoutSketchPrimaryRun(run) && (
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
                <span className={styles.thumbFrame}>
                  <AssetThumb
                    assetId={output.asset_id}
                    rating={output.content_rating}
                    badge
                    withheld={output.content_withheld}
                    className={`${styles.thumb} checkerboard`}
                    alt=""
                    draggable={false}
                  />
                </span>
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
        {sdwebuiSeed && (
          <p className={styles.comfyuiInfo} title={sdwebuiSeed.tooltip}>
            {sdwebuiSeed.text}
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
        {sdwebuiRequest && (
          <details className={styles.comfyuiDetails}>
            <summary>
              {t.runDetail.sdwebuiRequestSummary}
              <button
                type="button"
                className={styles.comfyuiDownloadLink}
                onClick={(e) => {
                  // <summary> の既定動作(開閉のトグル)を親に持たせない。
                  e.preventDefault()
                  e.stopPropagation()
                  handleDownloadSdwebuiRequest()
                }}
              >
                {t.runDetail.downloadJson}
              </button>
            </summary>
            <pre className={styles.paramsBlock}>{JSON.stringify(sdwebuiRequest, null, 2)}</pre>
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
          {run.origin === 'assistant' && (
            <>
              <dt>{t.runDetail.origin}</dt>
              <dd>{t.runDetail.originAssistant}</dd>
            </>
          )}
          {run.origin === 'import' && (
            <>
              <dt>{t.runDetail.origin}</dt>
              <dd>{t.lineageTransfer.importedRun.badge}</dd>
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
            {sdwebuiInfotext && (
              <details className={styles.comfyuiDetails}>
                <summary>{t.runDetail.sdwebuiInfotextSummary}</summary>
                <pre className={styles.infotextBlock}>{sdwebuiInfotext}</pre>
              </details>
            )}
            <pre className={styles.paramsBlock}>
              {JSON.stringify(usage && sdwebuiInfotext ? usageWithoutInfotext(usage) : run.usage, null, 2)}
            </pre>
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

      {/* Mod の送る前のチェックと判定(ADR-0051 5章)。記録が無ければ出さない。 */}
      <RunModChecksSection run={run} sectionClassName={styles.section} headingClassName={styles.heading} />

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
                <span className={styles.thumbFrame}>
                  <AssetThumb
                    assetId={input.asset_id}
                    // マスクの入力は判定の対象外(ADR-0049 3章)。
                    rating={input.role === 'mask' ? undefined : input.content_rating}
                    badge
                    withheld={input.content_withheld}
                    className={`${styles.thumb} checkerboard`}
                    alt=""
                    draggable={false}
                  />
                </span>
                <span className={styles.thumbLabel}>
                  {input.role} #{input.position}
                  {input.role === 'image' && input.position === 0 && (
                    <span className={styles.primaryBadge}>{t.runDetail.primaryParent}</span>
                  )}
                  {/* 四角を描いた画像(ADR-0053 4章。サーバーが足した入力)。 */}
                  {(input.asset_id === run.regions?.overlay_asset_id ||
                    thumbAssetDetails.get(input.asset_id)?.kind === 'regions') && (
                    <span className={styles.primaryBadge}>{t.regions.runDetail.overlayTag}</span>
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
