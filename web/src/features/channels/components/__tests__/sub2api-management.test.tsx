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
import { describe, expect, it } from 'vitest'

import { Sub2APIManagement } from '../sub2api-management'

describe('Sub2API channel management', () => {
  it('opens the configured console without sharing the current page context', () => {
    render(
      <Sub2APIManagement
        status={{
          enabled: true,
          configured: true,
          admin_url: 'https://supply.example.test/admin',
        }}
      />
    )
    const link = screen.getByRole('link', { name: 'Open Sub2API management' })
    expect(link).toHaveAttribute('href', 'https://supply.example.test/admin')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(
      screen.getByText('Service connection configured')
    ).toBeInTheDocument()
  })

  it('keeps the console available when the service connection needs setup', () => {
    render(
      <Sub2APIManagement
        status={{
          enabled: true,
          configured: false,
          admin_url: 'http://127.0.0.1:28082',
        }}
      />
    )
    expect(
      screen.getByRole('link', { name: 'Open Sub2API management' })
    ).toHaveAttribute('href', 'http://127.0.0.1:28082/')
    expect(
      screen.getByText('Service connection needs configuration')
    ).toBeInTheDocument()
  })

  it.each([
    '',
    'javascript:alert(1)',
    'https://name:password@example.test',
    'http://example.test',
  ])(
    'does not turn an unsafe or missing console URL into a link: %s',
    (admin_url) => {
      render(
        <Sub2APIManagement
          status={{ enabled: true, configured: false, admin_url }}
        />
      )
      expect(screen.queryByRole('link')).not.toBeInTheDocument()
      expect(
        screen.getByText(
          'The administrator needs to configure the Sub2API management URL.'
        )
      ).toBeInTheDocument()
    }
  )
})
