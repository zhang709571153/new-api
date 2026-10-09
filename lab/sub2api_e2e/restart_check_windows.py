"""Restart only the owned isolated Sub2API process and verify persistent login.

No production paths, service-manager operations or administrator commitments.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time

from bootstrap_windows import PRIVATE, get_json


def main():
    credentials = Path(json.loads((PRIVATE / 'current.json').read_text())['credentials_file']).resolve()
    if not credentials.is_relative_to(PRIVATE.resolve()):
        raise RuntimeError('Receipt is outside this isolated environment')
    state = json.loads(credentials.read_text())
    run = Path(state['run_dir']).resolve()
    current = next(item for item in reversed(state['processes']) if item['name'] == 'sub2api')
    executable = Path(current['executable']).resolve()
    if not executable.is_relative_to((PRIVATE / 'bin').resolve()):
        raise RuntimeError('Owned executable is outside isolated binary directory')
    base = f'http://127.0.0.1:{state["sub2api_port"]}'
    login_body = {'email': state['admin_email'], 'password': state['admin_password']}
    before = get_json(base + '/api/v1/auth/login', login_body)['data']
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000 | 0x0001, False, int(current['pid']))
    if not handle:
        raise RuntimeError('Owned process is not running')
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            raise RuntimeError('Cannot verify PID identity')
        if Path(buf.value).resolve() != executable:
            raise RuntimeError('PID identity changed; refusing termination')
        if not kernel.TerminateProcess(handle, 0):
            raise RuntimeError('Owned process termination failed')
    finally:
        kernel.CloseHandle(handle)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('DATABASE_', 'REDIS_', 'ADMIN_', 'JWT_', 'SERVER_', 'AUTO_SETUP', 'DATA_DIR', 'TOKEN_REFRESH_', 'RUN_MODE'))}
    env.update(DATA_DIR=str(run / 'sub2api-data'), SERVER_HOST='127.0.0.1', SERVER_PORT=str(state['sub2api_port']),
               TOKEN_REFRESH_ENABLED='false', RUN_MODE='standard', TZ='UTC')
    with (run / 'sub2api.log').open('ab') as log:
        process = subprocess.Popen([str(executable)], cwd=run, env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    state['processes'].append({'name': 'sub2api', 'pid': process.pid, 'executable': str(executable)})
    credentials.write_text(json.dumps(state, indent=2), encoding='utf-8')
    for _ in range(90):
        if process.poll() is not None:
            raise RuntimeError('Restarted Sub2API exited; inspect private log')
        try:
            after = get_json(base + '/api/v1/auth/login', login_body)['data']
            status = get_json(base + '/api/v1/admin/compliance', token=after['access_token'])['data']
            if after['user']['id'] != before['user']['id']:
                raise RuntimeError('Admin identity changed after restart')
            state['admin_access_token'] = after['access_token']
            credentials.write_text(json.dumps(state, indent=2), encoding='utf-8')
            result = {'status': 'PASS', 'scope': 'isolated-process-restart-login-persistence', 'admin_user_id': after['user']['id'],
                      'compliance_required': status['required'], 'production_changed': False, 'model_e2e_performed': False}
            destination = run / ('restart-' + str(int(time.time())) + '.json')
            destination.write_text(json.dumps(result, indent=2), encoding='utf-8')
            print(json.dumps(result, indent=2))
            return
        except (OSError, KeyError):
            time.sleep(1)
    raise RuntimeError('Restart login timed out')


if __name__ == '__main__':
    main()
