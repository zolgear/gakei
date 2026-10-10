/**
 * 画像の「タグをプロンプトに使う」(ADR-0039 1章)。ビューアのタグ節と、系列グラフの Asset の
 * インスペクターに置く。
 *
 * - タグは `GET /api/assets/{id}/prompt-tags`(removed を除き、WD Tagger の語彙にあるものを
 *   score の高い順、続けて人が付けた語彙にあるタグ。語彙が無ければ英数字だけのタグ)。
 *   プロンプトに使えるタグが無ければ何も出さない。
 * - コピー: カンマ区切りでクリップボードへ。送り先が分からないので括弧は `\(` `\)` にエスケープする。
 * - 置き換え / 後ろに足す: 生成のフォームのプロンプトに入れてスタジオへ移る(`StudioPromptActions`
 *   と同じく RunFormContext にリクエストを積む)。プロバイダーとモデルは今の選択のまま。
 *   括弧は今のフォームのプロバイダーに合わせる(OpenAI ではエスケープしない)。置き換えは、今の
 *   プロンプトが空でなければ2段階で確かめる。
 *
 * クエリのキーは `['tags', 'prompt', id]`。タグを編集すると `['tags']` ごと取り直される。
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { getAssetPromptTags } from '../../api/client'
import { useRunFormContext } from '../../context/useRunFormContext'
import { fmt, useI18n } from '../../i18n'
import { copyText } from '../../lib/copyText'
import { shouldConfirmReplace } from '../run-form/promptInsertion'
import { shouldEscapeParens, tagNamesToPrompt } from './promptTags'
import styles from './PromptTagsActions.module.css'

export function PromptTagsActions({ assetId }: { assetId: string }) {
  const { t } = useI18n()
  const pt = t.promptTags.fromImage
  const navigate = useNavigate()
  const { formState, requestPromptInsert } = useRunFormContext()
  const [confirmingReplace, setConfirmingReplace] = useState(false)
  const [copyStatus, setCopyStatus] = useState<{ assetId: string; ok: boolean } | null>(null)

  const query = useQuery({
    queryKey: ['tags', 'prompt', assetId],
    queryFn: () => getAssetPromptTags(assetId),
    staleTime: 30_000,
  })
  const tags: string[] = (query.data?.asset_id === assetId ? query.data.tags : undefined) ?? []
  if (tags.length === 0) return null

  const status = copyStatus?.assetId === assetId ? copyStatus : null

  async function handleCopy() {
    const ok = await copyText(tagNamesToPrompt(tags, true))
    setCopyStatus({ assetId, ok })
  }

  function send(mode: 'replace' | 'append-tags') {
    const text = tagNamesToPrompt(tags, shouldEscapeParens(formState.provider))
    requestPromptInsert(text, mode)
    setConfirmingReplace(false)
    navigate('/studio')
  }

  function handleReplaceClick() {
    if (shouldConfirmReplace(formState.prompt)) setConfirmingReplace(true)
    else send('replace')
  }

  return (
    <div className={styles.root}>
      <p className={styles.heading}>
        {pt.heading}
        <span className={styles.count}>{fmt(pt.count, { count: tags.length })}</span>
      </p>
      <div className={styles.row}>
        <button type="button" className={styles.button} title={pt.copyTitle} onClick={() => void handleCopy()}>
          {pt.copy}
        </button>
        {!confirmingReplace ? (
          <>
            <button type="button" className={styles.button} title={pt.replaceTitle} onClick={handleReplaceClick}>
              {pt.replace}
            </button>
            <button type="button" className={styles.button} title={pt.appendTitle} onClick={() => send('append-tags')}>
              {pt.append}
            </button>
          </>
        ) : (
          <span className={styles.confirmGroup}>
            <span className={styles.confirmLabel}>{pt.confirmReplace}</span>
            <button type="button" className={styles.confirmYes} onClick={() => send('replace')}>
              {pt.yes}
            </button>
            <button type="button" className={styles.confirmCancel} onClick={() => setConfirmingReplace(false)}>
              {pt.cancel}
            </button>
          </span>
        )}
        {status && (
          <span className={styles.status} role="status" data-ok={status.ok}>
            {status.ok ? pt.copied : pt.copyFailed}
          </span>
        )}
      </div>
    </div>
  )
}
