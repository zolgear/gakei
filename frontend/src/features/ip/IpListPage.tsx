/** IP の一覧(`/ip`。ADR-0059 11章)。狭い幅でサイドバーを開かずに IP のページへ行けるように置く。 */
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { listIp } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { IP_QUERY_KEY } from './ipQueries'
import { IpStateBadge } from './IpStateBadge'
import styles from './Ip.module.css'

export function IpListPage() {
  const { t } = useI18n()
  const m = t.ip
  const query = useQuery({ queryKey: [...IP_QUERY_KEY, 'list'], queryFn: listIp })
  const items = query.data?.items ?? []
  return (
    <div className={styles.page}>
      <h1 className={styles.pageTitle}>{m.panelHeading}</h1>
      {query.isLoading && <p className={styles.muted}>{m.loading}</p>}
      {query.isError && <p className={styles.errorText}>{m.loadFailed}</p>}
      {query.data && items.length === 0 && <p className={styles.muted}>{m.empty}</p>}
      <ul className={styles.rows}>
        {items.map((ip) => (
          <li key={ip.id} className={styles.rowItem}>
            <Link to={`/ip/${ip.id}`} className={styles.rowLink}>
              <span className={styles.rowName}>
                {ip.name}
                <IpStateBadge state={ip.state} />
              </span>
              <span className={styles.muted}>
                {m.ownerKinds[ip.owner_kind]}
                {ip.rights_holder && ` · ${ip.rights_holder}`} · {fmt(m.materialCount, { count: ip.material_count })}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}
