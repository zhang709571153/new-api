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
import { useAuthStore } from '@/stores/auth-store'

import type { TopNavLink } from '../types'
import { AuthenticatedLayout } from './authenticated-layout'
import { PublicHeader, type PublicHeaderProps } from './public-header'

type PublicLayoutProps = {
  children: React.ReactNode
  showMainContainer?: boolean
  showSidebarWhenAuthenticated?: boolean
  navContent?: React.ReactNode
  headerProps?: Omit<PublicHeaderProps, 'navContent'>
  navLinks?: TopNavLink[]
  showThemeSwitch?: boolean
  showAuthButtons?: boolean
  showNotifications?: boolean
  logo?: React.ReactNode
  siteName?: string
}

export function PublicLayout(props: PublicLayoutProps) {
  const signedIn = useAuthStore((state) => Boolean(state.auth.user))
  if (props.showSidebarWhenAuthenticated && signedIn) {
    return (
      <AuthenticatedLayout>
        <div
          id='content'
          tabIndex={-1}
          className='min-h-0 min-w-0 flex-1 overflow-y-auto'
        >
          {props.showMainContainer !== false ? (
            <div className='container px-4 py-6 md:px-6'>{props.children}</div>
          ) : (
            props.children
          )}
        </div>
      </AuthenticatedLayout>
    )
  }
  return (
    <div className='bg-background text-foreground relative min-h-svh overflow-x-clip'>
      <PublicHeader
        navContent={props.navContent}
        navLinks={props.navLinks}
        showThemeSwitch={props.showThemeSwitch}
        showAuthButtons={props.showAuthButtons}
        showNotifications={props.showNotifications}
        logo={props.logo}
        siteName={props.siteName}
        {...props.headerProps}
      />

      {props.showMainContainer !== false ? (
        <main className='container px-4 py-6 pt-20 md:px-4'>
          {props.children}
        </main>
      ) : (
        props.children
      )}
    </div>
  )
}
