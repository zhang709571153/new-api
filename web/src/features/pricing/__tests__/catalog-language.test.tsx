import { act, render, screen } from '@testing-library/react'
import i18n from 'i18next'
import { I18nextProvider, useTranslation } from 'react-i18next'
import { afterEach, expect, it } from 'vitest'

import zh from '@/i18n/locales/zh.json'

import { catalogI18n } from '../lib/catalog-language'

function Labels() {
  const { t } = useTranslation()
  return <span>{t('Input')}</span>
}
afterEach(async () => {
  await i18n.changeLanguage('en')
  i18n.removeResourceBundle('zhCN', 'translation')
})
it('catalog follows Chinese and subsequent language changes across the site', async () => {
  await act(async () => {
    i18n.addResourceBundle('zhCN', 'translation', zh.translation, true, true)
    await i18n.changeLanguage('zhCN')
  })
  render(
    <>
      <I18nextProvider i18n={catalogI18n}>
        <Labels />
      </I18nextProvider>
      <I18nextProvider i18n={i18n}>
        <Labels />
      </I18nextProvider>
    </>
  )
  expect(screen.getAllByText(i18n.t('Input'))).toHaveLength(2)
  expect(screen.queryByText('Input')).not.toBeInTheDocument()
  expect(i18n.language).toBe('zhCN')
  expect(catalogI18n.language).toBe('zhCN')
  await act(async () => {
    await i18n.changeLanguage('en')
  })
  expect(screen.getAllByText('Input')).toHaveLength(2)
})
