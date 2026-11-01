/**
 * サイドバーの「IP 素材」(ADR-0059 11章)。見られる IP の一覧で、開くと素材のサムネイルを並べる。
 * 素材はドラッグで入力に使える(使うときの申告と検査はサーバーが行う。ADR-0059 5章)。IP の名前から
 * IP のページ(`/ip/:id`)を開く。
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { listIp, listIpMaterials, type IpSummary } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { fmt, useI18n } from '../../i18n'
import { setAssetDragData } from '../run-form/dragDropAssets'
import { IP_QUERY_KEY } from './ipQueries'
import { IpStateBadge } from './IpStateBadge'
import styles from './Ip.module.css'

export function IpPanel() {
  const { t } = useI18n()
  const m = t.ip
  const query = useQuery({ queryKey: [...IP_QUERY_KEY, 'list'], queryFn: listIp })
  const [openId, setOpenId] = useState<string | null>(null)
  const items = query.data?.items ?? []
  return (
    <div className={styles.panel}>
      <h2 className={styles.panelHeading}>{m.panelHeading}</h2>
      {query.isLoading && <p className={styles.muted}>{m.loading}</p>}
      {query.isError && <p className={styles.errorText}>{m.loadFailed}</p>}
      {query.data && items.length === 0 && <p className={styles.muted}>{m.empty}</p>}
      <ul className={styles.ipList}>
        {items.map((ip) => (
          <IpPanelRow
            key={ip.id}
            ip={ip}
            open={openId === ip.id}
            onToggle={() => setOpenId(openId === ip.id ? null : ip.id)}
          />
        ))}
      </ul>
    </div>
  )
}

function IpPanelRow({ ip, open, onToggle }: { ip: IpSummary; open: boolean; onToggle: () => void }) {
  const { t } = useI18n()
  const m = t.ip
  const materials = useQuery({
    queryKey: [...IP_QUERY_KEY, ip.id, 'materials'],
    queryFn: () => listIpMaterials(ip.id),
    enabled: open,
  })
  const items = (materials.data?.items ?? []).filter((item) => !item.withdrawn_at)
  return (
    <li className={styles.ipRow}>
      <div className={styles.ipHead}>
        <button type="button" className={styles.ipToggle} aria-expanded={open} onClick={onToggle}>
          <span aria-hidden="true">{open ? '▾' : '▸'}</span>
          <span className={styles.ipName}>{ip.name}</span>
          <IpStateBadge state={ip.state} />
          <span className={styles.muted}>{fmt(m.materialCount, { count: ip.material_count })}</span>
        </button>
        <Link to={`/ip/${ip.id}`} className={styles.textLink}>
          {m.open}
        </Link>
      </div>
      {open && (
        <div className={styles.thumbGrid}>
          {materials.isLoading && <p className={styles.muted}>{m.loading}</p>}
          {materials.data && items.length === 0 && <p className={styles.muted}>{m.noMaterials}</p>}
          {items.map((item) => (
            <Link key={item.asset.id} to={`/assets/${item.asset.id}`} className={styles.thumb}>
              <img
                src={assetUrl(item.asset.id, 'thumb')}
                alt=""
                loading="lazy"
                draggable
                onDragStart={(e) => setAssetDragData(e.dataTransfer, item.asset.id)}
              />
            </Link>
          ))}
        </div>
      )}
    </li>
  )
}
