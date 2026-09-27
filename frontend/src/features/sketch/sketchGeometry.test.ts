import { describe, expect, it } from 'vitest'
import {
  brushRadiusFor,
  fitWithinLongEdge,
  planSketchOpen,
  shouldCacheSavedLineLayer,
  type SketchReopenAsset,
} from './sketchGeometry'

describe('fitWithinLongEdge', () => {
  it('長辺が上限以下ならそのまま返す', () => {
    expect(fitWithinLongEdge(1024, 1024, 2048)).toEqual({ width: 1024, height: 1024 })
    expect(fitWithinLongEdge(1536, 1024, 2048)).toEqual({ width: 1536, height: 1024 })
  })

  it('長辺が上限ちょうどならそのまま返す', () => {
    expect(fitWithinLongEdge(2048, 1024, 2048)).toEqual({ width: 2048, height: 1024 })
  })

  it('長辺が上限を超えるとアスペクト比を保って縮める', () => {
    expect(fitWithinLongEdge(4096, 2048, 2048)).toEqual({ width: 2048, height: 1024 })
  })

  it('縦長でも同様に縮める', () => {
    expect(fitWithinLongEdge(2048, 4096, 2048)).toEqual({ width: 1024, height: 2048 })
  })

  it('0以下の寸法は1にクランプする', () => {
    expect(fitWithinLongEdge(0, 0, 2048)).toEqual({ width: 1, height: 1 })
  })
})

describe('brushRadiusFor', () => {
  it('太さ3段はそれぞれ短辺 / 200・80・30', () => {
    expect(brushRadiusFor(2000, 'thin')).toBe(10)
    expect(brushRadiusFor(2000, 'medium')).toBe(25)
    expect(brushRadiusFor(2000, 'thick')).toBe(67)
  })

  it('最小は2px', () => {
    expect(brushRadiusFor(100, 'thin')).toBe(2)
    expect(brushRadiusFor(1, 'thin')).toBe(2)
  })
})

describe('planSketchOpen', () => {
  const MAX_LONG_EDGE = 2048

  function sketchAsset(overrides: Partial<SketchReopenAsset> = {}): SketchReopenAsset {
    return {
      id: 'sketch-1',
      kind: 'sketch',
      usedAsInput: false,
      sourceAssetId: null,
      width: 512,
      height: 512,
      ...overrides,
    }
  }

  it('白紙: 出力サイズに収まる白い下地、線レイヤーは空、保存は source/replaces を送らない', () => {
    expect(planSketchOpen({ kind: 'blank', width: 4096, height: 2048 }, false, MAX_LONG_EDGE)).toEqual({
      save: 'blank',
      base: { kind: 'white', width: 2048, height: 1024 },
      lineLayer: { kind: 'empty' },
    })
  })

  it('通常の画像への上描き: 従来どおり合成画像を下地にし、source_asset_id で保存する', () => {
    const asset: SketchReopenAsset = {
      id: 'upload-1',
      kind: 'upload',
      usedAsInput: false,
      sourceAssetId: null,
      width: 1024,
      height: 1024,
    }
    expect(planSketchOpen({ kind: 'asset', asset }, false, MAX_LONG_EDGE)).toEqual({
      save: 'source',
      base: { kind: 'image', assetId: 'upload-1' },
      lineLayer: { kind: 'empty' },
    })
  })

  it('使用済みのスケッチへの上描き: 未使用スケッチの再開にはせず、従来どおり source_asset_id で保存する', () => {
    const asset = sketchAsset({ usedAsInput: true, sourceAssetId: 'base-1' })
    // キャッシュがあっても、使用済みなら証跡として残すため再開扱いにしない。
    expect(planSketchOpen({ kind: 'asset', asset }, true, MAX_LONG_EDGE)).toEqual({
      save: 'source',
      base: { kind: 'image', assetId: 'sketch-1' },
      lineLayer: { kind: 'empty' },
    })
  })

  it('未使用スケッチ(上描き)・キャッシュあり: 元の下地(source)+ キャッシュの線レイヤーで再開する', () => {
    const asset = sketchAsset({ sourceAssetId: 'base-1' })
    expect(planSketchOpen({ kind: 'asset', asset }, true, MAX_LONG_EDGE)).toEqual({
      save: 'replace',
      base: { kind: 'image', assetId: 'base-1' },
      lineLayer: { kind: 'cache' },
    })
  })

  it('未使用スケッチ(白紙)・キャッシュあり: 白紙(寸法はスケッチ自身)+ キャッシュの線レイヤーで再開する', () => {
    const asset = sketchAsset({ sourceAssetId: null, width: 4096, height: 2048 })
    expect(planSketchOpen({ kind: 'asset', asset }, true, MAX_LONG_EDGE)).toEqual({
      save: 'replace',
      base: { kind: 'white', width: 2048, height: 1024 },
      lineLayer: { kind: 'cache' },
    })
  })

  it('未使用スケッチ(白紙)・キャッシュ無し: 白紙 + 合成画像を線レイヤーに読み込む', () => {
    const asset = sketchAsset({ sourceAssetId: null })
    expect(planSketchOpen({ kind: 'asset', asset }, false, MAX_LONG_EDGE)).toEqual({
      save: 'replace',
      base: { kind: 'white', width: 512, height: 512 },
      lineLayer: { kind: 'composite', assetId: 'sketch-1' },
    })
  })

  it('未使用スケッチ(上描き)・キャッシュ無し: 従来どおり合成画像を下地にする(消しゴムは効かない)', () => {
    const asset = sketchAsset({ sourceAssetId: 'base-1' })
    expect(planSketchOpen({ kind: 'asset', asset }, false, MAX_LONG_EDGE)).toEqual({
      save: 'replace',
      base: { kind: 'image', assetId: 'sketch-1' },
      lineLayer: { kind: 'empty' },
    })
  })
})

