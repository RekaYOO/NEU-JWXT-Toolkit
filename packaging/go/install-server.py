#!/usr/bin/env python3
"""Install and maintain the official Go toolbox on a Linux amd64 systemd host."""

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from urllib.parse import urlsplit


RELEASES = 'https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases'
APP = Path('/opt/neu-toolbox')
DATA = Path('/var/lib/neu-toolbox')
CONFIG = Path('/etc/neu-toolbox.env')
UNIT = Path('/etc/systemd/system/neu-toolbox.service')
MAINTENANCE = APP.parent / '.neu-toolbox-upgrades'
PENDING_MARKER = '.upgrade-pending'
DATABASE_FILES = ('toolbox.db', 'vault.key', 'toolbox.db-wal', 'toolbox.db-journal')
VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:[.-][0-9A-Za-z.-]+)?')
MAX_ARCHIVE = 512 * 1024**2
UNIT_COMMAND = '/opt/neu-toolbox/toolbox --mode remote-multi --listen 127.0.0.1:8000 --data /var/lib/neu-toolbox'


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        destination = urlsplit(new_url)
        if (destination.scheme != 'https' or destination.username is not None or destination.port is not None
                or destination.hostname not in ('github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com')):
            raise ValueError('GitHub 下载重定向地址不受信任。')
        return super().redirect_request(request, response, code, message, headers, new_url)


def download(url, destination, limit):
    opener = urllib.request.build_opener(HTTPSRedirect())
    with opener.open(url, timeout=30) as response, destination.open('xb') as output:
        remaining = limit
        while chunk := response.read(min(1024**2, remaining + 1)):
            remaining -= len(chunk)
            if remaining < 0:
                raise ValueError('GitHub 下载文件超过允许大小。')
            output.write(chunk)


def fetch_release_asset(relative_url, destination, limit):
    url = f'{RELEASES}/{relative_url}'
    try:
        download(url, destination, limit)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise ValueError(f'该 Release 缺少附件 {destination.name}。请查看 {RELEASES} 并确认所选发行包含 Go 版。') from error
        raise ValueError(f'GitHub 返回 HTTP {error.code}，下载失败。下载地址为 {url}') from error


def asset_metadata(manifest, version):
    metadata = json.loads(manifest.read_bytes())
    if not isinstance(metadata, dict) or metadata.get('schema_version') != 1 or metadata.get('runtime') != 'go-web':
        raise ValueError('发行清单不属于 Go 版。')
    actual_version = metadata.get('version')
    if not isinstance(actual_version, str) or not VERSION.fullmatch(actual_version) or (version and actual_version != version):
        raise ValueError('发行清单版本不匹配。')
    prefix = f'NEU-JWXT-Toolkit-{actual_version}-linux-amd64-go'
    assets = metadata.get('assets')
    asset = assets.get('linux-amd64') if isinstance(assets, dict) else None
    if not isinstance(asset, dict) or asset.get('name') != f'{prefix}.tar.gz':
        raise ValueError('发行清单缺少匹配的 Linux amd64 安装包。')
    size, digest = asset.get('size'), asset.get('sha256')
    if type(size) is not int or not 0 < size <= MAX_ARCHIVE or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('发行清单的大小或 SHA-256 无效。')
    return actual_version, prefix, size, digest


def extract_archive(archive, staging, prefix, version):
    seen = set()
    total = 0
    with tarfile.open(archive, 'r:gz') as stream:
        for member in stream:
            parts = member.name.split('/')
            total += member.size
            if (parts[0] != prefix or any(part in ('', '.', '..') or '\\' in part for part in parts)
                    or member.name in seen or len(seen) >= 10000 or not 0 <= total <= 1024**3
                    or not (member.isdir() or member.isfile())):
                raise ValueError('安装包条目无效。')
            seen.add(member.name)
            target = staging.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with stream.extractfile(member) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
    bundle = staging / prefix
    if (bundle.joinpath('VERSION').read_bytes() != f'{version}\n'.encode('ascii')
            or not bundle.joinpath('frontend/build/index.html').is_file()):
        raise ValueError('安装包版本或网页文件不匹配。')
    binary = bundle / 'toolbox'
    with binary.open('rb') as executable:
        if not executable.read(4) == b'\x7fELF':
            raise ValueError('安装包缺少 Linux 程序。')
    for directory, _, files in os.walk(bundle):
        Path(directory).chmod(0o755)
        for name in files:
            (Path(directory) / name).chmod(0o644)
    binary.chmod(0o755)
    return bundle


