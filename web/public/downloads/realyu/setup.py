"""Connect an existing Codex installation to Realyu, without third-party packages."""
import copy
import errno
from contextlib import closing
from datetime import datetime, timezone
import getpass
import hashlib
import http.client
import json
import os
import platform
from pathlib import Path
import queue
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import traceback
import urllib.error
import urllib.request
import uuid

BASE = 'https://api.realyu.fun/v1'
ROOT_VALUES = {
    'model_provider': 'realyu', 'model': 'gpt-6-luna',
    'openai_base_url': BASE,
    'model_reasoning_effort': 'high', 'service_tier': 'default',
    'cli_auth_credentials_store': 'file',
}
OWNED_ROOT = {*ROOT_VALUES, 'profile', 'model_catalog_json'}
OWNED_TABLES = ('model_providers.realyu', 'mcp_servers.realyu_images', 'mcp_servers.image_generation')
CLIENT_VERSION = '1.4.20'
SESSION_REASONS = {'SESSIONS_BUSY': 'busy', 'SESSIONS_RPC_FAILED': 'rpc_failed',
                   'SESSIONS_TIMEOUT': 'timeout', 'SESSIONS_CLIENT_UNSUPPORTED': 'unsupported'}
KEY_CHECK_URL = 'https://api.realyu.fun/api/client/key-check'
DIAGNOSTICS_URL = 'https://api.realyu.fun/api/client/diagnostics'


