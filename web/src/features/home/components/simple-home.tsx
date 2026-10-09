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
import { ChartNoAxesColumn, Link2, Users } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { useStatus } from '@/hooks/use-status'

import { HomeAtmosphere } from './home-atmosphere'
import { HomeModels } from './home-models'

import './home-atmosphere.css'

export function SimpleHome(props: { isAuthenticated: boolean }) {
  const { t } = useTranslation()
  const { status } = useStatus()
  const canRegister =
    status?.register_enabled !== false && !status?.self_use_mode_enabled
  return (
    <main className='realyu-home'>
      <HomeAtmosphere />
      <section className='realyu-home-hero mx-auto flex max-w-5xl flex-col items-center justify-center px-6 text-center'>
        <div className='realyu-home-intro w-full'>
          <p className='realyu-hero-kicker mb-6 inline-flex items-center gap-2 rounded-full px-4 py-2 text-xs font-medium'>
            <span aria-hidden='true' />
            {t('Ideas. Code. Images.')}
          </p>
          <h1 className='realyu-home-title font-bold text-balance whitespace-pre-line'>
            {t('AI, within reach.')}
          </h1>
          <p className='realyu-home-muted mx-auto mt-7 max-w-2xl text-lg leading-relaxed sm:text-xl'>
            {t('Connect with ease. Keep every use in view.')}
          </p>
          <div className='mt-10 flex flex-wrap justify-center gap-5'>
            {props.isAuthenticated ? (
              <Button
                size='lg'
                className='realyu-home-primary h-14 px-8 text-lg font-semibold'
                role='link'
                render={
                  <Link
                    to='/dashboard/$section'
                    params={{ section: 'overview' }}
                  />
                }
              >
                {t('My workspace')}
              </Button>
            ) : (
              <>
                {canRegister && (
                  <Button
                    size='lg'
                    className='realyu-home-primary h-14 px-8 text-lg font-semibold'
                    role='link'
                    render={<Link to='/sign-up' />}
                  >
                    {t('Get started')}
                  </Button>
                )}
                <Button
                  size='lg'
                  variant='ghost'
                  className='realyu-home-secondary h-14 rounded-2xl px-8 text-lg font-semibold'
                  role='link'
                  render={<Link to='/sign-in' />}
                >
                  {t('Sign in')}
                </Button>
              </>
            )}
          </div>
          <Button
            size='sm'
            variant='ghost'
            role='link'
            className='mt-4'
            render={<Link to='/wallet' />}
          >
            {t('Top up')}
          </Button>
        </div>
      </section>
      <section className='mx-auto grid max-w-6xl gap-4 px-6 pb-16 sm:px-8 sm:pb-20 md:grid-cols-3 lg:gap-6'>
        {[
          {
            icon: Link2,
            title: t('Easy connection'),
            text: t('A few simple steps, and you are ready to go.'),
          },
          {
            icon: ChartNoAxesColumn,
            title: t('Clear usage'),
            text: t('A clear view of usage, whenever you need it.'),
          },
          {
            icon: Users,
            title: t('Simple collaboration'),
            text: t('Share access. Keep everything organized.'),
          },
        ].map((item) => (
          <div
            key={item.title}
            className='realyu-home-benefit flex items-center gap-4 p-6'
          >
            <div className='realyu-home-icon flex size-12 shrink-0 items-center justify-center rounded-2xl'>
              <item.icon className='size-6' aria-hidden='true' />
            </div>
            <div>
              <h2 className='text-lg font-semibold'>{item.title}</h2>
              <p className='realyu-home-muted mt-2 text-sm leading-relaxed'>
                {item.text}
              </p>
            </div>
          </div>
        ))}
      </section>
      <HomeModels />
    </main>
  )
}
