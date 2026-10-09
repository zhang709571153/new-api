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
import { DEFAULT_LOGO, DEFAULT_SYSTEM_NAME } from '@/lib/constants'
import { cn } from '@/lib/utils'

interface HeaderLogoProps {
  src: string
  alt?: string
  loading: boolean
  logoLoaded: boolean
  className?: string
}

export function HeaderLogo(props: HeaderLogoProps) {
  return (
    <img
      src={props.src || DEFAULT_LOGO}
      alt={props.alt || DEFAULT_SYSTEM_NAME}
      width={180}
      height={40}
      className={cn(
        'block h-10 w-45 max-w-full object-contain',
        props.className
      )}
    />
  )
}
