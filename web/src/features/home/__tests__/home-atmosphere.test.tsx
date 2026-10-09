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
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { HomeAtmosphere } from '../components/home-atmosphere'

function preferences(reduced = false, fine = true) {
  const reduce = new EventTarget() as EventTarget & { matches: boolean }
  reduce.matches = reduced
  const pointer = new EventTarget() as EventTarget & { matches: boolean }
  pointer.matches = fine
  vi.spyOn(window, 'matchMedia').mockImplementation(
    (query) =>
      (query.includes('reduced-motion') ? reduce : pointer) as MediaQueryList
  )
  return reduce
}

function renderAtmosphere() {
  render(
    <main>
      <HomeAtmosphere />
      <a href='/sign-up'>Get started</a>
    </main>
  )
  const main = screen.getByRole('main')
  const glow = main.querySelector<HTMLElement>('.realyu-pointer-glow')
  if (!glow) throw new Error('Pointer glow is missing')
  return { main, glow }
}

function move(main: HTMLElement) {
  const event = new MouseEvent('pointermove', {
    clientX: 120,
    clientY: 180,
    bubbles: true,
  })
  Object.defineProperty(event, 'pointerType', { value: 'mouse' })
  fireEvent(main, event)
  act(() => vi.runOnlyPendingTimers())
}

afterEach(() => vi.useRealTimers())

describe('homepage motion preferences', () => {
  it('follows a mouse without covering links, and clears the glow on leaving', () => {
    vi.useFakeTimers()
    preferences()
    const { main, glow } = renderAtmosphere()
    move(main)
    expect(glow.style.getPropertyValue('--glow-x')).toBe('120px')
    expect(glow.style.opacity).toBe('1')
    expect(glow.closest('[aria-hidden="true"]')).toHaveClass(
      'pointer-events-none'
    )
    expect(screen.getByRole('link', { name: 'Get started' })).toBeVisible()
    fireEvent.pointerLeave(main)
    expect(glow.style.opacity).toBe('0')
  })

  it.each([
    [true, true],
    [false, false],
  ])('does not track a pointer with reduce=%s and fine=%s', (reduced, fine) => {
    vi.useFakeTimers()
    preferences(reduced, fine)
    const { main, glow } = renderAtmosphere()
    move(main)
    expect(glow.style.getPropertyValue('--glow-x')).toBe('')
    expect(glow.style.opacity).not.toBe('1')
  })

  it('stops an active glow when reduced motion is enabled without reloading', () => {
    vi.useFakeTimers()
    const reduced = preferences()
    const { main, glow } = renderAtmosphere()
    move(main)
    expect(glow.style.opacity).toBe('1')
    reduced.matches = true
    act(() => reduced.dispatchEvent(new Event('change')))
    expect(glow.style.opacity).toBe('0')
    move(main)
    expect(glow.style.opacity).toBe('0')
  })
})
