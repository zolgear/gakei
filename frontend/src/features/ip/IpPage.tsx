/**
 * IP のページ(`/ip/:ipId`。ADR-0059 11章)。上に名前・区分・権利者・状態、その下をタブで分ける。
 *
 * - 素材: 見られる人は誰でも。担当者と管理者は取り込みと取り下げ。
 * - 条件: 今の条件と、これまでの版。担当者と管理者は新しい版を足す。
 * - 許可: 担当者と管理者だけ。チームか個人に、用途と「入力に使う」を選んで出す・取り消す。
 * - 担当者: 見られる人は誰でも。管理者は足す・外す。
 * - 証跡: 担当者と管理者だけ。新しい順に、続きを読める。
 * - 状態(有効・停止・終了)は管理者だけが変える。終了は戻せないので確かめる。
 */
import { useState, type FormEvent } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router'
import {
  ApiError,
  addIpManager,
  addIpMaterial,
  addIpTerms,
  createIpGrant,
  getIp,
  listIpAudit,
  listIpGrantTargets,
  listIpGrants,
  listIpMaterials,
  listIpTerms,
  removeIpManager,
  revokeIpGrant,
  setIpState,
  withdrawIpMaterial,
  type IpDetail,
  type IpState,
} from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { formatDateTime } from '../../lib/format'
import { fmt, useI18n } from '../../i18n'
import { IP_QUERY_KEY } from './ipQueries'
import { IpStateBadge } from './IpStateBadge'
import { IpTermsFields } from './IpTermsForm'
import { termsDraftFrom, termsInputFrom, type TermsDraft } from './ipTerms'
import styles from './Ip.module.css'

type Tab = 'materials' | 'terms' | 'grants' | 'managers' | 'audit'

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

export function IpPage() {
  const { t } = useI18n()
  const m = t.ip
  const { ipId = '' } = useParams()
  const query = useQuery({ queryKey: [...IP_QUERY_KEY, ipId], queryFn: () => getIp(ipId), enabled: Boolean(ipId) })
  const [tab, setTab] = useState<Tab>('materials')
  const ip = query.data

  if (query.isLoading) return <div className={styles.page}><p className={styles.muted}>{m.loading}</p></div>
  if (!ip) return <div className={styles.page}><p className={styles.errorText}>{m.notFound}</p></div>

  const tabs: Tab[] = ip.can_manage
    ? ['materials', 'terms', 'grants', 'managers', 'audit']
    : ['materials', 'terms', 'managers']
  const current = tabs.includes(tab) ? tab : 'materials'

  return (
    <div className={styles.page}>
      <header className={styles.pageHeader}>
        <Link to="/ip" className={styles.textLink}>
          {m.backToList}
        </Link>
        <div className={styles.titleRow}>
          <h1 className={styles.pageTitle}>{ip.name}</h1>
          <IpStateBadge state={ip.state} />
        </div>
        <p className={styles.subline}>
          {m.ownerKinds[ip.owner_kind]}
          {ip.rights_holder && ` · ${ip.rights_holder}`}
          {ip.access && ` · ${fmt(m.myAccess, { purposes: ip.access.purposes.join('、') || '—' })}`}
        </p>
        {ip.description && <p className={styles.description}>{ip.description}</p>}
        {ip.can_administer && <StateControl ip={ip} />}
      </header>
      <div className={styles.tabs} role="tablist">
        {tabs.map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={current === id}
            className={styles.tab}
            onClick={() => setTab(id)}
          >
            {m.tabs[id]}
          </button>
        ))}
      </div>
      {current === 'materials' && <MaterialsTab ip={ip} />}
      {current === 'terms' && <TermsTab ip={ip} />}
      {current === 'grants' && <GrantsTab ip={ip} />}
      {current === 'managers' && <ManagersTab ip={ip} />}
      {current === 'audit' && <AuditTab ip={ip} />}
    </div>
  )
}

function useRefreshIp(ipId: string) {
  const queryClient = useQueryClient()
  return (detail?: IpDetail) => {
    if (detail) queryClient.setQueryData([...IP_QUERY_KEY, ipId], detail)
    queryClient.invalidateQueries({ queryKey: IP_QUERY_KEY })
  }
}

