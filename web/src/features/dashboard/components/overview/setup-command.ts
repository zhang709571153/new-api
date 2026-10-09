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
export type SetupPlatform = 'windows' | 'unix'
export type SetupClient = 'codex' | 'workbuddy'

const commandTemplates: Record<
  SetupClient,
  Record<SetupPlatform, (key: string) => string>
> = {
  codex: {
    windows: (key) =>
      `& ([scriptblock]::Create((irm 'https://api.realyu.fun/downloads/realyu/setup.ps1?v=2' -ea Stop).TrimStart([char]65279))) '${key}'`,
    unix: (key) =>
      `bash -o pipefail -c 'curl -fsS https://api.realyu.fun/downloads/realyu/setup.sh?v=2 | sh -s -- "$1"' -- '${key}'`,
  },
  workbuddy: {
    windows: (key) =>
      `& ([scriptblock]::Create((irm 'https://api.realyu.fun/downloads/realyu/setup-workbuddy.ps1?v=3' -ea Stop).TrimStart([char]65279))) '${key}'`,
    unix: (key) =>
      `bash -o pipefail -c 'curl -fsS https://api.realyu.fun/downloads/realyu/setup-workbuddy.sh?v=1 | sh -s -- "$1"' -- '${key}'`,
  },
}

export const setupCommandPreviews: Record<SetupPlatform, string> = {
  windows: commandTemplates.codex.windows('sk-****'),
  unix: commandTemplates.codex.unix('sk-****'),
}

export function setupCommandPreview(
  platform: SetupPlatform,
  client: SetupClient
): string {
  return commandTemplates[client][platform]('sk-****')
}

export function buildSetupCommand(
  platform: SetupPlatform,
  key: string,
  client: SetupClient = 'codex'
): string {
  if (!/^sk-[A-Za-z0-9_-]{16,256}$/.test(key)) {
    throw new Error('Invalid API key format')
  }
  return commandTemplates[client][platform](key)
}
