/**
 * 他の画像生成ツール(Stable Diffusion WebUI、ComfyUI、NovelAI、InvokeAI、SwarmUI)や C2PA が
 * 画像に埋め込んだ生成メタ情報(`AssetDetail.embedded_meta`、ADR-0018)の表示。
 * ADR-0014 の `OriginRecipeSection`(GAKEI 自身の埋め込み系列情報)とは別の自己申告であり、
 * 署名が無いため常に「未検証」と明示する。フォームへの読み込みは、A1111 形式(`tool = "a1111"`)で
 * SD WebUI が有効なときの「SD WebUI のフォームに読み込む」だけ(ADR-0038 9章。ADR-0018 4章の例外)。
 * ビューア(`Viewer.tsx`)と系列インスペクター(`LineageAssetInspectorContent.tsx`)の両方から使う。
 */
import type { EmbeddedGenerationMeta } from '../../api/client'
import { useI18n } from '../../i18n'
import { describeTool, isC2paUnknown, paramEntries, rawEntries, PROMPT_COLLAPSE_LENGTH } from './embeddedMeta'
import { ImportFromAssetButton } from '../sdwebui/ImportFromAssetButton'
import sharedStyles from './OriginRecipeSection.module.css'
import styles from './EmbeddedMetaSection.module.css'

export interface EmbeddedMetaSectionProps {
  meta: EmbeddedGenerationMeta
  /** この生成情報を持つ Asset(A1111 形式なら SD WebUI のフォームに読み込める)。 */
  assetId?: string
}

export function EmbeddedMetaSection({ meta, assetId }: EmbeddedMetaSectionProps) {
  const { t } = useI18n()
  const mt = t.lineage.embeddedMeta
  const params = paramEntries(meta)
  const raw = rawEntries(meta)

  return (
    <div className={sharedStyles.section}>
      <h3 className={sharedStyles.heading}>{mt.heading}</h3>
      <p className={sharedStyles.model}>{describeTool(meta, t)}</p>
      {meta.model && (
        <p className={sharedStyles.meta}>
          {mt.modelLabel}: {meta.model}
        </p>
      )}

      {meta.prompt && (
        <>
          <h4 className={styles.subheading}>{mt.promptHeading}</h4>
          {meta.prompt.length > PROMPT_COLLAPSE_LENGTH ? (
            <details className={sharedStyles.promptDetails}>
              <summary>{meta.prompt.slice(0, PROMPT_COLLAPSE_LENGTH)}…</summary>
              <p className={sharedStyles.prompt}>{meta.prompt}</p>
            </details>
          ) : (
            <p className={sharedStyles.prompt}>{meta.prompt}</p>
          )}
        </>
      )}

      {meta.negative_prompt && (
        <>
          <h4 className={styles.subheading}>{mt.negativePromptHeading}</h4>
          {meta.negative_prompt.length > PROMPT_COLLAPSE_LENGTH ? (
            <details className={sharedStyles.promptDetails}>
              <summary>{meta.negative_prompt.slice(0, PROMPT_COLLAPSE_LENGTH)}…</summary>
              <p className={sharedStyles.prompt}>{meta.negative_prompt}</p>
            </details>
          ) : (
            <p className={sharedStyles.prompt}>{meta.negative_prompt}</p>
          )}
        </>
      )}

      {params.length > 0 && (
        <>
          <h4 className={styles.subheading}>{mt.paramsHeading}</h4>
          <ul className={styles.paramsList}>
            {params.map(([name, value]) => (
              <li key={name}>
                <span className={styles.paramName}>{name}</span>
                <span className={styles.paramValue}>{value}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {raw.length > 0 && (
        <details>
          <summary>{mt.rawSummary}</summary>
          {raw.map(([key, value]) => (
            <div key={key}>
              <p className={sharedStyles.meta}>{key}</p>
              <pre className={styles.rawPre}>{value}</pre>
            </div>
          ))}
        </details>
      )}

      {meta.tool === 'a1111' && assetId && <ImportFromAssetButton assetId={assetId} />}

      {meta.truncated && <p className={sharedStyles.note}>{mt.truncatedNote}</p>}
      {isC2paUnknown(meta) && <p className={sharedStyles.note}>{mt.c2paUnknown}</p>}
      <p className={sharedStyles.note}>{mt.unverifiedNote}</p>
    </div>
  )
}