function StateControl({ ip }: { ip: IpDetail }) {
  const { t } = useI18n()
  const m = t.ip
  const refresh = useRefreshIp(ip.id)
  const [pending, setPending] = useState<IpState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const mutation = useMutation({
    mutationFn: (state: IpState) => setIpState(ip.id, state),
    onSuccess: (detail) => {
      setPending(null)
      setError(null)
      refresh(detail)
    },
    onError: (err) => {
      setPending(null)
      setError(errorText(err, m.failed))
    },
  })
  if (ip.state === 'terminated') return null
  return (
    <div className={styles.inlineRow}>
      {ip.state === 'active' ? (
        <button type="button" className={styles.secondaryButton} onClick={() => mutation.mutate('suspended')}>
          {m.suspend}
        </button>
      ) : (
        <button type="button" className={styles.secondaryButton} onClick={() => mutation.mutate('active')}>
          {m.resume}
        </button>
      )}
      <button type="button" className={styles.dangerButton} onClick={() => setPending('terminated')}>
        {m.terminate}
      </button>
      {error && <p className={styles.errorText}>{error}</p>}
      <ConfirmDialog
        open={pending === 'terminated'}
        message={fmt(m.terminateConfirm, { name: ip.name })}
        onConfirm={() => mutation.mutate('terminated')}
        onCancel={() => setPending(null)}
      />
    </div>
  )
}

function MaterialsTab({ ip }: { ip: IpDetail }) {
  const { t } = useI18n()
  const m = t.ip
  const queryClient = useQueryClient()
  const key = [...IP_QUERY_KEY, ip.id, 'materials']
  const query = useQuery({ queryKey: key, queryFn: () => listIpMaterials(ip.id) })
  const [error, setError] = useState<string | null>(null)
  const [withdrawTarget, setWithdrawTarget] = useState<string | null>(null)
  const refresh = () => queryClient.invalidateQueries({ queryKey: IP_QUERY_KEY })

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      for (const file of files) await addIpMaterial(ip.id, file)
    },
    onSuccess: () => {
      setError(null)
      refresh()
    },
    onError: (err) => {
      setError(errorText(err, m.failed))
      refresh()
    },
  })
  const withdraw = useMutation({
    mutationFn: (assetId: string) => withdrawIpMaterial(ip.id, assetId),
    onSuccess: () => {
      setWithdrawTarget(null)
      refresh()
    },
    onError: (err) => {
      setWithdrawTarget(null)
      setError(errorText(err, m.failed))
    },
  })

  const items = query.data?.items ?? []
  return (
    <section className={styles.section}>
      {ip.can_manage && ip.state === 'active' && (
        <label className={styles.uploadButton}>
          {upload.isPending ? m.uploading : m.addMaterials}
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            multiple
            hidden
            disabled={upload.isPending}
            onChange={(e) => {
              const files = [...(e.target.files ?? [])]
              e.target.value = ''
              if (files.length > 0) upload.mutate(files)
            }}
          />
        </label>
      )}
      {error && <p className={styles.errorText}>{error}</p>}
      {query.data && items.length === 0 && <p className={styles.muted}>{m.noMaterials}</p>}
      <div className={styles.materialGrid}>
        {items.map((item) => (
          <figure key={item.asset.id} className={styles.material} data-withdrawn={Boolean(item.withdrawn_at)}>
            <Link to={`/assets/${item.asset.id}`} className={styles.materialImage}>
              <img src={assetUrl(item.asset.id, 'thumb')} alt="" loading="lazy" />
            </Link>
            <figcaption className={styles.materialCaption}>
              <span>{formatDateTime(item.added_at)}</span>
              {item.withdrawn_at ? (
                <span className={styles.withdrawnBadge}>{m.withdrawn}</span>
              ) : (
                ip.can_manage && (
                  <button type="button" className={styles.textButton} onClick={() => setWithdrawTarget(item.asset.id)}>
                    {m.withdraw}
                  </button>
                )
              )}
            </figcaption>
          </figure>
        ))}
      </div>
      <ConfirmDialog
        open={withdrawTarget !== null}
        message={m.withdrawConfirm}
        previewImageUrl={withdrawTarget ? assetUrl(withdrawTarget, 'thumb') : undefined}
        onConfirm={() => withdrawTarget && withdraw.mutate(withdrawTarget)}
        onCancel={() => setWithdrawTarget(null)}
      />
    </section>
  )
}

