/**
 * チーム(ADR-0058)。設定の「チーム」(自分が入っているチーム。ユーザー設定)と、管理者設定の
 * 「チーム」(全部のチーム)で使う。どちらも認証モードのときだけ出す(`settingsToc`)。
 *
 * - 一覧の行を押すと、その下にメンバーを開く。
 * - 管理者: チームを作る・名前を変える・消す(確認あり)。メンバーを代表者にする・外す。
 * - 管理者と代表者: メールアドレスでメンバーを足す・外す(代表者は代表者でない人だけ)。
 */
import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  addTeamMember,
  createTeam,
  deleteTeam,
  getTeam,
  listTeams,
  removeTeamMember,
  setTeamMemberRole,
  updateTeam,
  type TeamDetail,
  type TeamRole,
  type TeamSummary,
} from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import type { UseToastResult } from '../../components/Toast'
import { fmt, useI18n } from '../../i18n'
import common from '../settings/settings.module.css'
import styles from './TeamsSection.module.css'

const TEAMS_QUERY_KEY = ['teams'] as const

interface TeamsSectionProps {
  /** 管理者設定(全部のチームを管理する)か。 */
  admin: boolean
  toast: UseToastResult
}

export function TeamsSection({ admin, toast }: TeamsSectionProps) {
  const { t } = useI18n()
  const m = t.teams
  const queryClient = useQueryClient()
  const scope = admin ? 'all' : 'mine'
  const query = useQuery({ queryKey: [...TEAMS_QUERY_KEY, scope], queryFn: () => listTeams(scope) })
  const [openId, setOpenId] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)

  const createMutation = useMutation({
    mutationFn: () => createTeam(name.trim()),
    onSuccess: (team) => {
      setName('')
      setError(null)
      setOpenId(team.id)
      queryClient.invalidateQueries({ queryKey: TEAMS_QUERY_KEY })
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : m.failed),
  })

  function submit(e: FormEvent) {
    e.preventDefault()
    if (name.trim()) createMutation.mutate()
  }

  const items = query.data?.items ?? []
  return (
    <section className={common.section}>
      {admin && (
        <form className={styles.createForm} onSubmit={submit}>
          <input
            className={common.input}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={m.namePlaceholder}
            aria-label={m.name}
            maxLength={100}
          />
          <button type="submit" className={common.primaryButton} disabled={!name.trim() || createMutation.isPending}>
            {m.create}
          </button>
        </form>
      )}
      {error && <p className={common.errorText}>{error}</p>}
      {query.isLoading && <p className={common.placeholder}>{m.loading}</p>}
      {query.isError && <p className={common.errorText}>{m.loadFailed}</p>}
      {query.data && items.length === 0 && <p className={common.placeholder}>{admin ? m.emptyAdmin : m.empty}</p>}
      {items.length > 0 && (
        <ul className={styles.list}>
          {items.map((team) => (
            <TeamRow
              key={team.id}
              team={team}
              open={openId === team.id}
              onToggle={() => setOpenId(openId === team.id ? null : team.id)}
              toast={toast}
            />
          ))}
        </ul>
      )}
    </section>
  )
}

interface TeamRowProps {
  team: TeamSummary
  open: boolean
  onToggle: () => void
  toast: UseToastResult
}

function TeamRow({ team, open, onToggle, toast }: TeamRowProps) {
  const { t } = useI18n()
  const m = t.teams
  return (
    <li className={styles.team} data-open={open}>
      <button type="button" className={styles.teamHead} aria-expanded={open} onClick={onToggle}>
        <span className={styles.chevron} aria-hidden="true">
          {open ? '▾' : '▸'}
        </span>
        <span className={styles.teamName}>{team.name}</span>
        {team.my_role === 'lead' && <span className={styles.roleBadge}>{m.roles.lead}</span>}
        <span className={styles.count}>{fmt(m.memberCount, { count: team.member_count })}</span>
      </button>
      {open && <TeamBody teamId={team.id} toast={toast} />}
    </li>
  )
}

