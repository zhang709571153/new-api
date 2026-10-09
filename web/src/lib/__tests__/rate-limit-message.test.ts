/*
Copyright (C) 2023-2026 QuantumNous

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

For commercial licensing, please contact support@quantumnous.com
*/
import { AxiosError } from 'axios'
import { createInstance } from 'i18next'
import { expect, test } from 'vitest'

import zh from '@/i18n/locales/zh.json'

import { AuthOperationError } from '../secure-verification'
import {
  getServerErrorMessage,
  getServerErrorMessageKey,
} from '../server-error-message'

test('a bare proxy 429 becomes a readable wait message for login and logout', () => {
  const error = new AxiosError('Request failed with status code 429')
  error.response = {
    status: 429,
    data: '',
    headers: {},
    statusText: 'Too Many Requests',
    config: { headers: {} },
  } as never
  expect(getServerErrorMessage(error)).not.toContain('status code')
  expect(AuthOperationError.from(error).message).toBe(
    'Too many attempts. Please wait a moment and try again.'
  )
})

test('the backend throttle code maps to safe guidance', () => {
  expect(getServerErrorMessageKey({ code: 'RATE_LIMITED' })).toBe(
    'Too many attempts. Please wait a moment and try again.'
  )
})

test('published Chinese resources resolve the new authentication, usage and allowance copy', async () => {
  const language = createInstance()
  await language.init({ lng: 'zh', resources: { zh }, keySeparator: false })
  expect(
    language.t('Too many attempts. Please wait a moment and try again.')
  ).toBe('操作较频繁，请稍等一会再试。')
  expect(language.t('Monthly allowance')).toBe('月度额度')
  expect(language.t('Weekly allowance (USD)')).toBe('每周额度')
  expect(language.t('Shown as generated')).toBe('生成时显示')
  expect(language.t('Average {{speed}} tokens/s', { speed: 9 })).toBe(
    '平均每秒 9 Token'
  )
})