def systemctl(*args):
    return subprocess.run(['systemctl', *args], check=True, text=True, capture_output=True, timeout=45).stdout.strip()


def load_state(service):
    return systemctl('show', '--property=LoadState', '--value', service)


def check_installation(port):
    if any(os.path.lexists(path) for path in (APP, DATA, CONFIG, UNIT, Path(f'{UNIT}.d'))) or load_state(UNIT.name) != 'not-found':
        raise ValueError('已存在 Go 版程序、配置或数据。请使用已有安装的升级或恢复流程。')
    with socket.socket() as probe:
        try:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(('127.0.0.1', port))
        except OSError as error:
            raise ValueError(f'回环端口 {port} 不可用，请用 --port 指定空闲端口。') from error


def probe_service(port, version, mode, timeout=35, gated=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
        try:
            client.request('GET', '/api/health')
            response = client.getresponse()
            if response.status != 200:
                raise ValueError(f'健康接口返回 HTTP {response.status}。')
            health = json.loads(response.read(16384))
            if not isinstance(health, dict) or (health.get('backend'), health.get('version'), health.get('deployment_mode')) != ('go', version, mode):
                raise ValueError('监听端口返回了不同的服务、版本或部署模式。')
            client.request('GET', '/api/status')
            status = client.getresponse()
            rejection = json.loads(status.read(16384)) if status.status == 401 else None
            if not isinstance(rejection, dict) or rejection.get('code') != 'ACCESS_REQUIRED':
                raise ValueError('匿名访问保护未通过检查。')
            client.request('GET', '/')
            home = client.getresponse()
            if home.status != 200 or 'text/html' not in home.getheader('Content-Type', ''):
                raise ValueError('首页未通过检查。')
            client.close()
            if gated is not None:
                client.request('GET', '/api/access/status')
                if client.getresponse().status != (503 if gated else 200):
                    if gated:
                        raise ValueError('候选服务未处于升级保护状态。')
                    time.sleep(0.25)
                    continue
            systemctl('is-active', '--quiet', UNIT.name)
            return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            client.close()
        time.sleep(0.5)
    raise ValueError('服务未在限定时间内通过健康检查。')


@contextmanager
def maintenance_lock():
    if sys.platform != 'linux' or platform.machine() != 'x86_64' or os.geteuid() != 0:
        raise ValueError('请在 Linux amd64 的 systemd 主机上使用 sudo 运行。')
    import fcntl

    with os.fdopen(os.open('/run/neu-toolbox-install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'rb') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('已有安装或维护进程正在运行。') from error
        yield


def stage_release(args, staging):
    manifest = args.manifest or staging / 'go-release-manifest.json'
    if args.manifest is None:
        release = f'download/v{args.version}' if args.version else 'latest/download'
        print('正在读取 GitHub Release 清单。', flush=True)
        fetch_release_asset(f'{release}/go-release-manifest.json', manifest, 256 * 1024)
    version, prefix, size, digest = asset_metadata(manifest, args.version)
    archive = staging / f'{prefix}.tar.gz'
    if args.archive:
        with args.archive.open('rb') as source, archive.open('xb') as output:
            if os.fstat(source.fileno()).st_size != size:
                raise ValueError('安装包大小不匹配。')
            shutil.copyfileobj(source, output)
    else:
        print(f'正在下载 v{version} 的 Linux amd64 安装包。', flush=True)
        fetch_release_asset(f'download/v{version}/{archive.name}', archive, size)
    print('正在校验安装包。', flush=True)
    with archive.open('rb') as package:
        if archive.stat().st_size != size or hashlib.file_digest(package, 'sha256').hexdigest() != digest:
            raise ValueError('安装包大小或 SHA-256 校验失败。')
    return version, extract_archive(archive, staging, prefix, version)


def install(args):
    import pwd

    with maintenance_lock():
        legacy = load_state('neu-jwxt-toolkit.service') != 'not-found'
        port = args.port or (18000 if legacy else 8000)
        check_installation(port)
        if legacy:
            print(f'检测到旧版服务。Go 版将在 127.0.0.1:{port} 试运行。', flush=True)
        with tempfile.TemporaryDirectory(prefix='.neu-toolbox-install-', dir=APP.parent) as temporary:
            version, bundle = stage_release(args, Path(temporary))
            unit_text = bundle.joinpath(UNIT.name).read_text(encoding='utf-8')
            if unit_text.count(f'ExecStart={UNIT_COMMAND}\n') != 1:
                raise ValueError('安装包内的 systemd 单元格式不匹配。')
            command = UNIT_COMMAND.replace('remote-multi', args.mode).replace(':8000', f':{port}')
            unit_text = unit_text.replace(f'ExecStart={UNIT_COMMAND}\n', f'ExecStart={command}\n')
            check_installation(port)
            try:
                pwd.getpwnam('neu-toolbox')
            except KeyError:
                subprocess.run(['useradd', '--system', '--user-group', '--no-create-home', '--shell', '/usr/sbin/nologin', 'neu-toolbox'], check=True)
            bundle.rename(APP)
            unit_created = False
            try:
                with CONFIG.open('x', encoding='ascii') as config:
                    config.write(f'NEU_JWXT_PUBLIC_ORIGIN={args.origin}\n')
                    for role in ('ACCESS', 'ADMIN'):
                        config.write(f'NEU_JWXT_{role}_PASSWORD={secrets.token_urlsafe(24)}\n')
                    if args.admin_account:
                        config.write(f'NEU_JWXT_ADMIN_ACCOUNT={args.admin_account}\n')
                with UNIT.open('x', encoding='utf-8') as unit:
                    unit_created = True
                    unit.write(unit_text)
                UNIT.chmod(0o644)
                print('正在启动服务并检查健康状态。', flush=True)
                systemctl('daemon-reload')
                systemctl('start', UNIT.name)
                probe_service(port, version, args.mode)
                if not legacy:
                    systemctl('enable', UNIT.name)
            except (ValueError, OSError, subprocess.SubprocessError, KeyboardInterrupt):
                if unit_created:
                    try:
                        systemctl('stop', UNIT.name)
                    except (OSError, subprocess.SubprocessError):
                        print('无法确认新服务已停止，请检查 systemctl status neu-toolbox。', file=sys.stderr)
                print(f'已保留本次安装文件以便检查。日志可用 journalctl -u {UNIT.name} 查看。', file=sys.stderr)
                raise
    print(f'Go {version} 已启动。反向代理上游为 127.0.0.1:{port}。')
    print(f'首次登录密码保存在 {CONFIG}，仅 root 可读。')
    if legacy:
        print('检测到旧版服务。新版以试运行方式启动，验收后可启用开机启动。')


def service_layout():
    properties = ('FragmentPath', 'DropInPaths', 'Type', 'User', 'Group', 'DynamicUser', 'WorkingDirectory',
                  'EnvironmentFiles', 'Environment', 'PassEnvironment', 'RootDirectory', 'RootImage',
                  'BindPaths', 'BindReadOnlyPaths', 'TemporaryFileSystem', 'ExtensionDirectories', 'ExtensionImages',
                  'ExecStart', 'ExecStartPre', 'ExecStartPost', 'ExecStop', 'ExecStopPost', 'ExecCondition')
    effective = dict(line.split('=', 1) for line in systemctl('show', '--all', UNIT.name,
                     *(f'--property={name}' for name in properties)).splitlines())
    required = {'FragmentPath': str(UNIT), 'Type': 'simple', 'User': 'neu-toolbox', 'Group': 'neu-toolbox', 'DynamicUser': 'no',
                'WorkingDirectory': str(APP), 'EnvironmentFiles': f'{CONFIG} (ignore_errors=no)'}
    empty = set(properties) - required.keys() - {'ExecStart'}
    command = re.fullmatch(r'\{ path=/opt/neu-toolbox/toolbox ; argv\[\]=/opt/neu-toolbox/toolbox '
                           r'--mode (remote-single|remote-multi) --listen 127\.0\.0\.1:([0-9]{1,5}) '
                           r'--data /var/lib/neu-toolbox ; [^{}]+ \}', effective.get('ExecStart', ''))
    if (any(effective.get(name) != value for name, value in required.items()) or any(effective.get(name) for name in empty)
            or not command or not 1 <= int(command[2]) <= 65535 or os.path.lexists(f'{UNIT}.d')):
        raise ValueError('该 systemd 服务使用了自定义布局。请参照部署指南手工更新。')
    file_info = CONFIG.lstat()
    if not stat.S_ISREG(file_info.st_mode) or file_info.st_uid != 0 or file_info.st_mode & 0o077:
        raise ValueError('环境文件必须是仅 root 可读的普通文件。')
    for line in CONFIG.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';')):
            continue
        assignment = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)', line)
        if not assignment or line.endswith('\\'):
            raise ValueError('自动维护要求环境文件使用单行赋值。')
        name, value = assignment.groups()
        if value.startswith(('"', "'")) and (len(value) < 2 or not value.endswith(value[0]) or value[-2] == '\\'):
            raise ValueError('自动维护要求环境文件使用单行赋值。')
        if name == 'NEU_JWXT_DATA_DIR':
            raise ValueError('自动维护要求使用默认数据目录，不能设置 NEU_JWXT_DATA_DIR。')
    if any(line.startswith('NEU_JWXT_DATA_DIR=') for line in systemctl('show-environment').splitlines()):
        raise ValueError('systemd 环境覆盖了数据目录。')
    return int(command[2]), command[1]