describe('shouldCacheSavedLineLayer', () => {
  const MAX_LONG_EDGE = 2048

  function sketchAsset(overrides: Partial<SketchReopenAsset> = {}): SketchReopenAsset {
    return {
      id: 'sketch-1',
      kind: 'sketch',
      usedAsInput: false,
      sourceAssetId: null,
      width: 512,
      height: 512,
      ...overrides,
    }
  }

  it('白紙の新規: 下地は null、応答の source_asset_id も null なので保存してよい', () => {
    const plan = planSketchOpen({ kind: 'blank', width: 512, height: 512 }, false, MAX_LONG_EDGE)
    expect(shouldCacheSavedLineLayer(plan, null)).toBe(true)
  })

  it('通常の上描き: 下地の Asset id と応答の source_asset_id が一致するので保存してよい', () => {
    const asset: SketchReopenAsset = {
      id: 'upload-1',
      kind: 'upload',
      usedAsInput: false,
      sourceAssetId: null,
      width: 512,
      height: 512,
    }
    const plan = planSketchOpen({ kind: 'asset', asset }, false, MAX_LONG_EDGE)
    expect(shouldCacheSavedLineLayer(plan, 'upload-1')).toBe(true)
  })

  it('未使用スケッチ・キャッシュありの再開: 下地(元の source)と応答の source_asset_id が一致するので保存してよい', () => {
    const asset = sketchAsset({ sourceAssetId: 'base-1' })
    const plan = planSketchOpen({ kind: 'asset', asset }, true, MAX_LONG_EDGE)
    expect(shouldCacheSavedLineLayer(plan, 'base-1')).toBe(true)
  })

  it('白紙スケッチの composite 再開: 下地は null、応答の source_asset_id も null なので保存してよい', () => {
    const asset = sketchAsset({ sourceAssetId: null })
    const plan = planSketchOpen({ kind: 'asset', asset }, false, MAX_LONG_EDGE)
    expect(shouldCacheSavedLineLayer(plan, null)).toBe(true)
  })

  it('キャッシュ無し上描きの焼き込み再開: 下地はスケッチ自身の id、応答の source_asset_id は元画像なので保存しない(バグの再現ケース)', () => {
    const asset = sketchAsset({ sourceAssetId: 'base-1' })
    const plan = planSketchOpen({ kind: 'asset', asset }, false, MAX_LONG_EDGE)
    // サーバーは置き換え元(sketch-1)の source_asset_id(base-1)を引き継ぐ。
    expect(shouldCacheSavedLineLayer(plan, 'base-1')).toBe(false)
  })

  it('サーバーが使用済みと判断して連鎖した場合: 応答の source_asset_id が置き換え元自身になるので保存しない', () => {
    const asset = sketchAsset({ sourceAssetId: 'base-1' })
    // クライアントは未使用と判断してキャッシュありの再開プランを立てたが、
    const plan = planSketchOpen({ kind: 'asset', asset }, true, MAX_LONG_EDGE)
    // 保存時点でサーバーは使用済みと判断し、置き換え元自身(sketch-1)を source_asset_id にした。
    expect(shouldCacheSavedLineLayer(plan, 'sketch-1')).toBe(false)
  })
})
