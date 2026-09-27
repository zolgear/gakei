/**
 * 編集前との比較ビュー(`/runs/:runId/compare`、ADR-0009「7. 編集前との比較」)。
 * Edit で生成した出力について、入力(編集前)と出力(編集後)を並べて/重ねて見比べる。
 * 選択中の入力・出力・表示モードは `?before=&after=&mode=` に同期し、戻る/進む・再読み込みで
 * 再現できるようにする(`compareState.ts` の純粋関数任せ)。
 * 表示中は左のサイドバーを畳み(系列ページと同じ作法)、離れたら元に戻す。
 */
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useLocation, useNavigate, useParams } from 'react-router'
import { getAsset, getRun } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { useBackNavigate } from '../../lib/useBackNavigate'
import { useIsMobileViewport } from '../../lib/viewport'
import { useResourcePanel } from '../../context/useResourcePanel'
import { useI18n } from '../../i18n'
import { CompareCanvas } from './CompareCanvas'
import {
  buildComparePath,
  compareInputCandidates,
  compareOutputCandidates,
  parseCompareSearch,
  resolveComparison,
  resolveEffectiveMode,
  type CompareMode,
} from './compareState'
import styles from './CompareView.module.css'

export function CompareView() {
  const { t } = useI18n()
  const { runId } = useParams<{ runId: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const goBack = useBackNavigate(runId ? `/runs/${runId}` : '/')
  const isMobile = useIsMobileViewport()
  const { selectedPanel, collapsePanel, openPanel } = useResourcePanel()

  // 比較中はサイドバーを畳み、離れたら元に戻す(`/lineage/:assetId` と同じ作法)。
  useEffect(() => {
    if (selectedPanel !== null) {
      const panel = selectedPanel
      collapsePanel()
      return () => openPanel(panel)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const runQuery = useQuery({
    queryKey: ['run', runId],
    queryFn: () => getRun(runId as string),
    enabled: runId !== undefined,
  })

  const requested = parseCompareSearch(location.search)
  const run = runQuery.data
  const resolved = run ? resolveComparison(run, { before: requested.before, after: requested.after }) : null
  const effectiveMode = resolveEffectiveMode(requested.mode, isMobile)

  const inputCandidates = run ? compareInputCandidates(run) : []
  const outputCandidates = run ? compareOutputCandidates(run) : []

  // URL に before/after が(未指定 or 削除済み等で無効で)反映されていなければ、解決した
  // 既定値(主たる親・先頭の出力)で正規化する(直接 `/runs/:id/compare` を開いた場合など)。
  // mode は明示的に選んだ時だけ書き込む(既定値は URL に無くても resolveEffectiveMode が
  // 毎回同じ結果を返すので、書かなくても再現できる)。ユーザー操作の履歴は汚さない。
  useEffect(() => {
    if (!run || !resolved) return
    if (requested.before === resolved.before && requested.after === resolved.after) return
    navigate(buildComparePath(runId as string, { before: resolved.before, after: resolved.after, mode: requested.mode }), {
      replace: true,
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, resolved?.before, resolved?.after])

  const beforeAssetQuery = useQuery({
    queryKey: ['asset', resolved?.before],
    queryFn: () => getAsset(resolved?.before as string),
    enabled: Boolean(resolved?.before),
  })
  const afterAssetQuery = useQuery({
    queryKey: ['asset', resolved?.after],
    queryFn: () => getAsset(resolved?.after as string),
    enabled: Boolean(resolved?.after),
  })

  if (!runId) return null

  // 選択の変更は history を積まない(replace)。比較ビューは1つの画面の中の状態切り替えであり、
  // 「戻る」ボタンは選択/モードを一段階ずつ戻すのではなく、常に比較ビュー自体を抜ける操作にする
  // (URL には状態を書くが、それは再読み込み・共有で再現するためであって、戻る操作の単位ではない)。
  function selectBefore(assetId: string) {
    navigate(buildComparePath(runId as string, { before: assetId, after: resolved?.after ?? null, mode: requested.mode }), {
      replace: true,
    })
  }
  function selectAfter(assetId: string) {
    navigate(buildComparePath(runId as string, { before: resolved?.before ?? null, after: assetId, mode: requested.mode }), {
      replace: true,
    })
  }
  function selectMode(mode: CompareMode) {
    navigate(buildComparePath(runId as string, { before: resolved?.before ?? null, after: resolved?.after ?? null, mode }), {
      replace: true,
    })
  }

  const isLoading = runQuery.isLoading || beforeAssetQuery.isLoading || afterAssetQuery.isLoading
  const isError = runQuery.isError || beforeAssetQuery.isError || afterAssetQuery.isError
  const before = beforeAssetQuery.data
  const after = afterAssetQuery.data
  const ready = !isLoading && !isError && before !== undefined && after !== undefined

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <button type="button" className={styles.backButton} onClick={goBack}>
          {t.compare.back}
        </button>
        <h1 className={styles.title}>{t.compare.title}</h1>
        {!isMobile && (
          <div className={styles.modeSwitch} role="group" aria-label={t.compare.modeGroupLabel}>
            <button
              type="button"
              className={styles.modeButton}
              data-active={effectiveMode === 'side'}
              aria-label={t.compare.sideBySide}
              title={t.compare.sideBySideTitle}
              onClick={() => selectMode('side')}
            >
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <rect x="1.5" y="2.5" width="5.5" height="11" rx="1" stroke="currentColor" strokeWidth="1.3" />
                <rect x="9" y="2.5" width="5.5" height="11" rx="1" stroke="currentColor" strokeWidth="1.3" />
              </svg>
            </button>
            <button
              type="button"
              className={styles.modeButton}
              data-active={effectiveMode === 'slider'}
              aria-label={t.compare.slider}
              title={t.compare.sliderTitle}
              onClick={() => selectMode('slider')}
            >
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <rect x="1.5" y="2.5" width="13" height="11" rx="1" stroke="currentColor" strokeWidth="1.3" />
                <path d="M8 2.5v11" stroke="currentColor" strokeWidth="1.3" />
              </svg>
            </button>
          </div>
        )}
      </div>

      {runQuery.isError && <p className={styles.placeholder}>{t.compare.runLoadError}</p>}
      {run && inputCandidates.length === 0 && (
        <p className={styles.placeholder}>{t.compare.noInputs}</p>
      )}
      {run && inputCandidates.length > 0 && outputCandidates.length === 0 && (
        <p className={styles.placeholder}>{t.compare.noOutputs}</p>
      )}

      {run && inputCandidates.length > 0 && outputCandidates.length > 0 && (
        <>
          {(inputCandidates.length > 1 || outputCandidates.length > 1) && (
            <div className={styles.pickerRow}>
              {inputCandidates.length > 1 && (
                <div className={styles.picker}>
                  <span className={styles.pickerLabel}>{t.compare.inputLabel}</span>
                  <div className={styles.chips}>
                    {inputCandidates.map((c) => (
                      <button
                        key={c.assetId}
                        type="button"
                        className={styles.chip}
                        data-active={c.assetId === resolved?.before}
                        title={`${c.role} #${c.position}`}
                        onClick={() => selectBefore(c.assetId)}
                      >
                        <img src={assetUrl(c.assetId, 'thumb')} alt="" draggable={false} />
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {outputCandidates.length > 1 && (
                <div className={styles.picker}>
                  <span className={styles.pickerLabel}>{t.compare.outputLabel}</span>
                  <div className={styles.chips}>
                    {outputCandidates.map((c) => (
                      <button
                        key={c.assetId}
                        type="button"
                        className={styles.chip}
                        data-active={c.assetId === resolved?.after}
                        title={c.outputIndex !== null ? `#${c.outputIndex}` : undefined}
                        onClick={() => selectAfter(c.assetId)}
                      >
                        <img src={assetUrl(c.assetId, 'thumb')} alt="" draggable={false} />
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          <div className={styles.canvasArea}>
            {isLoading && <p className={styles.placeholder}>{t.compare.loading}</p>}
            {!isLoading && isError && <p className={styles.placeholder}>{t.compare.imageLoadError}</p>}
            {ready && <CompareCanvas before={before} after={after} mode={effectiveMode} />}
          </div>
        </>
      )}
    </div>
  )
}
