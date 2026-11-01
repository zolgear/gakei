/**
 * ビューアの詳細の「IP」(ADR-0059 11章)。画像が引き継ぐ IP の名前(IP のページへのリンク)と、
 * 素材そのものかの印、契約の注記(クレジットの表記など)。引き継がなければ出さない。
 */
import { Link } from 'react-router'
import type { AssetDetail } from '../../api/client'
import { useI18n } from '../../i18n'
import styles from './Ip.module.css'

export function AssetIpSection({ asset }: { asset: AssetDetail }) {
  const { t } = useI18n()
  const ips = asset.ips ?? []
  if (!asset.ip_marked) return null
  return (
    <section className={styles.assetIp} aria-label={t.ip.assetSection}>
      <h3 className={styles.panelHeading}>{t.ip.assetSection}</h3>
      {ips.length === 0 && <p className={styles.muted}>{t.ip.markLabel}</p>}
      {ips.map((ip) => (
        <div key={ip.id} className={styles.assetIpRow}>
          <Link to={`/ip/${ip.id}`} className={styles.textLink}>
            {ip.name}
          </Link>
          {ip.material && <span className={styles.currentBadge}>{t.ip.materialBadge}</span>}
          {ip.notes && <p className={styles.declarationNotes}>{ip.notes}</p>}
        </div>
      ))}
    </section>
  )
}
