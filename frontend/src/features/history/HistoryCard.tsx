/**
 * 履歴の「ノード風カード」。生成/編集は UI 上で区別しない(ADR-0009)ので GENERATE/EDIT の
 * 大きなラベルは出さない。入力画像があれば「入力 n」を表示し、色点でも入力の有無を示す
 * (水色=入力あり、アクセント色=入力なし)。
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router'
import { ApiError, cancelRun, deleteRun, getRun, type RunSummary } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { UserAvatar } from '../../components/UserAvatar'
import { RunDetailIcon } from '../../components/icons'
import { useRunFormContext } from '../../context/useRunFormContext'
import { useLineageOrigin } from '../../context/useLineageOrigin'
import { useResourcePanel } from '../../context/useResourcePanel'
import { useAddToInputs } from '../run-form/useAddToInputs'
import { AddToInputsDialog } from '../run-form/AddToInputsDialog'
import { paramsForRerun } from '../run-form/paramsBuilder'
import { inputsFromRunInputs } from '../run-form/editInputs'
import { restoreRunRegions, stripAppendedText } from '../regions/regions'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { removeItemFromPagedData, type InfiniteQueryData } from '../../lib/queryCache'
import {
  formatDateTime,
  formatDuration,
  formatElapsedSeconds,
  shortenModelName,
  statusLabel,
} from '../../lib/format'
import { buildParamChips, formatTokenCount } from './historyChips'
import { canDeleteRun } from './runDeletion'
import { buildRunDeleteWarning } from './runDeleteWarning'
import { resolveHistoryCardNavigation } from './historyNavigation'
import { HistoryCardMedia } from './HistoryCardMedia'
import { fmt, useI18n } from '../../i18n'
import { AssetThumb } from '../content-rating/AssetThumb'
import styles from './HistoryCard.module.css'
import { focalStyle } from '../../lib/focalPoint'

/**
 * カードの見た目。`detail` は上に画像、下にプロンプトと操作を並べる形。`overlay` は履歴ページの
 * 「画像」の形で、同じ大きさのカードいっぱいに画像を敷き、ヘッダーとプロンプト・操作をその上に重ねる。
 * 中身(行き先、操作、ダイアログ)は同じで、違いは CSS と親のサムネイルの置き場所だけ。
 */
export type HistoryCardVariant = 'detail' | 'overlay'

interface HistoryCardProps {
  run: RunSummary
  variant?: HistoryCardVariant
  /**
   * 見せる出力の番号(`run.outputs` の添字)。重ねる形では出力ごとにカードを分けて並べる
   * (`expandHistoryCards`)。画像の行き先・タイトル・「入力に使う」はこの出力のもの。
   * 削除などの操作は Run 単位のまま。
   */
  outputIndex?: number
}

function RerunIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M3 8a5 5 0 1 0 1.6-3.7M3 2.2v3.3h3.3"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function UseAsInputIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M10.5 2.5l3 3L6 13H3v-3z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function TrashIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M3 4.5h10M6.5 4.5V3a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v1.5M4.5 4.5v8a1 1 0 0 0 1 1h5a1 1 0 0 0 1-1v-8M6.5 7.5v3.5M9.5 7.5v3.5"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function LineageIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <rect x="2" y="2.5" width="5" height="4" rx="1" stroke="currentColor" strokeWidth="1.5" />
      <rect x="11" y="7" width="5" height="4" rx="1" stroke="currentColor" strokeWidth="1.5" />
      <rect x="2" y="11.5" width="5" height="4" rx="1" stroke="currentColor" strokeWidth="1.5" />
      <path
        d="M7 4.5c3 0 1.5 4.5 4 4.5M7 13.5c3 0 1.5-4.5 4-4.5"
        stroke="currentColor"
        strokeWidth="1.5"
      />
    </svg>
  )
}

