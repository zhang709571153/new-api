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
import { useEffect, useRef } from 'react'

/** Decorative only; pointer updates never trigger a React render. */
export function HomeAtmosphere() {
  const glowRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const glow = glowRef.current
    const surface = glow?.closest('main')
    if (!glow || !surface) return

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')
    const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)')
    let frame = 0
    let x = 0
    let y = 0

    const clear = () => {
      window.cancelAnimationFrame(frame)
      frame = 0
      glow.style.opacity = '0'
    }
    const move = (event: PointerEvent) => {
      if (
        reduced.matches ||
        !finePointer.matches ||
        event.pointerType !== 'mouse'
      ) {
        return
      }
      const bounds = surface.getBoundingClientRect()
      x = event.clientX - bounds.left
      y = event.clientY - bounds.top
      if (frame) return
      frame = window.requestAnimationFrame(() => {
        glow.style.setProperty('--glow-x', `${x}px`)
        glow.style.setProperty('--glow-y', `${y}px`)
        glow.style.opacity = '1'
        frame = 0
      })
    }

    surface.addEventListener('pointermove', move, { passive: true })
    surface.addEventListener('pointerleave', clear)
    reduced.addEventListener('change', clear)
    finePointer.addEventListener('change', clear)
    return () => {
      clear()
      surface.removeEventListener('pointermove', move)
      surface.removeEventListener('pointerleave', clear)
      reduced.removeEventListener('change', clear)
      finePointer.removeEventListener('change', clear)
    }
  }, [])

  return (
    <div aria-hidden='true' className='realyu-atmosphere pointer-events-none'>
      <div className='realyu-aurora realyu-aurora-one' />
      <div className='realyu-aurora realyu-aurora-two' />
      <div className='realyu-aurora realyu-aurora-three' />
      <div className='realyu-sky-texture' />
      <div className='realyu-orbit realyu-orbit-one' />
      <div className='realyu-orbit realyu-orbit-two' />
      <div ref={glowRef} className='realyu-pointer-glow' />
    </div>
  )
}
