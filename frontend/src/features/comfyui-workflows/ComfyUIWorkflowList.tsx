/**
 * 登録済みの ComfyUI ワークフローの一覧(ADR-0013 7章)。設定の ComfyUI のページ(`/settings/comfyui`)の
 * 「ワークフロー」の節に置く(ADR-0031 1章の 2026-10-01 追記)。
 * - 登録・編集は設定の枠の中の別の画面(`/settings/comfyui/workflows/new`、`/:id`)で開く。
 *   ComfyUI のページから開いた印を履歴の state に付け、そちらの「戻る」で1つ戻れるようにする。
 * - 削除は「操作」(ADR-0031 2章)。確認ダイアログのあとすぐ実行する論理削除。過去の Run の記録は
 *   変わらない(Run 作成時に確定したグラフを `run.params.comfyui_prompt` に保存済みのため)。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { ApiError, deleteComfyWorkflow, listComfyWorkflows, type ComfyWorkflowSummary } from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { fmt, useI18n } from '../../i18n'
import { formatDateTime } from '../../lib/format'
import { useSettingsShell } from '../settings/settingsShell'
import common from '../settings/settings.module.css'
import { FROM_COMFYUI_PAGE_STATE, workflowFormPath } from './workflowFormDraft'

export function ComfyUIWorkflowList() {
  const { t } = useI18n()
  const l = t.comfyui.list
  const queryClient = useQueryClient()
  const { toast } = useSettingsShell()
  const [deleteTarget, setDeleteTarget] = useState<ComfyWorkflowSummary | null>(null)

  const query = useQuery({ queryKey: ['comfyui-workflows'], queryFn: listComfyWorkflows })
  const workflows = query.data?.items ?? []

  const deleteMutation = useMutation({
    mutationFn: (workflowId: string) => deleteComfyWorkflow(workflowId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['comfyui-workflows'] })
      queryClient.invalidateQueries({ queryKey: ['capabilities'] })
      toast.show({ message: l.deleted })
    },
    onError: (err: unknown) => {
      toast.show({ message: err instanceof ApiError ? err.message : l.deleteFailed })
    },
  })

  return (
    <>
      <div className={common.actions}>
        <Link to={workflowFormPath()} state={FROM_COMFYUI_PAGE_STATE} className={common.linkButton}>
          {l.newButton}
        </Link>
      </div>

      {query.isLoading && <p className={common.placeholder}>{l.loading}</p>}
      {query.isError && <p className={common.errorText}>{l.loadError}</p>}
      {query.data && workflows.length === 0 && <p className={common.placeholder}>{l.empty}</p>}

      {workflows.length > 0 && (
        <ul className={common.list}>
          {workflows.map((wf) => (
            <li key={wf.id} className={common.item}>
              <div className={common.itemText}>
                <span className={common.itemName}>
                  <Link to={workflowFormPath(wf.id)} state={FROM_COMFYUI_PAGE_STATE}>
                    {wf.name}
                  </Link>
                </span>
                <dl className={common.itemMeta}>
                  <dt>{t.comfyui.form.operationLabel}</dt>
                  <dd>{t.comfyui.operationLabels[wf.operation]}</dd>
                  <dt>{l.updatedAt}</dt>
                  <dd title={wf.updated_at}>{formatDateTime(wf.updated_at)}</dd>
                  <dt>{l.templateSha}</dt>
                  <dd className={common.mono} title={wf.template_sha256}>
                    {wf.template_sha256.slice(0, 8)}
                  </dd>
                </dl>
              </div>
              <div className={common.itemActions}>
                <Link to={workflowFormPath(wf.id)} state={FROM_COMFYUI_PAGE_STATE} className={common.linkButton}>
                  {l.edit}
                </Link>
                <button
                  type="button"
                  className={common.dangerButton}
                  disabled={deleteMutation.isPending}
                  onClick={() => setDeleteTarget(wf)}
                >
                  {l.delete}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        message={deleteTarget ? fmt(l.deleteConfirmMessage, { name: deleteTarget.name }) : ''}
        warning={l.deleteConfirmWarning}
        confirmLabel={l.deleteConfirmLabel}
        cancelLabel={l.cancel}
        onConfirm={() => {
          if (deleteTarget) deleteMutation.mutate(deleteTarget.id)
          setDeleteTarget(null)
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </>
  )
}