export function HistoryCard({ run, variant = 'detail', outputIndex = 0 }: HistoryCardProps) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { setFormState } = useRunFormContext()
  const { setOriginAssetId } = useLineageOrigin()
  const { openPanel } = useResourcePanel()
  const addToInputs = useAddToInputs()
  const [rerunError, setRerunError] = useState<string | null>(null)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const outputs = run.outputs ?? []
  const shownOutput = outputs[outputIndex] ?? outputs[0]
  const hasInputs = run.input_count > 0
  const accentClass = hasInputs ? styles.dotEdit : styles.dotGenerate
  const paramChips = buildParamChips(run.params)
  // 領域の指示(ADR-0053): 足した文はプロンプトから除いて見せ、四角を送ったことをチップで示す。
  const shownPrompt = stripAppendedText(run.prompt, run.regions?.appended_text)
  const regionCount = run.regions?.regions.length ?? 0
  const tokenLabel = formatTokenCount(run.usage)
  const parentAssetId = run.primary_parent_asset_id
  const extraInputCount = run.input_count > 1 ? run.input_count - 1 : 0

  // 画像領域は出力があればそのビューアへ、出力が無く実行中・待機中ならスタジオの生成画面へ、
  // それ以外は Run 詳細へ。プロンプト部分は常に Run 詳細へ。「系列を見る」は辿れる起点
  // (出力、無ければ主たる親)があるときだけ出す(失敗した Run にも出せるように)。
  const nav = resolveHistoryCardNavigation({
    runId: run.id,
    status: run.status,
    firstOutputAssetId: shownOutput?.asset_id ?? null,
    primaryParentAssetId: parentAssetId ?? null,
  })

  const cancelMutation = useMutation({
    mutationFn: () => cancelRun(run.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['runs'] })
    },
  })

  const rerunMutation = useMutation({
    mutationFn: () => getRun(run.id),
    onSuccess: (detail) => {
      setRerunError(null)
      // comfyui_*(ADR-0013)はサーバーが 422 で拒否するので、フォームへ戻す前に取り除く。
      // 実際に使った seed(comfyui_seed)があれば seed として固定する(同じ画像を再現できる
      // ように。ランダムに戻すのは seed の専用欄のトグル1つ)。
      // 領域の指示(ADR-0053 4章)。四角を戻し、サーバーが足した入力とプロンプトの末尾の文を除く。
      const restored = restoreRunRegions({ prompt: detail.prompt, inputs: detail.inputs ?? [], regions: detail.regions })
      setFormState({
        provider: detail.provider,
        model: detail.model,
        prompt: restored.prompt,
        params: paramsForRerun((detail.params ?? {}) as Record<string, unknown>) as Record<
          string,
          string | number | boolean
        >,
        inputs: inputsFromRunInputs(restored.inputs),
        // 削除済みのグループなら null(=「なし」)で返ってくる。
        assetGroupId: detail.asset_group?.id ?? null,
        regions: restored.regions,
        regionsAssetId: restored.regionsAssetId,
      })
      navigate('/studio')
    },
    onError: (err: unknown) => {
      setRerunError(err instanceof ApiError ? err.message : t.history.card.rerunFailed)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteRun(run.id),
    onSuccess: () => {
      setDeleteConfirmOpen(false)
      setDeleteError(null)
      queryClient.setQueryData<InfiniteQueryData<RunSummary>>(['runs'], (old) =>
        removeItemFromPagedData(old, run.id),
      )
      // 出力 Asset も削除されたので、ストック等のキャッシュも整合させる。
      queryClient.invalidateQueries({ queryKey: ['assets'] })
    },
    onError: (err: unknown) => {
      setDeleteConfirmOpen(false)
      setDeleteError(err instanceof ApiError ? err.message : t.history.card.deleteFailed)
    },
  })

  function handleShowLineage() {
    const originAssetId = nav.lineageOriginAssetId
    if (!originAssetId) return
    setOriginAssetId(originAssetId)
    // モバイルはドロワー内の小さいグラフだと実用的でないので、大きい表示へ直接遷移する。
    const isMobile = typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches
    if (isMobile) {
      navigate(`/lineage/${originAssetId}`)
    } else {
      openPanel('graph')
    }
  }

  const pixelSize =
    run.status === 'succeeded' && shownOutput?.width && shownOutput?.height
      ? `${shownOutput.width}×${shownOutput.height}`
      : null

  const elapsed = run.status === 'running' ? formatElapsedSeconds(run.started_at) : null

  const parentOverlay = parentAssetId && (
    <div className={styles.parentOverlay}>
      <span className={styles.parentThumbFrame}>
        <AssetThumb
          assetId={parentAssetId}
          rating={run.primary_parent_content_rating}
          withheld={run.primary_parent_content_withheld}
          className={styles.parentThumb}
          style={focalStyle(run.primary_parent_focal_point)}
          alt={t.history.card.parent}
          draggable={false}
        />
      </span>
      <span>
        {extraInputCount > 0 ? fmt(t.history.card.parentWithExtra, { extra: extraInputCount }) : t.history.card.parent}
      </span>
    </div>
  )

  return (
    <article className={styles.card} data-highlight={run.status === 'running'} data-variant={variant}>
      <div className={styles.header}>
        <span className={`${styles.dot} ${accentClass}`} aria-hidden="true" />
        {hasInputs && (
          <span className={styles.inputBadge}>{fmt(t.history.card.inputBadge, { count: run.input_count })}</span>
        )}
        {/* アシスタントが作った Run(ADR-0048 9章)。実行元は Run 詳細にも出す。 */}
        {run.origin === 'assistant' && <span className={styles.inputBadge}>{t.history.card.originAssistant}</span>}
        <span className={styles.model}>{run.model_label ?? shortenModelName(run.model)}</span>
        <span className={styles.status} data-status={run.status}>
          {statusLabel(run.status)}
        </span>
      </div>

      <div className={styles.media}>
        <HistoryCardMedia run={run} output={shownOutput} href={nav.mediaHref} variant={variant} />

        {variant === 'detail' && parentOverlay}
      </div>

      <div className={styles.body}>
        {/* 重ねる形では、画像の左下は下の帯に隠れるので、親のサムネイルと出力の大きさを帯の先頭に置く。 */}
        {variant === 'overlay' && (parentOverlay || pixelSize) && (
          <div className={styles.overlayLead}>
            {parentOverlay}
            {pixelSize && <span className={styles.pixelSize}>{pixelSize}</span>}
          </div>
        )}
        <Link
          to={nav.runHref}
          className={styles.promptLink}
          aria-label={
            run.status === 'queued' || run.status === 'running'
              ? t.history.card.watchProgress
              : t.history.card.viewDetail
          }
        >
          {/* 先頭出力のタイトル(ADR-0024)。あればプロンプトを 1 行に縮め、2 行分の高さに収める
              (カードの高さを揃えたまま)。 */}
          {shownOutput?.title ? (
            <div className={styles.titledPrompt}>
              <p className={styles.title} title={shownOutput.title}>
                {shownOutput.title}
              </p>
              <p className={styles.promptOneLine}>{shownPrompt || t.history.card.noPrompt}</p>
            </div>
          ) : (
            <p className={styles.prompt}>{shownPrompt || t.history.card.noPrompt}</p>
          )}
        </Link>
        <div className={styles.meta}>
          {formatDateTime(run.queued_at)}
          {run.finished_at && ` · ${formatDuration(run.started_at, run.finished_at)}`}
          {!run.finished_at && elapsed && ` · ${elapsed}`}
          {tokenLabel && ` · ${tokenLabel}`}
          {run.created_by && (
            <span className={styles.byRow}>
              {' · '}
              {run.created_by.avatar_url && (
                <UserAvatar
                  name={run.created_by.name ?? run.created_by.email}
                  avatarUrl={run.created_by.avatar_url}
                  size={16}
                />
              )}
              {fmt(t.history.card.by, { name: run.created_by.name ?? run.created_by.email ?? '-' })}
            </span>
          )}
        </div>

        {(paramChips.length > 0 || regionCount > 0) && (
          <div className={styles.chipRow}>
            {regionCount > 0 && (
              <span className={styles.chip} title={t.regions.runDetail.delivery}>
                {fmt(t.regions.form.chip, { count: regionCount })}
              </span>
            )}
            {paramChips.map((chip) => (
              <span key={chip} className={styles.chip}>
                {chip}
              </span>
            ))}
          </div>
        )}

        <div className={styles.actions}>
          {run.status === 'queued' && (
            <button
              type="button"
              className={styles.textButton}
              onClick={() => cancelMutation.mutate()}
              disabled={cancelMutation.isPending}
            >
              {t.history.card.cancel}
            </button>
          )}
          <span className={styles.actionsSpacer} />
          {/* プロンプトの文字のリンクと同じ遷移先(Run 詳細)を、アイコンからも開けるようにする。
              状態によらず出す(失敗・中止・実行中の Run にも詳細はある)。 */}
          <Link
            to={nav.runHref}
            className={styles.iconButton}
            aria-label={t.common.openGeneratedDetail}
            title={t.common.openGeneratedDetail}
          >
            <RunDetailIcon size={15} />
          </Link>
          {nav.showLineageButton && (
            <button
              type="button"
              className={styles.iconButton}
              aria-label={t.history.card.showLineage}
              onClick={handleShowLineage}
            >
              <LineageIcon />
            </button>
          )}
          {run.status !== 'queued' && run.status !== 'running' && (
            <button
              type="button"
              className={styles.iconButton}
              aria-label={t.history.card.rerun}
              onClick={() => rerunMutation.mutate()}
              disabled={rerunMutation.isPending}
            >
              <RerunIcon />
            </button>
          )}
          {run.status === 'succeeded' && shownOutput && (
            <button
              type="button"
              className={`${styles.iconButton} ${styles.iconButtonEdit}`}
              aria-label={t.history.card.useAsInput}
              onClick={() => addToInputs.request(shownOutput.asset_id)}
            >
              <UseAsInputIcon />
            </button>
          )}
          {canDeleteRun(run.status) && (
            <button
              type="button"
              className={`${styles.iconButton} ${styles.iconButtonDanger}`}
              aria-label={t.history.card.delete}
              onClick={() => setDeleteConfirmOpen(true)}
              disabled={deleteMutation.isPending}
            >
              <TrashIcon />
            </button>
          )}
        </div>
        {rerunError && <p className={styles.errorText}>{rerunError}</p>}
        {deleteError && <p className={styles.errorText}>{deleteError}</p>}
      </div>

      <AddToInputsDialog
        open={addToInputs.isPending}
        onAdd={() => addToInputs.resolve('add')}
        onReplace={() => addToInputs.resolve('replace')}
        onCancel={addToInputs.cancel}
      />
      <ConfirmDialog
        open={deleteConfirmOpen}
        message={fmt(t.history.card.deleteConfirm.message, { count: outputs.length })}
        warning={buildRunDeleteWarning(run.descendant_run_count)}
        previewImageUrl={shownOutput ? assetUrl(shownOutput.asset_id, 'thumb') : undefined}
        previewWithheld={shownOutput?.content_withheld}
        previewFocalPoint={shownOutput?.focal_point}
        onConfirm={() => deleteMutation.mutate()}
        onCancel={() => setDeleteConfirmOpen(false)}
      />
    </article>
  )
}
