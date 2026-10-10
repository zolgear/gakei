/**
 * capabilities の ParamDef 1つ分のフォーム部品。enum は select、int は数値入力、bool は select。
 * スタジオの設定グリッド(ADR-0009 1章・2026-09-22 承認分)向けにコンパクト化:
 * 見出しは `label`(日本語、等幅・薄い色)だけを表示し、API のパラメーター名は
 * ラベルの `title`(ツールチップ)に、説明文(`description`)は入力欄自体の `title` に移す
 * (画面上に説明文の行は置かない)。
 */
import type { ParamDef } from '../../api/client'
import { useI18n } from '../../i18n'
import {
  EMPTY_SPIN_PROBE_STEP,
  type SpinDirection,
  directionFromProbeValue,
  spinFromDefault,
} from './emptySpin'
import { UNSPECIFIED } from './paramsBuilder'
import { SeedField } from './SeedField'
import { unspecifiedOptionLabel, unspecifiedPlaceholder } from './unspecifiedLabel'
import { PromptEditModeToggle } from '../prompt-tags/PromptEditModeToggle'
import { PromptTagEditor } from '../prompt-tags/PromptTagEditor'
import { isPromptLikeParam, usePromptEditScope, type PromptEditScopeValue } from '../prompt-tags/PromptEditScope'
import { shouldEscapeParens } from '../prompt-tags/promptTags'
import { usePromptEditMode } from '../prompt-tags/usePromptEditMode'
import styles from './ParamField.module.css'

interface ParamFieldProps {
  def: ParamDef
  value: string
  enabled: boolean
  /** 無効のときに「使用不可」のかわりに出す説明(例: 組み合わせ生成のときの枚数)。 */
  disabledNote?: string | null
  /** 有効のときに欄の下に出す補足(例: 高解像度補助の拡大後の寸法)。`warning` は目立たせる。 */
  hint?: { text: string; warning?: boolean } | null
  onChange: (name: string, value: string) => void
}

interface NumberParamInputProps {
  def: ParamDef
  id: string
  value: string
  enabled: boolean
  title: string | undefined
  onChange: (name: string, value: string) => void
}

/**
 * int / float 共通の数値入力。空欄でスピンボタンや ArrowUp/ArrowDown を使うと
 * ブラウザは 0 を起点に値を入れてしまうため、ParamDef.default から step ぶんずらした値に
 * 差し替える(default が無いときは何もせずブラウザ任せ)。
 * キーボードは keydown で横取りする(preventDefault)。マウスのスピンボタンは押した位置を
 * 拾えないブラウザ(Chrome はスピンボタン上の pointerdown が input に届かない)があるので、
 * 空欄のあいだだけ min / max を外して step=1 にしておき(`EMPTY_SPIN_PROBE_STEP`)、
 * ブラウザが入れた 1 / -1 から向きを判定して値を差し替える。
 */
function NumberParamInput({ def, id, value, enabled, title, onChange }: NumberParamInputProps) {
  // 空欄のときだけ向きを測るためのモードに入る(default が無いなら通常どおり)。
  const probing = value === '' && spinFromDefault(def, 'up') !== null

  function applySpin(direction: SpinDirection, input: HTMLInputElement): boolean {
    const spun = spinFromDefault(def, direction)
    if (spun === null) return false
    // props の value がずれていると state が変わらず再描画されないことがあるので、
    // 表示(DOM の値)も合わせて書き戻す。
    input.value = spun
    onChange(def.name, spun)
    return true
  }

  return (
    <input
      id={id}
      type="number"
      min={probing ? undefined : (def.minimum ?? undefined)}
      max={probing ? undefined : (def.maximum ?? undefined)}
      step={probing ? EMPTY_SPIN_PROBE_STEP : def.type === 'float' ? (def.step ?? 'any') : undefined}
      placeholder={unspecifiedPlaceholder(def)}
      value={value}
      disabled={!enabled}
      title={title}
      onKeyDown={(e) => {
        if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return
        if (e.currentTarget.value !== '') return
        if (applySpin(e.key === 'ArrowUp' ? 'up' : 'down', e.currentTarget)) e.preventDefault()
      }}
      onChange={(e) => {
        // inputType が無い変化 = タイピングやペーストではなく step 操作。
        // ブラウザによっては undefined ではなく空文字列で来る(Chrome のスピンボタン)。
        const inputType = (e.nativeEvent as InputEvent).inputType
        if (probing && (inputType == null || inputType === '')) {
          const direction = directionFromProbeValue(e.target.value)
          if (direction && applySpin(direction, e.currentTarget)) return
        }
        onChange(def.name, e.target.value)
      }}
    />
  )
}

