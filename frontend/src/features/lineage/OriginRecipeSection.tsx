/**
 * 埋め込まれた系列情報(`AssetDetail.origin`、v1/v2 どちらの `meta` も可、ADR-0014)の表示 +
 * 「この設定をフォームに読み込む」ボタン。署名の無い自己申告なので、常に「未検証」と明示する。
 * ビューア(`Viewer.tsx`、`/assets/:id`)と系列インスペクター(`LineageAssetInspectorContent.tsx`、
 * `/lineage/:assetId`)の両方から使う共通コンポーネント。ボタンの見た目・capabilities 取得・
 * フォームへの読み込みロジックは `RecipeLoadButton` に一本化し、系列グラフの埋め込み Run
 * ノード用の `EmbeddedNodeInspector` とも共有する。
 */
import { Link } from 'react-router'
import type { AssetOrigin } from '../../api/client'
import { useI18n } from '../../i18n'
import { extractOriginRunInfo } from './originRecipe'
import { RecipeLoadButton } from './RecipeLoadButton'
import styles from './OriginRecipeSection.module.css'

export interface OriginRecipeSectionProps {
  origin: AssetOrigin
}

export function OriginRecipeSection({ origin }: OriginRecipeSectionProps) {
  const { t } = useI18n()
  const runInfo = extractOriginRunInfo(origin.meta)

  const rt = t.lineage.originRecipe

  return (
    <div className={styles.section}>
      <h3 className={styles.heading}>{rt.heading}</h3>
      {runInfo?.provider && runInfo.model && (
        <p className={styles.model}>
          {runInfo.provider} / {runInfo.model}
        </p>
      )}
      {runInfo?.prompt &&
        (runInfo.prompt.length > 80 ? (
          <details className={styles.promptDetails}>
            <summary>{runInfo.prompt.slice(0, 80)}…</summary>
            <p className={styles.prompt}>{runInfo.prompt}</p>
          </details>
        ) : (
          <p className={styles.prompt}>{runInfo.prompt}</p>
        ))}
      <p className={styles.meta}>
        {rt.sourcePrefix} {origin.same_instance ? rt.sourceSameInstance : rt.sourceOtherInstance}
      </p>
      {origin.asset_id && (
        <Link to={`/lineage/${origin.asset_id}`} className={styles.link}>
          {rt.viewOriginInLineage}
        </Link>
      )}
      <RecipeLoadButton runInfo={runInfo} />
      <p className={styles.note}>{rt.inputsNotRestored}</p>
    </div>
  )
}
