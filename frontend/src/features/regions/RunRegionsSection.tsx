/**
 * Run 詳細の「領域の指示」(ADR-0053 4章)。`run.regions` の四角を番号と色の順に並べ、名前・種類・
 * 指示・描く文字・参照画像を出す。送った文そのものはプロンプト(`run.prompt` の末尾)に入っている。
 * ComfyUI に差し込んだ Run(`comfyui_bboxes`。6章)は、重ねた画像も足した文も無いので四角の一覧だけ。
 */
import { Link, useLocation } from 'react-router'
import type { AssetDetail, RunRegions } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { AssetThumb } from '../content-rating/AssetThumb'
import { nodeTargetPath } from '../lineage/nodeTargetPath'
import { orderedRegions, regionBadgeTextColor, regionColor, regionLabel } from './regions'
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
      {regions.delivery === 'comfyui_bboxes' && <p className={styles.delivery}>{m.deliveryComfyui}</p>}
      {regions.frame === 'output' && <p className={styles.delivery}>{m.frameOutput}</p>}
      {/* 送った四角の画像(Asset の種類 regions。入力の1枚として送った)。 */}
      {regions.overlay_asset_id && (
        <Link
          to={nodeTargetPath({ id: regions.overlay_asset_id, type: 'asset' }, location.pathname)}
          className={styles.overlay}
        >
          <AssetThumb
            assetId={regions.overlay_asset_id}
            rating={assetDetails.get(regions.overlay_asset_id)?.content_rating ?? undefined}
            withheld={assetDetails.get(regions.overlay_asset_id)?.content_withheld === true}
            className={styles.overlayThumb}
            alt=""
            draggable={false}
          />
          <span>{m.overlayImage}</span>
        </Link>
      )}
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
              {regionLabel(region) && <span className={styles.label}>{regionLabel(region)}</span>}
              <span className={styles.kind}>{t.regions.kinds[region.kind]}</span>
              <span className={styles.instruction}>
                {region.instruction ||
                  (regions.delivery === 'image_and_text' &&
                  (region.kind === 'change' || region.kind === 'add') &&
                  !region.text &&
                  !region.reference_asset_id
                    ? m.marker
                    : m.noInstruction)}
              </span>
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