interface PromptParamFieldProps extends ParamFieldProps {
  scope: PromptEditScopeValue
}

/**
 * プロンプトに当たる文章のパラメーター(SD WebUI の `negative_prompt` など)。プロンプト欄と同じく
 * 「テキスト / タグ」を切り替えられる(ADR-0039 2章。モードは(プロバイダー, モデル, 欄)ごとに覚える)。
 */
function PromptParamField({ def, value, enabled, disabledNote, onChange, scope }: PromptParamFieldProps) {
  const { t } = useI18n()
  const pf = t.runForm.paramField
  const id = `param-${def.name}`
  const fieldTitle = def.description || undefined
  const [mode, setMode] = usePromptEditMode(scope.provider, scope.model, def.name)
  const tagsMode = mode === 'tags' && enabled

  return (
    <div className={styles.field} data-disabled={!enabled}>
      <div className={styles.labelRow}>
        <label htmlFor={id} title={def.name}>
          {def.label}
        </label>
        <PromptEditModeToggle mode={mode} onChange={setMode} fieldLabel={def.label} disabled={!enabled} />
      </div>
      {tagsMode ? (
        <PromptTagEditor
          id={id}
          className={styles.tagEditor}
          value={value}
          onChange={(next) => onChange(def.name, next)}
          escapeParens={shouldEscapeParens(scope.provider)}
          fieldLabel={def.label}
        />
      ) : (
        <textarea
          id={id}
          className={styles.textArea}
          maxLength={def.max_length ?? undefined}
          placeholder={unspecifiedPlaceholder(def)}
          value={value}
          disabled={!enabled}
          title={fieldTitle}
          onChange={(e) => onChange(def.name, e.target.value)}
        />
      )}
      {!enabled && (
        <p className={styles.disabledNote} title={disabledNote ?? pf.disabledTitle}>
          {disabledNote ?? pf.disabledNote}
        </p>
      )}
    </div>
  )
}

export function ParamField({ def, value, enabled, disabledNote, hint, onChange }: ParamFieldProps) {
  const { t } = useI18n()
  const pf = t.runForm.paramField
  const id = `param-${def.name}`
  const fieldTitle = def.description || undefined
  const promptScope = usePromptEditScope()

  // seed はランダム/固定の切り替えと乱数ボタンを持つ専用の欄を出す(ADR-0013 フォローアップ)。
  if (def.widget === 'seed') {
    return <SeedField def={def} value={value} enabled={enabled} onChange={onChange} />
  }

  if (def.type === 'text' && promptScope && isPromptLikeParam(def.name)) {
    return (
      <PromptParamField
        def={def}
        value={value}
        enabled={enabled}
        disabledNote={disabledNote}
        onChange={onChange}
        scope={promptScope}
      />
    )
  }

  return (
    <div className={styles.field} data-disabled={!enabled}>
      <label htmlFor={id} title={def.name}>
        {def.label}
      </label>

      {def.type === 'enum' && (
        <select
          id={id}
          value={value}
          disabled={!enabled}
          title={fieldTitle}
          onChange={(e) => onChange(def.name, e.target.value)}
        >
          <option value={UNSPECIFIED}>{unspecifiedOptionLabel(def)}</option>
          {(def.choices ?? []).map((choice) => (
            <option key={choice} value={choice}>
              {def.choice_labels?.[choice] ?? choice}
            </option>
          ))}
        </select>
      )}

      {(def.type === 'int' || def.type === 'float') && (
        <NumberParamInput
          def={def}
          id={id}
          value={value}
          enabled={enabled}
          title={fieldTitle}
          onChange={onChange}
        />
      )}

      {def.type === 'text' && (
        <textarea
          id={id}
          className={styles.textArea}
          maxLength={def.max_length ?? undefined}
          placeholder={unspecifiedPlaceholder(def)}
          value={value}
          disabled={!enabled}
          title={fieldTitle}
          onChange={(e) => onChange(def.name, e.target.value)}
        />
      )}

      {def.type === 'bool' && (
        <select
          id={id}
          value={value}
          disabled={!enabled}
          title={fieldTitle}
          onChange={(e) => onChange(def.name, e.target.value)}
        >
          <option value={UNSPECIFIED}>{unspecifiedOptionLabel(def)}</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      )}

      {enabled && hint && (
        <p className={hint.warning ? styles.hintWarning : styles.hint} role={hint.warning ? 'alert' : undefined}>
          {hint.text}
        </p>
      )}

      {!enabled && (
        <p className={styles.disabledNote} title={disabledNote ?? pf.disabledTitle}>
          {disabledNote ?? pf.disabledNote}
        </p>
      )}
    </div>
  )
}
