/**
 * スタジオの入力エリア。下段配置(1行目に入力画像のチップ行、2列グリッド(左: プロンプト、
 * 右: 設定)を横に展開。ADR-0009 1章・2026-09-22 承認分)。狭い幅では縦積みにする。
 * サイドバー配置(ADR-0009 1章・2026-09-26 追記。2026-09-27 から既定)では、モデル → 入力画像(タイル)→
 * プロンプト → サイズ・主なパラメーター → その他(畳む)を1列に並べ、参考価格と生成ボタンを
 * 列の下端に固定する(`layout` prop の実効値で切り替え。狭い幅では常に下段)。
 * Ctrl+Enter(Mac は Cmd+Enter)はこのコンテナで keydown を拾う(グローバルにはしない)。
 *
 * 表示部品は `ModelSelect` / `ParamFields`(`PrimaryParamFields` / `OtherParamsDetails`)/
 * `PromptToolsRow` / `SubmitControls` / `InputPaneNotices` に分け、並べ方は
 * `InputPaneBottomLayout` / `InputPaneSidebarLayout` が担う。入力画像は下段が
 * `InputChipsRow`、サイドバーが `InputImageTiles`(同じ `InputImagesProps` / `EditInputsLogic`)。
 * 状態(フォーム・メンション・マスク/スケッチ編集)とハンドラはこのファイルに残す。
 * 「+ 追加」で開く `AddInputImagesDialog`(ADR-0009 1章・2026-09-26 追加)の開閉状態もここで持つ。
 */
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type DragEvent as ReactDragEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react'
import { useQuery } from '@tanstack/react-query'
import { fmt, useI18n } from '../../i18n'
import { isMobileViewport } from '../../lib/viewport'
import { listPromptSets, type AssetDetail } from '../../api/client'
import { useRunFormLogic } from '../run-form/useRunFormLogic'
import { useEditInputsLogic } from '../run-form/useEditInputsLogic'
import type { InsertPromptFn } from '../run-form/promptInsertion'
import { UNSPECIFIED } from '../run-form/paramsBuilder'
import { groupParamsForProvider } from '../run-form/paramGrouping'
import { MaskEditor } from '../mask/MaskEditor'
import { SketchEditor, type SketchSaveOptions } from '../sketch/SketchEditor'
import sketchStyles from '../sketch/SketchEditor.module.css'
import { recommendedSketchPrompt } from '../sketch/recommendedPrompt'
import { MentionPopover } from '../prompt-sets/MentionPopover'
import {
  filterPromptSetItems,
  findMentionAtCursor,
  type MentionCandidate,
  type MentionMatch,
} from '../prompt-sets/mentionQuery'
import { useMentionPlacement } from '../prompt-sets/useMentionPlacement'
import { AddInputImagesDialog } from './AddInputImagesDialog'
import { InputChipsRow, type InputImagesProps } from './InputChipsRow'
import { InputImageTiles } from './InputImageTiles'
import { ModelSelect } from './ModelSelect'
import { OtherParamsDetails, PrimaryParamFields } from './ParamFields'
import { AssetGroupField } from '../run-form/AssetGroupField'
import { PromptToolsRow } from './PromptToolsRow'
import { LoraPickerButton } from '../sdwebui/LoraPickerButton'
import { SubmitControls } from './SubmitControls'
import { InputPaneNotices } from './InputPaneNotices'
import { InputPaneBottomLayout } from './InputPaneBottomLayout'
import { InputPaneSidebarLayout } from './InputPaneSidebarLayout'
import type { StudioLayout } from './studioLayout'
import { isSubmitShortcut } from './submitShortcut'
import { isRelevantDragTypes } from '../run-form/dragDropAssets'
import { paramToSizeState, sizeToParam } from '../run-form/sizeValidation'
import { usePromptEditMode } from '../prompt-tags/usePromptEditMode'
import { PromptTagEditor } from '../prompt-tags/PromptTagEditor'
import { PromptEditModeToggle } from '../prompt-tags/PromptEditModeToggle'
import { PromptEditScopeContext } from '../prompt-tags/PromptEditScope'
import { shouldEscapeParens } from '../prompt-tags/promptTags'
import styles from './InputPane.module.css'

