/**
 * 管理者設定の「IP 素材」(ADR-0059 1章・2章)。機能の有効・無効と、IP の作成。認証モードのときだけ
 * 出す(`settingsToc`)。作った IP の素材・許可は IP のページで扱う。
 */
import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router'
import { ApiError, createIp, getIpSettings, listIp, updateIpSettings, type IpSummary } from '../../api/client'
import { useI18n } from '../../i18n'
import common from '../settings/settings.module.css'
import { IP_QUERY_KEY, IP_SETTINGS_QUERY_KEY } from './ipQueries'
import { IpStateBadge } from './IpStateBadge'
import { IpTermsFields } from './IpTermsForm'
import { termsDraftFrom, termsInputFrom, type TermsDraft } from './ipTerms'
import styles from './Ip.module.css'

export function IpAdminSection() {
  const { t } = useI18n()
  const m = t.ip
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const settings = useQuery({ queryKey: IP_SETTINGS_QUERY_KEY, queryFn: getIpSettings })
  const available = settings.data?.available === true
  const list = useQuery({ queryKey: [...IP_QUERY_KEY, 'list'], queryFn: listIp, enabled: available })
  const [name, setName] = useState('')
  const [ownerKind, setOwnerKind] = useState<IpSummary['owner_kind']>('third_party')
  const [rightsHolder, setRightsHolder] = useState('')
  const [terms, setTerms] = useState<TermsDraft>(termsDraftFrom(null))
  const [error, setError] = useState<string | null>(null)

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => updateIpSettings(enabled),
    onSuccess: (data) => {
      queryClient.setQueryData(IP_SETTINGS_QUERY_KEY, data)
      queryClient.invalidateQueries({ queryKey: IP_QUERY_KEY })
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : m.failed),
  })
  const create = useMutation({
    mutationFn: () =>
      createIp({
        name: name.trim(),
        owner_kind: ownerKind,
        rights_holder: rightsHolder.trim() || null,
        terms: termsInputFrom(terms),
      }),
    onSuccess: (ip) => {
      setError(null)
      queryClient.invalidateQueries({ queryKey: IP_QUERY_KEY })
      navigate(`/ip/${ip.id}`)
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : m.failed),
  })

  function submit(e: FormEvent) {
    e.preventDefault()
    if (name.trim()) create.mutate()
  }

  const enabled = settings.data?.enabled === true
  return (
    <section className={common.section}>
      <label className={common.checkboxRow}>
        <input
          type="checkbox"
          checked={enabled}
          disabled={!settings.data || toggle.isPending}
          onChange={(e) => toggle.mutate(e.target.checked)}
        />
        {m.admin.enabled}
      </label>
      {error && <p className={common.errorText}>{error}</p>}
      {available && (
        <>
          <ul className={styles.rows}>
            {(list.data?.items ?? []).map((ip) => (
              <li key={ip.id} className={styles.rowItem}>
                <Link to={`/ip/${ip.id}`} className={styles.rowLink}>
                  <span className={styles.rowName}>
                    {ip.name}
                    <IpStateBadge state={ip.state} />
                  </span>
                  <span className={styles.muted}>{ip.rights_holder ?? m.ownerKinds[ip.owner_kind]}</span>
                </Link>
              </li>
            ))}
          </ul>
          <form className={styles.form} onSubmit={submit}>
            <h3 className={styles.cardTitle}>{m.admin.create}</h3>
            <label className={styles.field}>
              <span>{m.admin.name}</span>
              <input className={styles.input} value={name} onChange={(e) => setName(e.target.value)} maxLength={200} />
            </label>
            <div className={styles.fieldRow}>
              <label className={styles.field}>
                <span>{m.admin.ownerKind}</span>
                <select
                  className={styles.input}
                  value={ownerKind}
                  onChange={(e) => setOwnerKind(e.target.value as IpSummary['owner_kind'])}
                >
                  <option value="third_party">{m.ownerKinds.third_party}</option>
                  <option value="own">{m.ownerKinds.own}</option>
                </select>
              </label>
              <label className={styles.field}>
                <span>{m.admin.rightsHolder}</span>
                <input
                  className={styles.input}
                  value={rightsHolder}
                  onChange={(e) => setRightsHolder(e.target.value)}
                  maxLength={200}
                />
              </label>
            </div>
            <IpTermsFields draft={terms} onChange={setTerms} />
            <div className={styles.inlineRow}>
              <button type="submit" className={styles.primaryButton} disabled={!name.trim() || create.isPending}>
                {m.admin.createButton}
              </button>
            </div>
          </form>
        </>
      )}
    </section>
  )
}
