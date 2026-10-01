/**
 * 設定 →「ユーザー設定」の「プロフィール」(ADR-0020)。oidc モードのときだけ描画される
 * (設定画面(`pages/SettingsPage.tsx`)が `settingsToc` の結果でこのコンポーネント自体の
 * 描画を制御するので、ここでも念のため oidc モードでなければ何も描かない)。
 * 現在のアバター(`UserAvatar`。無ければ頭文字)、名前・メール(読み取り専用)、
 * 「画像をアップロード」「ストックから選ぶ」「削除」を出す。ストックから選ぶ一覧は
 * `AddInputImagesDialog` から切り出した `StockPickerGrid` を単一選択モードで使う。
 * どちらの経路も、画像を選んだ後にトリミングの共通部品(`ImageCropDialog`、ADR-0020 5章、
 * 縦横比 1 固定)を経て範囲を決めてから確定する。
 * 更新に成功したら `setUser()` でログイン中のユーザー情報だけを差し替える(UserMenu・
 * 履歴・Run 詳細に即座に反映される)。
 */
import { useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  ApiError,
  deleteAvatar,
  setAvatarFromAsset,
  uploadAvatar,
  type AssetSummary,
  type AuthUser as ApiAuthUser,
} from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { setUser, useAuth } from '../auth/authState'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Modal } from '../../components/Modal'
import { UserAvatar } from '../../components/UserAvatar'
import type { UseToastResult } from '../../components/Toast'
import { useI18n } from '../../i18n'
import { StockPickerGrid } from '../stock/StockPickerGrid'
import { ImageCropDialog } from '../crop/ImageCropDialog'
import type { CropRect } from '../crop/cropMath'
import styles from './ProfileSection.module.css'
import common from './settings.module.css'

interface ProfileSectionProps {
  toast: UseToastResult
}

/** アバターは常に正方形(ADR-0020)。 */
const AVATAR_ASPECT = 1

/** サーバー応答(生成型)をログイン状態(`authState.ts`)の `AuthUser` へ反映する。 */
function applyAuthUser(user: ApiAuthUser): void {
  setUser({ id: user.id, name: user.name, email: user.email, role: user.role, avatar_url: user.avatar_url ?? null })
}