def service_user():
    import pwd

    return pwd.getpwnam('neu-toolbox')


def run_toolbox(program, *arguments, env=None, required=True):
    account = service_user()
    result = subprocess.run([program / 'toolbox', *arguments], env=env or {}, user=account.pw_uid, group=account.pw_gid,
                            extra_groups=[], cwd=program, capture_output=True, text=True, timeout=180, umask=0o077)
    if required and result.returncode:
        raise ValueError(f'工具箱维护命令 {arguments[0]} 执行失败。')
    return result.stdout.strip() if result.returncode == 0 else None


def bundled_version(program):
    info = program.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('工具箱程序目录必须由 root 管理。')
    version = run_toolbox(program, '--version')
    if not VERSION.fullmatch(version or ''):
        raise ValueError('无法核对已安装的版本。')
    return version


def version_order(version):
    core, separator, prerelease = version.partition('-')
    if (not re.fullmatch(r'(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*)){2}', core) or len(version) > 128
            or (separator and not prerelease)):
        raise ValueError('升级需要使用语义化发行版本，例如 2.3.0 或 2.3.0-rc.1。')
    identifiers = []
    if prerelease:
        for part in prerelease.split('.'):
            if not re.fullmatch(r'[0-9A-Za-z-]+', part) or re.fullmatch(r'0[0-9]+', part):
                raise ValueError('预发行版本格式无效。')
            identifiers.append((0, int(part)) if part.isascii() and part.isdigit() else (1, part))
    return tuple(map(int, core.split('.'))), not prerelease, identifiers