function TermsTab({ ip }: { ip: IpDetail }) {
  const { t } = useI18n()
  const m = t.ip
  const refresh = useRefreshIp(ip.id)
  const versions = useQuery({ queryKey: [...IP_QUERY_KEY, ip.id, 'terms'], queryFn: () => listIpTerms(ip.id) })
  const [draft, setDraft] = useState<TermsDraft | null>(null)
  const [error, setError] = useState<string | null>(null)
  const mutation = useMutation({
    mutationFn: (value: TermsDraft) => addIpTerms(ip.id, termsInputFrom(value)),
    onSuccess: (detail) => {
      setDraft(null)
      setError(null)
      refresh(detail)
    },
    onError: (err) => setError(errorText(err, m.failed)),
  })

  function submit(e: FormEvent) {
    e.preventDefault()
    if (draft) mutation.mutate(draft)
  }

  return (
    <section className={styles.section}>
      {draft ? (
        <form className={styles.form} onSubmit={submit}>
          <IpTermsFields draft={draft} onChange={setDraft} />
          {error && <p className={styles.errorText}>{error}</p>}
          <div className={styles.inlineRow}>
            <button type="submit" className={styles.primaryButton} disabled={mutation.isPending}>
              {m.saveTerms}
            </button>
            <button type="button" className={styles.secondaryButton} onClick={() => setDraft(null)}>
              {m.cancel}
            </button>
          </div>
        </form>
      ) : (
        ip.can_manage &&
        ip.state !== 'terminated' && (
          <button type="button" className={styles.secondaryButton} onClick={() => setDraft(termsDraftFrom(ip.terms))}>
            {m.reviseTerms}
          </button>
        )
      )}
      {(versions.data ?? []).map((terms) => (
        <article key={terms.version} className={styles.card} data-current={terms.version === ip.terms?.version}>
          <h3 className={styles.cardTitle}>
            {fmt(m.terms.version, { version: terms.version })}
            {terms.version === ip.terms?.version && <span className={styles.currentBadge}>{m.terms.current}</span>}
          </h3>
          <dl className={styles.detailList}>
            {terms.contract_number && (
              <>
                <dt>{m.terms.contractNumber}</dt>
                <dd>{terms.contract_number}</dd>
              </>
            )}
            <dt>{m.terms.period}</dt>
            <dd>{fmt(m.terms.periodValue, { from: terms.valid_from ?? '—', until: terms.valid_until ?? '—' })}</dd>
            <dt>{m.terms.purposes}</dt>
            <dd>{terms.purposes?.length ? terms.purposes.join('、') : '—'}</dd>
            <dt>{m.terms.externalAi}</dt>
            <dd>{m.terms.externalAiOptions[terms.external_ai]}</dd>
            {terms.notes && (
              <>
                <dt>{m.terms.notes}</dt>
                <dd className={styles.preWrap}>{terms.notes}</dd>
              </>
            )}
            <dt>{m.terms.createdAt}</dt>
            <dd>
              {formatDateTime(terms.created_at)}
              {terms.created_by_name && ` · ${terms.created_by_name}`}
            </dd>
          </dl>
        </article>
      ))}
    </section>
  )
}