class SetupError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Diagnostics:
    """Allowlisted facts only: never store subprocess output, config or credentials."""
    def __init__(self):
        self.started = time.monotonic()
        report_id = os.environ.get('REALYU_DIAGNOSTIC_ID', '')
        if not re.fullmatch(r'[a-f0-9]{32}', report_id):
            report_id = uuid.uuid4().hex
        self.data = {'schema_version': 1, 'client_version': CLIENT_VERSION,
            'id': report_id, 'time': datetime.now(timezone.utc).isoformat(),
            'platform': sys.platform, 'architecture': platform.machine(),
            'stage': 'discovery', 'status': 'running', 'candidates': [], 'discovery': []}
        self.runtimes = []
        if sys.platform == 'win32':
            try:
                import ctypes
                self.data['elevation'] = 'elevated' if ctypes.windll.shell32.IsUserAnAdmin() else 'standard'
            except Exception:
                self.data['elevation'] = 'unknown'

    def path(self, path):
        value = str(path)
        for name in ('LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'HOME'):
            root = os.environ.get(name)
            if root:
                value = value.replace(root, '%' + name + '%')
        return re.sub(r'sk-[A-Za-z0-9_-]+', '[redacted]', value)

    def save(self, home):
        for root in (home, Path(tempfile.gettempdir())):
            folder = root / 'realyu-diagnostics'
            try:
                if folder.is_symlink() or root.is_symlink():
                    continue
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                target = folder / (self.data['id'] + '.json')
                atomic_write(target, (json.dumps(self.data, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
                return target
            except OSError:
                continue
        return None

    def upload(self):
        try:
            return self._upload()
        except Exception:
            return 'failed'

    def _upload(self):
        if os.environ.get('REALYU_DIAGNOSTICS_UPLOAD') == '0':
            return 'disabled'
        fields = ('id', 'client_version', 'platform', 'architecture', 'stage', 'code',
            'http_status', 'request_id', 'exception', 'errno', 'winerror', 'rollback', 'elevation',
            'session_issue', 'session_failed', 'session_actual_provider', 'session_nested_provider',
            'session_persisted_provider', 'session_thread_match')
        payload = {name: self.data[name] for name in fields if self.data.get(name) is not None}
        # Keep the wire contract compatible with the already deployed receiver.
        # More precise categories remain in the local report and terminal.
        payload['code'] = {'SESSIONS_MIGRATION_FAILED': 'SESSIONS_VERIFY_FAILED',
                           'CONFIG_IMAGE_TOOL_CONFLICT': 'CONFIG_MERGE_UNSUPPORTED'}.get(
                               payload.get('code'), payload.get('code'))
        issue = payload.get('session_issue')
        if issue in ('unsupported', 'backup_failed', 'settings_invalid', 'session_error'):
            payload['session_issue'] = 'rpc_failed'
        elif issue == 'settings_mismatch':
            payload['session_issue'] = 'persistence_mismatch'
        selected = self.data.get('selected', {})
        payload.update(codex_version=selected.get('version', ''), source=selected.get('source', ''))
        known_exceptions = {'SetupError', 'TimeoutExpired', 'PermissionError', 'OSError',
            'FileNotFoundError', 'FileExistsError', 'NotADirectoryError', 'IsADirectoryError',
            'TOMLDecodeError', 'ValueError', 'UnicodeError', 'UnicodeDecodeError', 'JSONDecodeError',
            'RuntimeError', 'TypeError', 'KeyError', 'AttributeError'}
        if payload.get('exception') not in known_exceptions:
            payload['exception'] = 'Other'
        request = urllib.request.Request(DIAGNOSTICS_URL,
            data=json.dumps(payload, ensure_ascii=True).encode('utf-8'), method='POST',
            headers={'Content-Type': 'application/json', 'User-Agent': 'Realyu-Setup/1.0'})
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=5) as response:
                receipt = json.loads(response.read(512))
                if response.status == 202 and receipt.get('success') is True and receipt.get('id') == self.data['id']:
                    return 'uploaded'
        except Exception:
            pass
        return 'failed'




def unrelated(config):
    result = copy.deepcopy(config)
    for key in OWNED_ROOT:
        result.pop(key, None)
    for table in OWNED_TABLES:
        parent, child = table.split('.')
        if parent in result:
            result[parent].pop(child, None)
            if not result[parent]:
                result.pop(parent)
    return result


def merge_config(source, home, python, standalone=False, use_catalog=True, helper_path=None, source_home=None):
    """Keep comments/unrelated settings; reject unusual TOML rather than damaging it."""
    before = tomllib.loads(source)
    if before.get('forced_login_method') == 'chatgpt':
        raise SetupError('CONFIG_LOGIN_POLICY', '当前配置要求使用 ChatGPT 登录，禁止 API Key 登录。请联系配置此限制的管理员。')
    # The neutral name may already belong to another image integration. Only
    # replace an entry that points to this installer's own helper and home.
    existing_images = before.get('mcp_servers', {}).get('image_generation')
    if existing_images is not None:
        owned_root = source_home or home
        command = Path(existing_images.get('command', ''))
        args = existing_images.get('args', [])
        python_helper = args == ['-I', '-B', '-X', 'utf8', str(owned_root / 'realyu_images.py')]
        frozen_helper = (args == ['images'] and command.name == 'realyu-client.exe'
            and (command.parent == owned_root or command.parent.parent == owned_root / 'realyu-runtime' / 'clients'))
        owned_home = existing_images.get('env', {}).get('REALYU_CODEX_HOME') == str(owned_root)
        if existing_images.get('url') or not owned_home or not (python_helper or frozen_helper):
            raise SetupError('CONFIG_IMAGE_TOOL_CONFLICT', '已有其他图片集成使用 image_generation 名称，未修改配置。请将诊断编号提供给支持人员。')
    kept, section = [], ''
    for line in source.splitlines(keepends=True):
        header = re.match(r'^\s*\[([^\[\]]+)\]\s*(?:#.*)?$', line)
        if header:
            section = header.group(1).strip()
        elif line.lstrip().startswith('[['):
            section = '__array__'
        if any(section == name or section.startswith(name + '.') for name in OWNED_TABLES):
            continue
        if not section and re.match(r'^\s*(?:' + '|'.join(OWNED_ROOT) + r')\s*=', line):
            continue
        kept.append(line)
    values = dict(ROOT_VALUES)
    values['openai_base_url'] = BASE
    if use_catalog:
        values['model_catalog_json'] = str(home / 'realyu-models.json')
    prefix = ''.join(f'{key} = {json.dumps(value)}\n' for key, value in values.items())
    generated = prefix + '\n' + ''.join(kept).strip() + '\n\n'
    image_command = (helper_path or home / 'realyu-client.exe') if standalone else python
    image_args = ['images'] if standalone else ['-I', '-B', '-X', 'utf8', str(home / 'realyu_images.py')]
    # Persist the private runtime's CA store for image requests after the shell exits.
    # Never turn off certificate verification to accommodate a portable runtime.
    ca_bundle = os.environ.get('REALYU_CA_BUNDLE') if not standalone else None
    certificate_env = ''
    if ca_bundle:
        if not Path(ca_bundle).is_absolute() or not Path(ca_bundle).is_file():
            raise SetupError('TLS_BUNDLE_MISSING', '安全连接组件不可用，请重新运行配置命令。')
        certificate_env = 'SSL_CERT_FILE = ' + json.dumps(ca_bundle) + '\n'
    image_runtime = os.environ.get('REALYU_IMAGE_RUNTIME') if not standalone else None
    if image_runtime:
        runtime = Path(image_runtime)
        if not runtime.is_absolute() or not (runtime / 'realyu_image_official.py').is_file():
            raise SetupError('IMAGE_RUNTIME_MISSING', '图片组件不可用，请重新运行配置命令。原配置未变。')
        certificate_env += 'REALYU_IMAGE_RUNTIME = ' + json.dumps(str(runtime)) + '\n'
    generated += f'''[model_providers.realyu]
name = "RealYu API"
base_url = "{BASE}"
wire_api = "responses"
requires_openai_auth = true

[mcp_servers.image_generation]
command = {json.dumps(str(image_command))}
args = {json.dumps(image_args)}
tool_timeout_sec = 360
startup_timeout_sec = 60
enabled_tools = ["generate_image", "edit_image", "diagnose_image"]

[mcp_servers.image_generation.env]
REALYU_CODEX_HOME = {json.dumps(str(home))}
{certificate_env}

[mcp_servers.image_generation.tools.diagnose_image]
approval_mode = "prompt"

[mcp_servers.image_generation.tools.generate_image]
approval_mode = "prompt"

[mcp_servers.image_generation.tools.edit_image]
approval_mode = "prompt"
'''
    after = tomllib.loads(generated)
    if unrelated(before) != unrelated(after):
        raise SetupError('CONFIG_MERGE_UNSUPPORTED', '原配置包含无法安全自动合并的格式，未修改配置。请将诊断编号提供给支持人员。')
    return generated


def codex_candidates(diagnostics):
    candidates = []
    # An old shim at the front of PATH must not hide a working installation.
    for directory in os.environ.get('PATH', '').split(os.pathsep):
        if directory and Path(directory).is_absolute():
            installed = shutil.which('codex', path=directory)
            if installed:
                candidates.append(('path', Path(installed)))
    override = os.environ.get('REALYU_CODEX_EXE')
    if override:
        if not Path(override).is_absolute() or not Path(override).is_file():
            raise SetupError('CODEX_OVERRIDE_INVALID', 'REALYU_CODEX_EXE 必须指向已安装 Codex CLI 的完整文件路径。')
        candidates.insert(0, ('explicit', Path(override)))
    patterns = []
    if sys.platform == 'win32':
        local = os.environ.get('LOCALAPPDATA')
        if local:
            patterns.extend((Path(local), pattern, 'desktop') for pattern in (
                'OpenAI/Codex/bin/*/codex.exe', 'OpenAI/Codex/bin/codex.exe',
                'Programs/OpenAI/Codex/bin/codex.exe',
                'Programs/OpenAI/Codex/resources/codex.exe',
                'Programs/Codex/resources/codex.exe'))
        for variable in ('ProgramFiles', 'ProgramFiles(x86)'):
            if os.environ.get(variable):
                patterns.extend((Path(os.environ[variable]), pattern, 'desktop') for pattern in (
                    'OpenAI/Codex/bin/codex.exe', 'OpenAI/Codex/resources/codex.exe',
                    'Codex/resources/codex.exe'))
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        script = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); $ErrorActionPreference='Stop'; @(Get-AppxPackage -Name '*Codex*' | Select-Object -ExpandProperty InstallLocation) | ConvertTo-Json -Compress"
        try:
            result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', script],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20,
                stdin=subprocess.DEVNULL)
            diagnostics.data['discovery'].append({'source': 'store', 'returncode': result.returncode})
            if result.returncode == 0:
                locations = json.loads(result.stdout.lstrip('\ufeff') or '[]')
                for location in ([locations] if isinstance(locations, str) else locations or []):
                    if isinstance(location, str) and Path(location).is_absolute():
                        for relative in ('app/resources/codex.exe', 'resources/codex.exe', 'bin/codex.exe'):
                            candidates.append(('store', Path(location) / relative))
        except (OSError, subprocess.SubprocessError, ValueError):
            diagnostics.data['discovery'].append({'source': 'store', 'status': 'unavailable'})
        appdata = os.environ.get('APPDATA')
        if appdata:
            patterns.extend((Path(appdata) / 'npm/node_modules/@openai', pattern, 'npm') for pattern in (
                'codex/vendor/*/codex/codex.exe',
                'codex/node_modules/@openai/codex-*/vendor/*/codex/codex.exe',
                'codex-*/vendor/*/codex/codex.exe'))
    elif sys.platform == 'darwin':
        for root in (Path('/Applications'), Path.home() / 'Applications'):
            for app in ('Codex.app', 'ChatGPT.app'):
                for relative in ('Contents/Resources/codex',
                                 'Contents/Resources/codex-cli/bin/codex',
                                 'Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'):
                    candidates.append(('desktop', root / app / relative))
    else:
        candidates.extend(('path', path) for path in (
            Path.home() / '.local/bin/codex', Path('/usr/local/bin/codex'), Path('/usr/bin/codex')))
    for root, pattern, source in patterns:
        try:
            candidates.extend((source, path) for path in root.glob(pattern))
        except OSError:
            diagnostics.data['discovery'].append({'source': source, 'status': 'unreadable'})
    found = {}
    priority = {'explicit': 4, 'store': 3, 'desktop': 2, 'npm': 1, 'path': 0}
    for source, path in candidates:
        try:
            if not path.is_file():
                continue
            identity = os.path.normcase(str(path.absolute()))
            if identity not in found or priority[source] > priority[found[identity][0]]:
                found[identity] = (source, path)
        except OSError:
            diagnostics.data['discovery'].append({'source': source, 'status': 'unreadable'})
    return sorted(found.values(), key=lambda item: priority[item[0]], reverse=True)


def run_codex(path, arguments, **kwargs):
    return subprocess.run([str(path), *arguments], capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=20, **kwargs)


def codex_failure_reason(output):
    """Classify known errors without retaining potentially sensitive raw output."""
    text = output.lower()
    for reason, markers in (
            ('model_catalog_schema', ('model_catalog_json', 'missing field')),
            ('login_policy', ('forced_login_method', 'login method')),
            ('access_denied', ('permission denied', 'access is denied', '拒绝访问')),
            ('unsupported_command', ('unrecognized subcommand', 'unexpected argument')),
            ('config_parse', ('toml parse', 'error loading config', 'invalid value'))):
        if any(marker in text for marker in markers):
            return reason
    return 'unclassified'


def prepare_store_cli(source):
    """Store files can be readable but non-executable outside their package.

    Mirror the installed, registered package's CLI to a user-owned cache, just
    as the desktop does. Verify content before execution; never change Store ACLs.
    """
    with source.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    root = Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()) / 'Realyu'
    folder = root / 'codex-runtime' / digest
    for parent in (root, root / 'codex-runtime', folder):
        if parent.is_symlink():
            raise SetupError('CODEX_RUNTIME_CACHE_UNSAFE', 'Codex 组件缓存目录是符号链接，请检查目录后重试。')
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / 'codex.exe'
    if destination.is_symlink():
        raise SetupError('CODEX_RUNTIME_CACHE_UNSAFE', 'Codex 组件缓存文件是符号链接，请检查目录后重试。')
    if destination.exists():
        with destination.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() == digest:
                return destination
    descriptor, temporary = tempfile.mkstemp(prefix='.codex-', dir=folder)
    os.close(descriptor)
    try:
        shutil.copyfile(source, temporary)
        with open(temporary, 'rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise SetupError('CODEX_RUNTIME_COPY_INVALID', '已安装 Codex 的组件复制校验失败，请检查磁盘后重试。')
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


def find_codex(diagnostics=None):
    diagnostics = diagnostics or Diagnostics()
    candidates = codex_candidates(diagnostics)
    for source, path in candidates:
        record = {'source': source, 'path': diagnostics.path(path)}
        diagnostics.data['candidates'].append(record)
        try:
            try:
                check = run_codex(path, ['--version'], stdin=subprocess.DEVNULL)
            except PermissionError:
                if source != 'store':
                    raise
                print('正在准备已安装的 Microsoft Store Codex 组件…', flush=True)
                path = prepare_store_cli(path)
                record['runtime_path'] = diagnostics.path(path)
                check = run_codex(path, ['--version'], stdin=subprocess.DEVNULL)
            match = re.search(r'codex(?:-cli)?\s+v?(\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.]+)?)', check.stdout + check.stderr, re.I)
            record['returncode'] = check.returncode
            if check.returncode or not match:
                record['status'] = 'unrecognized' if not check.returncode else 'launch_failed'
                continue
            record['version'] = match.group(1)
            help_result = run_codex(path, ['login', '--help'], stdin=subprocess.DEVNULL)
            if help_result.returncode or '--with-api-key' not in help_result.stdout:
                record['status'] = 'api_key_login_unsupported'
                continue
            record['status'] = 'ready'
            diagnostics.runtimes.append((source, str(path), record['version']))
        except subprocess.TimeoutExpired:
            record['status'] = 'timeout'
        except OSError as error:
            record.update(status='launch_failed', errno=error.errno, winerror=getattr(error, 'winerror', None))
    if diagnostics.runtimes:
        # Prefer the app the user installed over an unrelated old CLI on PATH.
        priority = {'explicit': 4, 'store': 3, 'desktop': 2, 'npm': 1, 'path': 0}
        selected = max(diagnostics.runtimes, key=lambda item: (priority[item[0]],
            tuple(int(part) for part in re.match(r'(\d+)\.(\d+)\.(\d+)', item[2]).groups())))
        diagnostics.data['selected'] = {'source': selected[0], 'path': diagnostics.path(selected[1]), 'version': selected[2]}
        return selected[1]
    if candidates:
        if any(item.get('status') == 'api_key_login_unsupported' for item in diagnostics.data['candidates']):
            raise SetupError('CODEX_LOGIN_UNSUPPORTED', '已找到 Codex，但此客户端不支持 API Key 登录。请更新对应的 Codex 应用；诊断文件记录了检测到的版本。')
        if all(item.get('status') == 'timeout' for item in diagnostics.data['candidates']):
            raise SetupError('CODEX_PROBE_TIMEOUT', '已找到 Codex，但启动检查超时。请先打开应用完成初始化，完全退出后重试。')
        raise SetupError('CODEX_UNRUNNABLE', '已找到 Codex 文件，但无法正常启动其命令行组件。请先打开 Codex 完成初始化，并检查系统拦截或安装损坏；无需重复下载本工具。')
    if any(item.get('status') or item.get('returncode', 0) for item in diagnostics.data['discovery']):
        raise SetupError('CODEX_DISCOVERY_INCOMPLETE', '无法完整读取 Codex 安装信息。请在安装 Codex 的同一 Windows 用户下运行，或用 REALYU_CODEX_EXE 指定其命令行组件路径。')
    raise SetupError('CODEX_NOT_FOUND', '未在当前用户的应用安装目录或 PATH 中找到 Codex。已安装在其他位置时，可用 REALYU_CODEX_EXE 指定命令行组件路径；否则请先安装 Codex。')


def compatible_catalog(codex, source, catalog, python, standalone, diagnostics):
    """Probe real parsers in disposable homes; never gate on a version number."""
    runtimes = {codex}
    # A co-installed desktop can read the same CODEX_HOME. Do not give it a
    # catalog only the newest CLI understands. Old PATH-only CLIs are irrelevant.
    runtimes.update(path for kind, path, _ in diagnostics.runtimes if kind in ('store', 'desktop'))
    use_catalog = True
    diagnostics.data['probes'] = []
    source_home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix='realyu-compat-') as directory:
        home = Path(directory)
        (home / 'realyu-models.json').write_bytes(catalog)
        env = os.environ.copy()
        env['CODEX_HOME'] = str(home)
        for name in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'REALYU_API_KEY'):
            env.pop(name, None)
        for runtime in sorted(runtimes):
            for custom in (True, False):
                config = merge_config(source, home, python, standalone=standalone, use_catalog=custom,
                                      source_home=source_home)
                # Offline inspection must not authenticate or call a model endpoint.
                config = config.replace(BASE, 'http://127.0.0.1:1/v1')
                (home / 'config.toml').write_text(config, encoding='utf-8')
                record = {'path': diagnostics.path(runtime), 'custom_catalog': custom}
                diagnostics.data['probes'].append(record)
                try:
                    result = run_codex(runtime, ['debug', 'models'], env=env, stdin=subprocess.DEVNULL)
                    record['returncode'] = result.returncode
                    if result.returncode == 0:
                        record['status'] = 'compatible'
                        if not custom:
                            use_catalog = False
                        break
                    record['reason'] = codex_failure_reason(result.stderr)
                    # Older clients may not expose debug models at all. Their
                    # native catalog plus mcp list still validates the config.
                    if not custom:
                        result = run_codex(runtime, ['mcp', 'list', '--json'], env=env, stdin=subprocess.DEVNULL)
                        record['config_returncode'] = result.returncode
                        if result.returncode == 0:
                            record['status'] = 'compatible_native_catalog'
                            use_catalog = False
                            break
                        record['config_reason'] = codex_failure_reason(result.stderr)
                    record['status'] = 'config_rejected'
                except subprocess.TimeoutExpired:
                    record['status'] = 'timeout'
                except OSError as error:
                    record.update(status='launch_failed', errno=error.errno)
            else:
                raise SetupError('CODEX_CONFIG_INCOMPATIBLE', '已找到 Codex，但客户端无法读取兼容配置。原配置未改动；请将诊断编号提供给支持人员，检查现有配置或客户端限制。')
    diagnostics.data['catalog_mode'] = 'custom' if use_catalog else 'native'
    return use_catalog


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SetupError('KEY_REDIRECT_BLOCKED', '服务地址发生跳转，已停止验证。密钥未发送到其他地址。')