def fsync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def move_directory(source, target):
    source.rename(target)
    fsync_dir(source.parent)
    fsync_dir(target.parent)


def sync_program(program):
    for directory, _, files in os.walk(program, topdown=False):
        for name in files:
            with (Path(directory) / name).open('rb') as file:
                os.fsync(file.fileno())
        fsync_dir(directory)


def read_state():
    MAINTENANCE.mkdir(mode=0o700, exist_ok=True)
    info = MAINTENANCE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError(f'{MAINTENANCE} 必须是仅 root 可读的目录。')
    fsync_dir(MAINTENANCE.parent)
    state_file = MAINTENANCE / 'state.json'
    state = json.loads(state_file.read_bytes()) if state_file.exists() else {}
    if not isinstance(state, dict) or any(value is not None and (not isinstance(value, str)
            or not re.fullmatch(r'txn-[a-zA-Z0-9_-]+', value)) for value in state.values()):
        raise ValueError('维护状态文件无效。')
    return state


def write_state(state):
    staging = MAINTENANCE / '.state-new'
    with staging.open('w', encoding='ascii') as output:
        json.dump(state, output)
        output.flush()
        os.fsync(output.fileno())
    staging.replace(MAINTENANCE / 'state.json')
    fsync_dir(MAINTENANCE)


