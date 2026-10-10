/**
 * 設定グリッドのパラメーター部分。`PrimaryParamFields` は常に見えるサイズ入力 + 主な
 * パラメーター、`OtherParamsDetails` はそれ以外を畳んだ `<details>`(振り分けは
 * `paramGrouping.ts`)。どちらも状態を持たず、値とハンドラを props で受ける。
 */
import type { ConditionalParam, ParamDef, SizeConstraints } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import {
  HIRES_MAX_LONG_EDGE,
  SDWEBUI_MAX_TOTAL_OUTPUTS,
  activeRawParams,
  fieldDisabledNote,
  hiresTargetSize,
  isFieldEnabled,
  totalOutputCount,
} from '../run-form/dependencies'
import { ParamField } from '../run-form/ParamField'
import { isWideParam } from '../run-form/paramGrouping'
import type { RawParamValues } from '../run-form/paramsBuilder'
import { unspecifiedRawValue } from '../run-form/paramsBuilder'
import { SizeInput } from '../run-form/SizeInput'
import { roundSizeStateToMultiple, type SizeState } from '../run-form/sizeValidation'
import styles from './InputPane.module.css'

interface ParamFieldsCommonProps {
  defs: ParamDef[]
  rawParams: RawParamValues
  conditionalParams: ConditionalParam[]
  /** 入力にマスクがあるか。無ければ `mask_only` の項目を無効にして理由を示す。 */
  hasMask: boolean
  onParamChange: (name: string, value: string) => void
}

interface PrimaryParamFieldsProps extends ParamFieldsCommonProps {
  /** モデルがサイズを持たない場合は null/undefined(サイズ入力自体を出さない)。 */
  sizeConstraints: SizeConstraints | null | undefined
  sizeState: SizeState
  onSizeChange: (next: SizeState) => void
  primary: ParamDef[]
  /**
   * サイズ欄のすぐ上の段に、サイズと同じ幅で出すパラメーター(SD WebUI の枚数とバッチ回数。
   * `paramGrouping.ts`)。2つ以上あれば、狭い幅でも横に並べる。
   */
  aboveSize?: ParamDef[]
}

/**
 * サイズ入力(あれば)と、主に出すパラメーター。フラグメントなので親のグリッドに直接並ぶ。
 * `aboveSize` の項目はサイズ入力の上の段に、サイズと同じく2マス分で置く。SD WebUI の枚数と
 * バッチ回数は横に並べ、その下に合計の枚数(上限を超えれば赤字)を添える(ADR-0038 2章)。
 */
export function PrimaryParamFields({
  sizeConstraints,
  sizeState,
  onSizeChange,
  defs,
  rawParams,
  conditionalParams,
  hasMask,
  onParamChange,
  primary,
  aboveSize = [],
}: PrimaryParamFieldsProps) {
  const { t } = useI18n()
  // 有効・無効の判定は送る値(無効の項目を未指定にした値)で行う。欄に出す値は rawParams のまま
  // (無効のあいだも値を残し、有効に戻せば元の値で使える)。
  const active = activeRawParams(defs, rawParams, conditionalParams, { hasMask })
  // 高解像度補助(ADR-0038 10章)の拡大後の寸法は、倍率の欄の下に出す
  // (送る値と同じく、サイズを制約の倍数に切り捨ててから計算する)
  const hiresTarget = hiresTargetSize(
    defs,
    active,
    sizeConstraints ? roundSizeStateToMultiple(sizeConstraints, sizeState) : sizeState,
  )
  const hiresHint = hiresTarget
    ? {
        text: fmt(hiresTarget.tooLarge ? t.runForm.paramField.hiresTargetTooLarge : t.runForm.paramField.hiresTarget, {
          width: hiresTarget.width,
          height: hiresTarget.height,
          max: HIRES_MAX_LONG_EDGE,
        }),
        warning: hiresTarget.tooLarge,
      }
    : null
  // SD WebUI の合計の枚数(枚数 × バッチ回数)。組み合わせ生成で無効のときは出さない。
  const total = totalOutputCount(defs, active)
  const renderField = (def: ParamDef) => (
    <ParamField
      key={def.name}
      def={def}
      value={rawParams[def.name] ?? unspecifiedRawValue(def.type)}
      enabled={isFieldEnabled(defs, active, conditionalParams, def.name, { hasMask })}
      disabledNote={fieldDisabledNote(defs, active, def.name, { hasMask })}
      hint={def.name === 'hr_scale' ? hiresHint : null}
      onChange={onParamChange}
    />
  )
  return (
    <>
      {aboveSize.length > 1 ? (
        <div className={styles.spanTwo}>
          <div className={styles.aboveSizeRow}>{aboveSize.map(renderField)}</div>
          {total && (
            <p
              className={total.tooMany ? styles.totalCountWarning : styles.totalCount}
              role={total.tooMany ? 'alert' : undefined}
            >
              {fmt(total.tooMany ? t.runForm.paramField.totalCountTooMany : t.runForm.paramField.totalCount, {
                total: total.total,
                max: SDWEBUI_MAX_TOTAL_OUTPUTS,
              })}
            </p>
          )}
        </div>
      ) : (
        aboveSize.map((def) => (
          <div key={def.name} className={styles.spanTwo}>
            {renderField(def)}
          </div>
        ))
      )}

      {sizeConstraints && (
        <div className={styles.spanTwo}>
          <SizeInput constraints={sizeConstraints} value={sizeState} onChange={onSizeChange} />
        </div>
      )}

      {primary.map((def) =>
        // 文章(ネガティブプロンプトなど)と seed の欄は、1マスでは狭いので2マス分を使う。
        isWideParam(def) ? (
          <div key={def.name} className={styles.spanTwo}>
            {renderField(def)}
          </div>
        ) : (
          renderField(def)
        ),
      )}
    </>
  )
}

interface OtherParamsDetailsProps extends ParamFieldsCommonProps {
  other: ParamDef[]
}

/** 「その他」に畳むパラメーター。無いときは `<details>` ごと出さない。 */
export function OtherParamsDetails({
  other,
  defs,
  rawParams,
  conditionalParams,
  hasMask,
  onParamChange,
}: OtherParamsDetailsProps) {
  const { t } = useI18n()
  const ip = t.workspace.inputPane
  if (other.length === 0) return null
  const active = activeRawParams(defs, rawParams, conditionalParams, { hasMask })
  return (
    <details className={styles.otherDetails}>
      <summary className={styles.otherSummary}>{ip.otherParams}</summary>
      <div className={styles.otherBody}>
        {other.map((def) => (
          <ParamField
            key={def.name}
            def={def}
            value={rawParams[def.name] ?? unspecifiedRawValue(def.type)}
            enabled={isFieldEnabled(defs, active, conditionalParams, def.name, { hasMask })}
            disabledNote={fieldDisabledNote(defs, active, def.name, { hasMask })}
            onChange={onParamChange}
          />
        ))}
      </div>
    </details>
  )
}
