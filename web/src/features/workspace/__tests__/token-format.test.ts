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
import { describe, expect, it } from 'vitest'

import { formatWorkspaceTokens } from '../token-format'

describe('workspace token display', () => {
  it.each([
    [0, '0'],
    [9999, '9,999'],
    [10000, '1.00万'],
    [12345, '1.23万'],
    [999999, '100.00万'],
    [1000000, '1.00百万'],
    [12345678, '12.35百万'],
    [99999999, '100.00百万'],
    [100000000, '1.00亿'],
    [123456789, '1.23亿'],
  ])('formats %s Chinese tokens as %s', (tokens, expected) => {
    expect(formatWorkspaceTokens(tokens as number, 'zhCN')).toBe(expected)
  })

  it.each(['zhTW', 'zh-TW', 'zh-Hant', 'zh-HK'])(
    'uses Traditional Chinese units for %s',
    (locale) => {
      expect(formatWorkspaceTokens(10000, locale)).toBe('1.00萬')
      expect(formatWorkspaceTokens(1000000, locale)).toBe('1.00百萬')
      expect(formatWorkspaceTokens(100000000, locale)).toBe('1.00億')
    }
  )

  it.each(['en', 'fr', 'ja', 'ru', 'vi'])(
    'uses compact locale formatting for %s',
    (locale) => {
      expect(formatWorkspaceTokens(1234567, locale)).toBe(
        new Intl.NumberFormat(locale, {
          notation: 'compact',
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        }).format(1234567)
      )
    }
  )

  it.each([undefined, null, Number.NaN, Infinity, -Infinity, -1])(
    'does not display an invalid count (%s)',
    (value) => {
      expect(formatWorkspaceTokens(value, 'zhCN')).toBe('—')
    }
  )

  it('keeps small counts integral and handles an invalid language without crashing', () => {
    expect(formatWorkspaceTokens(1234.9, 'zhCN')).toBe('1,234')
    expect(formatWorkspaceTokens(10000, 'invalid_locale')).toBe(
      new Intl.NumberFormat(undefined, {
        notation: 'compact',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(10000)
    )
  })
})
