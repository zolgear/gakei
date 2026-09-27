/**
 * 「この設定をフォームに読み込む」ボタン。自己申告(未検証)のレシピ(`OriginRunInfo`)を
 * スタジオのフォームへ読み込む処理と、今の環境に無いモデルを指しているときの無効化判定を
 * 1箇所にまとめる。`OriginRecipeSection`(v1/v2 の `origin.meta`)と `EmbeddedNodeInspector`
 * (系列グラフの埋め込み Run ノード)の両方から使う(ADR-0014)。
 */
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { getCapabilities } from '../../api/client'
import { useRunFormContext } from '../../context/useRunFormContext'
import { useI18n } from '../../i18n'
import { buildOriginFormState, originRecipeDisabledReason, type OriginRunInfo } from './originRecipe'
import styles from './OriginRecipeSection.module.css'

export interface RecipeLoadButtonProps {
  runInfo: OriginRunInfo | null
}

export function RecipeLoadButton({ runInfo }: RecipeLoadButtonProps) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const { setFormState } = useRunFormContext()
  // provider/model が今の環境に無いモデルを指していないかの判定にだけ使う。
  const capsQuery = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities })
  const disabledReason = originRecipeDisabledReason(runInfo, capsQuery.data)

  function handleLoadRecipe() {
    if (runInfo === null || disabledReason !== null) return
    setFormState(buildOriginFormState(runInfo))
    navigate('/studio')
  }

  return (
    <>
      <button
        type="button"
        className={styles.button}
        onClick={handleLoadRecipe}
        disabled={disabledReason !== null}
        title={disabledReason ?? undefined}
      >
        {t.lineage.originRecipe.loadIntoForm}
      </button>
      {disabledReason && <p className={styles.note}>{disabledReason}</p>}
    </>
  )
}
