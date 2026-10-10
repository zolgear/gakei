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
  perRunOutputCount,
  totalOutputCount,
} from '../run-form/dependencies'
import { REPEAT_MAX, REPEAT_MIN, repeatParamDef, repeatTotalText } from '../run-form/repeat'
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
   * `paramGrouping.ts`)。繰り返し回数(ADR-0042)と合わせて、狭い幅でも横に並べる。
   */
  aboveSize?: ParamDef[]
  /** 繰り返し回数(ADR-0042)の欄の生の値と、解釈した回数(範囲外なら null)。 */
  repeatRaw: string
  repeat: number | null
  onRepeatChange: (raw: string) => void
}

/** 繰り返し回数の欄を、グリッドの並びのどこに置くか(この名前の欄の直後。無ければ末尾)。 */
const REPEAT_AFTER = ['n', 'batch_size']

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
  repeatRaw,
  repeat,
  onRepeatChange,
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
  // 繰り返しを含めた合計(2回以上のとき)。1回の枚数が決まらない(組み合わせ生成)ときは出さない。
  const perRun = perRunOutputCount(defs, active)
  const repeatTotal = perRun === null ? null : repeatTotalText(perRun, repeat)
  const repeatField = (hint: { text: string; warning?: boolean } | null) => (
    <ParamField
      key="__repeat"
      def={repeatParamDef()}
      value={repeatRaw}
      enabled
      hint={
        repeat === null
          ? { text: fmt(t.workspace.inputPane.repeatInvalid, { min: REPEAT_MIN, max: REPEAT_MAX }), warning: true }
          : hint
      }
      onChange={(_name, value) => onRepeatChange(value)}
    />
  )
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
  // SD WebUI 以外(サイズ欄の上の段が無いとき)は、繰り返し回数を枚数の欄の直後に置く
  // (null がその位置)。枚数の欄が無ければ末尾。
  const primaryWithRepeat: (ParamDef | null)[] = [...primary]
  if (aboveSize.length === 0) {
    const anchor = primary.findIndex((d) => REPEAT_AFTER.includes(d.name))
    primaryWithRepeat.splice(anchor === -1 ? primary.length : anchor + 1, 0, null)
  }
  return (
    <>
      {aboveSize.length > 0 && (
        <div className={styles.spanTwo}>
          {/* 枚数・バッチ回数・繰り返し回数を横に並べる(ADR-0042 4章) */}
          <div className={styles.aboveSizeRow} data-columns={aboveSize.length + 1}>
            {aboveSize.map(renderField)}
            {repeatField(null)}
          </div>
          {total?.tooMany ? (
            <p className={styles.totalCountWarning} role="alert">
              {fmt(t.runForm.paramField.totalCountTooMany, { total: total.total, max: SDWEBUI_MAX_TOTAL_OUTPUTS })}
            </p>
          ) : (
            (repeatTotal || total) && (
              <p className={styles.totalCount}>
                {repeatTotal ?? fmt(t.runForm.paramField.totalCount, { total: total?.total ?? 0 })}
              </p>
            )
          )}
        </div>
      )}

      {sizeConstraints && (
        <div className={styles.spanTwo}>
          <SizeInput constraints={sizeConstraints} value={sizeState} onChange={onSizeChange} />
        </div>
      )}

      {primaryWithRepeat.map((def) =>
        def === null ? (
          repeatField(repeatTotal ? { text: repeatTotal } : null)
        ) : // 文章(ネガティブプロンプトなど)と seed の欄は、1マスでは狭いので2マス分を使う。
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
