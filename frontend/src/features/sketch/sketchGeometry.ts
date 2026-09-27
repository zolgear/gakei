/** スケッチエディタの寸法・ブラシ半径の計算。DOM に依存しない純粋関数。 */
export interface PixelSize {
  width: number
  height: number
}

/**
 * 長辺が `maxLongEdge` を超えないよう、アスペクト比を保ったまま縮める(拡大はしない)。
 * 白紙スケッチの Canvas 実寸(=入力トークンの予算、Canvas の重さ)を抑えるために使う。
 */
export function fitWithinLongEdge(width: number, height: number, maxLongEdge: number): PixelSize {
  const longEdge = Math.max(width, height)
  if (longEdge <= 0 || longEdge <= maxLongEdge) {
    return { width: Math.max(1, Math.round(width)), height: Math.max(1, Math.round(height)) }
  }
  const scale = maxLongEdge / longEdge
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  }
}

/** ブラシの太さ3段(点の大きさで示す。文字ラベルは置かない)。 */
export type BrushSize = 'thin' | 'medium' | 'thick'

const BRUSH_SIZE_DIVISORS: Record<BrushSize, number> = {
  thin: 200,
  medium: 80,
  thick: 30,
}

/** Canvas の短辺から、太さ3段それぞれのブラシ半径(px)を計算する。最小 2px。 */
export function brushRadiusFor(shortEdge: number, size: BrushSize): number {
  return Math.max(2, Math.round(shortEdge / BRUSH_SIZE_DIVISORS[size]))
}

/**
 * スケッチエディタをどう開き、保存時にサーバーへ何を送るか(ADR-0010、2026-09-23 追記の
 * 上描き・2026-09-25 追記の「未使用スケッチの再編集」)。DOM に依存しない純粋関数にして、
 * 分岐(白紙 / 通常の上描き / 未使用スケッチの再開の3パターン)をテストしやすくする。
 *
 * `SketchEditor.tsx` の `SketchBase` / `AssetDetail` と構造的に一致する最小限の型を受け取る
 * (循環 import を避けるため、ここでは import しない)。
 */
export interface SketchReopenAsset {
  id: string
  /** AssetDetail.kind。'sketch' 以外は常に通常の上描き扱い。 */
  kind: string
  usedAsInput: boolean
  sourceAssetId: string | null
  width: number
  height: number
}

export type SketchOpenTarget =
  | { kind: 'blank'; width: number; height: number }
  | { kind: 'asset'; asset: SketchReopenAsset }

/** 保存時にサーバーへ送る値。'blank' は source_asset_id・replaces_asset_id のどちらも送らない。 */
export type SketchSaveMode = 'blank' | 'source' | 'replace'

/** Canvas の下地の作り方。'white': 白紙。'image': その Asset の preview を等倍で読み込む。 */
export type SketchBaseInit =
  | ({ kind: 'white' } & PixelSize)
  | { kind: 'image'; assetId: string }

/**
 * 線レイヤーの初期化。'empty': 何も描かない。'cache': IndexedDB のキャッシュ
 * (呼び出し側が読み込み済みの Blob をそのまま使う)。'composite': その Asset の preview を
 * 読み込んで線レイヤーに描く(下地が白のときだけ意味がある)。
 */
export type SketchLineLayerInit =
  | { kind: 'empty' }
  | { kind: 'cache' }
  | { kind: 'composite'; assetId: string }

export interface SketchOpenPlan {
  save: SketchSaveMode
  base: SketchBaseInit
  lineLayer: SketchLineLayerInit
}

/**
 * `hasCachedLayer` は呼び出し側が `sketchLayerStore`(IndexedDB)を先に引いた結果
 * (対象が未使用スケッチのときだけ意味を持つ)。ここでは同期の分岐だけを行う。
 */