def network_failure(error):
    """Classify transport failures without keeping exception text or addresses."""
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    number = getattr(cause, 'errno', None)
    winerror = getattr(cause, 'winerror', None)
    retryable = False
    if isinstance(cause, http.client.IncompleteRead):
        kind, retryable = 'response_incomplete', True
    elif isinstance(cause, ssl.SSLCertVerificationError):
        kind = 'certificate'
    elif isinstance(cause, (ssl.SSLEOFError, ssl.SSLZeroReturnError)):
        kind, retryable = 'tls_closed', True
    elif isinstance(cause, ssl.SSLError):
        kind = 'tls'
    elif isinstance(cause, socket.gaierror):
        kind = 'dns'
        retryable = number in (socket.EAI_AGAIN, 11002)
    elif isinstance(cause, TimeoutError) or number == errno.ETIMEDOUT or winerror == 10060:
        kind, retryable = 'timeout', True
    elif isinstance(cause, (ConnectionResetError, ConnectionAbortedError)) or number in (
            errno.ECONNRESET, errno.ECONNABORTED) or winerror in (10053, 10054):
        kind, retryable = 'connection_closed', True
    elif isinstance(cause, OSError):
        kind = 'connection'
    elif isinstance(cause, http.client.HTTPException):
        kind = 'protocol'
    else:
        kind = 'network'
    details = {'kind': kind, 'retryable': retryable}
    for name, value in (('errno', number), ('winerror', winerror)):
        if type(value) is int:
            details[name] = value
    return details


