#!/usr/bin/env python3
"""Preview or apply legacy Python data import into a Linux Go installation."""

import argparse
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import pwd
import stat
import subprocess
import tempfile


TOOLBOX = '/opt/neu-toolbox/toolbox'
USER = 'neu-toolbox'
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
MIB = 1024**2
ORDINARY_FILES = (
    'config.json', 'research_training_favorites.json', 'course_selection_catalog_history.json',
    'course_selection_automation_settings.json', 'course_selection_tasks.json',
)
SHARED_FILES = ('system_mail_config.json', 'grade_tracking_config.json', 'auth_recovery_config.json')


@contextmanager
def open_directory(parent, parts):
    descriptor = os.dup(parent)
    try:
        for part in parts:
            child = os.open(part, DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def snapshot(source, destination, account, terms, shared, group):
    owner = hashlib.sha256(account.encode()).hexdigest()
    remaining = 256 * MIB
    count = 0
    root = os.open(source, DIRECTORY)
    try:
        def copy_file(directory, relative):
            nonlocal count, remaining
            descriptor = os.open(relative.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
            with os.fdopen(descriptor, 'rb') as stream:
                info = os.fstat(stream.fileno())
                limit = 128 * MIB if relative.name == 'course_selection_catalog_history.json' else 4 * MIB
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_size > min(limit, remaining) or count >= 1024):
                    raise ValueError(f'旧资料类型或大小超出限制：{relative}')
                payload = stream.read(info.st_size + 1)
                if len(payload) != info.st_size:
                    raise ValueError(f'旧文件读取时发生变化：{relative}')
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            count += 1
            remaining -= len(payload)

        names = [Path(name) for name in (SHARED_FILES if shared else ORDINARY_FILES)]
        if not shared:
            names.append(Path('gpa_preferences', f'{owner}.json'))
            names.extend(Path('timetable_agendas', owner, hashlib.sha256(term.encode()).hexdigest() + '.json') for term in terms)
            for relative in (Path('成绩'), Path('成绩', 'gpa_simulations', owner[:24])):
                try:
                    with open_directory(root, relative.parts) as directory:
                        for name in sorted(os.listdir(directory)):
                            if name.endswith('.json'):
                                copy_file(directory, relative / name)
                except FileNotFoundError:
                    continue
        for relative in names:
            try:
                with open_directory(root, relative.parts[:-1]) as directory:
                    copy_file(directory, relative)
            except FileNotFoundError:
                continue
    finally:
        os.close(root)
    for directory, _, files in os.walk(destination):
        os.chown(directory, 0, group)
        os.chmod(directory, 0o750)
        for name in files:
            target = Path(directory) / name
            os.chown(target, 0, group)
            target.chmod(0o640)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True, help='已在新版启用的账号')
    parser.add_argument('--source', type=Path, default=Path('/var/lib/neu-jwxt-toolkit'))
    parser.add_argument('--data', type=Path, default=Path('/var/lib/neu-toolbox'))
    parser.add_argument('--terms', default='', help='要导入个人日程的学期代码，以逗号分隔')
    parser.add_argument('--shared-settings', action='store_true', help='仅导入共享设置，归属指定账号')
    parser.add_argument('--apply-digest', help='预览中显示的 digest')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('请使用 sudo 运行。')
    account = args.account.strip()
    terms = [term.strip() for term in args.terms.split(',')] if args.terms else []
    if not account or (args.shared_settings and terms):
        parser.error('请输入目标账号。共享设置模式不接受 --terms。')
    try:
        os.umask(0o077)
        source = args.source.resolve(strict=True)
        data = args.data.resolve(strict=True)
        if source == data or source.is_relative_to(data) or data.is_relative_to(source):
            raise ValueError('旧版目录与新版数据目录必须独立。')
        if (source / 'toolbox.db').exists():
            raise ValueError('来源目录已包含 Go 数据库。')
        group = pwd.getpwnam(USER).pw_gid
        with tempfile.TemporaryDirectory(prefix='neu-toolbox-import-', dir='/var/tmp') as temporary:
            staged = Path(temporary)
            snapshot(source, staged, account, terms, args.shared_settings, group)
            command = ['runuser', '-u', USER, '--', TOOLBOX, 'import-legacy', '--source', str(staged),
                       '--data', str(data), '--account', account]
            if args.shared_settings:
                command += ['--shared-settings-owner', account]
            if terms:
                command += ['--terms', ','.join(terms)]
            if args.apply_digest:
                command += ['--apply-digest', args.apply_digest]
            return subprocess.run(command, check=False).returncode
    except (KeyError, OSError, ValueError) as error:
        parser.exit(1, f'迁移失败。{error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
