/**
 * ストックとビューアの「配置で開く」(ADR-0050 5章)。選んだ画像を素材にして配置の編集画面を
 * 開き、保存したら生成のフォームの入力に置いてスタジオへ移る(「入力に使う」と同じく
 * フォームの状態を書き換えてから `/studio` へ移動する)。
 */
import { useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { getCapabilities } from '../../api/client'
import { useRunFormContext } from '../../context/useRunFormContext'
import { fmt, msg } from '../../i18n'
import { editMaxInputImages } from '../../lib/capabilities'
import { applyCompositeToInputs } from './compositeInputs'
import { CompositeEditor, type CompositeSaveResult } from './CompositeEditor'

export function useOpenInArrange(options: { onNotice?: (message: string) => void } = {}): {
  open: (assetIds: string[]) => void
  editor: ReactNode
} {
  const { formState, setFormState } = useRunFormContext()
  const navigate = useNavigate()
  const capsQuery = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities })
  const maxInputImages = editMaxInputImages(capsQuery.data, formState.provider, formState.model)
  const [sourceAssetIds, setSourceAssetIds] = useState<string[] | null>(null)

  function handleSave(result: CompositeSaveResult) {
    const applied = applyCompositeToInputs(formState.inputs, result.asset.id, result.sourceAssetIds, {
      removeSources: result.removeSources,
      maxInputImages,
    })
    setSourceAssetIds(null)
    setFormState({ ...formState, inputs: applied.inputs })
    if (applied.droppedCount > 0) {
      options.onNotice?.(fmt(msg().composite.droppedInputs, { max: maxInputImages, count: applied.droppedCount }))
    }
    navigate('/studio')
  }

  const editor = sourceAssetIds ? (
    <CompositeEditor
      init={{ kind: 'new', sourceAssetIds }}
      inputAssetIds={formState.inputs.filter((i) => i.role === 'image').map((i) => i.assetId)}
      showRemoveSourcesOption={maxInputImages > 1}
      onSave={handleSave}
      onCancel={() => setSourceAssetIds(null)}
    />
  ) : null

  return { open: (ids) => setSourceAssetIds([...new Set(ids)]), editor }
}