export function planSketchOpen(
  target: SketchOpenTarget,
  hasCachedLayer: boolean,
  maxLongEdge: number,
): SketchOpenPlan {
  if (target.kind === 'blank') {
    return {
      save: 'blank',
      base: { kind: 'white', ...fitWithinLongEdge(target.width, target.height, maxLongEdge) },
      lineLayer: { kind: 'empty' },
    }
  }

  const asset = target.asset
  // 未使用スケッチの再編集(ADR-0010、2026-09-25 追記)の対象は、まだどの Run の入力にも
  // なっていない kind=sketch の Asset だけ。それ以外(使用済みのスケッチ・通常の画像)は
  // 従来どおりの上描き(合成画像を下地に、新しい source_asset_id = その Asset)。
  const isResumableSketch = asset.kind === 'sketch' && !asset.usedAsInput
  if (!isResumableSketch) {
    return { save: 'source', base: { kind: 'image', assetId: asset.id }, lineLayer: { kind: 'empty' } }
  }

  if (hasCachedLayer) {
    // 線レイヤーのキャッシュがあるので、下地は焼き込み済みの合成画像ではなく
    // 元の下地(source_asset_id、無ければ白紙)から作り直す。消しゴムが効く。
    return asset.sourceAssetId
      ? {
          save: 'replace',
          base: { kind: 'image', assetId: asset.sourceAssetId },
          lineLayer: { kind: 'cache' },
        }
      : {
          save: 'replace',
          base: { kind: 'white', ...fitWithinLongEdge(asset.width, asset.height, maxLongEdge) },
          lineLayer: { kind: 'cache' },
        }
  }

  if (asset.sourceAssetId === null) {
    // 白紙のスケッチで線レイヤーのキャッシュも無い: 下地は白のまま、合成画像
    // (=下地が白なので実質は線だけ)を線レイヤーに読み込む。消しゴムが効く。
    return {
      save: 'replace',
      base: { kind: 'white', ...fitWithinLongEdge(asset.width, asset.height, maxLongEdge) },
      lineLayer: { kind: 'composite', assetId: asset.id },
    }
  }

  // 上描きのスケッチで線レイヤーのキャッシュが無い(別のブラウザ・消去後など):
  // 従来どおり合成画像を下地として焼き込み、描き足す(消しゴムは効かない)。
  return { save: 'replace', base: { kind: 'image', assetId: asset.id }, lineLayer: { kind: 'empty' } }
}

/**
 * 保存した線レイヤーを次回の再開用に IndexedDB へキャッシュしてよいかどうか
 * (「以前の線が消えるバグ」対応。2026-09-25 追記のさらに後の修正)。
 *
 * キャンバスの下地(`plan.base`。白紙なら null、既存 Asset なら its id)と、
 * 保存レスポンスの Asset の `source_asset_id`(null もありうる)が一致するときだけ、
 * 今回描いた線をその新しい Asset の id をキーに保存してよい。一致しない典型例は、
 * 「線レイヤーのキャッシュが無い上描きスケッチ」を開いたとき: 下地には(元画像ではなく)
 * 焼き込み済みの合成画像そのものを使う(`planSketchOpen` の最後の分岐)ので、保存は
 * `replace` になり、サーバーは置き換え元の `source_asset_id`(=元画像)を新しい Asset に
 * 引き継ぐ。ここで今回の線だけを新しい id で保存してしまうと、次に開いたときに
 * 「元画像 + 今回の線だけ」になり、それ以前の線が消える。一致しなければ保存を諦め、
 * 次回は合成画像を下地として開く(線は焼き込まれるが、消えることはない)。
 *
 * サーバー側で置き換え元が使用済みと判断され、期待と違う連鎖(`source_asset_id` が
 * 置き換え元自身になる)になった場合も、この比較で自然に「保存しない」側に倒れる。
 */
export function shouldCacheSavedLineLayer(
  plan: SketchOpenPlan,
  savedSourceAssetId: string | null,
): boolean {
  const baseAssetId = plan.base.kind === 'image' ? plan.base.assetId : null
  return baseAssetId === savedSourceAssetId
}