@contextmanager
def data_lock():
    import fcntl

    if not stat.S_ISDIR(DATA.lstat().st_mode) or os.path.lexists(DATA / '.restore-in-progress'):
        raise ValueError('数据目录不存在、使用了链接或包含尚未完成的恢复。')
    descriptor = os.open(DATA / 'runtime.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise ValueError('数据锁文件无效。')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('数据仍被其他进程使用。') from error
        yield


def copy_database(source, target, owner=None, replace=False):
    source_directory = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, name in enumerate(DATABASE_FILES):
            destination = target / name
            try:
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_directory)
            except FileNotFoundError:
                if index < 2:
                    raise ValueError('数据库或密钥缺失。')
                if replace:
                    destination.unlink(missing_ok=True)
                continue
            with os.fdopen(descriptor, 'rb') as original:
                info = os.fstat(original.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (name == 'vault.key' and info.st_size != 32):
                    raise ValueError(f'{name} 文件无效。')
                with tempfile.NamedTemporaryFile(dir=target, prefix='.upgrade-', delete=False) as output:
                    staged = Path(output.name)
                    try:
                        shutil.copyfileobj(original, output)
                        output.flush()
                        if owner is not None:
                            os.fchown(output.fileno(), owner.pw_uid, owner.pw_gid)
                        os.fsync(output.fileno())
                        staged.replace(destination)
                    finally:
                        staged.unlink(missing_ok=True)
        if replace:
            (target / 'toolbox.db-shm').unlink(missing_ok=True)
        fsync_dir(target)
    finally:
        os.close(source_directory)


def stage_rollback(previous, staging):
    program = staging / 'program'
    shutil.copytree(previous / 'program', program)
    (program / PENDING_MARKER).unlink(missing_ok=True)
    account = service_user()
    working = staging / 'database'
    working.mkdir(mode=0o700)
    source = working / 'source'
    source.mkdir(mode=0o700)
    copy_database(previous / 'data', source, account)
    for directory in (source, working):
        os.chown(directory, account.pw_uid, account.pw_gid)
    archive, restored = working / 'rollback.age', working / 'restored'
    environment = {'NEU_JWXT_BACKUP_PASSWORD': secrets.token_urlsafe(32)}
    run_toolbox(program, 'backup', '--data', source, '--file', archive, env=environment)
    run_toolbox(program, 'restore', '--data', restored, '--file', archive, env=environment)
    return program, restored


def installed_health(version, layout, gated=None):
    port, mode = layout
    probe_service(port, version, mode, gated=gated)


def finish_activation(state, layout):
    transaction = MAINTENANCE / state['activate']
    if (transaction / 'next').exists():
        if os.path.lexists(APP):
            raise ValueError('程序目录已被占用，无法完成切换。')
        move_directory(transaction / 'next', APP)
    (APP / PENDING_MARKER).unlink(missing_ok=True)
    fsync_dir(APP)
    state['activate'] = None
    write_state(state)
    systemctl('start', UNIT.name)
    version = bundled_version(APP)
    installed_health(version, layout, gated=False)
    print(f'Go {version} 已通过健康检查。')


def recover_pending(state, layout):
    transaction = MAINTENANCE / state['pending']
    systemctl('stop', UNIT.name)
    if (transaction / 'program').exists():
        with data_lock():
            if os.path.lexists(APP):
                move_directory(APP, transaction / 'failed-program')
            copy_database(transaction / 'data', DATA, service_user(), replace=True)
            move_directory(transaction / 'program', APP)
    try:
        systemctl('reset-failed', UNIT.name)
    except subprocess.CalledProcessError:
        pass
    systemctl('start', UNIT.name)
    version = bundled_version(APP)
    installed_health(version, layout, gated=False)
    state['pending'] = None
    write_state(state)
    print(f'已恢复并验证 Go {version}。', flush=True)


def switch_release(state, layout, program, version, restored=None, gated=True):
    transaction = Path(tempfile.mkdtemp(prefix='txn-', dir=MAINTENANCE))
    snapshot = transaction / 'data'
    snapshot.mkdir(mode=0o700)
    if gated:
        with (program / PENDING_MARKER).open('x') as marker:
            marker.write('Waiting for the administrator to confirm startup.\n')
    sync_program(program)
    move_directory(program, transaction / 'next')
    state['pending'] = transaction.name
    write_state(state)
    try:
        print('正在停止服务并保存升级前的程序、数据库和密钥。', flush=True)
        systemctl('stop', UNIT.name)
        with data_lock():
            copy_database(DATA, snapshot)
            move_directory(APP, transaction / 'program')
            if restored is not None:
                copy_database(restored, DATA, service_user(), replace=True)
            if gated:
                move_directory(transaction / 'next', APP)
        if gated:
            systemctl('start', UNIT.name)
            installed_health(version, layout, gated=True)
    except (ValueError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        try:
            recover_pending(state, layout)
        except (ValueError, OSError, subprocess.SubprocessError) as recovery_error:
            raise ValueError(f'切换失败且恢复未完成，请运行 install.sh recover。{recovery_error}') from error
        raise ValueError(f'切换失败，原版本已重新运行。{error}') from error
    state.update(pending=None, previous=transaction.name, activate=transaction.name)
    write_state(state)
    print(f'上一版检查点保存在 {transaction}。', flush=True)
    finish_activation(state, layout)


def maintain(args):
    with maintenance_lock():
        layout = service_layout()
        state = read_state()
        if args.action == 'status':
            print(f'当前程序版本：{bundled_version(APP)}' if os.path.lexists(APP) else '当前程序目录待恢复。')
            print(f'服务状态：{systemctl("show", "--property=ActiveState", "--value", UNIT.name)}')
            if state.get('previous'):
                print(f'最近检查点：{MAINTENANCE / state["previous"]}')
            if state.get('pending') or state.get('activate'):
                print('存在未完成的维护操作，可运行 install.sh recover。')
            return
        if args.action == 'recover':
            if state.get('pending'):
                recover_pending(state, layout)
            elif state.get('activate'):
                finish_activation(state, layout)
            else:
                systemctl('start', UNIT.name)
                installed_health(bundled_version(APP), layout, gated=False)
                print('服务已通过健康检查。')
            return
        if state.get('pending') or state.get('activate') or os.path.lexists(APP / PENDING_MARKER):
            raise ValueError('存在未完成的维护操作，请先运行 install.sh recover。')
        current = bundled_version(APP)
        with tempfile.TemporaryDirectory(prefix='.neu-toolbox-stage-', dir=APP.parent) as temporary:
            staging = Path(temporary)
            staging.chmod(0o711)
            if args.action == 'update':
                version, program = stage_release(args, staging)
                if version_order(version) == version_order(current):
                    print(f'当前已安装 Go {current}。')
                    return
                if version_order(version) < version_order(current):
                    raise ValueError('update 仅接受更新版本。回退请使用 rollback。')
                if run_toolbox(program, 'linux-upgrade-protocol', required=False) != '1':
                    raise ValueError('候选版本不支持受保护的 Linux 升级。')
                installed_health(current, layout, gated=False)
                print('正在用数据库副本检查与新版的兼容性。', flush=True)
                run_toolbox(program, 'check-upgrade', DATA)
                switch_release(state, layout, program, version)
            else:
                if not state.get('previous'):
                    raise ValueError('没有可用的上一版本检查点。')
                program, restored = stage_rollback(MAINTENANCE / state['previous'], staging)
                version = bundled_version(program)
                gated = run_toolbox(program, 'linux-upgrade-protocol', required=False) == '1'
                if not gated:
                    print('上一版本使用旧启动方式，将在保存当前数据后直接启动并检查。', flush=True)
                switch_release(state, layout, program, version, restored, gated)


def add_release_options(parser):
    parser.add_argument('--version', help='指定 GitHub Release 标签，例如 v2.3.0。默认使用最新正式发行。')
    parser.add_argument('--archive', type=Path, help='离线使用的可信 Linux 归档')
    parser.add_argument('--manifest', type=Path, help='与离线归档配套的官方发行清单')


def validate_release_options(parser, args):
    if args.version:
        args.version = args.version.removeprefix('v')
        if not VERSION.fullmatch(args.version):
            parser.error('--version 必须是发行标签，例如 v2.3.0。')
    if (args.archive is None) != (args.manifest is None):
        parser.error('离线操作需要同时提供 --archive 和 --manifest。')


def install_options(parser, args):
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error('--port 必须为 1–65535。')
    if args.admin_account is not None and not re.fullmatch(r'[0-9]{1,64}', args.admin_account):
        parser.error('--admin-account 必须是数字学号。')
    if args.origin is None:
        if not sys.stdin.isatty():
            parser.error('非交互安装需要指定 --origin。')
        try:
            args.origin = input('请输入工具箱的公开 HTTPS 地址，例如 https://toolbox.example.com\n> ').strip()
        except (EOFError, KeyboardInterrupt):
            parser.exit(130, '安装已中止。\n')
    try:
        origin = urlsplit(args.origin)
        if (not re.fullmatch(r'https://[A-Za-z0-9.\[\]:-]+/?', args.origin) or not origin.hostname
                or origin.port == 0):
            raise ValueError()
    except ValueError:
        parser.error('--origin 必须是没有路径的 HTTPS 地址。')


def main():
    parser = argparse.ArgumentParser(prog='install.sh', description='从项目 GitHub Release 安装并维护 Linux Go 工具箱。')
    commands = parser.add_subparsers(dest='action', required=True)
    install_parser = commands.add_parser('install', help='首次安装，可省略 install 子命令。')
    install_parser.add_argument('--origin', help='工具箱的公开 HTTPS 地址。交互终端可按提示填写。')
    install_parser.add_argument('--mode', choices=('remote-single', 'remote-multi'), default='remote-multi')
    install_parser.add_argument('--port', type=int)
    install_parser.add_argument('--admin-account', help='指定默认管理员的学号并自动准入该账号。')
    add_release_options(install_parser)
    update_parser = commands.add_parser('update', help='校验并升级现有 Go 服务。')
    add_release_options(update_parser)
    rollback_parser = commands.add_parser('rollback', help='切换到上一版本并恢复检查点数据。')
    rollback_parser.add_argument('--restore-data', required=True, action='store_true', help='确认恢复旧数据并另存当前数据。')
    commands.add_parser('status', help='查看服务及检查点状态。')
    commands.add_parser('recover', help='恢复中断的维护操作或重新验证服务。')
    arguments = sys.argv[1:]
    if not arguments or (arguments[0].startswith('-') and arguments[0] not in ('-h', '--help')):
        arguments = ['install', *arguments]
    args = parser.parse_args(arguments)
    if args.action in ('install', 'update'):
        validate_release_options(install_parser if args.action == 'install' else update_parser, args)
    if args.action == 'install':
        install_options(install_parser, args)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, interrupt)
        os.umask(0o077)
        if args.action == 'install':
            install(args)
        else:
            maintain(args)
    except KeyboardInterrupt:
        parser.exit(130, '操作已中止。\n')
    except (ValueError, OSError, subprocess.SubprocessError, tarfile.TarError, KeyError) as error:
        detail = getattr(error, 'stderr', None) or str(error)
        parser.exit(1, f'操作失败。{detail.strip()}\n')


if __name__ == '__main__':
    main()
