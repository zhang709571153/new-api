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
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { CodexGuide } from '@/features/dashboard/components/overview/codex-guide'
import { isDevelopmentSite } from '@/lib/development-site'

import { DevelopmentBanner } from '../development-banner'

vi.mock('@/lib/development-site', () => ({ isDevelopmentSite: vi.fn() }))
afterEach(() => vi.clearAllMocks())

describe('development deployment boundary', () => {
  it('only matches the configured development hostname', async () => {
    const actual = await vi.importActual<
      typeof import('@/lib/development-site')
    >('@/lib/development-site')
    expect(actual.isDevelopmentSite('dev.realyu.fun')).toBe(true)
    expect(actual.isDevelopmentSite('api.realyu.fun')).toBe(false)
    expect(actual.isDevelopmentSite('dev.realyu.fun.example.org')).toBe(false)
  })

  it('shows a test-data notice and a live-site link', () => {
    vi.mocked(isDevelopmentSite).mockReturnValue(true)
    render(<DevelopmentBanner />)
    expect(screen.getByRole('status')).toHaveTextContent(
      'Changes do not affect your live balance.'
    )
    expect(
      screen.getByRole('link', { name: 'Open live site' })
    ).toHaveAttribute('href', 'https://api.realyu.fun')
  })

  it('leaves production without a development banner', () => {
    vi.mocked(isDevelopmentSite).mockReturnValue(false)
    render(<DevelopmentBanner />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('does not offer production installer commands with development keys', () => {
    vi.mocked(isDevelopmentSite).mockReturnValue(true)
    render(
      <CodexGuide
        hasKey
        credential={{
          secret: 'sk-test-only',
          pending: false,
          visible: true,
          setVisible: vi.fn(),
          failed: false,
          reload: vi.fn(async () => true),
        }}
      />
    )
    expect(
      screen.getByText(
        'Client setup is unavailable on this test site. Use the live site for your work devices.'
      )
    ).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Copy setup command' })
    ).not.toBeInTheDocument()
  })
})
