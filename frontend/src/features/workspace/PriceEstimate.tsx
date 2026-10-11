/**
 * 生成ボタンの左に出す参考価格(ADR-0009 1節「参考価格(2026-09-22)」)。
 * `GET /api/pricing/estimate` の結果を、短い金額表示 + ツールチップの内訳に変換する。
 * 見積もりであって請求額ではない。
 */
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { estimatePrice } from '../../api/client'
import { describeEstimate } from './priceEstimateText'
import styles from './PriceEstimate.module.css'

interface PriceEstimateProps {
  model: string
  operation: 'generate' | 'edit'
  /** SizeState を送信値へ変換した後の文字列(未指定/auto はどちらも "auto")。 */
  size: string
  /** 未指定なら "auto" を渡すこと。 */
  quality: string
  n: number
  promptLength: number
  /** role=image の入力 Asset id(position 順)。 */
  inputAssetIds: string[]
  /** 繰り返し回数(ADR-0042)。見積もりは1回分を取り、表示で掛ける。 */
  repeat?: number
}

/** クエリキーだけに使う。文字入力のたびに叩かないよう、100文字単位に丸める。 */
function roundPromptLength(promptLength: number): number {
  return Math.round(promptLength / 100) * 100
}

export function PriceEstimate({
  model,
  operation,
  size,
  quality,
  n,
  promptLength,
  inputAssetIds,
  repeat = 1,
}: PriceEstimateProps) {
  const roundedPromptLength = roundPromptLength(promptLength)
  const inputAssetIdsKey = inputAssetIds.join(',')

  const query = useQuery({
    queryKey: ['pricing-estimate', model, operation, size, quality, n, roundedPromptLength, inputAssetIdsKey],
    queryFn: () =>
      estimatePrice({
        model,
        operation,
        size,
        quality,
        n,
        prompt_length: promptLength,
        input_asset_ids: inputAssetIds,
      }),
    enabled: model !== '',
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  })

  if (!query.data) return null

  const { text, title } = describeEstimate(query.data, repeat)

  return (
    <span className={styles.price} title={title}>
      {text}
    </span>
  )
}
