/**
 * Run 詳細の「領域の指示」(ADR-0053 4章)。`run.regions` の四角を番号と色の順に並べ、種類・指示・
 * 描く文字・参照画像を出す。送った文そのものはプロンプト(`run.prompt` の末尾)に入っている。
 */
import { Link, useLocation } from 'react-router'
import type { AssetDetail, RunRegions } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { AssetThumb } from '../content-rating/AssetThumb'
import { nodeTargetPath } from '../lineage/nodeTargetPath'
import { orderedRegions, regionBadgeTextColor, regionColor } from './regions'
import styles from './RunRegionsSection.module.css'

interface RunRegionsSectionProps {
  regions: RunRegions
  sectionClassName: string
  headingClassName: string
  /** 入力の Asset の詳細(参照画像のレーティングのマスクに使う。参照画像は Run の入力にある)。 */
  assetDetails: ReadonlyMap<string, AssetDetail>
}

export function RunRegionsSection({ regions, sectionClassName, headingClassName, assetDetails }: RunRegionsSectionProps) {
  const { t } = useI18n()
  const m = t.regions.runDetail
  const location = useLocation()
  const items = orderedRegions(regions.regions)
  if (items.length === 0) return null
  return (
    <section className={sectionClassName}>
      <h2 className={headingClassName}>{m.heading}</h2>
      {regions.delivery === 'image_and_text' && <p className={styles.delivery}>{m.delivery}</p>}
      <ol className={styles.list}>
        {items.map((region, index) => (
          <li key={region.number} className={styles.item}>
            <span
              className={styles.badge}
              style={{ background: regionColor(index), color: regionBadgeTextColor(index) }}
              aria-hidden="true"
            >
              {region.number}
            </span>
            <div className={styles.body}>
              <span className={styles.kind}>{t.regions.kinds[region.kind]}</span>
              <span className={styles.instruction}>{region.instruction || m.noInstruction}</span>
              {region.text && <span className={styles.text}>{fmt(m.textLine, { text: region.text })}</span>}
            </div>
            {region.reference_asset_id && (
              <Link
                to={nodeTargetPath({ id: region.reference_asset_id, type: 'asset' }, location.pathname)}
                className={styles.reference}
                title={m.reference}
                aria-label={m.reference}
              >
                <AssetThumb
                  assetId={region.reference_asset_id}
                  rating={assetDetails.get(region.reference_asset_id)?.content_rating ?? undefined}
                  withheld={assetDetails.get(region.reference_asset_id)?.content_withheld === true}
                  className={styles.referenceThumb} alt="" draggable={false} />
              </Link>
            )}
          </li>
        ))}
      </ol>
    </section>
  )
}
