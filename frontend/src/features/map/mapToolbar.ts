/**
 * マップの上部(1 段)の出し分け。見た目を持たない計算だけをここに置く。
 *
 * 上部はタブ・見せ方・検索欄・「絞り込み」「表示」のボタンだけにし、たまにしか触らない設定は
 * ボタンを押して開くパネル(canvas に重ねる)へ入れる。どの項目をどちらのパネルに出すかと、
 * ボタンに添える値の決め方をここに集める。
 */
import type { MapDistanceMetric } from './umapInput'
import type { MapView } from './mapUrlState'

/** 「表示」パネルに出す項目(どう見せるか)。 */
export interface MapDisplayFields {
  /** 類似度のしきい値(ネットワークのみ)。 */
  threshold: boolean
  /** 系列の辺を重ねる(ネットワークのみ)。 */
  lineage: boolean
  /** 配置の距離の取り方(地図のみ。平面でも出す)。 */
  metric: boolean
  /** 一致の件数(検索しているときだけ)。 */
  matchCount: boolean
}

/** タブと検索の有無から、「表示」パネルの項目を決める。 */
export function mapDisplayFields(view: MapView, searching: boolean): MapDisplayFields {
  return {
    threshold: view === 'network',
    lineage: view === 'network',
    metric: view === 'umap3d',
    matchCount: searching,
  }
}

/** 「表示」ボタンに添える主要な値(今どうなっているかをボタンだけで分かるように)。 */
export type MapDisplaySummary =
  | { kind: 'threshold'; text: string }
  | { kind: 'metric'; metric: MapDistanceMetric }

/** ネットワークはしきい値、地図は距離の取り方を添える。 */
export function mapDisplaySummary(
  view: MapView,
  threshold: number,
  metric: MapDistanceMetric,
): MapDisplaySummary {
  if (view === 'network') return { kind: 'threshold', text: threshold.toFixed(2) }
  return { kind: 'metric', metric }
}
