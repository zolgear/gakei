/**
 * 設定画面の末尾に置く「GAKEI について」セクション(ADR-0011 Consequences の
 * 2026-09-24・09-25 の項目、ADR-0021 3章)。著作権表示・ライセンス・GitHub リポジトリへの
 * リンクに加え、バージョン(と commit)、第三者ライセンス表記へのリンクを出す。
 * 依存のライセンスは GitHub 側の LICENSE を正とし、一覧はここには出さない
 * (第三者ライセンス表記のページの役目)。
 * データは `./about`(コード)と `GET /api/about`、見出しなどの文言は i18n の辞書に持つ。
 */
import { useQuery } from '@tanstack/react-query'
import { getAbout } from '../../api/client'
import { useI18n } from '../../i18n'
import { GAKEI_COPYRIGHT, GAKEI_LICENSE_NAME, GAKEI_LICENSE_URL, GAKEI_REPOSITORY_URL } from './about'
import styles from './AboutSection.module.css'
import { ABOUT_QUERY_KEY } from './queryKeys'

const THIRD_PARTY_NOTICES_URL = '/api/about/third-party-notices'

export function AboutSection() {
  const { t } = useI18n()
  const aboutQuery = useQuery({ queryKey: ABOUT_QUERY_KEY, queryFn: getAbout })
  const about = aboutQuery.data

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionHeading}>{t.settings.about.heading}</h2>

      <p className={styles.line}>{GAKEI_COPYRIGHT}</p>

      {about && (
        <p className={styles.line}>
          {t.settings.about.versionLabel}: {about.version}
          {about.commit ? ` (${about.commit.slice(0, 7)})` : ''}
        </p>
      )}

      <p className={styles.line}>
        {t.settings.about.licenseLabel}:{' '}
        <a href={GAKEI_LICENSE_URL} target="_blank" rel="noreferrer">
          {GAKEI_LICENSE_NAME}
        </a>
      </p>

      <p className={styles.line}>
        <a href={GAKEI_REPOSITORY_URL} target="_blank" rel="noreferrer">
          {t.settings.about.repositoryLink}
        </a>
      </p>

      <p className={styles.line}>
        <a href={THIRD_PARTY_NOTICES_URL} target="_blank" rel="noreferrer">
          {t.settings.about.thirdPartyLink}
        </a>
      </p>
    </section>
  )
}
