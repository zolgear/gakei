/**
 * アプリ内のサムネイル(`<img src="/api/assets/{id}/content?...">`)を入力画像のドロップ領域へ
 * ドラッグ&ドロップしたとき、ブラウザがそれを「外部ファイル」として dataTransfer.files に
 * 変換してしまう(同一オリジンの img ドラッグを File 化する挙動)ことがある。そのままアップロード
 * すると、サムネイル画像そのものが新しい Asset として重複登録されてしまう(実際に発生した不具合)。
 *
 * これを防ぐため、ドラッグ元(ストックのタイル・履歴カードの出力サムネイルなど)には
 * `dataTransfer.setData(GAKEI_ASSET_ID_DATA_TYPE, assetId)` を明示的に設定してもらい、
 * ドロップ側はまずこのマーカーを見る(印は `setAssetDragData` で付ける)。マーカーが無くても、
 * ブラウザが自動生成する `text/uri-list`、または `text/html` の `<img src>` が、このページと
 * 同じオリジンの `/api/assets/{id}/content` を指していれば、それも既存 Asset の参照として扱う
 * (保険)。どちらでもなく、ページの中から始まったドラッグ(`lib/pageDrag.ts`)なら、ブラウザが
 * 添えた File は取り込まない(元の画像とつながらない重複を作らないため)。外から持ち込んだ
 * ファイルだけを「新規画像」として扱い、`image/*` でない項目(URL ショートカット等)は黙って無視する。
 *
 * 解釈は純粋関数(`interpretDroppedData`)にして vitest で単体テストする。実際の DataTransfer
 * から読むのは `readDropSource` の1か所にまとめる。
 */
import { isDragFromThisPage } from '../../lib/pageDrag'


/** ドラッグ元が dataTransfer に設定する、アプリ内 Asset 参照のカスタム MIME タイプ。 */
export const GAKEI_ASSET_ID_DATA_TYPE = 'application/x-gakei-asset-id'

/** ドラッグ元に Asset の印を付ける(ストックのタイル・履歴のカード・マップの画像など)。 */
export function setAssetDragData(dataTransfer: Pick<DataTransfer, 'setData' | 'effectAllowed'>, assetId: string): void {
  dataTransfer.setData(GAKEI_ASSET_ID_DATA_TYPE, assetId)
  dataTransfer.effectAllowed = 'copy'
}

export interface DroppedFileLike {
  type: string
}

export interface DropSource<TFile extends DroppedFileLike> {
  /** dataTransfer.getData(GAKEI_ASSET_ID_DATA_TYPE)。無ければ null または空文字。 */
  assetIdData: string | null
  /** dataTransfer.getData('text/uri-list')。無ければ null または空文字。 */
  uriListData: string | null
  /** dataTransfer.getData('text/html')。無ければ null・空文字・省略。 */
  htmlData?: string | null
  /** dataTransfer.files を配列にしたもの。 */
  files: TFile[]
  /** このページの中から始まったドラッグか(`isDragFromThisPage()`)。省略は外から。 */
  fromPage?: boolean
  /**
   * このページのオリジン(`location.origin`)。絶対 URL はこれと同じオリジンのときだけ信じる。
   * 省略したときは相対 URL だけを信じる。
   */
  origin?: string | null
}

export type DropInterpretation<TFile extends DroppedFileLike> =
  | { kind: 'asset'; assetId: string }
  | { kind: 'files'; files: TFile[] }
  | { kind: 'none' }

const CONTENT_PATH_RE = /^\/api\/assets\/([0-9a-fA-F-]{36})\/content\/?$/

/**
 * 1つの URL が、このアプリの Asset 配信 URL(`/api/assets/{id}/content`。`variant` などの
 * 問い合わせ付きも)を指していれば、その Asset ID を返す。絶対 URL は `origin` と同じオリジンの
 * ときだけ受ける(別のサイトや別の GAKEI の URL は信じない)。
 */
export function extractAssetIdFromUrl(url: string, origin?: string | null): string | null {
  const value = url.trim()
  if (!value) return null
  const isRelative = value.startsWith('/') && !value.startsWith('//')
  if (!isRelative && !origin) return null
  let parsed: URL
  try {
    // 相対 URL を解くための仮の基準。オリジンが無いときは相対 URL だけがここに来る。
    parsed = new URL(value, origin || 'http://gakei.invalid')
  } catch {
    return null
  }
  if (!isRelative && parsed.origin !== new URL(origin as string).origin) return null
  const match = parsed.pathname.match(CONTENT_PATH_RE)
  return match ? match[1] : null
}

