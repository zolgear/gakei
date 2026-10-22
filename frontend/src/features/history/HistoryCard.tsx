/**
 * 履歴の「ノード風カード」。生成/編集は UI 上で区別しない(ADR-0009)ので GENERATE/EDIT の
 * 大きなラベルは出さない。入力画像があれば「入力 n」を表示し、色点でも入力の有無を示す
 * (水色=入力あり、アクセント色=入力なし)。
 */
import { Link } from 'react-router'
import type { RunSummary } from '../../api/client'
import { UserAvatar } from '../../components/UserAvatar'
import { stripAppendedText } from '../regions/regions'
import {
  formatDateTime,
  formatDuration,
  formatElapsedSeconds,
  shortenModelName,
  statusLabel,
} from '../../lib/format'
import { buildParamChips, buildParamChipsWithActualSize, formatTokenCount } from './historyChips'
import { resolveHistoryCardNavigation } from './historyNavigation'
import { HistoryCardMedia } from './HistoryCardMedia'
import { useHistoryRunActions } from './useHistoryRunActions'
import { fmt, useI18n } from '../../i18n'
import { AssetThumb } from '../content-rating/AssetThumb'
import styles from './HistoryCard.module.css'
import { focalStyle } from '../../lib/focalPoint'

/**
 * カードの見た目。`detail` は上に画像、下にプロンプトと操作を並べる従来の形(検索ページ)。
 * `overlay` は履歴ページの「詳細」の形で、カードいっぱいに画像を敷き、ヘッダーとプロンプト・操作を
 * その上に重ねる。中身(行き先、操作、ダイアログ)は同じで、違いは CSS と親のサムネイルの置き場所だけ。
 */
export type HistoryCardVariant = 'detail' | 'overlay'

interface HistoryCardProps {
  run: RunSummary
  variant?: HistoryCardVariant
  /**
   * 見せる出力の番号(`run.outputs` の添字)。履歴ページでは出力ごとにカードを分けて並べる
   * (`expandHistoryCards`)。画像の行き先・タイトル・「入力に使う」はこの出力のもの。
   * 削除などの操作は Run 単位のまま。
   */
  outputIndex?: number
}

export function HistoryCard({ run, variant = 'detail', outputIndex = 0 }: HistoryCardProps) {
  const { t } = useI18n()
  const outputs = run.outputs ?? []
  const shownOutput = outputs[outputIndex] ?? outputs[0]
  const hasInputs = run.input_count > 0
  const accentClass = hasInputs ? styles.dotEdit : styles.dotGenerate
  // 重ねるカードでは、サイズを出力の実寸と合わせてチップの1つにまとめる(従来のカードは今のまま)。
  const paramChips =
    variant === 'overlay' && run.status === 'succeeded'
      ? buildParamChipsWithActualSize(run.params, shownOutput)
      : buildParamChips(run.params)
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

  const runActions = useHistoryRunActions(run, shownOutput, nav)

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
        {/* 重ねる形では、画像の左下は下の帯に隠れるので、親のサムネイルを帯の先頭に置く(サイズはチップの行にまとめる)。 */}
        {variant === 'overlay' && parentOverlay && (
          <div className={styles.overlayLead}>
            {parentOverlay}
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

        {runActions.actions}
        {runActions.errors}
      </div>

      {runActions.dialogs}
    </article>
  )
}