function GrantsTab({ ip }: { ip: IpDetail }) {
  const { t } = useI18n()
  const m = t.ip
  const queryClient = useQueryClient()
  const key = [...IP_QUERY_KEY, ip.id, 'grants']
  const query = useQuery({ queryKey: key, queryFn: () => listIpGrants(ip.id) })
  const teams = useQuery({ queryKey: [...IP_QUERY_KEY, ip.id, 'grant-targets'], queryFn: () => listIpGrantTargets(ip.id) })
  const purposes = ip.terms?.purposes ?? []
  const [target, setTarget] = useState<'team' | 'user'>('team')
  const [teamId, setTeamId] = useState('')
  const [email, setEmail] = useState('')
  const [chosen, setChosen] = useState<string[]>([])
  const [canUse, setCanUse] = useState(true)
  const [validFrom, setValidFrom] = useState('')
  const [validUntil, setValidUntil] = useState('')
  const [error, setError] = useState<string | null>(null)

  const onDone = (data: { items: unknown[] }) => {
    queryClient.setQueryData(key, data)
    queryClient.invalidateQueries({ queryKey: IP_QUERY_KEY })
  }
  const create = useMutation({
    mutationFn: () =>
      createIpGrant(ip.id, {
        team_id: target === 'team' ? teamId : null,
        email: target === 'user' ? email.trim() : null,
        purposes: chosen,
        can_use: canUse,
        valid_from: validFrom || null,
        valid_until: validUntil || null,
      }),
    onSuccess: (data) => {
      setError(null)
      setChosen([])
      setEmail('')
      onDone(data)
    },
    onError: (err) => setError(errorText(err, m.failed)),
  })
  const revoke = useMutation({
    mutationFn: (grantId: string) => revokeIpGrant(ip.id, grantId),
    onSuccess: onDone,
    onError: (err) => setError(errorText(err, m.failed)),
  })

  const canSubmit = (target === 'team' ? Boolean(teamId) : Boolean(email.trim())) && !create.isPending
  return (
    <section className={styles.section}>
      {ip.state !== 'terminated' && (
        <form
          className={styles.form}
          onSubmit={(e) => {
            e.preventDefault()
            if (canSubmit) create.mutate()
          }}
        >
          <div className={styles.inlineRow}>
            <select className={styles.input} value={target} onChange={(e) => setTarget(e.target.value as 'team' | 'user')} aria-label={m.grants.target}>
              <option value="team">{m.grants.toTeam}</option>
              <option value="user">{m.grants.toUser}</option>
            </select>
            {target === 'team' ? (
              <select className={styles.input} value={teamId} onChange={(e) => setTeamId(e.target.value)} aria-label={m.grants.team}>
                <option value="">{m.grants.chooseTeam}</option>
                {(teams.data ?? []).map((team) => (
                  <option key={team.id} value={team.id}>
                    {team.name}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className={styles.input}
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder={m.grants.emailPlaceholder}
                aria-label={m.grants.email}
              />
            )}
          </div>
          <fieldset className={styles.fieldset}>
            <legend>{m.grants.purposes}</legend>
            {purposes.length === 0 && <p className={styles.muted}>{m.grants.noPurposes}</p>}
            {purposes.map((purpose) => (
              <label key={purpose} className={styles.checkRow}>
                <input
                  type="checkbox"
                  checked={chosen.includes(purpose)}
                  onChange={(e) =>
                    setChosen(e.target.checked ? [...chosen, purpose] : chosen.filter((p) => p !== purpose))
                  }
                />
                {purpose}
              </label>
            ))}
          </fieldset>
          <label className={styles.checkRow}>
            <input type="checkbox" checked={canUse} onChange={(e) => setCanUse(e.target.checked)} />
            {m.grants.canUse}
          </label>
          <div className={styles.fieldRow}>
            <label className={styles.field}>
              <span>{m.terms.validFrom}</span>
              <input className={styles.input} type="date" value={validFrom} onChange={(e) => setValidFrom(e.target.value)} />
            </label>
            <label className={styles.field}>
              <span>{m.terms.validUntil}</span>
              <input className={styles.input} type="date" value={validUntil} onChange={(e) => setValidUntil(e.target.value)} />
            </label>
          </div>
          <div className={styles.inlineRow}>
            <button type="submit" className={styles.primaryButton} disabled={!canSubmit}>
              {m.grants.create}
            </button>
          </div>
        </form>
      )}
      {error && <p className={styles.errorText}>{error}</p>}
      {query.data && query.data.items.length === 0 && <p className={styles.muted}>{m.grants.empty}</p>}
      <ul className={styles.rows}>
        {(query.data?.items ?? []).map((grant) => (
          <li key={grant.id} className={styles.rowItem} data-inactive={!grant.active}>
            <div className={styles.rowText}>
              <span className={styles.rowName}>
                {grant.team_name ?? grant.user?.name ?? grant.user?.email ?? '—'}
                {!grant.active && <span className={styles.inactiveBadge}>{grant.revoked_at ? m.grants.revoked : m.grants.inactive}</span>}
              </span>
              <span className={styles.muted}>
                {grant.purposes.join('、') || '—'} · {grant.can_use ? m.grants.useAllowed : m.grants.viewOnly}
                {(grant.valid_from || grant.valid_until) &&
                  ` · ${fmt(m.terms.periodValue, { from: grant.valid_from ?? '—', until: grant.valid_until ?? '—' })}`}
              </span>
            </div>
            {!grant.revoked_at && (
              <button type="button" className={styles.textButton} onClick={() => revoke.mutate(grant.id)} disabled={revoke.isPending}>
                {m.grants.revoke}
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

function ManagersTab({ ip }: { ip: IpDetail }) {
  const { t } = useI18n()
  const m = t.ip
  const managers = ip.managers ?? []
  const refresh = useRefreshIp(ip.id)
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const add = useMutation({
    mutationFn: () => addIpManager(ip.id, email.trim()),
    onSuccess: (detail) => {
      setEmail('')
      setError(null)
      refresh(detail)
    },
    onError: (err) => setError(errorText(err, m.failed)),
  })
  const remove = useMutation({
    mutationFn: (userId: string) => removeIpManager(ip.id, userId),
    onSuccess: () => refresh(),
    onError: (err) => setError(errorText(err, m.failed)),
  })
  return (
    <section className={styles.section}>
      {managers.length === 0 && <p className={styles.muted}>{m.noManagers}</p>}
      <ul className={styles.rows}>
        {managers.map((manager) => (
          <li key={manager.user_id} className={styles.rowItem}>
            <div className={styles.rowText}>
              <span className={styles.rowName}>{manager.name || manager.email}</span>
              {manager.name && manager.email && <span className={styles.muted}>{manager.email}</span>}
            </div>
            {ip.can_administer && (
              <button type="button" className={styles.textButton} onClick={() => remove.mutate(manager.user_id)}>
                {m.removeManager}
              </button>
            )}
          </li>
        ))}
      </ul>
      {ip.can_administer && (
        <form
          className={styles.inlineRow}
          onSubmit={(e) => {
            e.preventDefault()
            if (email.trim()) add.mutate()
          }}
        >
          <input
            className={styles.input}
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder={m.managerEmailPlaceholder}
            aria-label={m.grants.email}
          />
          <button type="submit" className={styles.secondaryButton} disabled={!email.trim() || add.isPending}>
            {m.addManager}
          </button>
        </form>
      )}
      {error && <p className={styles.errorText}>{error}</p>}
    </section>
  )
}

function AuditTab({ ip }: { ip: IpDetail }) {
  const { t } = useI18n()
  const m = t.ip
  const query = useInfiniteQuery({
    queryKey: [...IP_QUERY_KEY, ip.id, 'audit'],
    queryFn: ({ pageParam }) => listIpAudit(ip.id, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? null,
  })
  const events = query.data?.pages.flatMap((page) => page.items) ?? []
  const actions = m.auditActions as Record<string, string>
  return (
    <section className={styles.section}>
      {query.data && events.length === 0 && <p className={styles.muted}>{m.noAudit}</p>}
      <ul className={styles.rows}>
        {events.map((event) => (
          <li key={event.id} className={styles.rowItem}>
            <div className={styles.rowText}>
              <span className={styles.rowName}>{actions[event.action] ?? event.action}</span>
              <span className={styles.muted}>
                {formatDateTime(event.occurred_at)}
                {event.user && ` · ${event.user.name || event.user.email}`}
              </span>
            </div>
          </li>
        ))}
      </ul>
      {query.hasNextPage && (
        <button type="button" className={styles.secondaryButton} onClick={() => void query.fetchNextPage()} disabled={query.isFetchingNextPage}>
          {m.more}
        </button>
      )}
    </section>
  )
}
