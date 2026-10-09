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
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { useAuthStore } from '@/stores/auth-store'

import { claimWelcomeCredit } from '../api'
import { WelcomeCredit } from '../welcome-credit'

vi.mock('../api', () => ({ claimWelcomeCredit: vi.fn() }))
let client: QueryClient
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  useAuthStore.getState().auth.setUser({ id: 3, username: 'alice', role: 1 })
})
afterEach(() => {
  client.clear()
  useAuthStore.setState(useAuthStore.getInitialState(), true)
})
function renderCredit() {
  return render(
    <QueryClientProvider client={client}>
      <WelcomeCredit />
    </QueryClientProvider>
  )
}
it('still grants the welcome credit and refreshes balances without a permanent success banner', async () => {
  vi.mocked(claimWelcomeCredit).mockResolvedValue({
    user_id: 3,
    status: 'granted',
    source: 'registration',
    reason: '',
    amount_cents: 500,
  })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const { container } = renderCredit()
  await waitFor(() =>
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['workspace', 3] })
  )
  expect(claimWelcomeCredit).toHaveBeenCalledTimes(1)
  expect(container).toBeEmptyDOMElement()
})
it('retains actionable pending review and failure retry feedback', async () => {
  vi.mocked(claimWelcomeCredit)
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce({
      user_id: 3,
      status: 'pending',
      source: 'registration',
      reason: 'review',
      amount_cents: 500,
    })
  const user = userEvent.setup()
  renderCredit()
  await user.click(
    await screen.findByRole('button', { name: 'Retry welcome credit' })
  )
  expect(await screen.findByRole('status')).toHaveTextContent(
    'Welcome credit is awaiting review.'
  )
  expect(claimWelcomeCredit).toHaveBeenCalledTimes(2)
})
