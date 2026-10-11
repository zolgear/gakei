/**
 * 指示(プロンプト)で探した結果(ADR-0045 6章、2026-10-11 追記)を、検索ページの履歴のカードで
 * 並べる形にする。純粋関数のみ。
 *
 * カードはキーワード検索の Run と同じ `HistoryCard`(`RunSummary`)を使い、類似度を添える。
 * 類似度の書き方は画像の意味の検索(`formatScore`)に揃える。
 */
import type { RunSummary, SemanticSearchResponse } from '../../api/client'
import { formatScore } from '../embeddings/duplicates'

export interface PromptRunCard {
  run: RunSummary
  /** 表示用の類似度(例: "0.873")。 */
  score: string
}

/** 応答の `runs` を、類似度の高い順(応答の順)のままカードの形にする。 */
export function promptRunCards(response: Pick<SemanticSearchResponse, 'runs'> | undefined): PromptRunCard[] {
  return (response?.runs ?? []).map((hit) => ({ run: hit.run, score: formatScore(hit.score) }))
}
