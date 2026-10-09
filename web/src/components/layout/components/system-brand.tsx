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
import { Link } from '@tanstack/react-router'

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from '@/components/ui/sidebar'
import { useStatus } from '@/hooks/use-status'
import { useSystemConfig } from '@/hooks/use-system-config'
import { DEFAULT_SYSTEM_NAME } from '@/lib/constants'
import { cn } from '@/lib/utils'

import { HeaderLogo } from './header-logo'

type SystemBrandProps = {
  defaultName?: string
  defaultVersion?: string
  /**
   * Visual layout:
   * - 'sidebar': stacked card style (used inside the sidebar header).
   * - 'inline': compact horizontal pill (used inside the top app bar).
   */
  variant?: 'sidebar' | 'inline'
}

/**
 * System brand component
 * Displays current system logo + name.
 * - inline: compact pill in the top app bar; clicking navigates to home (/)
 * - sidebar: stacked card in the sidebar header (display only)
 */
export function SystemBrand(props: SystemBrandProps) {
  const { status } = useStatus()
  const { logo } = useSystemConfig()

  const variant = props.variant ?? 'sidebar'
  const name = status?.system_name || props.defaultName || DEFAULT_SYSTEM_NAME

  if (variant === 'inline') {
    return (
      <Link
        to='/'
        aria-label={name}
        className={cn(
          'text-foreground inline-flex min-w-0 items-center rounded-md px-1.5 transition-colors outline-none select-none',
          'hover:bg-accent focus-visible:ring-ring/40 focus-visible:ring-2'
        )}
      >
        <HeaderLogo src={logo} alt={name} loading={false} logoLoaded />
      </Link>
    )
  }

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton
          size='lg'
          className='hover:text-sidebar-foreground active:text-sidebar-foreground cursor-default hover:bg-transparent active:bg-transparent'
          render={<div />}
        >
          <div className='flex min-w-0 flex-1 flex-col items-start gap-1 group-data-[collapsible=icon]:items-center'>
            <HeaderLogo
              src={logo}
              alt={name}
              loading={false}
              logoLoaded
              className='group-data-[collapsible=icon]:hidden'
            />
          </div>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
