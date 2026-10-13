/**
 * `update_form` の結果のカード「フォームを変更」(ADR-0048 11章)。変えた項目の変更前と変更後
 * (プロンプトは長ければ省略、パラメーターはキーごと、入力画像はサムネイル)と「元に戻す」を出す。
 *
 * 変更前の値は、このページで反映したときに取った記録(`formWrites`)にしか無い。会話を開き直した
 * ときなど記録の無いカードは、変更後だけを出し、「元に戻す」を出さない。スタジオの外で反映した
 * ときは、次にスタジオを開いたときにフォームに入る(`requestFormLoad` のとおり)ので、その旨を出す。
 */
import { useQuery } from '@tanstack/react-query'
import { useLocation } from 'react-router'
import { getCapabilities, type AssistantMessageResponse, type CapabilitiesResponse } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { useI18n, type Messages } from '../../i18n'
import { findProvider } from '../../lib/capabilities'
import { isStudioPath } from '../lineage/nodeTargetPath'
import { useAssetGroups } from '../stock/groups/assetGroupQueries'
import { describeFormWrite, previewPrompt, readFormWrite, type FormChange, type FormPatchInput } from './formWrite'
import { useAssistantSession } from './useAssistantSession'
import styles from './AssistantPanel.module.css'

type CardMessages = Messages['assistant']['formCard']

/** パラメーターの表示名(フォームと同じ定義のラベル。見つからなければキーのまま)。 */
function paramLabel(caps: CapabilitiesResponse | undefined, provider: string, model: string, key: string): string {
  if (key === 'size') return ''
  const modelCaps = findProvider(caps, provider)?.models.find((m) => m.model === model)
  for (const op of modelCaps?.operations ?? []) {
    const def = op.params.find((p) => p.name === key)
    if (def) return def.label
  }
  return ''
}

function valueText(value: string | number | boolean | null, c: CardMessages): string {
  if (value === null) return c.unspecified
  if (typeof value === 'boolean') return value ? c.on : c.off
  return String(value)
}

function InputThumbs({ inputs, c }: { inputs: FormPatchInput[]; c: CardMessages }) {
  if (inputs.length === 0) return <span>{c.none}</span>
  return (
    <span className={styles.formInputs}>
      {inputs.map((input, index) => (
        <span key={`${input.assetId}-${index}`} className={styles.formInput}>
          <img className={`${styles.attachmentThumb} checkerboard`} src={assetUrl(input.assetId, 'thumb')} alt="" loading="lazy" />
          {input.role !== 'image' && <span className={styles.formInputRole}>{c.roles[input.role]}</span>}
        </span>
      ))}
    </span>
  )
}

export function FormWriteCard({ message }: { message: AssistantMessageResponse }) {
  const { t } = useI18n()
  const c = t.assistant.formCard
  const w = t.assistant.writeCard
  const location = useLocation()
  const { formWrites, undoFormWrite } = useAssistantSession()
  const capsQuery = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities })
  const groupsQuery = useAssetGroups()
  const write = readFormWrite(message)
  if (!write) return null
  const record = formWrites[message.id] ?? null
  const changes = describeFormWrite(write, record ? record.before : null)
  const provider = record?.after.provider || write.formProvider
  const model = record?.after.model || write.formModel

  function groupName(id: string | null | undefined): string {
    if (id === null || id === undefined) return c.none
    const group = (groupsQuery.data?.items ?? []).find((g) => g.id === id)
    return group ? group.name : c.unknownGroup
  }

  function fieldLabel(change: FormChange): string {
    switch (change.field) {
      case 'model':
        return c.fields.model
      case 'prompt':
        return c.fields.prompt
      case 'inputs':
        return c.fields.inputs
      case 'group':
        return c.fields.group
      case 'param': {
        if (change.key === 'size') return c.fields.size
        const label = paramLabel(capsQuery.data, provider, model, change.key)
        return label || change.key
      }
    }
  }

  function renderValue(change: FormChange, which: 'before' | 'after') {
    switch (change.field) {
      case 'model':
        return which === 'before' ? change.before : change.after
      case 'prompt': {
        const text = which === 'before' ? change.before : change.after
        if (text === null) return null
        if (text === '') return c.none
        const preview = previewPrompt(text)
        return <span title={preview.truncated ? text : undefined}>{preview.text}</span>
      }
      case 'param': {
        const value = which === 'before' ? change.before : change.after
        return value === undefined ? null : valueText(value, c)
      }
      case 'inputs': {
        const inputs = which === 'before' ? change.before : change.after
        return inputs === null ? null : <InputThumbs inputs={inputs} c={c} />
      }
      case 'group': {
        const id = which === 'before' ? change.before : change.after
        return id === undefined ? null : groupName(id)
      }
    }
  }

  const outsideStudio = !isStudioPath(location.pathname)

  return (
    <div className={styles.card} data-kind="form" data-undone={record?.undone ? true : undefined}>
      <div className={styles.cardHeader}>
        <span className={styles.cardKind}>{c.title}</span>
      </div>
      {changes.length === 0 ? (
        <p className={styles.cardNote}>{c.noChange}</p>
      ) : (
        <dl className={styles.writeChanges}>
          {changes.map((change) => {
            const before = record ? renderValue(change, 'before') : null
            const after = renderValue(change, 'after')
            return (
              <div key={change.field === 'param' ? `param-${change.key}` : change.field} className={styles.writeChange}>
                <dt>{fieldLabel(change)}</dt>
                <dd>
                  {before !== null && (
                    <span className={styles.beforeValue}>
                      <span className={styles.changeTag}>{w.before}</span>
                      {before}
                    </span>
                  )}
                  <span className={styles.afterValue}>
                    {record && <span className={styles.changeTag}>{w.after}</span>}
                    {after}
                  </span>
                </dd>
              </div>
            )
          })}
        </dl>
      )}
      {record && (
        <div className={styles.cardActions}>
          {record.undone ? (
            <span className={styles.cardNotice}>{c.undone}</span>
          ) : (
            <button type="button" className={styles.smallButton} onClick={() => undoFormWrite(message.id)}>
              {w.undo}
            </button>
          )}
          {outsideStudio && <span className={styles.cardNote}>{c.outsideStudio}</span>}
        </div>
      )}
    </div>
  )
}
