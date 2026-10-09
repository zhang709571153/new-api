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
import type { TFunction } from 'i18next'

export function getTutorials(t: TFunction) {
  const prepare = {
    title: t('Install and sign up'),
    text: t(
      'Install the desktop app with Codex. Register on this website with a username, optional nickname and password, then sign in.'
    ),
  }
  const join = {
    title: t('Join your team'),
    text: t(
      'Open My team, choose Join team and enter the invitation code from your team owner. Ask the owner to set your allowance, then check your balance in My workspace.'
    ),
  }
  const restart = {
    title: t('Restart and try it'),
    text: t(
      'Wait for setup to finish, fully quit Codex and reopen it. Send a short message to check the connection. Set up again on a new computer or after changing teams or keys.'
    ),
  }
  return [
    {
      id: 'windows',
      title: 'Windows',
      documentUrl:
        'https://ocn7w7pvybt8.feishu.cn/wiki/QWU5wbS31i5RYWk5eZhc8XSVneg',
      image: '/tutorial-images/windows-setup.png',
      imageAlt: t('Windows setup command with the key hidden'),
      steps: [
        prepare,
        join,
        {
          title: t('Run the Windows command'),
          text: t(
            'In My workspace, find One-click setup, select Windows and copy the command. Press the Windows key, search for PowerShell and open it. Paste the command and press Enter.'
          ),
        },
        restart,
      ],
    },
    {
      id: 'mac',
      title: 'macOS',
      documentUrl:
        'https://ocn7w7pvybt8.feishu.cn/wiki/SOJIw0jL2iif1Pk6CZrc9PPznhh',
      image: '/tutorial-images/mac-setup.png',
      imageAlt: t('macOS setup command with the key hidden'),
      steps: [
        prepare,
        join,
        {
          title: t('Run the Mac command'),
          text: t(
            'In My workspace, select macOS / Linux under One-click setup and copy the command. Press Command + Space, open Terminal, paste with Command + V and press Enter. Quit Codex with Command + Q after setup.'
          ),
        },
        restart,
      ],
    },
    {
      id: 'team',
      title: t('Team administration'),
      documentUrl:
        'https://ocn7w7pvybt8.feishu.cn/wiki/ILOfwWqYcieLsnk34RLcLCT4nsf',
      steps: [
        {
          title: t('Invite colleagues'),
          text: t(
            'In My team, choose Invite members and copy the invitation code. Share it internally. It can be reused until replaced; replacing it does not remove existing members.'
          ),
        },
        {
          title: t('Set member allowances'),
          text: t(
            'Choose Set allowance beside a member, enter their remaining allowance and save. Entering 50 sets the remaining allowance to 50 in the displayed currency; it does not top up the shared pool.'
          ),
        },
        {
          title: t('Review usage and manage access'),
          text: t(
            'Check member spending and Team usage trends. Edit nicknames to identify colleagues, and use Pause or Resume to manage access. After resetting a member key, help that member configure their app again.'
          ),
        },
        {
          title: t('Before dissolving a team'),
          text: t(
            'The shared pool uses the owner’s wallet and plan. Leaving and dissolving the team removes all members and disables team keys and invitations. Personal balances, personal keys and usage history are kept.'
          ),
        },
      ],
    },
    {
      id: 'features',
      title: t('Other features'),
      documentUrl:
        'https://ocn7w7pvybt8.feishu.cn/wiki/EIdMwVYkhikGk3kWvBvcEsLCnjJ',
      steps: [
        {
          title: t('Balances and plans'),
          text: t(
            'My workspace shows available balance and spending. Plan cards show monthly and weekly limits, refresh times and expiry. Team members are limited by both their allowance and the shared pool. Ask the owner when more allowance is needed.'
          ),
        },
        {
          title: t('Usage and trends'),
          text: t(
            'My usage lists request times, models, tokens and costs. Usage trends compares models over a selected period. Tokens measure text usage; check the cost column for charges.'
          ),
        },
        {
          title: t('Account and keys'),
          text: t(
            'Account lets you edit your nickname and password. My key lets you view, copy or reset your connection key. Resetting invalidates the old key; copy and run the current setup command again.'
          ),
        },
        {
          title: t('Top-ups and everyday tasks'),
          text: t(
            'Open Plans or Top up to see available purchases. If unavailable or out of stock, contact the administrator. In Codex, describe your task and attach the relevant files; for image edits, attach the original image.'
          ),
        },
      ],
    },
  ]
}