/**
 * `text/uri-list` の値(1件、または複数行のうち最初の非コメント行)が、このアプリの
 * Asset 配信 URL を指していれば、その Asset ID を返す(`extractAssetIdFromUrl`)。
 */
export function extractAssetIdFromContentUrl(uriListValue: string, origin?: string | null): string | null {
  const firstLine = uriListValue
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.length > 0 && !line.startsWith('#'))
  if (!firstLine) return null
  return extractAssetIdFromUrl(firstLine, origin)
}

/** `text/html` の最初の `<img src>` が、このアプリの Asset 配信 URL なら、その Asset ID を返す。 */
export function extractAssetIdFromHtml(html: string, origin?: string | null): string | null {
  const match = html.match(/<img\b[^>]*?\ssrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i)
  if (!match) return null
  const src = (match[1] ?? match[2] ?? match[3] ?? '').replace(/&amp;/g, '&')
  return extractAssetIdFromUrl(src, origin)
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

/**
 * 画像を運んでいそうなドラッグか(`dragover` では中身を読めないので種類だけを見る)。ファイルか、
 * アプリ内 Asset の印。ページの中の `<img>` のドラッグはブラウザが File を添えるので前者に入る。
 * 画像で探す欄のように、リンクだけのドラッグでは枠を出したくない受け口が使う。
 */
export function dragMayCarryImage(types: readonly string[]): boolean {
  return types.includes('Files') || types.includes(GAKEI_ASSET_ID_DATA_TYPE)
}

/** `image/*` の項目だけを残す(item 4: 非画像はエラーにせず黙って無視する)。 */
export function filterImageFiles<TFile extends DroppedFileLike>(files: TFile[]): TFile[] {
  return files.filter((f) => f.type.startsWith('image/'))
}

/**
 * ドロップされた内容を解釈する。優先順位:
 * 1. `GAKEI_ASSET_ID_DATA_TYPE` があれば、既存 Asset の参照として扱う。
 * 2. 無ければ `text/uri-list`、次に `text/html` の `<img src>` が、同じオリジンのこのアプリの
 *    Asset URL を指していないか見る(保険)。
 * 3. ページの中から始まったドラッグなら、ここで 'none'(ブラウザが添えた File を取り込まない)。
 * 4. 外からなら dataTransfer.files の `image/*` の項目だけを新規ファイルとして扱う
 *    (image/* が1件も無ければ 'none')。
 */
export function interpretDroppedData<TFile extends DroppedFileLike>(
  source: DropSource<TFile>,
): DropInterpretation<TFile> {
  if (source.assetIdData) {
    return { kind: 'asset', assetId: source.assetIdData }
  }

  if (source.uriListData) {
    const assetId = extractAssetIdFromContentUrl(source.uriListData, source.origin)
    if (assetId) return { kind: 'asset', assetId }
  }

  if (source.htmlData) {
    const assetId = extractAssetIdFromHtml(source.htmlData, source.origin)
    if (assetId) return { kind: 'asset', assetId }
  }

  if (source.fromPage) return { kind: 'none' }

  const imageFiles = filterImageFiles(source.files)
  if (imageFiles.length > 0) return { kind: 'files', files: imageFiles }

  return { kind: 'none' }
}

/** drop の DataTransfer から `interpretDroppedData` に渡す値を読む(ページ内か・オリジンも)。 */
export function readDropSource(dataTransfer: DataTransfer): DropSource<File> {
  return {
    assetIdData: dataTransfer.getData(GAKEI_ASSET_ID_DATA_TYPE) || null,
    uriListData: dataTransfer.getData('text/uri-list') || null,
    htmlData: dataTransfer.getData('text/html') || null,
    files: Array.from(dataTransfer.files),
    fromPage: isDragFromThisPage(),
    origin: typeof window === 'undefined' ? null : window.location.origin,
  }
}

/** drop の DataTransfer をそのまま解釈する。 */
export function interpretDrop(dataTransfer: DataTransfer): DropInterpretation<File> {
  return interpretDroppedData(readDropSource(dataTransfer))
}