def validate_key(key, diagnostics=None):
    if not re.fullmatch(r'sk-[A-Za-z0-9_-]{16,256}', key):
        raise SetupError('KEY_FORMAT_INVALID', '密钥不完整，请从工作台重新复制一键配置命令。')
    # Model listing uses spend authorization and rejects zero-allowance team keys.
    # Setup authenticates an existing key; generation continues to enforce quotas.
    request = urllib.request.Request(KEY_CHECK_URL, headers={
        'Authorization': 'Bearer ' + key, 'User-Agent': 'Realyu-Setup/1.0'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    attempts = []
    if diagnostics:
        diagnostics.data['key_validation'] = {'attempts': attempts}
    try:
        # Identity GET only: a transient failure may retry once. Never retry
        # rejected keys, HTTP errors, certificate failures or model requests.
        for attempt in range(2):
            started = time.monotonic()
            details = {'attempt': attempt + 1}
            attempts.append(details)
            try:
                with opener.open(request, timeout=15) as response:
                    if diagnostics:
                        diagnostics.data['http_status'] = 200
                        request_id = response.headers.get('X-Oneapi-Request-Id', '')
                        if isinstance(request_id, str) and re.fullmatch(r'[0-9]{14}[A-Za-z0-9]{8,50}', request_id):
                            diagnostics.data['request_id'] = request_id
                    payload = response.read(1024 * 1024)
                    length = getattr(response, 'headers', {}).get('Content-Length', '')
                    if isinstance(length, str) and length.isdecimal() and len(length) < 12:
                        expected = int(length)
                        if len(payload) < expected <= 1024 * 1024:
                            # read(limit) does not raise on a short HTTP body.
                            raise http.client.IncompleteRead(b'', expected - len(payload))
                    data = json.loads(payload)
                details['kind'] = 'response'
                break
            except (urllib.error.HTTPError, SetupError):
                raise
            except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
                details.update(network_failure(error))
                if attempt == 0 and details['retryable']:
                    time.sleep(0.5)
                    continue
                raise SetupError('KEY_NETWORK_ERROR', '无法连接密钥验证服务，请检查网络、系统时间和 HTTPS 证书后重试。') from None
            finally:
                details['elapsed_seconds'] = round(time.monotonic() - started, 3)
    except urllib.error.HTTPError as error:
        if diagnostics:
            diagnostics.data['http_status'] = error.code
            request_id = error.headers.get('X-Oneapi-Request-Id', '') if error.headers else ''
            if re.fullmatch(r'[0-9]{14}[A-Za-z0-9]{8,50}', request_id):
                diagnostics.data['request_id'] = request_id
        if error.code == 401:
            raise SetupError('KEY_REJECTED', '密钥验证未通过（HTTP 401），请在工作台检查密钥是否有效。') from None
        if error.code == 403:
            raise SetupError('KEY_ACCESS_DENIED', '访问被拒绝（HTTP 403），请检查密钥权限或网络访问限制。') from None
        if error.code == 429:
            raise SetupError('KEY_RATE_LIMITED', '服务暂时限流（HTTP 429），请稍后重试，无需重装 Codex。') from None
        raise SetupError('KEY_HTTP_ERROR', f'密钥验证服务异常（HTTP {error.code}），请稍后重试。') from None
    except SetupError:
        raise
    except (ValueError, UnicodeError):
        raise SetupError('KEY_RESPONSE_INVALID', '密钥验证服务返回了无法识别的数据，请稍后重试。原配置未变。') from None
    usage = data.get('data') if isinstance(data, dict) else None
    if (not isinstance(data, dict) or data.get('code') is not True or not isinstance(usage, dict)
            or usage.get('object') != 'token_usage' or type(usage.get('expires_at')) is not int
            or type(usage.get('unlimited_quota')) is not bool
            or type(usage.get('total_available')) is not int):
        raise SetupError('KEY_RESPONSE_INVALID', '密钥验证服务返回了无法识别的数据，请稍后重试。原配置未变。')
    if usage['expires_at'] and usage['expires_at'] < datetime.now(timezone.utc).timestamp():
        raise SetupError('KEY_EXPIRED', '密钥已过期，请在工作台更新有效期或复制新的配置命令。')
    if diagnostics and not usage['unlimited_quota'] and usage['total_available'] <= 0:
        diagnostics.data['key_warning'] = 'KEY_QUOTA_EMPTY'


def atomic_write(path, data):
    descriptor, temporary = tempfile.mkstemp(prefix='.realyu-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def failure_details(error, operation, path):
    """Keep the initial failure and rollback failures without exception messages."""
    details = {'operation': operation, 'file': path.name, 'exception': type(error).__name__}
    if isinstance(error, SetupError):
        details['code'] = error.code
    if isinstance(error, OSError):
        details.update(errno=error.errno, winerror=getattr(error, 'winerror', None))
    return details


def ensure_sessions_idle(home):
    """Codex holds an OS lock for every loaded thread, including idle UI tabs."""
    folder = home / 'thread-writer-locks'
    if folder.is_symlink():
        raise SetupError('SESSIONS_PATH_UNSAFE', '会话锁目录是链接，请检查配置目录。')
    for path in folder.glob('*.lock'):
        if path.name.startswith('.'):
            continue
        if path.is_symlink():
            raise SetupError('SESSIONS_PATH_UNSAFE', '会话锁文件是链接，请检查配置目录。')
        try:
            with path.open('r+b') as stream:
                if os.name == 'nt':
                    import msvcrt
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError as error:
                        if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                            raise
                        raise SetupError('SESSIONS_BUSY', '旧会话仍被 Codex 占用，请完全退出后重跑配置。') from None
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    try:
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError as error:
                        if error.errno not in (errno.EACCES, errno.EAGAIN):
                            raise
                        raise SetupError('SESSIONS_BUSY', '旧会话仍被 Codex 占用，请完全退出后重跑配置。') from None
                    fcntl.flock(stream, fcntl.LOCK_UN)
        except FileNotFoundError:
            continue


class CodexSessionClient:
    """Bounded stdio RPC; never forwards conversation data to diagnostics."""
    def __init__(self, codex, home):
        self.codex, self.home = codex, home
        self.process = None
        self.responses = queue.Queue()
        self.sequence = 0

    def __enter__(self):
        # Only initialization is retried: no conversation has been mutated yet.
        for attempt in range(2):
            self.responses = queue.Queue()
            self.sequence = 0
            try:
                return self.start()
            except SetupError as error:
                if error.code not in ('SESSIONS_RPC_FAILED', 'SESSIONS_TIMEOUT') or attempt == 1:
                    raise

    def start(self):
        env = {k: v for k, v in os.environ.items()
            if k.upper() not in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'REALYU_API_KEY')
            and 'PROXY' not in k.upper()}
        env['CODEX_HOME'] = str(self.home)
        command = [str(self.codex)]
        config = tomllib.loads((self.home / 'config.toml').read_text(encoding='utf-8-sig'))
        # Migration must not launch the user's MCP programs or send model turns.
        disabled = ','.join(json.dumps(name) + '={enabled=false,required=false}'
            for name in config.get('mcp_servers', {}))
        if disabled:
            command += ['-c', 'mcp_servers={' + disabled + '}']
        command += ['-c', 'features.shell_snapshot=false', 'app-server']
        self.process = subprocess.Popen(command, cwd=self.home, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.reader = threading.Thread(target=self.read_responses, daemon=True)
        self.reader.start()
        try:
            self.call('initialize', {'clientInfo': {'name': 'realyu_session_migration', 'version': CLIENT_VERSION},
                                     'capabilities': {'experimentalApi': True}})
            self.send({'method': 'initialized', 'params': {}})
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def read_responses(self):
        try:
            for line in self.process.stdout:
                value = json.loads(line)
                if 'id' in value:
                    if 'method' in value:
                        # Never approve commands, tools, or authentication prompts.
                        self.send({'id': value['id'], 'error': {'code': -32601, 'message': 'Unavailable during setup'}})
                    else:
                        self.responses.put(value)
        except (OSError, ValueError):
            pass
        finally:
            self.responses.put(None)

    def send(self, value):
        try:
            self.process.stdin.write(json.dumps(value) + '\n')
            self.process.stdin.flush()
        except (OSError, ValueError):
            raise SetupError('SESSIONS_RPC_FAILED', 'Codex 会话组件意外退出。请关闭 Codex 后重试原配置命令。') from None

    def call(self, method, params):
        self.sequence += 1
        self.send({'id': self.sequence, 'method': method, 'params': params})
        deadline = time.monotonic() + 60
        while True:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise queue.Empty
                response = self.responses.get(timeout=remaining)
            except queue.Empty:
                raise SetupError('SESSIONS_TIMEOUT', '旧会话处理超时，请重跑配置继续迁移。') from None
            # A timed-out read may reply after the next read was sent. Discard
            # only older request IDs so one slow thread cannot poison the rest.
            if isinstance(response, dict) and type(response.get('id')) is int and response['id'] < self.sequence:
                continue
            break
        if not isinstance(response, dict) or response.get('id') != self.sequence:
            raise SetupError('SESSIONS_RPC_FAILED', 'Codex 会话组件未返回有效结果，请更新 Codex 后重试。')
        if 'error' in response:
            error = response['error']
            if error.get('code') == -32601 or 'requires experimentalApi' in str(error.get('message', '')):
                raise SetupError('SESSIONS_CLIENT_UNSUPPORTED', '当前 Codex 不支持所需会话接口，连接配置已保留，请更新 Codex 后重试迁移。')
            if 'active writer' in str(error.get('message', '')):
                raise SetupError('SESSIONS_BUSY', '旧会话仍被另一个 Codex 进程占用。请完全退出 Codex，再执行原命令继续迁移。')
            raise SetupError('SESSIONS_RPC_FAILED', 'Codex 无法处理某个旧会话，请完全退出并更新 Codex 后重试。历史备份已保留。')
        return response['result']

    def list_all(self, include_archived=False):
        threads, seen = [], set()
        self.archived_count = 0
        sources = ['cli', 'vscode', 'exec', 'appServer', 'subAgent', 'subAgentReview',
            'subAgentCompact', 'subAgentThreadSpawn', 'subAgentOther', 'unknown']
        for archived in ((False, True) if include_archived else (False,)):
            cursor, cursors = None, set()
            while True:
                page = self.call('thread/list', {'archived': archived, 'limit': 100,
                    'cursor': cursor, 'sortKey': 'created_at', 'sortDirection': 'asc',
                    'modelProviders': [], 'sourceKinds': sources})
                for item in page['data']:
                    if item['id'] in seen:
                        raise SetupError('SESSIONS_CHANGED', '扫描期间会话发生变化，请关闭 Codex 后重新执行。')
                    seen.add(item['id'])
                    threads.append({**item, 'archived': bool(item.get('archived', archived))})
                cursor = page.get('nextCursor')
                if cursor is None:
                    break
                if cursor in cursors:
                    raise SetupError('SESSIONS_RPC_FAILED', '客户端无法完整列出历史会话，请更新 Codex 后重试。')
                cursors.add(cursor)
        # Native thread/list hides persisted forks without their own user turn.
        # Read IDs only from the newest local index and let Codex read/mutate
        # every thread itself. No SQL writes or client schema migrations.
        databases = sorted(self.home.glob('state_*.sqlite'),
            key=lambda p: int(p.stem.split('_')[-1]) if p.stem.split('_')[-1].isdigit() else -1)
        if databases:
            database = databases[-1]
            if database.is_symlink():
                raise SetupError('SESSIONS_PATH_UNSAFE', '会话数据库是链接，已停止迁移。')
            with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                columns = {row[1] for row in db.execute('PRAGMA table_info(threads)')}
                if not {'id', 'archived'}.issubset(columns):
                    raise SetupError('SESSIONS_INDEX_UNSUPPORTED', '当前客户端的会话索引格式暂不受支持，请更新 Codex 后重新执行。')
                indexed = db.execute('SELECT id, archived FROM threads').fetchall()
            if not include_archived:
                archived_ids = {thread_id for thread_id, archived in indexed if archived}
                threads = [item for item in threads if item['id'] not in archived_ids and not item.get('archived')]
            for thread_id, archived in indexed:
                if archived:
                    self.archived_count += 1
                    if not include_archived:
                        continue
                if thread_id not in seen:
                    try:
                        result = self.call('thread/read', {'threadId': thread_id, 'includeTurns': False})
                        item = result.get('thread') if isinstance(result, dict) else None
                        if not isinstance(item, dict):
                            item = {'id': thread_id, 'read_error': 'rpc_failed'}
                        elif item.get('id') != thread_id:
                            item = {'id': thread_id, 'read_error': 'thread_id_mismatch'}
                    except SetupError as error:
                        if error.code not in SESSION_REASONS:
                            raise
                        # Keep the indexed identity/archive state for counting
                        # and cascade restoration; never resume unreadable data.
                        item = {'id': thread_id, 'read_error': SESSION_REASONS[error.code]}
                    threads.append({**item, 'archived': bool(archived)})
                    seen.add(thread_id)
        return threads

    def __exit__(self, *_):
        if self.process is None:
            return
        try:
            self.process.stdin.close()
            self.process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait(timeout=10)
        finally:
            self.reader.join(timeout=2)
            self.process.stdout.close()


def session_filesystem_path(path):
    """Use one absolute namespace for containment checks and long-path I/O."""
    resolved = path.resolve()
    if os.name != 'nt':
        return resolved
    value = str(resolved)
    if value.startswith('\\\\?\\'):
        if re.match(r'^[A-Za-z]:\\', value[4:]) or value[4:].upper().startswith('UNC\\'):
            return resolved
    elif value.startswith('\\\\') and not value.startswith('\\\\.\\'):
        return Path('\\\\?\\UNC\\' + value[2:])
    elif re.match(r'^[A-Za-z]:\\', value):
        return Path('\\\\?\\' + value)
    raise SetupError('SESSIONS_PATH_UNSAFE', '历史会话路径格式不受支持，已停止迁移；原会话未改动。')


def backup_sessions(home, backup, threads, include_databases=True):
    """Keep recoverable local originals; never upload history or change DB schemas."""
    root = session_filesystem_path(home)
    target = session_filesystem_path(backup / 'sessions')
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    journal = [{key: item.get(key) for key in ('id', 'modelProvider', 'archived', 'path')} for item in threads]
    atomic_write(target / 'manifest.json', json.dumps(journal).encode())
    sources = {Path(item['path']) for item in threads if item.get('path')}
    for source in sorted(sources):
        resolved = session_filesystem_path(source)
        if not resolved.is_relative_to(root) or any(p.is_symlink() for p in (source, *source.parents)):
            raise SetupError('SESSIONS_PATH_UNSAFE', '历史会话路径不在当前配置目录内，已停止迁移；原会话未改动。')
        destination = target / resolved.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        before = resolved.stat()
        with resolved.open('rb') as incoming, destination.open('wb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        os.chmod(destination, 0o600)
        after = resolved.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise SetupError('SESSIONS_CHANGED', '备份期间会话发生变化，请完全退出 Codex 后重新执行。')
    databases = {*home.glob('state_*.sqlite'), *home.glob('thread_history_*.sqlite')} if include_databases else set()
    for source in sorted(databases):
        if source.is_symlink():
            raise SetupError('SESSIONS_PATH_UNSAFE', '会话数据库是链接，已停止迁移。')
        with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            with closing(sqlite3.connect(target / source.name)) as snapshot:
                db.backup(snapshot)
        os.chmod(target / source.name, 0o600)


def restore_archive_states(client, states):
    if not states:
        return
    current = {item['id']: item for item in client.list_all(include_archived=True)}
    if not set(states).issubset(current):
        raise SetupError('SESSIONS_CHANGED', '迁移记录中的会话已发生变化，请保留备份并联系支持人员。')
    # Archive parents first; then undo any cascading archive of active children.
    for thread_id, archived in states.items():
        if archived and not current[thread_id]['archived']:
            client.call('thread/archive', {'threadId': thread_id})
    current = {item['id']: item for item in client.list_all(include_archived=True)}
    for thread_id, archived in states.items():
        if not archived and current[thread_id]['archived']:
            client.call('thread/unarchive', {'threadId': thread_id})
    current = {item['id']: item for item in client.list_all(include_archived=True)}
    if any(thread_id not in current or current[thread_id]['archived'] != archived
           for thread_id, archived in states.items()):
        raise SetupError('SESSIONS_VERIFY_FAILED', '旧会话归档状态未能还原，历史备份和恢复记录已保留，请提供诊断编号。')


def check_session_migration_support(home, codex):
    """Probe native settings and writer exclusion without touching user history."""
    if not any((home / name).exists() for name in ('sessions', 'archived_sessions')) and not any(home.glob('state_*.sqlite')):
        return
    with tempfile.TemporaryDirectory(prefix='realyu-session-probe-') as directory:
        probe = Path(directory)
        (probe / 'config.toml').write_text('''model_provider="realyu"
model="gpt-6-luna"
[model_providers.realyu]
name="Migration capability check"
base_url="http://127.0.0.1:1/v1"
wire_api="responses"
requires_openai_auth=true
''', encoding='utf-8')
        atomic_write(probe / 'auth.json', b'{"OPENAI_API_KEY":"sk-offline-capability-probe"}')
        with CodexSessionClient(codex, probe) as client:
            result = client.call('thread/start', {'cwd': str(probe), 'modelProvider': 'realyu'})
            client.call('thread/settings/update', {'threadId': result['thread']['id'], 'model': result['model']})
            try:
                ensure_sessions_idle(probe)
            except SetupError as error:
                if error.code == 'SESSIONS_BUSY':
                    return
                raise
    raise SetupError('SESSIONS_CLIENT_UNSUPPORTED', '此 Codex 版本缺少安全的会话迁移能力。连接配置已保留，请更新 Codex 后重试迁移。')


def session_provider_label(value):
    """Only fixed labels can leave the local session index in diagnostics."""
    if value is None:
        return 'missing'
    if not isinstance(value, str) or not value:
        return 'invalid'
    return value if value in ('realyu', 'openai') else 'other'


def session_parent(item):
    """Only native spawned-child metadata establishes a parent dependency."""
    source = item.get('source')
    agent = source.get('subAgent') if isinstance(source, dict) else None
    spawned = agent.get('thread_spawn') if isinstance(agent, dict) else None
    parent = item.get('parentThreadId')
    if isinstance(spawned, dict) and isinstance(parent, str) and parent and spawned.get('parent_thread_id') == parent:
        return parent
    return None


def builtin_openai_connection_ready(client):
    """Check the effective native config, not merely the TOML we wrote."""
    effective = client.call('config/read', {'includeLayers': False})
    config = effective.get('config') if isinstance(effective, dict) else None
    return isinstance(config, dict) and config.get('openai_base_url') == BASE


def builtin_openai_root(item):
    """A missing or malformed child relationship must never become a root."""
    source = item.get('source')
    return (item.get('modelProvider') == 'openai' and not item.get('read_error')
            and item.get('parentThreadId') is None
            and not (isinstance(source, dict) and 'subAgent' in source))


def compatible_session_children(threads, client):
    """Use Codex's built-in-provider endpoint override, never rewrite history."""
    indexed = {item['id']: item for item in threads}
    children = {item['id']: session_parent(item) for item in threads
                if item.get('modelProvider') == 'openai' and session_parent(item) in indexed
                and not item.get('read_error')}
    if not children:
        return {}
    if not builtin_openai_connection_ready(client):
        return {}
    return children


def child_connection_ready(thread_id, children, current, failures, compatible_roots=()):
    """An old child is usable only when its entire native parent chain is ready."""
    seen = set()
    while True:
        if thread_id in seen or thread_id in failures:
            return False
        seen.add(thread_id)
        item = current.get(thread_id, {})
        provider = item.get('modelProvider')
        parent = session_parent(item)
        if item.get('read_error') or provider not in ('openai', 'realyu'):
            return False
        if thread_id in compatible_roots and builtin_openai_root(item):
            return True
        if provider == 'openai' and (thread_id not in children or parent != children[thread_id]):
            return False
        if parent is None:
            return provider == 'realyu'
        # A migrated intermediate child still depends on its own native parent.
        thread_id = parent


def resume_session(client, item, **overrides):
    """Read/save settings without contacting old endpoints or starting tools."""
    config_params = {'includeLayers': False}
    if isinstance(item.get('cwd'), str) and item['cwd']:
        config_params['cwd'] = item['cwd']
    effective = client.call('config/read', config_params)['config']
    disabled = {name: {'enabled': False, 'required': False} for name in effective.get('mcp_servers', {})}
    # Codex may prewarm a WebSocket during resume, before any turn/start.
    # Never let the new credential reach an old provider while reading settings.
    # These native overrides are session-local, not written to config.toml.
    offline = 'http://127.0.0.1:1/v1'
    providers = {name: {'base_url': offline, 'supports_websockets': False}
                 for name in effective.get('model_providers', {})}
    return client.call('thread/resume', {'threadId': item['id'], 'excludeTurns': True,
        'config': {'mcp_servers': disabled, 'features.shell_snapshot': False,
                   'model_providers': providers, 'openai_base_url': offline}, **overrides})


def migrate_session(client, item, details, original_settings):
    """Use the native settings event so a resume override survives process exit."""
    params = {'threadId': item['id']}
    result = resume_session(client, item, modelProvider='realyu', model=original_settings['model'])
    returned = result.get('thread')
    returned = returned if isinstance(returned, dict) else {}
    details.update(expected_provider='realyu',
        returned_provider=session_provider_label(result.get('modelProvider')),
        nested_provider=session_provider_label(returned.get('modelProvider')),
        returned_thread_match='matches' if returned.get('id') == item['id'] else
            ('different' if returned.get('id') else 'missing'))
    if returned.get('id') != item['id']:
        return 'thread_id_mismatch'
    if result.get('modelProvider') != 'realyu':
        return 'provider_mismatch'
    model = result.get('model')
    if not isinstance(model, str) or not model:
        return 'settings_invalid'
    settings = {**params, 'model': model}
    if original_settings.get('reasoningEffort') is not None:
        settings['effort'] = original_settings['reasoningEffort']
    details['phase'] = 'save'
    client.call('thread/settings/update', settings)
    client.call('thread/unsubscribe', params)
    return None


def verify_session(client, item, details, original_settings=None):
    """A fresh default resume must read the saved route, without an override."""
    details['phase'] = 'verify'
    result = resume_session(client, item)
    thread = result.get('thread')
    if not isinstance(thread, dict) or thread.get('id') != item['id']:
        return 'thread_id_mismatch'
    details['persisted_provider'] = session_provider_label(result.get('modelProvider'))
    details['nested_provider'] = session_provider_label(thread.get('modelProvider'))
    # Older Codex returns the original session metadata inside `thread` even
    # after saving new settings. The fresh resume's top-level provider is the
    # effective persisted route; the nested label is only diagnostic metadata.
    if result.get('modelProvider') != 'realyu':
        return 'persistence_mismatch'
    if original_settings and any(result.get(key) != original_settings.get(key) for key in ('model', 'reasoningEffort')):
        return 'settings_mismatch'
    client.call('thread/unsubscribe', {'threadId': item['id']})
    return None


def migrate_sessions(home, codex, backup, diagnostics):
    status = {'status': 'scanning', 'total': 0, 'migrated': 0, 'already_current': 0,
              'attempted': 0, 'failed': 0, 'skipped_busy': 0, 'skipped_archived': 0,
              'skipped_children': 0, 'compatible_children': 0, 'compatible_roots': 0}
    diagnostics.data['sessions'] = status
    for name in ('session_issue', 'session_failed', 'session_actual_provider', 'session_nested_provider',
                 'session_persisted_provider', 'session_thread_match'):
        diagnostics.data.pop(name, None)
    if not any((home / name).exists() for name in ('sessions', 'archived_sessions')) and not any(home.glob('state_*.sqlite')):
        status['status'] = 'complete'
        return
    ensure_sessions_idle(home)
    check_session_migration_support(home, codex)
    journal_path = home / 'realyu-session-migration.json'
    if journal_path.is_symlink():
        raise SetupError('SESSIONS_PATH_UNSAFE', '会话迁移记录是链接，已停止迁移。')
    retry_ids = set()
    with CodexSessionClient(codex, home) as client:
        if journal_path.exists():
            journal = json.loads(journal_path.read_text(encoding='utf-8'))
            states = journal.get('archived')
            schema, phase = journal.get('schema'), journal.get('phase')
            if (schema not in (1, 2) or not isinstance(states, dict)
                    or any(type(value) is not bool for value in states.values())
                    or (schema == 2 and (phase not in ('restore_pending', 'retry') or (phase == 'retry' and states)))):
                raise SetupError('SESSIONS_JOURNAL_INVALID', '会话迁移记录无法读取，请保留备份并联系支持人员。')
            # Only recover unfinished archive operations from installers <=1.4.14.
            # New runs never archive/unarchive; completed retry records cannot
            # overwrite archive changes the user made since their previous run.
            needs_restore = (schema == 1 and not isinstance(journal.get('failures'), dict)) or phase == 'restore_pending'
            if needs_restore:
                restore_archive_states(client, states)
                # Commit recovery immediately: a later scan/backup failure must
                # not replay this old snapshot over the user's next changes.
                atomic_write(journal_path, json.dumps({'schema': 2, 'phase': 'retry', 'archived': {},
                    'failures': journal.get('failures', {})}).encode())
            if isinstance(journal.get('failures'), dict):
                retry_ids = set(journal['failures'])
        threads = client.list_all()
        status['skipped_archived'] = client.archived_count
        children = compatible_session_children(threads, client)
    # Defend the boundary even if an older client ignores the list filter.
    status['skipped_archived'] += sum(bool(item.get('archived')) for item in threads)
    threads = [item for item in threads if not item.get('archived')]
    status['total'] = len(threads)
    internal = {item['id'] for item in threads if item.get('parentThreadId') is not None
                or (isinstance(item.get('source'), dict) and 'subAgent' in item['source'])}
    roots = [item for item in threads if item['id'] not in internal]
    pending = [item for item in roots if item.get('modelProvider') != 'realyu' or item['id'] in retry_ids]
    pending_ids = {item['id'] for item in pending}
    if pending:
        ensure_sessions_idle(home)
        # One consistent SQLite backup for the batch. Each rollout is backed up
        # separately before its own mutation, so one bad path cannot block others.
        backup_sessions(home, backup, [])
    atomic_write(journal_path, json.dumps({'schema': 2, 'phase': 'retry', 'archived': {}}).encode())
    status['status'] = 'migrating'
    print(f'[4/4] 正在处理 {len(roots)} 个未归档旧会话…', flush=True)
    failures, observations = {}, {}
    current = {item['id']: dict(item) for item in threads}
    labels = {'thread_id_mismatch': '返回的会话不匹配', 'provider_mismatch': '供应商未切换',
              'busy': '仍被占用', 'rpc_failed': '客户端未能处理', 'timeout': '处理超时',
              'unsupported': '客户端接口不支持', 'backup_failed': '备份未完成',
              'settings_invalid': '会话设置无法读取', 'persistence_mismatch': '连接设置未通过校验',
              'settings_mismatch': '原会话设置未保留',
              'session_error': '会话处理异常'}
    original_settings = {}
    for item in roots:
        observations[item['id']] = {'phase': 'read'}
        if item.get('read_error'):
            failures[item['id']] = item['read_error']

    def record_error(item, error):
        details = observations[item['id']]
        failures[item['id']] = SESSION_REASONS.get(getattr(error, 'code', ''),
            'backup_failed' if details['phase'] == 'backup' else 'session_error')
        details['exception'] = type(error).__name__ if type(error).__name__ in (
            'SetupError', 'OSError', 'PermissionError', 'FileNotFoundError', 'ValueError',
            'JSONDecodeError', 'TypeError', 'KeyError') else 'Other'

    def run_phase(items, phase, operation):
        client = None
        try:
            for item in items:
                if item['id'] in failures:
                    continue
                observations[item['id']]['phase'] = phase
                try:
                    if client is None:
                        ensure_sessions_idle(home)
                        client = CodexSessionClient(codex, home).__enter__()
                    reason = operation(client, item)
                    if reason:
                        failures[item['id']] = reason
                except Exception as error:
                    record_error(item, error)
                if item['id'] in failures and client is not None:
                    # A broken/timed-out thread must not poison the next one.
                    failed_client, client = client, None
                    failed_client.__exit__(None, None, None)
                    ensure_sessions_idle(home)
        finally:
            if client is not None:
                client.__exit__(None, None, None)
        # Native unsubscribe can retain the writer lock for 30 minutes. Only
        # process exit and the OS lock check establish a safe phase boundary.
        ensure_sessions_idle(home)

    def read_settings(client, item):
        original = resume_session(client, item)
        if original.get('thread', {}).get('id') != item['id']:
            return 'thread_id_mismatch'
        if not isinstance(original.get('model'), str) or not original['model']:
            return 'settings_invalid'
        original_settings[item['id']] = {key: original.get(key) for key in ('model', 'reasoningEffort')}
        client.call('thread/unsubscribe', {'threadId': item['id']})
        return None

    def save_settings(client, item):
        return migrate_session(client, item, observations[item['id']], original_settings[item['id']])

    def verify_settings(client, item):
        return verify_session(client, item, observations[item['id']], original_settings.get(item['id']))

    # Bound loaded history to ten roots. Readers exit before any mutation;
    # verifiers are born only after all batch mutation processes have exited.
    # Every thread is first-loaded in that fresh verifier without a provider
    # override, preserving the persistent-settings check while amortizing startup.
    for offset in range(0, len(roots), 10):
        batch = roots[offset:offset + 10]
        changing = [item for item in batch if item['id'] in pending_ids]
        for position, item in enumerate(batch, offset + 1):
            if item['id'] not in pending_ids or item['id'] in failures:
                continue
            observations[item['id']]['phase'] = 'backup'
            try:
                if not item.get('path'):
                    failures[item['id']] = 'backup_failed'
                else:
                    backup_sessions(home, backup / 'threads' / str(position), [item], include_databases=False)
            except Exception as error:
                record_error(item, error)
        last = offset + len(batch)
        if changing:
            print(f'旧会话处理 {offset + 1}–{last}/{len(roots)}：读取原设置', flush=True)
            run_phase(changing, 'resume', read_settings)
            print(f'旧会话处理 {offset + 1}–{last}/{len(roots)}：保存新连接', flush=True)
            run_phase(changing, 'resume', save_settings)
        print(f'旧会话处理 {offset + 1}–{last}/{len(roots)}：重新读取校验', flush=True)
        run_phase(batch, 'verify', verify_settings)
        for position, item in enumerate(batch, offset + 1):
            status['attempted'] += int(item['id'] in pending_ids)
            reason = failures.get(item['id'])
            if reason:
                status['skipped_busy'] += int(reason == 'busy')
                status['failed'] += int(reason != 'busy')
                print(f"第 {position} 个旧会话未完成（{labels.get(reason, '读取异常')}），继续处理其余会话。", flush=True)
            else:
                status['migrated' if item['id'] in pending_ids else 'already_current'] += 1
                current[item['id']]['modelProvider'] = 'realyu'
        print(f'旧会话处理进度：{last}/{len(roots)}', flush=True)
    # Preserve the existing, narrowly validated built-in child connection path.
    # Internal tasks without a verified parent chain remain untouched.
    for thread_id in internal:
        if thread_id in children and child_connection_ready(thread_id, children, current, failures):
            status['compatible_children'] += 1
        else:
            status['skipped_children'] += 1
    if failures:
        status['status'] = 'partial'
        atomic_write(journal_path, json.dumps({'schema': 2, 'phase': 'retry', 'archived': {},
            'failures': failures, 'observations': {key: observations[key] for key in failures}}).encode())
        status['issues'] = [{'position': index, 'reason': failures[item['id']], **observations[item['id']]}
                           for index, item in enumerate(roots, 1) if item['id'] in failures]
        reasons = set(failures.values())
        first = status['issues'][0]
        diagnostics.data.update(session_issue=next(iter(reasons)) if len(reasons) == 1 else 'mixed',
            session_failed=len(failures), session_actual_provider=first.get('returned_provider', 'missing'),
            session_nested_provider=first.get('nested_provider', 'missing'),
            session_persisted_provider=first.get('persisted_provider', 'missing'),
            session_thread_match=first.get('returned_thread_match', 'missing'))
        return
    status['status'] = 'complete'
    journal_path.unlink(missing_ok=True)


def complete_session_setup(home, codex, backup, diagnostics):
    """History is optional: no failure here may undo a working connection."""
    diagnostics.data['stage'] = 'sessions'
    started = time.monotonic()
    try:
        migrate_sessions(home, codex, backup, diagnostics)
    except (Exception, KeyboardInterrupt) as error:
        sessions = diagnostics.data.setdefault('sessions', {})
        code = error.code if isinstance(error, SetupError) else 'SESSIONS_MIGRATION_FAILED'
        sessions.update(status='deferred' if code == 'SESSIONS_BUSY' else 'incomplete', reason=code)
        diagnostics.data.update(status='success_with_warnings', stage='sessions', code=code, migration_warning=True)
        if code == 'SESSIONS_BUSY':
            print('连接配置成功。Codex 尚未关闭，旧会话迁移已暂缓；完全退出后重跑原命令即可补齐。', flush=True)
        else:
            print(f'连接配置成功。旧会话迁移未完成 [{code}]；已完成项和历史备份保留，重跑可继续。', flush=True)
        return
    finally:
        diagnostics.data.setdefault('sessions', {})['elapsed_seconds'] = round(time.monotonic() - started, 3)
    sessions = diagnostics.data['sessions']
    ready = sessions['migrated'] + sessions['already_current']
    print(f"连接配置成功。旧对话已完成 {ready} 个，归档跳过 {sessions.get('skipped_archived', 0)} 个，"
          f"占用 {sessions['skipped_busy']} 个，异常 {sessions['failed']} 个。", flush=True)
    if sessions.get('compatible_children'):
        print(f"另有 {sessions['compatible_children']} 个内部子会话保留兼容连接，请从原父会话继续。", flush=True)
    if sessions.get('skipped_children'):
        print(f"已跳过 {sessions['skipped_children']} 个内部子会话，原记录保留。", flush=True)
    if sessions['status'] == 'partial':
        print('未完成项不影响新对话及其他已完成的旧对话；请保留诊断编号，重跑会自动补齐。', flush=True)
        diagnostics.data.update(status='success_with_warnings', stage='sessions',
                                code='SESSIONS_VERIFY_FAILED', migration_warning=True)
        return
    print('重新打开 Codex 后可使用新连接。', flush=True)
    diagnostics.data.update(status='success', stage='complete', code='OK')


def install(home, python, codex, key, helper, catalog, standalone=False, use_catalog=True, diagnostics=None):
    home = home.expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    config_path, auth_path = home / 'config.toml', home / 'auth.json'
    catalog_path = home / 'realyu-models.json'
    helper_path = home / 'realyu_images.py'
    paths = [config_path, auth_path, catalog_path]
    if standalone:
        # Windows cannot replace a running EXE. Retain the previous version for
        # existing MCP processes and backups; point new sessions at immutable bytes.
        digest = hashlib.sha256(helper).hexdigest()
        helper_path = home / 'realyu-runtime' / 'clients' / digest / 'realyu-client.exe'
    else:
        paths.append(helper_path)
    if any(path.is_symlink() for path in paths):
        raise SetupError('CONFIG_SYMLINK', '配置目录中存在符号链接，已停止配置，原文件未变。')
    old = {path.name: path.read_bytes() if path.exists() else None for path in paths}
    source = (old['config.toml'] or b'').decode('utf-8-sig')
    config = merge_config(source, home, python, standalone=standalone, use_catalog=use_catalog,
        helper_path=helper_path)
    backup = home / 'realyu-backups' / uuid.uuid4().hex
    backup.mkdir(parents=True, mode=0o700)
    for name, data in old.items():
        if data is not None:
            atomic_write(backup / name, data)
    if diagnostics:
        diagnostics.data['backup'] = diagnostics.path(backup)
    print('[2/4] 原配置已备份。' if any(data is not None for data in old.values())
          else '[2/4] 首次配置，无需备份。', flush=True)
    env = os.environ.copy()
    env['CODEX_HOME'] = str(home)
    changed = []
    operation, active_path = 'stage_helper', helper_path
    # Configuration and credentials are changed together; any failed login rolls back.
    try:
        print('[3/4] 正在配置连接与图片功能…', flush=True)
        if standalone:
            for path in (helper_path, *helper_path.parents):
                if path == home:
                    break
                if path.is_symlink() or path.is_junction():
                    raise SetupError('CONFIG_SYMLINK', '图片工具目录中存在链接，已停止配置，原配置未变。')
            helper_path.parent.mkdir(parents=True, exist_ok=True)
            if helper_path.exists():
                if helper_path.read_bytes() != helper:
                    raise SetupError('HELPER_CACHE_INVALID', '图片工具缓存校验失败，原配置未变。请将诊断编号提供给支持人员。')
            else:
                atomic_write(helper_path, helper)
        updates = [(config_path, config.encode('utf-8')), (catalog_path, catalog)]
        if not standalone:
            updates.append((helper_path, helper))
        for active_path, data in updates:
            operation = 'write'
            if old[active_path.name] != data:
                atomic_write(active_path, data)
                changed.append(active_path)
        operation, active_path = 'login', auth_path
        # Login may change auth.json even when the subprocess returns an error.
        changed.append(auth_path)
        run = subprocess.run([codex, 'login', '--with-api-key'], input=key + '\n',
            env=env, text=True, encoding='utf-8', capture_output=True, timeout=45)
        if run.returncode:
            if diagnostics:
                diagnostics.data['login_returncode'] = run.returncode
                diagnostics.data['login_reason'] = codex_failure_reason(run.stderr or '')
            raise SetupError('CODEX_LOGIN_FAILED', 'Codex 未能完成 API Key 登录。请查看诊断编号；可能是客户端限制或配置目录不可写。')
        try:
            auth = json.loads(auth_path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            raise SetupError('CODEX_CREDENTIALS_INVALID', 'Codex 未生成可读取的文件型登录信息。请查看诊断编号，检查客户端凭据存储策略。') from None
        if auth.get('OPENAI_API_KEY') != key or auth.get('tokens'):
            raise SetupError('CODEX_CREDENTIALS_INVALID', 'Codex 未正确保存 API Key 登录。请查看诊断编号，检查客户端凭据存储策略。')
        os.chmod(auth_path, 0o600)
    except BaseException as error:
        rollback_errors = []
        if diagnostics:
            diagnostics.data['original_failure'] = failure_details(error, operation, active_path)
        for path in reversed(changed):
            try:
                current = path.read_bytes() if path.exists() else None
                if current == old[path.name]:
                    continue
                if old[path.name] is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, old[path.name])
            except OSError as restore_error:
                rollback_errors.append(failure_details(restore_error, 'restore', path))
        if diagnostics:
            diagnostics.data['rollback'] = 'failed' if rollback_errors else ('restored' if changed else 'not_needed')
            diagnostics.data['rollback_errors'] = rollback_errors
        if rollback_errors:
            raise SetupError('CONFIG_RESTORE_FAILED', '配置失败且部分文件无法自动恢复，请停止重试并从提示的备份位置恢复，或联系支持人员。') from None
        print('配置未完成，原配置和登录信息已恢复。' if changed else '配置未完成，原配置和登录信息未变。', flush=True)
        raise
    return backup


def read_key():
    if sys.stdin.isatty():
        return getpass.getpass('请输入 API 密钥（输入内容不显示）：').strip()
    # Windows PowerShell may prepend a UTF-8 BOM even for an ASCII key.
    # Decode bytes explicitly instead of using the machine's legacy code page.
    stream = getattr(sys.stdin, 'buffer', sys.stdin)
    value = stream.readline()
    if isinstance(value, bytes):
        value = value.decode('utf-8-sig')
    return value.lstrip('\ufeff').strip()


def main(standalone=False, diagnostics=None):
    diagnostics = diagnostics or Diagnostics()
    if not standalone:
        # Validate the offline SDK bundle before reading a key or changing any
        # existing account/configuration. Bootstrap pins its full archive hash.
        runtime = os.environ.get('REALYU_IMAGE_RUNTIME', '')
        if not runtime or not Path(runtime).is_absolute():
            raise SetupError('IMAGE_RUNTIME_MISSING', '图片组件未准备好，请重新运行完整配置命令。原配置未变。')
        probe = subprocess.run([sys.executable, '-I', '-B', '-c',
            'import runpy, sys; runpy.run_path(sys.argv[1], run_name="realyu_preflight")',
            str(Path(__file__).with_name('realyu_images.py'))],
            capture_output=True, timeout=60)
        if probe.returncode:
            raise SetupError('IMAGE_RUNTIME_MISSING', '图片组件无法启动，请重新运行配置命令。原配置未变。')
    if os.name == 'nt':
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, 'reconfigure'):
                stream.reconfigure(encoding='utf-8', errors='replace')
    print('Realyu 一键配置', flush=True)
    print('将自动备份并切换配置，尝试迁移旧对话；Codex 未关闭时会跳过迁移。', flush=True)
    codex = find_codex(diagnostics)
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    diagnostics.data['stage'] = 'compatibility'
    helper_path = Path(sys.executable) if standalone else Path(__file__).with_name('realyu_images.py')
    helper = helper_path.read_bytes()
    catalog = Path(__file__).with_name('models.json').read_bytes()
    models = json.loads(catalog)['models']
    if {model['slug'] for model in models} != {'gpt-5.6-terra', 'gpt-5.6-sol', 'gpt-6-sol', 'gpt-6.1-sol', 'gpt-6-luna', 'gpt-6-astra', 'gpt-5.6-luna', 'gpt-5.5'}:
        raise SetupError('PACKAGE_CATALOG_INVALID', '安装包中的模型列表校验失败，请重新运行配置命令。原配置未变。')
    config_path = home.expanduser() / 'config.toml'
    source = config_path.read_text(encoding='utf-8-sig') if config_path.exists() else ''
    use_catalog = compatible_catalog(codex, source, catalog, Path(sys.executable), standalone, diagnostics)
    selected = diagnostics.data.get('selected', {})
    print('已识别 Codex' + (' ' + selected['version'] if selected.get('version') else '') + '。', flush=True)
    if not use_catalog:
        print('已自动使用兼容配置：保留客户端原生模型目录，默认模型仍为 gpt-6-luna。', flush=True)
    print(f'配置目录：{home}', flush=True)
    # getpass uses the Windows console even when stdin is a pipe. Read piped
    # credentials directly so headless setup works without echoing the key.
    key = read_key()
    diagnostics.data['stage'] = 'key_validation'
    print('[1/4] 正在验证 API 密钥…', flush=True)
    validate_key(key, diagnostics)
    print('密钥身份验证通过。', flush=True)
    if diagnostics.data.get('key_warning') == 'KEY_QUOTA_EMPTY':
        print('提示：此 Key 当前可用额度为 0，不影响完成连接配置；使用模型前请在工作台检查额度，团队成员请联系负责人分配额度。', flush=True)
    diagnostics.data['stage'] = 'configuration'
    backup = install(home, Path(sys.executable), codex, key, helper, catalog, standalone=standalone,
        use_catalog=use_catalog, diagnostics=diagnostics)
    if backup.is_dir() and any(backup.iterdir()):
        print(f'备份位置：{backup}', flush=True)
    complete_session_setup(home.expanduser().resolve(), codex, backup, diagnostics)


def run_setup(standalone=False):
    diagnostics = Diagnostics()
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser()
    code = 0
    try:
        main(standalone=standalone, diagnostics=diagnostics)
    except Exception as error:
        code = 1
        if isinstance(error, SetupError):
            failure, message = error.code, str(error)
        elif isinstance(error, tomllib.TOMLDecodeError):
            failure, message = 'CONFIG_TOML_INVALID', '现有 config.toml 语法不正确，请修复配置后重试。原配置未被替换。'
        elif isinstance(error, subprocess.TimeoutExpired):
            failure, message = 'CODEX_TIMEOUT', 'Codex 执行超时，请完全退出应用后重试。'
        elif isinstance(error, PermissionError):
            failure, message = 'FILESYSTEM_ACCESS_DENIED', '无法读写配置文件，请检查目录权限、文件占用和安全软件拦截。'
        elif isinstance(error, OSError):
            failure, message = 'FILESYSTEM_ERROR', '文件操作失败，请检查剩余空间、目录是否存在及访问权限。'
        elif isinstance(error, (ValueError, UnicodeError)):
            failure, message = 'DATA_FORMAT_INVALID', '配置或安装文件格式无法读取，请将诊断编号提供给支持人员。'
        else:
            failure, message = 'SETUP_INTERNAL_ERROR', '配置工具遇到内部错误，请将诊断编号提供给支持人员。'
        diagnostics.data.update(status='failed', code=failure, exception=type(error).__name__)
        diagnostics.data['trace'] = [
            {'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
            for frame in traceback.extract_tb(error.__traceback__)[-8:]]
        if isinstance(error, OSError):
            diagnostics.data.update(errno=error.errno, winerror=getattr(error, 'winerror', None))
        print(f'配置未完成 [{failure}]：{message}', file=sys.stderr, flush=True)
        stages = {'discovery': '查找 Codex', 'compatibility': '检查客户端兼容性',
            'key_validation': '验证 Key 身份', 'configuration': '写入配置与登录', 'sessions': '迁移旧会话'}
        print('失败步骤：' + stages.get(diagnostics.data['stage'], '安装准备'), flush=True)
        if diagnostics.data.get('http_status'):
            print('HTTP 状态：' + str(diagnostics.data['http_status']), flush=True)
        if diagnostics.data.get('request_id'):
            print('服务端请求编号：' + diagnostics.data['request_id'], flush=True)
        if failure.startswith('KEY_'):
            advice = '按上方提示检查工作台 Key 或网络；额度为 0 不会阻止配置，无需重装 Codex。'
        elif failure.startswith(('CONFIG_', 'FILESYSTEM_', 'HELPER_')):
            advice = '保留原配置和备份，按上方提示检查目录权限或配置；不要删除整个 .codex 目录。'
        elif failure.startswith('CODEX_'):
            advice = '检查 Codex 是否安装在当前 Windows/macOS 用户下，并按具体错误提示处理。'
        elif failure == 'SESSIONS_BUSY':
            advice = '完全退出 Codex（Mac 用 Command+Q），重新执行原配置命令；已完成的会话会自动跳过，历史备份会保留。'
        elif failure.startswith('SESSIONS_'):
            advice = '配置已保存时无需重复登录。保留诊断编号和历史备份，联系支持人员核对具体迁移错误。'
        else:
            advice = '保留诊断编号并联系支持人员。'
        print('下一步：' + advice, flush=True)
        original = diagnostics.data.get('original_failure')
        if original:
            print(f"最初失败：{original['operation']} / {original['file']} / {original['exception']}"
                f"（errno={original.get('errno')}, winerror={original.get('winerror')}）", file=sys.stderr, flush=True)
        for item in diagnostics.data.get('rollback_errors', []):
            print('未能恢复的文件：' + item['file'], file=sys.stderr, flush=True)
        if diagnostics.data.get('backup'):
            print('备份位置：' + diagnostics.data['backup'], file=sys.stderr, flush=True)
    finally:
        diagnostics.data['elapsed_seconds'] = round(time.monotonic() - diagnostics.started, 3)
        diagnostics.data['completed_at'] = datetime.now(timezone.utc).isoformat()
        if code or diagnostics.data.get('migration_warning'):
            print('诊断详情：', flush=True)
            print(json.dumps(diagnostics.data, ensure_ascii=False, indent=2), flush=True)
            diagnostics.data['upload'] = diagnostics.upload()
        report = diagnostics.save(home)
        print('诊断编号：' + diagnostics.data['id'], flush=True)
        if report:
            print(f'诊断文件：{report}', flush=True)
        else:
            print('诊断文件无法写入，请保留上方错误码和诊断编号。', flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(run_setup())