function TeamBody({ teamId, toast }: { teamId: string; toast: UseToastResult }) {
  const { t } = useI18n()
  const m = t.teams
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: [...TEAMS_QUERY_KEY, 'detail', teamId], queryFn: () => getTeam(teamId) })
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<TeamRole>('member')
  const [rename, setRename] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function onChanged(detail?: TeamDetail) {
    setError(null)
    if (detail) queryClient.setQueryData([...TEAMS_QUERY_KEY, 'detail', teamId], detail)
    queryClient.invalidateQueries({ queryKey: TEAMS_QUERY_KEY })
  }
  function onFailed(err: unknown) {
    setError(err instanceof ApiError ? err.message : m.failed)
  }

  const addMutation = useMutation({
    mutationFn: () => addTeamMember(teamId, email.trim(), role),
    onSuccess: (detail) => {
      setEmail('')
      onChanged(detail)
    },
    onError: onFailed,
  })
  const roleMutation = useMutation({
    mutationFn: ({ userId, next }: { userId: string; next: TeamRole }) => setTeamMemberRole(teamId, userId, next),
    onSuccess: onChanged,
    onError: onFailed,
  })
  const removeMutation = useMutation({
    mutationFn: (userId: string) => removeTeamMember(teamId, userId),
    onSuccess: () => onChanged(),
    onError: onFailed,
  })
  const renameMutation = useMutation({
    mutationFn: (next: string) => updateTeam(teamId, { name: next }),
    onSuccess: (detail) => {
      setRename(null)
      onChanged(detail)
    },
    onError: onFailed,
  })
  const deleteMutation = useMutation({
    mutationFn: () => deleteTeam(teamId),
    onSuccess: () => {
      setConfirmDelete(false)
      toast.show({ message: m.deleted })
      queryClient.invalidateQueries({ queryKey: TEAMS_QUERY_KEY })
    },
    onError: (err: unknown) => {
      setConfirmDelete(false)
      onFailed(err)
    },
  })

  const team = query.data
  if (query.isLoading) return <p className={common.placeholder}>{m.loading}</p>
  if (!team) return <p className={common.errorText}>{m.loadFailed}</p>

  return (
    <div className={styles.body}>
      {team.can_manage_leads && (
        <div className={styles.adminRow}>
          {rename === null ? (
            <>
              <button type="button" className={common.secondaryButton} onClick={() => setRename(team.name)}>
                {m.rename}
              </button>
              <button type="button" className={common.dangerButton} onClick={() => setConfirmDelete(true)}>
                {m.delete}
              </button>
            </>
          ) : (
            <form
              className={styles.inlineForm}
              onSubmit={(e) => {
                e.preventDefault()
                if (rename.trim()) renameMutation.mutate(rename.trim())
              }}
            >
              <input
                className={common.input}
                value={rename}
                onChange={(e) => setRename(e.target.value)}
                aria-label={m.name}
                maxLength={100}
                autoFocus
              />
              <button type="submit" className={common.primaryButton} disabled={!rename.trim()}>
                {m.save}
              </button>
              <button type="button" className={common.secondaryButton} onClick={() => setRename(null)}>
                {m.cancel}
              </button>
            </form>
          )}
        </div>
      )}

      {team.members.length === 0 ? (
        <p className={common.placeholder}>{m.noMembers}</p>
      ) : (
        <ul className={styles.members}>
          {team.members.map((member) => {
            const removable =
              team.can_manage_members && (team.can_manage_leads || member.role !== 'lead')
            return (
              <li key={member.user_id} className={styles.member}>
                <span className={styles.memberText}>
                  <span className={styles.memberName}>{member.name || member.email}</span>
                  {member.name && member.email && <span className={styles.memberEmail}>{member.email}</span>}
                </span>
                {team.can_manage_leads ? (
                  <select
                    className={common.select}
                    value={member.role}
                    aria-label={fmt(m.roleOf, { name: member.name || member.email || '' })}
                    onChange={(e) =>
                      roleMutation.mutate({ userId: member.user_id, next: e.target.value as TeamRole })
                    }
                  >
                    <option value="member">{m.roles.member}</option>
                    <option value="lead">{m.roles.lead}</option>
                  </select>
                ) : (
                  member.role === 'lead' && <span className={styles.roleBadge}>{m.roles.lead}</span>
                )}
                {removable && (
                  <button
                    type="button"
                    className={common.textButton}
                    onClick={() => removeMutation.mutate(member.user_id)}
                    disabled={removeMutation.isPending}
                  >
                    {m.remove}
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {team.can_manage_members && (
        <form
          className={styles.inlineForm}
          onSubmit={(e) => {
            e.preventDefault()
            if (email.trim()) addMutation.mutate()
          }}
        >
          <input
            className={common.input}
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder={m.emailPlaceholder}
            aria-label={m.email}
          />
          {team.can_manage_leads && (
            <select
              className={common.select}
              value={role}
              aria-label={m.role}
              onChange={(e) => setRole(e.target.value as TeamRole)}
            >
              <option value="member">{m.roles.member}</option>
              <option value="lead">{m.roles.lead}</option>
            </select>
          )}
          <button type="submit" className={common.secondaryButton} disabled={!email.trim() || addMutation.isPending}>
            {m.add}
          </button>
        </form>
      )}
      {error && <p className={common.errorText}>{error}</p>}

      <ConfirmDialog
        open={confirmDelete}
        message={fmt(m.deleteConfirm, { name: team.name })}
        onConfirm={() => deleteMutation.mutate()}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  )
}
