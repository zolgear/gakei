/**
 * トリミング共通部品(ADR-0020 5章)。`CropEditor` を `Modal`(size large)に載せ、
 * 「決定」「キャンセル」を添える。初期矩形は `initialRect`(縦横比に合う中央の最大矩形)。
 * `naturalWidth`/`naturalHeight` を渡せなければ(アップロードしたファイルなど実寸が未知の場合)、
 * `CropEditor` が自分の `<img onLoad>` で実寸を取り、`onChange` 経由で初期矩形を伝えてくる。
 */
import { useState } from 'react'
import { Modal } from '../../components/Modal'
import { useI18n } from '../../i18n'
import { CropEditor } from './CropEditor'
import { initialRect, type CropRect } from './cropMath'
import styles from './ImageCropDialog.module.css'

export interface ImageCropDialogProps {
  open: boolean
  src: string
  aspect: number | null
  title: string
  onConfirm: (rect: CropRect) => void
  onCancel: () => void
  naturalWidth?: number
  naturalHeight?: number
}

const FALLBACK_RECT: CropRect = { x: 0, y: 0, width: 1, height: 1 }

function computeInitial(aspect: number | null, naturalWidth?: number, naturalHeight?: number): CropRect {
  if (naturalWidth && naturalHeight) return initialRect({ width: naturalWidth, height: naturalHeight }, aspect)
  return FALLBACK_RECT
}

export function ImageCropDialog({
  open,
  src,
  aspect,
  title,
  onConfirm,
  onCancel,
  naturalWidth,
  naturalHeight,
}: ImageCropDialogProps) {
  // 矩形の状態は `CropDialogBody` が持つ。`Modal` は閉じている間は子を描画しないので、次に
  // 開いたときは state が初期値から始まる。開いたまま画像や縦横比が変わったときは `key` で
  // 作り直して初期矩形に戻す(effect の中で setState して戻す形にしない)。
  return (
    <Modal open={open} title={title} onClose={onCancel} size="large">
      <CropDialogBody
        key={`${src}|${aspect ?? 'free'}|${naturalWidth ?? 0}x${naturalHeight ?? 0}`}
        src={src}
        aspect={aspect}
        naturalWidth={naturalWidth}
        naturalHeight={naturalHeight}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />
    </Modal>
  )
}

type CropDialogBodyProps = Omit<ImageCropDialogProps, 'open' | 'title'>

function CropDialogBody({
  src,
  aspect,
  naturalWidth,
  naturalHeight,
  onConfirm,
  onCancel,
}: CropDialogBodyProps) {
  const { t } = useI18n()
  const [rect, setRect] = useState<CropRect>(() => computeInitial(aspect, naturalWidth, naturalHeight))

  return (
    <>
      <div className={styles.body}>
        <p className={styles.hint}>{t.crop.hint}</p>
        <div className={styles.editorArea}>
          <CropEditor
            src={src}
            naturalWidth={naturalWidth}
            naturalHeight={naturalHeight}
            aspect={aspect}
            value={rect}
            onChange={setRect}
          />
        </div>
      </div>
      <div className={styles.footer}>
        <button type="button" className={styles.cancelButton} onClick={onCancel}>
          {t.crop.cancel}
        </button>
        <button type="button" className={styles.confirmButton} onClick={() => onConfirm(rect)}>
          {t.crop.confirm}
        </button>
      </div>
    </>
  )
}
