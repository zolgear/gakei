/**
 * Asset の詳細(ビューアの情報欄の中身。ADR-0009 1章 2026-10-15 追記)。
 * ビューア(`/assets/:id`)、スタジオの結果エリアの「詳細」タブ、系列インスペクターの Asset 表示の
 * 3か所で同じものを使う。
 *
 * 並びはビューアのまま: (画像なしの理由)→ タイトル → 寸法など → 生成元(プロンプト、Generated の詳細、
 * 元になった画像、最終プロンプト)→ タグ → レーティング → 判定 → 作り方 → 埋め込み情報 → グループ → 似た画像。
 * 無効な機能の節(レーティング、似た画像など)は各節が隠す。
 *
 * 外側の枠は持たず、フラグメントで返す(並べ方と間隔は置き場所の側が決める。ビューアは情報欄の
 * flex の子として並べている)。場所ごとの差は props で渡す。
 */
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { getRun, type AssetDetail } from '../../api/client'
import { useI18n } from '../../i18n'
import { AssetKindBadge } from '../asset-kind/AssetKindBadge'
import { createdLabel } from '../asset-kind/kindRibbon'
import { formatBytes, formatDateTime } from '../../lib/format'
import { runSeedDisplay } from '../run-detail/runSeedDisplay'
import { FinalPromptSection } from '../run-detail/FinalPromptSection'
import { baselineNegativePrompt } from '../run-detail/finalPrompt'
import { StudioPromptActions } from '../workspace/StudioPromptActions'
import { EmbeddedMetaSection } from '../lineage/EmbeddedMetaSection'
import { OriginRecipeSection } from '../lineage/OriginRecipeSection'
import { supportsSimilar } from '../embeddings/similarAssets'
import { useEmbeddingCapabilities } from '../embeddings/useEmbeddingCapabilities'
import { supportsAnnotation } from '../annotations/annotationStatus'
import { ContentRatingSection } from '../content-rating/ContentRatingSection'
import { AssetVerdictsSection, WithheldNotice } from '../mods/AssetVerdictsSection'
import { AssetGroupsSection } from './AssetGroupsSection'
import { SimilarAssetsSection } from './SimilarAssetsSection'
import { AssetTagsSection, AssetTitleSection } from './AssetAnnotationSection'
import styles from './AssetDetailSections.module.css'

export interface AssetDetailSectionsProps {
  asset: AssetDetail
  /**
   * 生成元の節の「Generated の詳細を見る」を押したときの処理(引数は生成元の Run の id)。
   * 渡せばボタンにする(系列インスペクターで、同じグラフの Run ノードへ切り替えるなど)。
   */
  onOpenRun?: (runId: string) => void
  /** `onOpenRun` が無いときの「Generated の詳細を見る」のリンク先。省略時は Run 詳細ページ。 */
  runDetailTo?: (runId: string) => string
  /** 別の Asset(元になった画像、似た画像)へのリンク先。省略時はビューア。 */
  assetLinkTo?: (assetId: string) => string
  /** 最終プロンプトの欄に出す操作。省略時はスタジオへ移って反映する `StudioPromptActions`。 */
  renderPromptActions?: (text: string) => ReactNode
}

const defaultRunDetailTo = (runId: string) => `/runs/${runId}`
const defaultAssetLinkTo = (assetId: string) => `/assets/${assetId}`
const defaultPromptActions = (text: string) => <StudioPromptActions prompt={text} />

