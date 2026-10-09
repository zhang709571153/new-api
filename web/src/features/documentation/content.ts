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

export type GuideApp = 'codex' | 'workbuddy'
export type GuidePlatform = 'windows' | 'mac'

export function getConnectionGuide(
  t: TFunction,
  app: GuideApp,
  platform: GuidePlatform
) {
  const account = {
    title: t('Choose personal or team'),
    text: t(
      'In My workspace, choose Personal or Team before copying a key or setup command. Team use needs an allowance from the owner.'
    ),
  }
  if (app === 'codex') {
    return {
      steps: [
        {
          title: t('Install Codex'),
          text: t(
            'Download the ChatGPT desktop app with Codex for your operating system. Open it once, then fully quit it, including the tray icon, before running setup.'
          ),
        },
        account,
        {
          title: t('Run the setup command'),
          text:
            platform === 'windows'
              ? t(
                  'In My workspace → One-click setup → Codex, choose Windows and copy the command. Open PowerShell from the Start menu, paste it and press Enter.'
                )
              : t(
                  'In My workspace → One-click setup → Codex, choose macOS / Linux and copy the command. Press Command + Space, open Terminal, paste it and press Enter. Use Command + Q to quit Codex before this step.'
                ),
        },
        {
          title: t('Reopen and try a message'),
          text: t(
            'Wait for setup to finish, reopen the desktop app and switch to Codex. Start a new chat and send “Hello” to check the connection.'
          ),
        },
      ],
      usage: t(
        'Open a local folder in Codex, describe what you want, and attach the relevant files. Review proposed edits before accepting them; continue in the same conversation for follow-up work.'
      ),
      example: t(
        'Read this folder, explain what it contains, and suggest the next step. Do not edit files yet.'
      ),
      downloadUrl: 'https://chatgpt.com/download/',
      helpUrl: 'https://learn.chatgpt.com/docs/app',
    }
  }
  return {
    steps: [
      {
        title: t('Install WorkBuddy'),
        text: t(
          'Download WorkBuddy for Windows or macOS from its official website and finish the first launch.'
        ),
      },
      account,
      {
        title:
          platform === 'windows'
            ? t('Run the setup command')
            : t('Add a custom model'),
        text:
          platform === 'windows'
            ? t(
                'For WorkBuddy 5.6.2 on Windows, copy the WorkBuddy command from My workspace and run it in PowerShell. Paste your key when asked; the input stays hidden. Other versions can use the manual setup below.'
              )
            : t(
                'In WorkBuddy, open Settings → Models → Add → Custom. Enter the connection details below and save. This is the official manual method; RealYu on macOS has not yet been tested on a Mac.'
              ),
      },
      {
        title: t('Choose a RealYu model'),
        text: t(
          'Start a new conversation, select a RealYu GPT model and send “Hello”. Keep your other models and account as they are.'
        ),
      },
    ],
    usage: t(
      'Choose a work folder, select your RealYu model, and describe the result you need. Attach relevant files, review requested permissions, then check the generated files and ask for changes in the same conversation.'
    ),
    example: t(
      'Summarize the files in this folder into a short report. Ask me before changing any original file.'
    ),
    downloadUrl: 'https://www.codebuddy.cn/work/',
    helpUrl:
      'https://www.codebuddy.ai/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Model',
  }
}
