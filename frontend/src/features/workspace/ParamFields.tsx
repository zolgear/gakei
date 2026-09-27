/**
 * 設定グリッドのパラメーター部分。`PrimaryParamFields` は常に見えるサイズ入力 + 主な
 * パラメーター、`OtherParamsDetails` はそれ以外を畳んだ `<details>`(振り分けは
 * `paramGrouping.ts`)。どちらも状態を持たず、値とハンドラを props で受ける。
 */
import type { ConditionalParam, ParamDef, SizeConstraints } from '../../api/client'
import { useI18n } from '../../i18n'
import { isFieldEnabled } from '../run-form/dependencies'
import { ParamField } from '../run-form/ParamField'
import type { RawParamValues } from '../run-form/paramsBuilder'
import { unspecifiedRawValue } from '../run-form/paramsBuilder'
import { SizeInput } from '../run-form/SizeInput'
import type { SizeState } from '../run-form/sizeValidation'
import styles from './InputPane.module.css'

interface ParamFieldsCommonProps {
  defs: ParamDef[]
  rawParams: RawParamValues
  conditionalParams: ConditionalParam[]
  onParamChange: (name: string, value: string) => void
}

interface PrimaryParamFieldsProps extends ParamFieldsCommonProps {
  /** モデルがサイズを持たない場合は null/undefined(サイズ入力自体を出さない)。 */
  sizeConstraints: SizeConstraints | null | undefined
  sizeState: SizeState
  onSizeChange: (next: SizeState) => void
  primary: ParamDef[]
}

/** サイズ入力(あれば)と、主に出すパラメーター。フラグメントなので親のグリッドに直接並ぶ。 */
export function PrimaryParamFields({
  sizeConstraints,
  sizeState,
  onSizeChange,
  defs,
  rawParams,
  conditionalParams,
  onParamChange,
  primary,
}: PrimaryParamFieldsProps) {
  return (
    <>
      {sizeConstraints && (
        <div className={styles.spanTwo}>
          <SizeInput constraints={sizeConstraints} value={sizeState} onChange={onSizeChange} />
        </div>
      )}

      {primary.map((def) => (
        <ParamField
          key={def.name}
          def={def}
          value={rawParams[def.name] ?? unspecifiedRawValue(def.type)}
          enabled={isFieldEnabled(defs, rawParams, conditionalParams, def.name)}
          onChange={onParamChange}
        />
      ))}
    </>
  )
}

interface OtherParamsDetailsProps extends ParamFieldsCommonProps {
  other: ParamDef[]
}

/** 「その他」に畳むパラメーター。無いときは `<details>` ごと出さない。 */
export function OtherParamsDetails({ other, defs, rawParams, conditionalParams, onParamChange }: OtherParamsDetailsProps) {
  const { t } = useI18n()
  const ip = t.workspace.inputPane
  if (other.length === 0) return null
  return (
    <details className={styles.otherDetails}>
      <summary className={styles.otherSummary}>{ip.otherParams}</summary>
      <div className={styles.otherBody}>
        {other.map((def) => (
          <ParamField
            key={def.name}
            def={def}
            value={rawParams[def.name] ?? unspecifiedRawValue(def.type)}
            enabled={isFieldEnabled(defs, rawParams, conditionalParams, def.name)}
            onChange={onParamChange}
          />
        ))}
      </div>
    </details>
  )
}