export function AssetDetailSections({
  asset,
  onOpenRun,
  runDetailTo = defaultRunDetailTo,
  assetLinkTo = defaultAssetLinkTo,
  renderPromptActions = defaultPromptActions,
}: AssetDetailSectionsProps) {
  const { t } = useI18n()
  // 似た画像(ADR-0033)は埋め込みが使えるときだけ出す。
  const embeddingCaps = useEmbeddingCapabilities()
  const run = asset.produced_by_run
  // queryKey はビューアの出力の切り替え・スタジオの結果エリアと共有する(同じ Run を二重に取得しない)。
  const runQuery = useQuery({
    queryKey: ['run', run?.id],
    queryFn: () => getRun(run?.id as string),
    enabled: Boolean(run),
  })
  // ComfyUI / SD WebUI の Run だけ、実際に使った seed を出す(runSeedDisplay.ts)。
  const seed = runQuery.data ? runSeedDisplay(runQuery.data, asset.output_index) : null
  const primaryParentAssetId = runQuery.data?.inputs?.find(
    (i) => i.role === 'image' && i.position === 0,
  )?.asset_id

  // 日時の見出しを種別で言い分ける(生成・アップロード・取り込み・作成。kindRibbon.ts)。
  const createdLabelText = {
    generated: t.viewer.createdGenerated,
    upload: t.viewer.createdUpload,
    imported: t.viewer.createdImported,
    created: t.viewer.created,
  }[createdLabel(asset.kind, asset.imported)]

  return (
    <>
      {/* 画像を残さなかった理由(ADR-0051 5章)。いちばん上に出す。 */}
      <WithheldNotice asset={asset} />
      <AssetKindBadge kind={asset.kind} imported={asset.imported} />
      {supportsAnnotation(asset) && <AssetTitleSection asset={asset} />}
      <dl className={styles.metaList}>
        <dt>{t.viewer.dimensions}</dt>
        <dd>
          {asset.width} × {asset.height} px
        </dd>
        <dt>{t.viewer.format}</dt>
        <dd>{asset.mime}</dd>
        <dt>{t.viewer.size}</dt>
        <dd>{formatBytes(asset.bytes)}</dd>
        <dt>sha256</dt>
        <dd className={styles.mono}>{asset.sha256.slice(0, 16)}...</dd>
        <dt>{createdLabelText}</dt>
        <dd>{formatDateTime(asset.created_at)}</dd>
      </dl>

      {run && (
        <>
          <h3 className={styles.subheading}>{t.viewer.promptHeading}</h3>
          <p className={styles.runPrompt}>{run.prompt.slice(0, 80)}</p>
          <p className={styles.runMeta}>
            {runQuery.data?.model_label ?? run.model} ・ {run.operation}
          </p>
          {seed && (
            <p className={styles.runMeta} title={seed.tooltip}>
              {seed.text}
            </p>
          )}
          {onOpenRun ? (
            <button type="button" className={styles.runLink} onClick={() => onOpenRun(run.id)}>
              {t.viewer.viewRunDetail}
            </button>
          ) : (
            <Link to={runDetailTo(run.id)} className={styles.runLink}>
              {t.viewer.viewRunDetail}
            </Link>
          )}
          {primaryParentAssetId && (
            <p className={styles.runMeta}>
              {t.viewer.sourceImage}{' '}
              <Link to={assetLinkTo(primaryParentAssetId)} className={styles.runLink}>
                {t.viewer.view}
              </Link>{' '}
              ・{' '}
              <Link
                to={`/runs/${run.id}/compare?before=${primaryParentAssetId}&after=${asset.id}`}
                className={styles.runLink}
              >
                {t.viewer.compareWithOriginal}
              </Link>
            </p>
          )}
          {/* 最終プロンプト(ADR-0030 3章)と、この画像の展開後のプロンプト(ADR-0038 7章)。 */}
          <FinalPromptSection
            className={styles.finalPrompt}
            textOutputs={run.text_outputs}
            outputIndex={asset.output_index ?? null}
            prompt={run.prompt}
            negativePrompt={runQuery.data ? baselineNegativePrompt(runQuery.data.params) : undefined}
            headingClassName={styles.subheading}
            renderActions={renderPromptActions}
          />
        </>
      )}

      {/* タグはプロンプトの下(Run が無い画像では大きさ・形式の下)。多いと畳む。
          レーティング(ADR-0049 4章)はタグの下。 */}
      {supportsAnnotation(asset) && <AssetTagsSection asset={asset} />}
      <ContentRatingSection asset={asset} />
      {/* Mod の判定(ADR-0051 5章)。判定が無ければ出さない。 */}
      <AssetVerdictsSection asset={asset} />

      {asset.origin && <OriginRecipeSection origin={asset.origin} />}
      {asset.embedded_meta && <EmbeddedMetaSection meta={asset.embedded_meta} assetId={asset.id} />}
      <AssetGroupsSection assetId={asset.id} group={asset.group ?? null} />
      {embeddingCaps && supportsSimilar(asset) && (
        <SimilarAssetsSection key={asset.id} assetId={asset.id} assetLinkTo={assetLinkTo} />
      )}
    </>
  )
}
