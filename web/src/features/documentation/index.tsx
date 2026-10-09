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
import { ExternalLink } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { CopyButton } from '@/components/copy-button'
import { PublicLayout } from '@/components/layout'
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { isDevelopmentSite } from '@/lib/development-site'

import {
  getConnectionGuide,
  type GuideApp,
  type GuidePlatform,
} from './content'

export function Documentation() {
  const { t } = useTranslation()
  const [app, setApp] = useState<GuideApp>('codex')
  const [platform, setPlatform] = useState<GuidePlatform>('windows')
  const guide = getConnectionGuide(t, app, platform)
  const setupEnabled = !isDevelopmentSite()
  const steps = guide.steps
  return (
    <PublicLayout showNotifications={false} showSidebarWhenAuthenticated>
      <div className='mx-auto max-w-3xl space-y-6 py-8'>
        <header className='space-y-3'>
          <p className='text-primary text-sm font-medium'>
            {t('Documentation')}
          </p>
          <h1 className='text-3xl font-semibold tracking-tight'>
            {t('Connect in a few simple steps')}
          </h1>
          <p className='text-muted-foreground'>
            {t(
              'Choose your app and computer. Follow the steps, then try your first conversation.'
            )}
          </p>
        </header>
        <div className='flex flex-wrap items-center gap-3'>
          <Tabs
            value={app}
            onValueChange={(value) => setApp(value as GuideApp)}
          >
            <TabsList aria-label={t('Application')}>
              <TabsTrigger value='codex'>Codex</TabsTrigger>
              <TabsTrigger value='workbuddy'>WorkBuddy</TabsTrigger>
            </TabsList>
          </Tabs>
          <Tabs
            value={platform}
            onValueChange={(value) => setPlatform(value as GuidePlatform)}
          >
            <TabsList aria-label={t('Operating system')}>
              <TabsTrigger value='windows'>Windows</TabsTrigger>
              <TabsTrigger value='mac'>macOS</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>
              {app === 'codex' ? 'Codex' : 'WorkBuddy'} ·{' '}
              {platform === 'windows' ? 'Windows' : 'macOS'}
            </CardTitle>
          </CardHeader>
          <CardContent className='space-y-6'>
            {!setupEnabled && (
              <p role='status' className='text-muted-foreground text-sm'>
                {t(
                  'These instructions are for the live site. Get keys and setup commands only from the live workspace, not this test site.'
                )}
              </p>
            )}
            <ol className='space-y-5'>
              {steps.map((step, index) => (
                <li key={step.title} className='flex gap-3'>
                  <span
                    aria-hidden='true'
                    className='bg-primary/10 text-primary flex size-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold'
                  >
                    {index + 1}
                  </span>
                  <div className='space-y-1'>
                    <h2 className='font-semibold'>{step.title}</h2>
                    <p className='text-muted-foreground text-sm leading-7'>
                      {step.text}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
            <div className='flex flex-wrap gap-2'>
              <Button
                role='link'
                variant='outline'
                render={
                  <a
                    href={guide.downloadUrl}
                    target='_blank'
                    rel='noopener noreferrer'
                  />
                }
              >
                {t('Official download')}
                <ExternalLink aria-hidden='true' />
              </Button>
              {setupEnabled ? (
                <Button
                  role='link'
                  render={
                    <Link
                      to='/dashboard/$section'
                      params={{ section: 'overview' }}
                    />
                  }
                >
                  {t('Open My workspace')}
                </Button>
              ) : (
                <Button
                  role='link'
                  render={
                    <a
                      href='https://api.realyu.fun/dashboard/overview'
                      target='_blank'
                      rel='noopener noreferrer'
                    />
                  }
                >
                  {t('Open live site')}
                  <ExternalLink aria-hidden='true' />
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
        {app === 'workbuddy' && <WorkBuddyConnection />}
        <Card>
          <CardHeader>
            <CardTitle>{t('Start using it')}</CardTitle>
          </CardHeader>
          <CardContent className='space-y-4'>
            <p className='text-muted-foreground text-sm leading-7'>
              {guide.usage}
            </p>
            <div className='bg-muted flex items-start gap-3 rounded-lg p-4'>
              <p className='flex-1 text-sm leading-6 select-text'>
                {guide.example}
              </p>
              <CopyButton
                value={guide.example}
                aria-label={t('Copy example')}
              />
            </div>
            <a
              href={guide.helpUrl}
              target='_blank'
              rel='noopener noreferrer'
              className='text-primary inline-flex items-center gap-1 text-sm underline underline-offset-4'
            >
              {t('Official usage guide')}
              <ExternalLink className='size-3' aria-hidden='true' />
            </a>
          </CardContent>
        </Card>

        <section className='space-y-2'>
          <h2 className='font-semibold'>{t('Need help?')}</h2>
          <Accordion>
            <AccordionItem value='copy'>
              <AccordionTrigger>
                {t('Cannot copy the command?')}
              </AccordionTrigger>
              <AccordionContent>
                {t(
                  'Select the full visible command and copy it manually. Never copy sk-**** or a screenshot of a key.'
                )}
              </AccordionContent>
            </AccordionItem>
            {app === 'codex' && (
              <AccordionItem value='setup'>
                <AccordionTrigger>
                  {t('Setup failed or the app still asks you to sign in?')}
                </AccordionTrigger>
                <AccordionContent>
                  {t(
                    'Fully quit Codex, copy a fresh command from the correct workspace, and run it again. If it fails, send the error code to your administrator with keys hidden.'
                  )}
                </AccordionContent>
              </AccordionItem>
            )}
            <AccordionItem value='balance'>
              <AccordionTrigger>
                {t('Connected, but cannot send a message?')}
              </AccordionTrigger>
              <AccordionContent>
                {t(
                  'Check the selected personal or team balance and Service status. Setup checks the key, not the spending balance; a team owner may still need to assign an allowance.'
                )}
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </section>
      </div>
    </PublicLayout>
  )
}

function WorkBuddyConnection() {
  const { t } = useTranslation()
  const url = 'https://api.realyu.fun/v1/chat/completions'
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('Manual connection details')}</CardTitle>
      </CardHeader>
      <CardContent className='space-y-3 text-sm'>
        <p className='text-muted-foreground leading-6'>
          {t(
            'In Settings → Models → Add → Custom, use these values. The URL is the complete request address, not just /v1.'
          )}
        </p>
        <dl className='divide-y rounded-lg border px-3'>
          <div className='grid gap-1 py-3 sm:grid-cols-[100px_1fr]'>
            <dt className='text-muted-foreground'>URL</dt>
            <dd className='flex min-w-0 items-center gap-2'>
              <code className='min-w-0 flex-1 break-all select-text'>
                {url}
              </code>
              <CopyButton value={url} aria-label={t('Copy URL')} />
            </dd>
          </div>
          <div className='grid gap-1 py-3 sm:grid-cols-[100px_1fr]'>
            <dt className='text-muted-foreground'>API Key</dt>
            <dd>
              <a
                className='underline underline-offset-4'
                href='https://api.realyu.fun/dashboard/overview'
              >
                {t('Open My workspace')}
              </a>
            </dd>
          </div>
          <div className='grid gap-1 py-3 sm:grid-cols-[100px_1fr]'>
            <dt className='text-muted-foreground'>{t('Model')}</dt>
            <dd>
              <code>gpt-6-astra</code>
            </dd>
          </div>
        </dl>
        <p className='text-muted-foreground leading-6'>
          {t(
            'Enable tool calls. Leave image input and reasoning options off for this setup, then save and select the model.'
          )}
        </p>
      </CardContent>
    </Card>
  )
}
