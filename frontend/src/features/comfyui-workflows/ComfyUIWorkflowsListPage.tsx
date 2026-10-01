/**
 * `/settings/comfyui/workflows`。登録済み ComfyUI ワークフローの一覧。
 * 接続の設定(接続/URL変更/接続テスト/切り離す)は `/settings/comfyui` に集約したので、
 * ここでは `ComfyUIConnectionSummary` で状態を1行だけ示し、そこへのリンクを出す。
 * 削除はアプリ内の確認(ConfirmDialog)で行う論理削除。過去の Run の記録は変わらない
 * (Run 作成時に確定したグラフを `run.params.comfyui_prompt` に保存済みのため)。
 * 未接続(`enabled === false`)でもワークフローの登録・編集はできる(ADR-0013 7章)ので、
 * 接続テストだけ押して保存を忘れた場合などに気づけるよう、1件以上登録済みなら警告を出す。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import {
  ApiError,
  deleteComfyWorkflow,
  getComfyUIStatus,
  listComfyWorkflows,
  type ComfyWorkflowSummary,
} from '../../api/client'
import { useBackNavigate } from '../../lib/useBackNavigate'
import { formatDateTime } from '../../lib/format'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { ToastHost, useToast } from '../../components/Toast'
import { fmt, useI18n, type Messages } from '../../i18n'
import { ComfyUIConnectionSummary } from './ComfyUIConnectionSummary'
import { listDisconnectedNotice } from './comfyuiConnectionForm'
import panelStyles from './ComfyUIStatus.module.css'
import styles from './ComfyUIWorkflowsListPage.module.css'

function operationLabels(t: Messages): Record<ComfyWorkflowSummary['operation'], string> {
  return {
    generate: t.comfyui.operationLabels.generate,
    edit: t.comfyui.operationLabels.edit,
  }
}

export function ComfyUIWorkflowsListPage() {
  const { t } = useI18n()
  const goBack = useBackNavigate('/settings/comfyui')
  const queryClient = useQueryClient()
  const toast = useToast()
  const [deleteTarget, setDeleteTarget] = useState<ComfyWorkflowSummary | null>(null)

  const query = useQuery({ queryKey: ['comfyui-workflows'], queryFn: listComfyWorkflows })
  const workflows = query.data?.items ?? []

  const statusQuery = useQuery({ queryKey: ['comfyui-status'], queryFn: getComfyUIStatus })
  const disconnectedNotice = statusQuery.data
    ? listDisconnectedNotice(statusQuery.data.enabled, workflows.length)
    : null

  const deleteMutation = useMutation({
    mutationFn: (workflowId: string) => deleteComfyWorkflow(workflowId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['comfyui-workflows'] })
      queryClient.invalidateQueries({ queryKey: ['capabilities'] })
      toast.show({ message: t.comfyui.list.deleted })
    },
    onError: (err: unknown) => {
      toast.show({ message: err instanceof ApiError ? err.message : t.comfyui.list.deleteFailed })
    },
    onSettled: () => setDeleteTarget(null),
  })

  return (
    <div className={styles.page}>
      <button type="button" className={styles.backLink} onClick={goBack}>
        {t.comfyui.form.back}
      </button>
      <div className={styles.headerRow}>
        <h1 className={styles.title}>
          {t.comfyui.list.title}
          <span className={styles.badge}>{t.comfyui.experimentalBadge}</span>
        </h1>
        <Link to="/settings/comfyui/workflows/new" className={styles.newButton}>
          {t.comfyui.list.newButton}
        </Link>
      </div>

      <ComfyUIConnectionSummary />

      {disconnectedNotice && (
        <div className={panelStyles.warningBox}>
          <p className={panelStyles.warningText}>
            {disconnectedNotice} <Link to="/settings/comfyui">{t.comfyui.list.disconnectedSettingsLink}</Link>
          </p>
        </div>
      )}

      {query.isLoading && <p className={styles.placeholder}>{t.comfyui.list.loading}</p>}
      {query.isError && <p className={styles.placeholder}>{t.comfyui.list.loadError}</p>}
      {!query.isLoading && !query.isError && workflows.length === 0 && (
        <p className={styles.placeholder}>{t.comfyui.list.empty}</p>
      )}

      {workflows.length > 0 && (
        <div className={styles.list}>
          {workflows.map((wf) => (
            <div key={wf.id} className={styles.row}>
              <div className={styles.rowMain}>
                <Link to={`/settings/comfyui/workflows/${wf.id}`} className={styles.rowName}>
                  {wf.name}
                </Link>
                <span className={styles.badge} data-operation={wf.operation}>
                  {operationLabels(t)[wf.operation]}
                </span>
              </div>
              <div className={styles.rowMeta}>
                <span title={wf.updated_at}>{fmt(t.comfyui.list.updated, { date: formatDateTime(wf.updated_at) })}</span>
                <span className={styles.mono} title={wf.template_sha256}>
                  sha: {wf.template_sha256.slice(0, 8)}
                </span>
              </div>
              <div className={styles.rowActions}>
                <Link to={`/settings/comfyui/workflows/${wf.id}`} className={styles.actionButton}>
                  {t.comfyui.list.edit}
                </Link>
                <button
                  type="button"
                  className={styles.deleteButton}
                  onClick={() => setDeleteTarget(wf)}
                >
                  {t.comfyui.list.delete}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        message={deleteTarget ? fmt(t.comfyui.list.deleteConfirmMessage, { name: deleteTarget.name }) : ''}
        warning={t.comfyui.list.deleteConfirmWarning}
        confirmLabel={t.comfyui.list.deleteConfirmLabel}
        cancelLabel={t.comfyui.list.cancel}
        onConfirm={() => {
          if (deleteTarget) deleteMutation.mutate(deleteTarget.id)
        }}
        onCancel={() => setDeleteTarget(null)}
      />

      <ToastHost toast={toast.toast} onDismiss={toast.dismiss} />
    </div>
  )
}