const MENTION_POPOVER_ID = 'prompt-mention-popover'
/** ↑↓ Enter Tab Esc のうち、メンション候補の操作として奪うキー(Ctrl/Cmd+Enter の送信は除く)。 */
const MENTION_NAV_KEYS = new Set(['ArrowUp', 'ArrowDown', 'Enter', 'Tab', 'Escape'])
// caret 行の上に開くため上方向の空きに余裕ができることが多く、1件2行だった頃の 8 件より
// 増やせる(見た目は1行/件に詰めてある。MentionPopover.module.css 参照)。
const MENTION_CANDIDATE_LIMIT = 20

interface InputPaneProps {
  onRunCreated: (runId: string) => void
  /**
   * `form.insertPrompt` をスタジオの `ResultPane`(系列インスペクター)へ渡すための窓口。
   * `StudioWorkspace` がこれを受け取り、そのまま `ResultPane` へ渡す(兄弟コンポーネント間の
   * 受け渡し)。関数は `useCallback([])` で identity が安定しているため、実質マウント時に
   * 一度だけ呼ばれる。
   */
  onExposeInsertPrompt?: (fn: InsertPromptFn) => void
  /**
   * 「新規生成」(AppBar)の合図(`navigate` の `state.resetAt`)。値が変わるたびフォーム全体を
   * 初期値に戻し(`form.resetForm()`)、送信直後の案内も消す。
   */
  resetAt: number | undefined
  /** 入力画像のチップを押したとき、その Asset を上段のプレビューに表示する(`?asset=` を変える)。 */
  onPreviewAsset: (assetId: string) => void
  /**
   * 実効の配置(`effectiveStudioLayout` の結果。狭い幅では常に 'bottom')。並べ方だけが変わり、
   * 状態とハンドラは共通(ADR-0009 1章・2026-09-26 追記)。
   */
  layout: StudioLayout
}

