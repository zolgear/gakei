/**
 * 系列グラフの Asset ノード。ヘッダー帯 + サムネイル。mask は分かる表示、deleted は薄く。
 *
 * `embedded`(ADR-0014 6章)は、この環境の DB 行ではなく祖先の PNG に埋め込まれていた
 * 自己申告(未検証)から組み立てたノード。破線の枠 + 「埋め込み」バッジで区別する。
 * `resolvedAssetId`(取り込み済みの対応する Asset)があればそのサムネイルを表示し、
 * 無ければ画像を持たない旨のプレースホルダーにする(埋め込みノードの `id` 自体は DB に
 * 無いため、サムネイルを要求すると 404 になる)。
 */
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { assetUrl } from '../../api/assetUrl'
import type { LineageAssetInfo } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { assetKindLabel } from '../../lib/format'
import styles from './LineageNodes.module.css'

export interface AssetNodeData {
  assetInfo: LineageAssetInfo
  deleted: boolean
  isRoot: boolean
  /** スタジオの結果エリアなど、埋め込み表示でノードを選んだときのハイライト(isRoot とは別)。 */
  selected?: boolean
  /** 埋め込み(未検証)ノードか(ADR-0014 6章)。 */
  embedded?: boolean
  /** 埋め込みノードを生んだ GAKEI インスタンス id(embedded のみ)。 */
  instance?: string | null
  /** 埋め込みノードに対応する、取り込み済みのローカル Asset id(あれば)。 */
  resolvedAssetId?: string | null
  /** サムネイルの URL を差し替える(共有のページは公開の URL を使う。ADR-0029)。 */
  thumbUrlFor?: (assetId: string) => string
  [key: string]: unknown
}

export function AssetLineageNode({ id, data }: NodeProps) {
  const { t } = useI18n()
  const {
    assetInfo,
    deleted,
    isRoot,
    selected,
    embedded = false,
    instance = null,
    resolvedAssetId = null,
    thumbUrlFor,
  } = data as AssetNodeData

  const embeddedTitle = embedded
    ? instance
      ? fmt(t.lineage.embeddedNodeTitleWithInstance, { instance: instance.slice(0, 8) })
      : t.lineage.embeddedNodeTitle
    : undefined

  // 埋め込みノードでサムネイルを持てるのは、取り込み済みで対応付いた場合(resolvedAssetId)
  // だけ。それ以外は、埋め込みノード自身の id(DB に無い)でサムネイルを要求しない。
  const thumbAssetId = embedded ? resolvedAssetId : id

  return (
    <div
      className={`${styles.node} ${isRoot ? styles.nodeRoot : ''} ${embedded ? styles.nodeEmbedded : ''}`}
      data-deleted={deleted}
      data-selected={selected === true}
      title={embeddedTitle}
    >
      <Handle type="target" position={Position.Top} className={styles.handle} />
      <div className={styles.header}>
        <span className={styles.dot} style={{ background: 'var(--color-accent)' }} aria-hidden="true" />
        <span>
          Asset
          {assetInfo.kind === 'mask' && ` · ${t.lineage.assetMask}`}
          {assetInfo.kind === 'sketch' && ` · ${t.lineage.assetSketch}`}
        </span>
        {embedded && <span className={styles.embeddedBadge}>{t.lineage.embeddedBadge}</span>}
        {deleted && <span className={styles.deletedTag}>{t.lineage.deletedTag}</span>}
      </div>
      {assetInfo.kind === 'mask' ? (
        <div className={styles.maskPlaceholder}>{t.lineage.assetMask}</div>
      ) : thumbAssetId ? (
        <img
          className={`${styles.thumb} checkerboard`}
          src={thumbUrlFor ? thumbUrlFor(thumbAssetId) : assetUrl(thumbAssetId, 'thumb')}
          alt=""
          draggable={false}
        />
      ) : (
        <div className={styles.embeddedPlaceholder}>
          <span>{t.lineage.noEmbeddedImage}</span>
          {assetKindLabel(assetInfo.kind) && <span>{assetKindLabel(assetInfo.kind)}</span>}
          {assetInfo.width && assetInfo.height && (
            <span>
              {assetInfo.width}×{assetInfo.height}
            </span>
          )}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className={styles.handle} />
    </div>
  )
}
