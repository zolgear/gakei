/**
 * 生成のフォームの「設定を保存」と「設定を読み込む」(ADR-0040)。プロバイダーに依らず出す。
 * 保存する params は今のフォームの値(送信と同じ組み立て。未指定の項目と無効の項目は入れない)。
 */
import type { ConditionalParam, ParamDef } from '../../api/client'
import { useI18n } from '../../i18n'
import { buildEnabledParams } from '../run-form/dependencies'
import { withSizeParam, type RawParamValues } from '../run-form/paramsBuilder'
import { sizeToParam, type SizeState } from '../run-form/sizeValidation'
import { suggestSetNameFromPrompt } from '../prompt-sets/promptSetNaming'
import { LoadParameterSetButton } from './LoadParameterSetButton'
import { buildFormSavePayload, formHasSeed } from './parameterSets'
import { SaveParameterSetButton } from './SaveParameterSetButton'

interface FormParameterSetButtonsProps {
  provider: string
  model: string
  prompt: string
  defs: ParamDef[]
  rawParams: RawParamValues
  conditionalParams: ConditionalParam[]
  sizeState: SizeState
  /** プロバイダーがサイズを持つか(持たなければ size を保存しない)。 */
  hasSize: boolean
  hasMask: boolean
}

export function FormParameterSetButtons({
  provider,
  model,
  prompt,
  defs,
  rawParams,
  conditionalParams,
  sizeState,
  hasSize,
  hasMask,
}: FormParameterSetButtonsProps) {
  const { t } = useI18n()
  const f = t.parameterSets.form
  const params = withSizeParam(
    buildEnabledParams(defs, rawParams, conditionalParams, hasMask),
    hasSize ? sizeToParam(sizeState) : undefined,
  )
  return (
    <>
      <SaveParameterSetButton
        label={f.saveButton}
        title={f.saveButtonTitle}
        disabled={provider === ''}
        buildPayload={(include) => buildFormSavePayload({ provider, model, prompt, params }, defs, include)}
        seedAvailable={formHasSeed(params, defs)}
        seedUnavailableMessage={t.parameterSets.save.seedUnavailableForm}
        defaultName={suggestSetNameFromPrompt(prompt)}
      />
      <LoadParameterSetButton />
    </>
  )
}