export function InputPane({ onRunCreated, onExposeInsertPrompt, resetAt, onPreviewAsset, layout }: InputPaneProps) {
  const { t } = useI18n()
  const ip = t.workspace.inputPane
  const promptTextareaRef = useRef<HTMLTextAreaElement | null>(null)
  // 送信直後の案内。フォームは送信しても変わらない(ADR-0009「送信後のフォーム」2026-09-24
  // 改訂)ので、ここは「実行を開始した」ことだけ短く伝える。
  const [submittedNotice, setSubmittedNotice] = useState<string | null>(null)
  // 「+ 追加」で開く入力画像追加ダイアログ(ADR-0009 1章・2026-09-26 追加)。
  const [addDialogOpen, setAddDialogOpen] = useState(false)
  const form = useRunFormLogic(onRunCreated, () => {
    setSubmittedNotice(ip.submittedNotice)
    // 送信後もフォームは変わらないので、テキストエリアの内容やカーソル位置はそのまま。
    // ボタンクリックで送信した場合でも、続けて Ctrl+Enter で送信できるようフォーカスだけ戻す
    // (モバイルでは自動フォーカスでキーボードが出てしまうので行わない)。
    if (!isMobileViewport()) promptTextareaRef.current?.focus()
  })
  // プロンプト欄の「テキスト / タグ」(ADR-0039 2章)。(プロバイダー, モデル)ごとに覚える。
  const [promptEditMode, setPromptEditMode] = usePromptEditMode(form.provider, form.model, 'prompt')
  const editLogic = useEditInputsLogic(
    form.inputs,
    form.setInputs,
    form.maxInputImages,
    form.maxInputImageBytes,
  )

  // `@` メンション呼び出し(プロンプトセット)。プロンプトセットの一覧はここでは
  // メンション中だけ取得すればよい(サイドバーのパネルが既に取得済みならキャッシュを共有する)。
  const [mention, setMention] = useState<MentionMatch | null>(null)
  const [mentionActiveIndex, setMentionActiveIndex] = useState(0)
  const promptSetsQuery = useQuery({
    queryKey: ['prompt-sets'],
    queryFn: listPromptSets,
    enabled: mention !== null,
  })
  const mentionCandidates: MentionCandidate[] = mention
    ? filterPromptSetItems(promptSetsQuery.data?.items ?? [], mention.query, MENTION_CANDIDATE_LIMIT)
    : []
  const mentionPlacement = useMentionPlacement(promptTextareaRef, mention?.start ?? null, mentionCandidates.length)

  // 「新規生成」(resetAt の変化)でフォーム全体(prompt・model・パラメーター)を初期値に戻し、
  // 案内も空にする。マウント時の値では動かさない(location.state は履歴に残るので、
  // 「新規生成」後に書いたプロンプトがリロードや戻る/進むで消えてしまう。他ページからの
  // 「新規生成」は AppBar が context を空にしてから来る)。
  const lastHandledResetAtRef = useRef(resetAt)
  useEffect(() => {
    if (resetAt === undefined || resetAt === lastHandledResetAtRef.current) return
    lastHandledResetAtRef.current = resetAt
    form.resetForm()
    setSubmittedNotice(null)
    setMention(null)
    // form.resetForm は毎レンダー新しい関数になるため依存に含めない。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetAt])

  // query が変わるたび(打鍵のたび)に選択位置を先頭へ戻す。
  useEffect(() => {
    setMentionActiveIndex(0)
  }, [mention])

  function updateMentionFromCursor(el: HTMLTextAreaElement) {
    setMention(findMentionAtCursor(el.value, el.selectionStart))
  }

  function confirmMention(candidate: MentionCandidate) {
    if (!mention) return
    const end = mention.start + 1 + mention.query.length
    form.replacePromptRange(mention.start, end, candidate.text)
    setMention(null)
  }

  // Escape はテキストを変えない(`@query` はそのまま残る)ため、閉じた直後の keyup で
  // カーソル位置を再スキャンすると同じメンションを見つけて即座に再度開いてしまう。
  // Escape で閉じたことを一度だけ覚えておき、直後の keyup(のみ)でその再スキャンを止める。
  const suppressMentionRescanRef = useRef(false)

  function closeMentionFromEscape() {
    suppressMentionRescanRef.current = true
    setMention(null)
  }

  function handlePromptKeyDown(e: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (!mention || isSubmitShortcut(e)) return
    if (!MENTION_NAV_KEYS.has(e.key)) return
    if (mentionCandidates.length === 0) {
      if (e.key === 'Escape') {
        e.preventDefault()
        closeMentionFromEscape()
      }
      return
    }
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setMentionActiveIndex((i) => (i + 1) % mentionCandidates.length)
        break
      case 'ArrowUp':
        e.preventDefault()
        setMentionActiveIndex((i) => (i - 1 + mentionCandidates.length) % mentionCandidates.length)
        break
      case 'Enter':
      case 'Tab':
        e.preventDefault()
        confirmMention(mentionCandidates[mentionActiveIndex])
        break
      case 'Escape':
        e.preventDefault()
        closeMentionFromEscape()
        break
    }
  }

  useEffect(() => {
    onExposeInsertPrompt?.(form.insertPrompt)
  }, [onExposeInsertPrompt, form.insertPrompt])

  // 系列インスペクターからの挿入/置き換え(`form.insertPrompt`)や `@` メンションの確定
  // (`form.replacePromptRange`)で state(prompt)が更新された後、そのコミットを待ってから
  // カーソル位置とフォーカスを合わせる。value を直接 DOM で書き換えるわけではない
  // (あくまで React が描画した後の selectionRange 操作)。どちらの経路でもメンション候補は閉じる。
  useLayoutEffect(() => {
    if (form.pendingCursor === null) return
    const cursor = form.pendingCursor
    form.clearPendingCursor()
    setMention(null)
    if (isMobileViewport()) return
    const el = promptTextareaRef.current
    if (!el) return
    el.focus()
    el.setSelectionRange(cursor, cursor)
    // form 全体を依存に含めると内容変更のたびに走ってしまうため、必要な値だけを見る。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.pendingCursor, form.prompt])

  function handleKeyDown(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (isSubmitShortcut(e)) {
      e.preventDefault()
      form.submit()
    }
  }

  function handlePaneDrop(e: ReactDragEvent<HTMLDivElement>) {
    // 下段全体でもドロップを受ける(チップ行の外にドロップされた場合の保険)。関係ないドラッグ
    // (テキスト選択など)まで奪わないよう、Files・アプリ内マーカー・text/uri-list のときだけ処理する。
    if (!isRelevantDragTypes(Array.from(e.dataTransfer.types))) return
    // 子(チップ行)が処理済みなら二重に処理しない(伝播を止め忘れた場合の保険)。
    if (e.defaultPrevented) return
    e.preventDefault()
    editLogic.handleDrop(e)
  }

  function handlePaneDragOver(e: ReactDragEvent<HTMLDivElement>) {
    if (!isRelevantDragTypes(Array.from(e.dataTransfer.types))) return
    e.preventDefault()
  }

  if (form.capsLoading) {
    return <div className={styles.capsError}>{ip.capsLoading}</div>
  }
  if (form.capsError || !form.caps) {
    return <div className={styles.capsError}>{ip.capsError}</div>
  }

  const caps = form.caps
  const { primary, other } = groupParamsForProvider(form.provider, form.defs)
  const selectedModel = form.providerEntry?.models.find((m) => m.model === form.model)

  // 参考価格(PriceEstimate)に渡す値。API の契約に合わせ、未指定はそれぞれ既定値にする。
  const qualityRaw = form.rawParams.quality
  const priceQuality = qualityRaw && qualityRaw !== UNSPECIFIED ? qualityRaw : 'auto'
  const nRaw = form.rawParams.n
  const priceN = nRaw && nRaw !== UNSPECIFIED && nRaw !== '' ? Number(nRaw) : 1
  const priceSize = sizeToParam(form.sizeState) ?? 'auto'

  // 参考価格の入力画像トークン計算用: role=image の入力を position 順に並べた asset id。
  const inputAssetIds = form.inputs
    .filter((i) => i.role === 'image')
    .slice()
    .sort((a, b) => a.position - b.position)
    .map((i) => i.assetId)

  // スケッチ(白紙)の Canvas サイズ。出力サイズの設定を解く(auto/未指定は 1024×1024)。
  const blankSketchSize = paramToSizeState(sizeToParam(form.sizeState))

  const sketchEditorState = editLogic.sketchEditor

  function readPromptCursorPos(): number | null {
    const el = document.getElementById('prompt')
    const hasFocus = el instanceof HTMLTextAreaElement && document.activeElement === el
    return hasFocus ? el.selectionStart : null
  }

  function handleSketchSave(
    asset: AssetDetail,
    options: SketchSaveOptions,
    replaceInputId?: string,
  ) {
    editLogic.saveSketch(asset, replaceInputId)
    if (options.insertRecommendedPrompt) {
      form.insertPrompt(recommendedSketchPrompt(), 'insert', readPromptCursorPos())
    }
  }

  const promptValid = form.promptLength > 0 && form.promptLength <= form.promptMax

  const blockReasons: string[] = []
  if (!promptValid) {
    blockReasons.push(ip.promptRequired)
  }
  if (!form.sizeValid) blockReasons.push(...form.sizeErrors)
  if (form.incompatibleErrors.length > 0) blockReasons.push(...form.incompatibleErrors)
  if (form.hasDeletedInputs) blockReasons.push(ip.deletedInputsPresent)
  if (form.imageInputCount > form.maxInputImages) {
    blockReasons.push(fmt(ip.maxInputImagesExceeded, { max: form.maxInputImages }))
  }
  if (form.inputCountRequirementReason) blockReasons.push(form.inputCountRequirementReason)
  if (form.providerUnavailableReason) blockReasons.push(form.providerUnavailableReason)
  if (!form.operationSupported) {
    blockReasons.push(ip.operationUnsupported)
  }
  if (form.maskRequired && !form.hasMask) {
    blockReasons.push(ip.maskRequired)
  }
  if (form.hasMask && !form.maskSupported) {
    blockReasons.push(ip.maskUnsupported)
  }

  // textarea + メンションポップオーバーは mention state と密結合なので、ここで組み立てて
  // スロット(ReactNode)として渡す(コンポーネント化しない)。
  const promptEditor = (
    <>
      <label htmlFor="prompt" className={styles.srOnly}>
        {ip.promptLabel}
      </label>
      {promptEditMode === 'tags' ? (
        <div className={styles.textareaWrap}>
          <PromptTagEditor
            id="prompt"
            className={styles.promptTagEditor}
            value={form.prompt}
            onChange={(next) => {
              setSubmittedNotice(null)
              form.setPrompt(next)
            }}
            escapeParens={shouldEscapeParens(form.provider)}
            fieldLabel={ip.promptLabel}
          />
        </div>
      ) : (
        <div className={styles.textareaWrap}>
          <textarea
            id="prompt"
            ref={promptTextareaRef}
            className={styles.promptTextarea}
            value={form.prompt}
            maxLength={form.promptMax}
            role="combobox"
            aria-expanded={mention !== null}
            aria-controls={mention !== null ? MENTION_POPOVER_ID : undefined}
            aria-activedescendant={
              mention !== null && mentionCandidates.length > 0
                ? `${MENTION_POPOVER_ID}-option-${mentionActiveIndex}`
                : undefined
            }
            onChange={(e) => {
              setSubmittedNotice(null)
              form.setPrompt(e.target.value)
              updateMentionFromCursor(e.target)
            }}
            onKeyDown={handlePromptKeyDown}
            onKeyUp={(e) => {
              if (mention && MENTION_NAV_KEYS.has(e.key)) return
              if (suppressMentionRescanRef.current) {
                suppressMentionRescanRef.current = false
                return
              }
              updateMentionFromCursor(e.currentTarget)
            }}
            onClick={(e) => updateMentionFromCursor(e.currentTarget)}
            onPaste={editLogic.handlePaste}
            placeholder={ip.promptPlaceholder}
          />
          {mention !== null && (
            <MentionPopover
              id={MENTION_POPOVER_ID}
              candidates={mentionCandidates}
              activeIndex={mentionActiveIndex}
              placement={mentionPlacement}
              onSelect={confirmMention}
              onHoverIndex={setMentionActiveIndex}
            />
          )}
        </div>
      )}
    </>
  )

  // 入力画像は下段(チップ行)とサイドバー(タイル)で見た目だけが違い、props 型
  // (`InputImagesProps`)と `EditInputsLogic` は共通(ADR-0009 1章・2026-09-26 追記)。
  const inputImagesProps: InputImagesProps = {
    logic: editLogic,
    maxInputImages: form.maxInputImages,
    exactInputImagesRequired: form.exactInputImagesRequired,
    maskSupported: form.maskSupported,
    onOpenMaskEditor: () => editLogic.setMaskEditorOpen(true),
    blankSketchSize: { width: blankSketchSize.width, height: blankSketchSize.height },
    onPreviewAsset,
    onOpenAddDialog: () => setAddDialogOpen(true),
  }
  const inputImages =
    layout === 'sidebar' ? <InputImageTiles {...inputImagesProps} /> : <InputChipsRow {...inputImagesProps} />

  const promptTools = (
    <PromptToolsRow
      promptText={form.prompt}
      promptLength={form.promptLength}
      promptMax={form.promptMax}
      editModeToggle={
        <PromptEditModeToggle
          mode={promptEditMode}
          onChange={(mode) => {
            setMention(null)
            setPromptEditMode(mode)
          }}
          fieldLabel={ip.promptLabel}
        />
      }
      providerTools={
        form.provider === 'sdwebui' ? (
          <LoraPickerButton
            prompt={form.prompt}
            escapeParens={shouldEscapeParens(form.provider)}
            onInsert={(text, mode, cursorPos) => {
              setSubmittedNotice(null)
              form.insertPrompt(text, mode, cursorPos)
            }}
          />
        ) : null
      }
    />
  )

  const submitControls = (
    <SubmitControls
      showPrice={Boolean(form.providerEntry?.supports_pricing)}
      price={{
        model: form.model,
        operation: form.operation,
        size: priceSize,
        quality: priceQuality,
        n: priceN,
        promptLength: form.promptLength,
        inputAssetIds,
      }}
      canSubmit={form.canSubmit}
      isSubmitting={form.isSubmitting}
      onSubmit={() => form.submit()}
      variant={layout === 'sidebar' ? 'stacked' : 'inline'}
    />
  )

  const notices = (
    <InputPaneNotices
      submittedNotice={submittedNotice}
      droppedParamsNotice={form.droppedParamsNotice}
      blockReasons={blockReasons}
      submitError={form.submitError}
    />
  )

  const modelField = (
    <ModelSelect
      caps={caps}
      provider={form.provider}
      model={form.model}
      selectedModelDescription={selectedModel?.description}
      onSelect={form.selectModel}
    />
  )

  const paramFields = (
    <PrimaryParamFields
      sizeConstraints={form.providerEntry?.size}
      sizeState={form.sizeState}
      onSizeChange={form.setSizeState}
      defs={form.defs}
      rawParams={form.rawParams}
      conditionalParams={form.conditionalParams}
      hasMask={form.hasMask}
      onParamChange={form.handleParamChange}
      primary={primary}
    />
  )

  const groupField = (
    <AssetGroupField
      value={form.assetGroupId}
      groups={form.assetGroups}
      onChange={form.setAssetGroupId}
      className={styles.spanTwo}
    />
  )

  const otherParams = (
    <OtherParamsDetails
      other={other}
      defs={form.defs}
      rawParams={form.rawParams}
      conditionalParams={form.conditionalParams}
      hasMask={form.hasMask}
      onParamChange={form.handleParamChange}
    />
  )

  const slots = {
    inputImages,
    promptEditor,
    promptTools,
    submitControls,
    notices,
    modelField,
    paramFields,
    groupField,
    otherParams,
  }

  return (
    <div
      className={styles.pane}
      data-layout={layout}
      onKeyDown={handleKeyDown}
      onDrop={handlePaneDrop}
      onDragOver={handlePaneDragOver}
    >
      <PromptEditScopeContext.Provider value={{ provider: form.provider, model: form.model }}>
        {layout === 'sidebar' ? <InputPaneSidebarLayout {...slots} /> : <InputPaneBottomLayout {...slots} />}
      </PromptEditScopeContext.Provider>

      <AddInputImagesDialog
        open={addDialogOpen}
        onClose={() => setAddDialogOpen(false)}
        logic={editLogic}
        maxInputImages={form.maxInputImages}
      />

      {editLogic.maskEditorOpen && editLogic.positionZeroAsset && (
        <MaskEditor
          baseAsset={editLogic.positionZeroAsset}
          existingMaskAssetId={editLogic.maskInput?.assetId}
          maxMaskBytes={form.maxMaskBytes}
          onCancel={() => editLogic.setMaskEditorOpen(false)}
          onSave={editLogic.saveMask}
        />
      )}

      {sketchEditorState?.mode === 'blank' && (
        <SketchEditor
          base={{ kind: 'blank', width: blankSketchSize.width, height: blankSketchSize.height }}
          onSave={(asset, options) => handleSketchSave(asset, options)}
          onCancel={editLogic.closeSketchEditor}
        />
      )}
      {/* ADR-0010(2026-09-25 追記): used_as_input を古いキャッシュではなく取り直す間、
          既存のスケッチエディタと同じ見た目の「読み込み中」を出す。 */}
      {sketchEditorState?.mode === 'over' && editLogic.sketchOverAssetLoading && (
        <div className={sketchStyles.overlay} role="dialog" aria-modal="true">
          <div className={sketchStyles.modal}>
            <div className={sketchStyles.header}>
              <h2 className={sketchStyles.title}>{t.sketch.title}</h2>
              <button
                type="button"
                className={sketchStyles.closeButton}
                onClick={editLogic.closeSketchEditor}
                aria-label={t.sketch.close}
                title={t.sketch.close}
              >
                ✕
              </button>
            </div>
            <div className={sketchStyles.canvasArea}>
              <p className={sketchStyles.loading}>{t.sketch.loading}</p>
            </div>
          </div>
        </div>
      )}
      {sketchEditorState?.mode === 'over' && editLogic.sketchOverAsset && (
        <SketchEditor
          base={{ kind: 'asset', asset: editLogic.sketchOverAsset }}
          onSave={(asset, options) => handleSketchSave(asset, options, sketchEditorState.inputId)}
          onCancel={editLogic.closeSketchEditor}
        />
      )}
    </div>
  )
}
