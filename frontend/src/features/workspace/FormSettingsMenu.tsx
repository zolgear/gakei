/**
 * プロンプト欄の操作列の「設定」のメニュー。アイコンはアイコンレールのパラメーターセットと同じ形で、
 * 押すと上に小さなメニューを開き、「設定を保存」「設定を読み込む」(パラメーターセット。ADR-0040)と
 * 「画像から設定を読み込む」(ADR-0038 9章、11章)を選ぶ。選ぶとメニューを閉じてそのダイアログを開く。
 * 外側のクリック(pointerdown)と Esc で閉じる。下段配置では操作列が画面の下にあるので、メニューは上に開く。
 *
 * 保存する params は今のフォームの値(送信と同じ組み立て。未指定の項目と無効の項目は入れない)。
 * プロバイダーに依らず出す。
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import type { CapabilitiesResponse, ConditionalParam, ParamDef } from '../../api/client'
import { ParameterSetIcon } from '../../components/icons'
import { ToastHost, useToast } from '../../components/Toast'
import { fmt, useI18n } from '../../i18n'
import { buildEnabledParams } from '../run-form/dependencies'
import { withSizeParam, type RawParamValues } from '../run-form/paramsBuilder'
import { sizeToParam, type SizeState } from '../run-form/sizeValidation'
import { suggestSetNameFromPrompt } from '../prompt-sets/promptSetNaming'
import { LoadParameterSetDialog } from '../parameter-sets/LoadParameterSetDialog'
import { buildFormSavePayload, formHasSeed } from '../parameter-sets/parameterSets'
import { SaveParameterSetDialog } from '../parameter-sets/SaveParameterSetButton'
import { ImportSettingsDialog } from '../image-settings/ImportSettingsDialog'
import styles from './PromptToolsRow.module.css'

type Dialog = 'save' | 'load' | 'image'

interface FormSettingsMenuProps {
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
  caps: CapabilitiesResponse | undefined
}

export function FormSettingsMenu({
  provider,
  model,
  prompt,
  defs,
  rawParams,
  conditionalParams,
  sizeState,
  hasSize,
  hasMask,
  caps,
}: FormSettingsMenuProps) {
  const { t } = useI18n()
  const f = t.parameterSets.form
  const label = t.shell.iconRail.parameterSets
  const [menuOpen, setMenuOpen] = useState(false)
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const menuId = useId()
  const toast = useToast()

  useEffect(() => {
    if (!menuOpen) return
    function handlePointerDown(e: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    function handleKeyDown(e: globalThis.KeyboardEvent) {
      if (e.key === 'Escape') {
        setMenuOpen(false)
        triggerRef.current?.focus()
      }
    }
    window.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [menuOpen])

  const params = withSizeParam(
    buildEnabledParams(defs, rawParams, conditionalParams, hasMask),
    hasSize ? sizeToParam(sizeState) : undefined,
  )
  const items: { id: Dialog; label: string; disabled?: boolean }[] = [
    { id: 'save', label: f.saveButton, disabled: provider === '' },
    { id: 'load', label: f.loadButton },
    { id: 'image', label: t.sdwebui.importParams.openDialog },
  ]
  const close = useCallback(() => setDialog(null), [])

  return (
    <div className={styles.menuRoot} ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className={styles.iconTrigger}
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        aria-controls={menuOpen ? menuId : undefined}
        onClick={() => setMenuOpen((v) => !v)}
      >
        <ParameterSetIcon size={16} />
      </button>
      {menuOpen && (
        <div id={menuId} className={styles.menu} role="menu" aria-label={label}>
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              role="menuitem"
              className={styles.item}
              disabled={item.disabled}
              onClick={() => {
                setMenuOpen(false)
                setDialog(item.id)
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
      <SaveParameterSetDialog
        open={dialog === 'save'}
        onClose={close}
        buildPayload={(include) => buildFormSavePayload({ provider, model, prompt, params }, defs, include)}
        seedAvailable={formHasSeed(params, defs)}
        seedUnavailableMessage={t.parameterSets.save.seedUnavailableForm}
        defaultName={suggestSetNameFromPrompt(prompt)}
        onSaved={(name) => {
          close()
          toast.show({ message: fmt(t.parameterSets.save.saved, { name }) })
        }}
      />
      <LoadParameterSetDialog open={dialog === 'load'} onClose={close} />
      <ImportSettingsDialog open={dialog === 'image'} onClose={close} currentPrompt={prompt} caps={caps} />
      <ToastHost toast={toast.toast} onDismiss={toast.dismiss} />
    </div>
  )
}
