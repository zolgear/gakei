import { useParams } from 'react-router'
import { Viewer } from '../features/viewer/Viewer'

export function AssetViewerPage() {
  const { id } = useParams<{ id: string }>()
  if (!id) return null
  // key={id} で asset を切り替えるたびに Viewer を再マウントし、
  // variant/倍率/コンテナ測定などのローカル状態が前の画像のまま残らないようにする。
  return <Viewer key={id} assetId={id} />
}