export function ProfileSection({ toast }: ProfileSectionProps) {
  const { t } = useI18n()
  const auth = useAuth()
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerSelectedAsset, setPickerSelectedAsset] = useState<AssetSummary | null>(null)
  const [assetCropOpen, setAssetCropOpen] = useState(false)
  const [uploadCrop, setUploadCrop] = useState<{ file: File; previewUrl: string } | null>(null)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)

  const uploadMutation = useMutation({
    mutationFn: (vars: { file: File; crop: CropRect }) => uploadAvatar(vars.file, vars.crop),
    onSuccess: (data) => {
      applyAuthUser(data)
      toast.show({ message: t.settings.profile.savedToast })
      closeUploadCrop()
    },
  })

  const fromAssetMutation = useMutation({
    mutationFn: (vars: { assetId: string; crop: CropRect }) => setAvatarFromAsset(vars.assetId, vars.crop),
    onSuccess: (data) => {
      applyAuthUser(data)
      toast.show({ message: t.settings.profile.savedToast })
      setAssetCropOpen(false)
      setPickerSelectedAsset(null)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: deleteAvatar,
    onSuccess: (data) => {
      applyAuthUser(data)
      toast.show({ message: t.settings.profile.deletedToast })
    },
  })

  // none モードには出さない(設定画面の `settingsToc` が既にこの分岐をしているが、
  // 単体で使われても安全なように二重に見ておく)。全 hooks の後で早期 return する。
  if (auth.mode !== 'oidc' || !auth.user) return null
  const user = auth.user

  const activeError = uploadMutation.error ?? fromAssetMutation.error ?? deleteMutation.error
  const errorMessage =
    activeError instanceof ApiError ? activeError.message : activeError ? t.settings.profile.communicationFailed : null

  /** 選んだファイルを直接アップロードせず、まずトリミングのダイアログを開く。 */
  function handleFileChange(files: FileList | null) {
    const file = files?.[0]
    if (fileInputRef.current) fileInputRef.current.value = ''
    if (!file) return
    setUploadCrop({ file, previewUrl: URL.createObjectURL(file) })
  }

  function closeUploadCrop() {
    setUploadCrop((prev) => {
      if (prev) URL.revokeObjectURL(prev.previewUrl)
      return null
    })
  }

  function handleUploadCropConfirm(crop: CropRect) {
    if (!uploadCrop) return
    uploadMutation.mutate({ file: uploadCrop.file, crop })
  }

  function handleTogglePickerAsset(asset: AssetSummary) {
    setPickerSelectedAsset((prev) => (prev?.id === asset.id ? null : asset))
  }

  /** ストック一覧で1枚選んで「この画像にする」→ 一覧を閉じてトリミングのダイアログへ進む。 */
  function handleConfirmPicker() {
    if (!pickerSelectedAsset) return
    setPickerOpen(false)
    setAssetCropOpen(true)
  }

  function handleClosePicker() {
    setPickerOpen(false)
    setPickerSelectedAsset(null)
  }

  function handleAssetCropConfirm(crop: CropRect) {
    if (!pickerSelectedAsset) return
    fromAssetMutation.mutate({ assetId: pickerSelectedAsset.id, crop })
  }

  /** トリミングをキャンセルしたら、選択をやり直せるよう一覧に戻る。 */
  function handleAssetCropCancel() {
    setAssetCropOpen(false)
    setPickerOpen(true)
  }

  return (
    <section className={common.section}>

      <div className={styles.identityRow}>
        <UserAvatar name={user.name ?? user.email} avatarUrl={user.avatar_url} size={72} />
        <div className={styles.identityText}>
          <span className={styles.name}>{user.name ?? '-'}</span>
          {user.email && <span className={styles.email}>{user.email}</span>}
        </div>
      </div>

      <div className={common.actions}>
        <button
          type="button"
          className={common.secondaryButton}
          disabled={uploadMutation.isPending}
          onClick={() => fileInputRef.current?.click()}
        >
          {uploadMutation.isPending ? t.workspace.inputChips.uploading : t.settings.profile.upload}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className={styles.hiddenFileInput}
          onChange={(e) => handleFileChange(e.target.files)}
        />
        <button type="button" className={common.secondaryButton} onClick={() => setPickerOpen(true)}>
          {t.settings.profile.chooseFromStock}
        </button>
        {user.avatar_url && (
          <button
            type="button"
            className={common.dangerButton}
            disabled={deleteMutation.isPending}
            onClick={() => setDeleteConfirmOpen(true)}
          >
            {t.settings.profile.delete}
          </button>
        )}
      </div>

      {errorMessage && <p className={common.errorText}>{errorMessage}</p>}
      <p className={common.helpText}>{t.settings.profile.help}</p>

      <Modal open={pickerOpen} title={t.settings.profile.pickerTitle} onClose={handleClosePicker} size="large">
        <div className={styles.pickerBody}>
          <StockPickerGrid
            open={pickerOpen}
            selection="single"
            selectedIds={pickerSelectedAsset ? [pickerSelectedAsset.id] : []}
            onToggle={handleTogglePickerAsset}
            emptyText={t.workspace.addInputsDialog.empty}
            loadFailedText={t.workspace.addInputsDialog.loadFailed}
            loadMoreText={t.workspace.addInputsDialog.loadMore}
          />
        </div>
        <div className={styles.pickerFooter}>
          <button type="button" className={common.secondaryButton} onClick={handleClosePicker}>
            {t.common.close}
          </button>
          <button
            type="button"
            className={common.primaryButton}
            disabled={!pickerSelectedAsset}
            onClick={handleConfirmPicker}
          >
            {t.settings.profile.pickerConfirm}
          </button>
        </div>
      </Modal>

      {uploadCrop && (
        <ImageCropDialog
          open={uploadCrop !== null}
          src={uploadCrop.previewUrl}
          aspect={AVATAR_ASPECT}
          title={t.crop.title}
          onConfirm={handleUploadCropConfirm}
          onCancel={closeUploadCrop}
        />
      )}

      {pickerSelectedAsset && (
        <ImageCropDialog
          open={assetCropOpen}
          src={assetUrl(pickerSelectedAsset.id, 'preview')}
          aspect={AVATAR_ASPECT}
          title={t.crop.title}
          naturalWidth={pickerSelectedAsset.width}
          naturalHeight={pickerSelectedAsset.height}
          onConfirm={handleAssetCropConfirm}
          onCancel={handleAssetCropCancel}
        />
      )}

      <ConfirmDialog
        open={deleteConfirmOpen}
        message={t.settings.profile.deleteConfirm.message}
        confirmLabel={t.settings.profile.deleteConfirm.confirmLabel}
        onConfirm={() => {
          setDeleteConfirmOpen(false)
          deleteMutation.mutate()
        }}
        onCancel={() => setDeleteConfirmOpen(false)}
      />
    </section>
  )
}
