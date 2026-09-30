/**
 * スタジオの外(ビューア、Run の詳細ページ、`/lineage` のインスペクター)から「プロンプトに挿入/
 * 置き換え」をするための `RunPromptActions`(ADR-0030 3章。PE の出力の欄で使う)。
 *
 * スタジオのプロンプト欄は `useRunFormLogic` のローカル state なので、ここからは直接書き換え
 * られない。そこで RunFormContext の `requestPromptInsert` にリクエストを積んでスタジオへ移り、
 * スタジオ側が消費して反映する(サイドバーのプロンプトセットの「末尾に追加」と同じ仕組み)。
 * スタジオへ移るのは「入力に使う」「同じ設定で新規作成」「プロンプトセットの項目」と同じ。
 * 置き換えの確認に使う現在のプロンプトは context の `formState.prompt`(スタジオが最後に
 * 書き込んだ値。リロード後は localStorage から復元した値)。
 */
import { useNavigate } from 'react-router'
import { useRunFormContext } from '../../context/useRunFormContext'
import type { InsertPromptFn } from '../run-form/promptInsertion'
import { RunPromptActions } from './RunPromptActions'

export function StudioPromptActions({ prompt }: { prompt: string }) {
  const { formState, requestPromptInsert } = useRunFormContext()
  const navigate = useNavigate()

  // スタジオの外なのでカーソル位置は無い(RunPromptActions は null を渡す)。insert は末尾に足す。
  const insertPrompt: InsertPromptFn = (text, mode) => {
    requestPromptInsert(text, mode)
    navigate('/studio')
  }

  return <RunPromptActions prompt={prompt} currentPrompt={formState.prompt} insertPrompt={insertPrompt} />
}
