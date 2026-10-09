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
import { expect, test } from 'vitest'

import { StreamTpsCell } from '../timing-metrics-cell'

test('streaming responses describe progressive delivery and honest average token speed', () => {
  render(<StreamTpsCell isStream tokensPerSecond={9.2} />)
  expect(screen.getByText('Shown as generated')).toBeVisible()
  expect(screen.getByText('Average 9 tokens/s')).toBeVisible()
  expect(screen.queryByText('9 t/s')).not.toBeInTheDocument()
})

test('non-streaming responses describe complete delivery without a made-up speed', () => {
  render(<StreamTpsCell isStream={false} />)
  expect(screen.getByText('Shown when complete')).toBeVisible()
  expect(screen.queryByText(/tokens\/s/)).not.toBeInTheDocument()
})

test('task responses retain their async status and do not invent token throughput', () => {
  render(<StreamTpsCell isStream={false} isTask />)
  expect(screen.getByText('Async')).toBeVisible()
  expect(screen.queryByText(/tokens\/s/)).not.toBeInTheDocument()
})
