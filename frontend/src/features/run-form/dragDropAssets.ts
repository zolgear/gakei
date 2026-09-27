/**
 * アプリ内のサムネイル(`<img src="/api/assets/{id}/content?...">`)を入力画像のドロップ領域へ
 * ドラッグ&ドロップしたとき、ブラウザがそれを「外部ファイル」として dataTransfer.files に
 * 変換してしまう(同一オリジンの img ドラッグを File 化する挙動)ことがある。そのままアップロード
 * すると、サムネイル画像そのものが新しい Asset として重複登録されてしまう(実際に発生した不具合)。
 *
 * これを防ぐため、ドラッグ元(ストックのタイル・履歴カードの出力サムネイルなど)には
 * `dataTransfer.setData(GAKEI_ASSET_ID_DATA_TYPE, assetId)` を明示的に設定してもらい、
 * ドロップ側はまずこのマーカーを見る。マーカーが無くても、ブラウザが自動生成する
 * `text/uri-list` が自アプリの `/api/assets/{id}/content` を指していれば、それも既存 Asset
 * の参照として扱う(保険)。どちらでもなければ、dataTransfer.files を「外部からの新規画像」
 * として扱うが、`image/*` でない項目(URL ショートカット等)は黙って無視する。
 *
 * 純粋関数のみ(副作用なし)にして vitest で単体テストする。実際の DragEvent/DataTransfer は
 * 呼び出し側(コンポーネント)で読み取り、ここにはプレーンなデータだけを渡す。
 */

/** ドラッグ元が dataTransfer に設定する、アプリ内 Asset 参照のカスタム MIME タイプ。 */
export const GAKEI_ASSET_ID_DATA_TYPE = 'application/x-gakei-asset-id'

export interface DroppedFileLike {
  type: string
}

export interface DropSource<TFile extends DroppedFileLike> {
  /** dataTransfer.getData(GAKEI_ASSET_ID_DATA_TYPE)。無ければ null または空文字。 */
  assetIdData: string | null
  /** dataTransfer.getData('text/uri-list')。無ければ null または空文字。 */
  uriListData: string | null
  /** dataTransfer.files を配列にしたもの。 */
  files: TFile[]
}

export type DropInterpretation<TFile extends DroppedFileLike> =
  | { kind: 'asset'; assetId: string }
  | { kind: 'files'; files: TFile[] }
  | { kind: 'none' }

/**
 * `text/uri-list` の値(1件、または複数行のうち最初の非コメント行)が、自アプリの
 * Asset 配信 URL(`/api/assets/{id}/content`)を指していれば、その Asset ID を返す。
 * 絶対 URL(`http://host/api/assets/...`)でも相対 URL でも、パス部分だけ見て判定する。
 */
export function extractAssetIdFromContentUrl(uriListValue: string): string | null {
  const firstLine = uriListValue
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.length > 0 && !line.startsWith('#'))
  if (!firstLine) return null

  const match = firstLine.match(/\/api\/assets\/([0-9a-fA-F-]{36})\/content(?:[/?]|$)/)
  return match ? match[1] : null
}

/**
 * dataTransfer.types にファイル・アプリ内 Asset マーカー・text/uri-list のいずれかが
 * 含まれるか(= このモジュールが処理できる種類のドラッグか)。下段全体のような広いドロップ領域で、
 * テキスト選択のドラッグ(例: textarea への通常のテキストドロップ)まで奪ってしまわないよう、
 * `dragover`/`drop` を実際に処理する前にこれで絞り込む。
 */
export function isRelevantDragTypes(types: readonly string[]): boolean {
  return (
    types.includes('Files') ||
    types.includes(GAKEI_ASSET_ID_DATA_TYPE) ||
    types.includes('text/uri-list')
  )
}

/** `image/*` の項目だけを残す(item 4: 非画像はエラーにせず黙って無視する)。 */
export function filterImageFiles<TFile extends DroppedFileLike>(files: TFile[]): TFile[] {
  return files.filter((f) => f.type.startsWith('image/'))
}

/**
 * ドロップされた内容を解釈する。優先順位:
 * 1. `GAKEI_ASSET_ID_DATA_TYPE` があれば、既存 Asset の参照として扱う。
 * 2. 無ければ `text/uri-list` が自アプリの Asset URL を指していないか見る(保険)。
 * 3. どちらでもなければ dataTransfer.files を見て、`image/*` の項目だけを新規ファイルとして扱う
 *    (image/* が1件も無ければ 'none')。
 */
export function interpretDroppedData<TFile extends DroppedFileLike>(
  source: DropSource<TFile>,
): DropInterpretation<TFile> {
  if (source.assetIdData) {
    return { kind: 'asset', assetId: source.assetIdData }
  }

  if (source.uriListData) {
    const assetId = extractAssetIdFromContentUrl(source.uriListData)
    if (assetId) return { kind: 'asset', assetId }
  }

  const imageFiles = filterImageFiles(source.files)
  if (imageFiles.length > 0) return { kind: 'files', files: imageFiles }

  return { kind: 'none' }
}
