"""Verify Go bundle contents and optionally smoke-test the native executable."""

import argparse
import hashlib
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import urllib.error
import zipfile


def check_members(entries, prefix):
    seen = set()
    total = 0
    for name, size in entries:
        parts = name.rstrip('/').split('/')
        if (len(seen) >= 10000 or not parts or parts[0] != prefix or
                any(not part or part in {'.', '..'} or part.endswith(('.', ' ')) or
                    re.search(r'[<>:"|?*\\\x00-\x1f]', part) or
                    re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I)
                    for part in parts)):
            raise ValueError('Invalid archive member')
        key = '/'.join(parts).casefold()
        filename = parts[-1].casefold()
        if (filename in {'vault.key', 'session.pkl', 'credentials.json', 'session.json',
                         'grade_tracking_snapshot.json', 'system_mail_outbox.json', '.env'} or filename.startswith('.env.') or
                filename.endswith(('.age', '.pem', '.pfx', '.db', '.sqlite', '.sqlite3',
                                   '.db-wal', '.db-shm', '.db-journal', '.sqlite-wal', '.sqlite-shm',
                                   '.sqlite-journal', '.sqlite3-wal', '.sqlite3-shm', '.sqlite3-journal'))):
            raise ValueError('User data found in bundle')
        if key in seen:
            raise ValueError('Duplicate archive member')
        seen.add(key)
        total += size
        if size < 0 or size > 256 * 1024**2 or total > 1024**3:
            raise ValueError('Archive exceeds extraction limits')


def inspect_archive(path, version, platform):
    prefix = f"NEU-JWXT-Toolkit-{version}-{platform}-amd64-go"
    if platform == "windows":
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if any(stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR) for item in members):
                raise ValueError('Unexpected special file in ZIP')
            check_members(((item.filename, item.file_size) for item in members), prefix)
            names = archive.namelist()
            contents = {name: archive.read(name) for name in names if not name.endswith("/")}
    else:
        with tarfile.open(path) as archive:
            members = archive.getmembers()
            if any(not (member.isfile() or member.isdir()) for member in members):
                raise ValueError("Unexpected special file in archive")
            for member in members:
                expected = 0o755 if member.isdir() or member.name == f"{prefix}/toolbox" else 0o644
                if member.mode != expected:
                    raise ValueError(f"Invalid Linux permissions for {member.name}: {member.mode:o}, expected {expected:o}")
            check_members(((item.name, item.size) for item in members), prefix)
            names = [member.name for member in members]
            contents = {member.name: archive.extractfile(member).read() for member in members if member.isfile()}
    if len(names) != len(set(names)):
        raise ValueError("Duplicate archive entries")
    for name in names:
        parsed = PurePosixPath(name)
        if parsed.is_absolute() or ".." in parsed.parts or parsed.parts[0] != prefix or "\\" in name:
            raise ValueError("Unsafe archive path")
    executable = "toolbox.exe" if platform == "windows" else "toolbox"
    required = [executable, "VERSION", "LICENSE", "THIRD_PARTY_NOTICE.md", "README.md", "BACKUP.md", "IMPORT.md",
                "frontend/build/index.html", "frontend/build/asset-manifest.json", "licenses/dependencies.json"]
    if platform == "windows":
        required.extend(["app.ico", "启动工具箱.cmd", "启动工具箱.exe"])
    else:
        required.append("neu-toolbox.service")
    for theme in ("sakura", "blue", "garden"):
        filename = f"themes/{theme}.json"
        expected = Path(__file__).resolve().parents[1] / "frontend/src/theme/templates" / f"{theme}.json"
        if contents.get(f"{prefix}/{filename}") != expected.read_bytes():
            raise ValueError(f"Missing or mismatched theme: {theme}")
    for name in required:
        if not contents.get(f"{prefix}/{name}"):
            raise ValueError(f"Missing required bundle file: {name}")
    if contents[f"{prefix}/VERSION"] != (version + "\n").encode("ascii"):
        raise ValueError("Version mismatch")
    if platform == "linux" and b"\r" in contents[f"{prefix}/neu-toolbox.service"]:
        raise ValueError("Unexpected carriage return in Linux service unit")
    if platform == "windows":
        launcher = contents[f"{prefix}/启动工具箱.cmd"].replace(b"\r\n", b"")
        if b"\n" in launcher or b"\r" in launcher:
            raise ValueError("Invalid Windows launcher line endings")
    magic = b"MZ" if platform == "windows" else b"\x7fELF"
    if not contents[f"{prefix}/{executable}"].startswith(magic):
        raise ValueError("Wrong executable platform")
    dependencies = json.loads(contents[f"{prefix}/licenses/dependencies.json"])
    for dependency in dependencies:
        for name in dependency["license_files"]:
            if not contents.get(f"{prefix}/licenses/{name}"):
                raise ValueError("Missing dependency license")
    manifest = json.loads(contents[f"{prefix}/frontend/build/asset-manifest.json"])
    for name in manifest["files"].values():
        if f"{prefix}/frontend/build/{name.lstrip('/')}" not in contents:
            raise ValueError("Missing frontend asset")
    return {"entries": len(names), "dependencies": len(dependencies)}


def smoke_remote(executable, temporary, options, mode):
    def session_cookie(headers, name, lifetime):
        fields = headers.get_all('Set-Cookie', [])
        if len(fields) != 1:
            raise ValueError(f'Expected one {name} cookie')
        parsed = SimpleCookie(fields[0])
        if set(parsed) != {name}:
            raise ValueError(f'Unexpected cookie name for {name}')
        cookie = parsed[name]
        if (not cookie.value or not cookie['secure'] or not cookie['httponly'] or
                cookie['samesite'].lower() != 'lax' or cookie['path'] != '/' or
                cookie['domain'] or cookie['max-age'] != str(lifetime)):
            raise ValueError(f'Unexpected scope or lifetime for {name}')
        return f'{name}={cookie.coded_value}'

    access, admin = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    environment = {**os.environ, 'NEU_JWXT_DATA_DIR': str(temporary / mode),
                   'NEU_JWXT_PUBLIC_ORIGIN': 'https://toolbox.example.invalid',
                   'NEU_JWXT_ACCESS_PASSWORD': access, 'NEU_JWXT_ADMIN_PASSWORD': admin}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    invalid = subprocess.run([str(executable), '--mode', mode, '--init-only'], cwd=temporary,
                             env={**environment, 'NEU_JWXT_ACCESS_PASSWORD': secrets.token_urlsafe(24),
                                  'NEU_JWXT_ADMIN_PASSWORD': 'short'}, capture_output=True, timeout=30, **options)
    if invalid.returncode == 0 or b'8 to 256' not in invalid.stderr:
        raise ValueError('First initialization did not reject the invalid administrator password')
    with sqlite3.connect(temporary / mode / 'toolbox.db') as database:
        if database.execute("SELECT count(*) FROM site_settings WHERE setting_key IN ('credential-access','credential-admin')").fetchone()[0]:
            raise ValueError('Failed initialization persisted partial site credentials')
    for iteration in range(2):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        def call(path, body=None, cookies='', expected=200, method=None):
            request = urllib.request.Request(origin + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                             headers={'Content-Type': 'application/json', 'Cookie': cookies})
            try:
                response = opener.open(request, timeout=4)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                if response.status != expected:
                    raise ValueError(f'{path}: expected {expected}, received {response.status}')
                return json.load(response), response.headers

        with (temporary / f'{mode}.log').open('ab') as log:
            process = subprocess.Popen([str(executable), '--mode', mode, '--listen', f'127.0.0.1:{port}'],
                                       cwd=temporary, env=environment, stdout=log, stderr=log, **options)
            try:
                deadline = time.monotonic() + 20
                while True:
                    if process.poll() is not None:
                        raise ValueError('Remote service exited before readiness')
                    try:
                        health, _ = call('/api/health')
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.1)
                if type(health.get('mobile_api_version')) is not int or health['mobile_api_version'] != 1:
                    raise ValueError('Remote health response incompatible with Android client')
                if health.get('shutdown_token') or call('/api/admin/accounts', expected=401)[0].get('error_code') != 'ACCESS_REQUIRED':
                    raise ValueError('Remote access boundary mismatch')
                _, headers = call('/api/access/login', {'password': access})
                access_cookie = session_cookie(headers, 'neu_jwxt_access', 7 * 24 * 3600)
                if call('/api/admin/accounts', cookies=access_cookie, expected=401)[0].get('error_code') != 'ADMIN_REQUIRED':
                    raise ValueError('Access cookie grants administrative access')
                _, headers = call('/api/admin/login', {'password': admin}, access_cookie)
                admin_cookie = session_cookie(headers, 'neu_toolbox_admin', 8 * 3600)
                if call('/api/admin/accounts', cookies=admin_cookie, expected=401)[0].get('error_code') != 'ACCESS_REQUIRED':
                    raise ValueError('Administrator cookie unexpectedly granted site access')
                cookies = access_cookie + '; ' + admin_cookie
                if iteration == 0:
                    call('/api/admin/accounts', {'account': '20990001'}, cookies)
                    if mode == 'remote-single':
                        call('/api/admin/accounts', {'account': '20990002'}, cookies, expected=409)
                    else:
                        call('/api/admin/accounts', {'account': '20990002'}, cookies)
                    call('/api/admin/accounts/20990001/disable', cookies=cookies, method='POST')
                    if mode == 'remote-single':
                        call('/api/admin/accounts', {'account': '20990002'}, cookies)
                if mode == 'remote-single':
                    call('/api/admin/accounts/20990001/enable', cookies=cookies, method='POST', expected=409)
                listing, _ = call('/api/admin/accounts', cookies=cookies)
                statuses = {row['account']: row['enabled'] for row in listing['accounts']}
                if listing['deployment_mode'] != mode or statuses != {'20990001': False, '20990002': True}:
                    raise ValueError('Admission state mismatch')
                call('/api/admin/logout', cookies=cookies, method='POST')
                call('/api/admin/accounts', cookies=cookies, expected=401)
            finally:
                process.terminate()
                process.wait(timeout=15)
        if iteration == 0:
            for incompatible in ('local-single', 'remote-single' if mode == 'remote-multi' else 'remote-multi'):
                attempt = subprocess.run([str(executable), '--init-only', '--mode', incompatible],
                                         cwd=temporary, env=environment, capture_output=True, timeout=30, **options)
                if attempt.returncode == 0 or b'deployment mode differs from stored data' not in attempt.stderr:
                    raise ValueError(f'{mode} data did not reject initialization as {incompatible}')


def run_desktop_cycle(executable, temporary, environment, options, version):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with (temporary / "runtime.log").open("wb") as log:
        process = subprocess.Popen([str(executable), "--listen", f"127.0.0.1:{port}", "--mode", "local-single", "--tray=false", "--open-browser=false"], cwd=temporary, env=environment, stdout=log, stderr=log, **options)
        try:
            deadline = time.monotonic() + 20
            while True:
                if process.poll() is not None:
                    raise ValueError(f"Service exited; inspect {temporary / 'runtime.log'}")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                        health = json.load(response)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.1)
            if health.get("version") != version or health.get("backend") != "go":
                raise ValueError("Unexpected health response")
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
                if response.status != 200 or b'<div id="root"' not in response.read():
                    raise ValueError("Frontend unavailable")
            token = health.get("shutdown_token")
            if not isinstance(token, str) or len(token) != 64:
                raise ValueError("Desktop shutdown token unavailable")
            request = urllib.request.Request(f"http://127.0.0.1:{port}/api/runtime/shutdown", data=b"", method="POST",
                                             headers={"X-NEU-Shutdown-Token": token, "Origin": f"http://127.0.0.1:{port}"})
            with urllib.request.urlopen(request, timeout=3) as response:
                if json.load(response).get("success") is not True:
                    raise ValueError("Shutdown was not acknowledged")
            if process.wait(timeout=15) != 0:
                raise ValueError("Desktop did not shut down cleanly")
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)


def smoke(output, version):
    platform = "windows" if sys.platform == "win32" else "linux" if sys.platform.startswith("linux") else None
    if platform is None:
        raise ValueError("Native smoke supports Windows and Linux")
    temporary = Path(tempfile.mkdtemp(prefix="neu-go-bundle-smoke-"))
    prefix = f"NEU-JWXT-Toolkit-{version}-{platform}-amd64-go"
    extracted = temporary / "bundle"
    if platform == "windows":
        with zipfile.ZipFile(output / (prefix + ".zip")) as archive:
            archive.extractall(extracted)
    else:
        with tarfile.open(output / (prefix + ".tar.gz")) as archive:
            archive.extractall(extracted, filter="data")
    executable = extracted / prefix / ("toolbox.exe" if platform == "windows" else "toolbox")
    environment = {**os.environ, "NEU_JWXT_DATA_DIR": str(temporary / "synthetic-data"), "NEU_JWXT_MODE": "local-single"}
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    actual = subprocess.check_output([str(executable), "--version"], cwd=temporary, env=environment, text=True, **options).strip()
    if actual != version or (temporary / "synthetic-data").exists():
        raise ValueError("Version command failed or changed data")
    run_desktop_cycle(executable, temporary, environment, options, version)
    backup_environment = {**environment, "NEU_JWXT_BACKUP_PASSWORD": secrets.token_urlsafe(32)}
    backup = temporary / "synthetic-backup.age"
    restored = temporary / "restored"
    source_directory = temporary / "synthetic-data"
    source_files = [source_directory / name for name in ("toolbox.db", "vault.key")]
    source_digests = [hashlib.sha256(path.read_bytes()).digest() for path in source_files]
    for command, directory in (("backup", temporary / "synthetic-data"), ("restore", restored)):
        subprocess.run([str(executable), command, "--data", str(directory), "--file", str(backup)],
                       check=True, cwd=temporary, env=backup_environment, capture_output=True, timeout=90, **options)
    if not all((restored / name).is_file() for name in ("toolbox.db", "vault.key")):
        raise ValueError("Restored data directory is incomplete")
    if source_digests != [hashlib.sha256(path.read_bytes()).digest() for path in source_files]:
        raise ValueError("Backup or restore modified the stopped source database or vault key")
    damaged = temporary / 'damaged-backup.age'
    damaged.write_bytes(backup.read_bytes()[:-16])
    original = {name: (restored / name).read_bytes() for name in ('toolbox.db', 'vault.key')}
    for reason, source_file, destination, restore_env in (
        ('wrong password', backup, temporary / 'wrong-password', {**backup_environment, 'NEU_JWXT_BACKUP_PASSWORD': secrets.token_urlsafe(32)}),
        ('damaged archive', damaged, temporary / 'damaged-restore', backup_environment),
        ('existing destination', backup, restored, backup_environment),
    ):
        attempt = subprocess.run([str(executable), 'restore', '--data', str(destination), '--file', str(source_file)],
                                 cwd=temporary, env=restore_env, capture_output=True, timeout=90, **options)
        if attempt.returncode == 0 or (destination != restored and destination.exists()):
            raise ValueError(f'Restore did not reject {reason} cleanly')
    if any((restored / name).read_bytes() != contents for name, contents in original.items()):
        raise ValueError('Rejected restore changed the existing database or vault key')
    migration = temporary / 'migration-conflict'
    migration.mkdir()
    (migration / 'vault.key').write_bytes(original['vault.key'])
    migration_db = migration / 'toolbox.db'
    with sqlite3.connect(restored / 'toolbox.db') as source_db, sqlite3.connect(migration_db) as fixture:
        source_db.backup(fixture)
        fixture.execute('DROP TABLE diagnostic_logs')
        fixture.execute('PRAGMA user_version=7')
        fixture.execute("INSERT INTO accounts(id,created_at) VALUES('migration-fixture','2000-01-01T00:00:00Z')")
    attempt = subprocess.run([str(executable), '--init-only', '--data', str(migration)], cwd=temporary,
                             env={**environment, 'NEU_JWXT_DATA_DIR': str(migration)}, capture_output=True, timeout=30, **options)
    if attempt.returncode == 0 or b'sent_at' not in attempt.stderr:
        raise ValueError('Expected schema 7 migration to reject the conflicting sent_at column')
    with sqlite3.connect(migration_db) as fixture:
        if (fixture.execute('PRAGMA user_version').fetchone() != (7,)
                or fixture.execute("SELECT name FROM sqlite_master WHERE name='diagnostic_logs'").fetchone() is not None
                or fixture.execute("SELECT created_at FROM accounts WHERE id='migration-fixture'").fetchone() != ('2000-01-01T00:00:00Z',)):
            raise ValueError('Failed migration did not preserve the original schema and account')
    key_fixture = temporary / 'migration-key'
    key_fixture.mkdir()
    key_db = key_fixture / 'toolbox.db'
    with sqlite3.connect(restored / 'toolbox.db') as source_db, sqlite3.connect(key_db) as fixture:
        source_db.backup(fixture)
        expected_schema, = fixture.execute('PRAGMA user_version').fetchone()
        fixture.execute('PRAGMA user_version=10')
        fixture.execute('DROP TABLE diagnostic_logs')
    key_path = key_fixture / 'vault.key'
    key_path.write_bytes(b'incomplete')
    key_command = [str(executable), '--init-only', '--data', str(key_fixture)]
    key_env = {**environment, 'NEU_JWXT_DATA_DIR': str(key_fixture)}
    rejected = subprocess.run(key_command, cwd=temporary, env=key_env, capture_output=True, timeout=30, **options)
    if rejected.returncode == 0 or b'vault key is invalid' not in rejected.stderr:
        raise ValueError('Expected invalid vault key rejection')
    with sqlite3.connect(key_db) as fixture:
        if fixture.execute('PRAGMA user_version').fetchone() != (10,) or fixture.execute("SELECT name FROM sqlite_master WHERE name='diagnostic_logs'").fetchone():
            raise ValueError('Invalid vault key left a partially migrated database')
    key_path.write_bytes(original['vault.key'])
    subprocess.run(key_command, cwd=temporary, env=key_env, check=True, capture_output=True, timeout=30, **options)
    with sqlite3.connect(key_db) as fixture:
        if fixture.execute('PRAGMA user_version').fetchone() != (expected_schema,) or not fixture.execute("SELECT name FROM sqlite_master WHERE name='diagnostic_logs'").fetchone():
            raise ValueError('Migration did not complete after restoring the vault key')
    legacy = temporary / "legacy"
    plan = legacy / "成绩" / "fixture.json"
    plan.parent.mkdir(parents=True)
    source = b'{"courses":[],"export_info":{"exported_by":"20990001"}}'
    plan.write_bytes(source)
    with sqlite3.connect(restored / "toolbox.db") as database:
        database.execute("INSERT INTO accounts(id,created_at) VALUES(?,?)", ("20990001", "2000-01-01T00:00:00Z"))
    def import_plan(*arguments, source_dir=legacy, account='20990001'):
        result = subprocess.check_output([str(executable), "import-legacy", "--data", str(restored),
                                          "--source", str(source_dir), "--account", account, *arguments],
                                         cwd=temporary, env=environment, timeout=30, **options)
        return json.loads(result)

    preview = import_plan()
    item, = preview['items']
    def saved_plan():
        with sqlite3.connect(restored / "toolbox.db") as database:
            return database.execute("SELECT payload,revision FROM documents WHERE account_id=? AND namespace=? AND document_key=?",
                                    ("20990001", item['namespace'], item['key'])).fetchone()

    if item['status'] != 'new' or saved_plan() is not None:
        raise ValueError('Legacy preview was not read-only')
    applied = import_plan('--apply-digest', preview['digest'])
    saved = saved_plan()
    if applied['applied'] != 1 or not saved:
        raise ValueError('Legacy plan not imported')
    repeat = import_plan()
    if repeat['items'][0]['status'] != 'unchanged' or import_plan('--apply-digest', repeat['digest'])['applied'] != 0:
        raise ValueError('Repeated legacy import was not idempotent')
    if saved_plan() != saved or plan.read_bytes() != source:
        raise ValueError('Repeated legacy import altered the source or destination')

    foreign = plan.with_name('another-account.json')
    foreign_source = source.replace(b'20990001', b'20990002')
    foreign.write_bytes(foreign_source)
    if import_plan()['items'] != repeat['items']:
        raise ValueError('Foreign GPA plan included in preview')
    changed = json.loads(source)
    changed['courses'].append({'course_name': 'synthetic revision'})
    changed_source = json.dumps(changed).encode()
    plan.write_bytes(changed_source)
    conflict = import_plan()
    if conflict['items'][0]['status'] != 'conflict' or conflict['digest'] == repeat['digest']:
        raise ValueError('Changed legacy plan did not generate a distinct conflict preview')
    additional = plan.with_name('additional.json')
    additional.write_bytes(source)
    mixed = import_plan()
    if sorted(entry['status'] for entry in mixed['items']) != ['conflict', 'new']:
        raise ValueError('Expected one conflict and one importable document')
    candidate = next(entry for entry in mixed['items'] if entry['status'] == 'new')
    for digest in (repeat['digest'], mixed['digest']):
        try:
            import_plan('--apply-digest', digest)
        except subprocess.CalledProcessError as failure:
            if json.loads(failure.output)['applied'] != 0:
                raise ValueError('Rejected legacy import reported writes') from failure
        else:
            raise ValueError('Rejected legacy import unexpectedly succeeded')
        if saved_plan() != saved or plan.read_bytes() != changed_source or foreign.read_bytes() != foreign_source:
            raise ValueError('Rejected legacy import altered source or destination')
        with sqlite3.connect(restored / 'toolbox.db') as database:
            if database.execute('SELECT 1 FROM documents WHERE account_id=? AND namespace=? AND document_key=?',
                                ('20990001', candidate['namespace'], candidate['key'])).fetchone() or additional.read_bytes() != source:
                raise ValueError('Rejected mixed import modified the additional document')
    favorites_dir = temporary / 'legacy-favorites'
    favorites_dir.mkdir()
    favorites_file = favorites_dir / 'research_training_favorites.json'
    private_fields = dict.fromkeys(('contact', 'phone', 'email', 'application_reason', 'rejection_reason'), 'fixture-private')
    users = {account: {'records': [{'batch_id': 'B1', 'topic_id': topic, 'topic': {'topic_id': topic, 'title': title, **private_fields}}]}
             for account, topic, title in [('20990001', 'T1', 'own fixture'), ('20990002', 'T2', 'foreign fixture')]}
    original_favorites = json.dumps({'version': 1, 'users': users}).encode()
    favorites_file.write_bytes(original_favorites)
    preview = import_plan(source_dir=favorites_dir)
    favorite, = preview['items']
    if favorite['namespace'] != 'research-favorites' or favorite['status'] != 'new':
        raise ValueError('Unexpected favorite preview')
    if import_plan('--apply-digest', preview['digest'], source_dir=favorites_dir)['applied'] != 1:
        raise ValueError('Favorite import count mismatch')
    with sqlite3.connect(restored / 'toolbox.db') as database:
        (owner, payload), = database.execute("SELECT account_id,payload FROM documents WHERE namespace='research-favorites'")
    snapshot = json.loads(payload)['topic']
    if owner != '20990001' or snapshot['topic_id'] != 'T1' or snapshot['title'] != 'own fixture' or private_fields.keys() & snapshot.keys():
        raise ValueError('Imported favorite ownership, content or privacy mismatch')
    if favorites_file.read_bytes() != original_favorites:
        raise ValueError('Favorite import modified the legacy file')
    for _ in range(2):
        run_desktop_cycle(executable, temporary, {**environment, 'NEU_JWXT_DATA_DIR': str(restored)}, options, version)
        persisted, = import_plan(source_dir=favorites_dir)['items']
        if persisted['status'] != 'unchanged' or persisted['key'] != favorite['key']:
            raise ValueError('Restart did not preserve the imported favorite')
    agenda_dir = temporary / 'legacy-agendas'
    current, retired = '2026-2027-1', '2025-2026-2'
    originals = {}
    for owner, term, title in [('20990001', current, 'own'), ('20990002', current, 'foreign'), ('20990001', retired, 'old')]:
        path = agenda_dir / 'timetable_agendas' / hashlib.sha256(owner.encode()).hexdigest() / (hashlib.sha256(term.encode()).hexdigest() + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {'id': 'event1', 'selection_mode': 'date', 'date': '2099-11-16', 'title': title, 'start_time': '08:00', 'end_time': '09:00'}
        move = {'source': '2026-11-16', 'target': {'own': '2026-11-21', 'foreign': '2026-11-22', 'old': '2026-11-23'}[title]}
        originals[path] = json.dumps({'events': [event], 'moves': [move], '_retired': term == retired}).encode()
        path.write_bytes(originals[path])
    if import_plan(source_dir=agenda_dir)['items']:
        raise ValueError('Inferred an unspecified legacy agenda term')
    terms = ['--terms', f'{current},{retired}']
    preview = import_plan(*terms, source_dir=agenda_dir)
    if import_plan(*terms, '--apply-digest', preview['digest'], source_dir=agenda_dir)['applied'] != 2:
        raise ValueError('Unexpected agenda import count')
    with sqlite3.connect(restored / 'toolbox.db') as database:
        agendas = {(owner, term): json.loads(payload) for owner, term, payload in database.execute("SELECT account_id,document_key,payload FROM documents WHERE namespace='agenda'")}
    if set(agendas) != {('20990001', current), ('20990001', retired)} or [event['title'] for event in agendas['20990001', current]['events']] != ['own']:
        raise ValueError('Agenda owner or event mismatch')
    ended = agendas['20990001', retired]
    if agendas['20990001', current]['moves'] != [{'source': '2026-11-16', 'target': '2026-11-21'}]:
        raise ValueError('Agenda move ownership or direction mismatch')
    if ended.get('_retired') is not True or ended['events'] or ended['moves'] or any(path.read_bytes() != original for path, original in originals.items()):
        raise ValueError('Retired agenda or legacy sources changed unexpectedly')
    policies = {'20990001': 'through_2024', '20990002': 'from_2025'}
    preferences_dir = temporary / 'legacy-policies'
    (preferences_dir / 'gpa_preferences').mkdir(parents=True)
    with sqlite3.connect(restored / 'toolbox.db') as database:
        database.execute('INSERT INTO accounts(id,created_at) VALUES(?,?)', ('20990002', '2000-01-01T00:00:00Z'))
    for owner, policy in policies.items():
        path = preferences_dir / 'gpa_preferences' / (hashlib.sha256(owner.encode()).hexdigest() + '.json')
        path.write_text(json.dumps({'mode': policy}), encoding='utf-8')
    for owner in policies:
        preview = import_plan(source_dir=preferences_dir, account=owner)
        if import_plan('--apply-digest', preview['digest'], source_dir=preferences_dir, account=owner)['applied'] != 1:
            raise ValueError('Unexpected GPA preference import count')
    with sqlite3.connect(restored / 'toolbox.db') as database:
        stored = {owner: json.loads(payload)['mode'] for owner, payload in database.execute("SELECT account_id,payload FROM documents WHERE namespace='gpa-policy' AND document_key='default'")}
    if stored != policies:
        raise ValueError('Imported GPA preferences do not match their owners')
    for mode in ('remote-single', 'remote-multi'):
        smoke_remote(executable, temporary, options, mode)
    return {"platform": platform, "version": actual, "health": "passed", "frontend": "passed", "graceful_shutdown": "passed", "backup_restore": "passed", "restore_rejects_wrong_password_damage_and_overwrite": "passed", "legacy_gpa_import": "passed", "legacy_gpa_conflict_and_ownership": "passed", "legacy_favorite_ownership_and_privacy": "passed", "remote_admission_restart": "passed", "remote_single_account_limit": "passed", "remote_anonymous_mobile_api_version": 1, "synthetic_data": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--report", type=Path, help="Write verification evidence to this path instead of the release directory")
    args = parser.parse_args()
    output = args.output.resolve()
    manifest = json.loads((output / "go-release-manifest.json").read_text())
    version = manifest["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[.-][0-9A-Za-z.-]+)?", version):
        raise ValueError("Invalid version")
    evidence = {"version": version, "archives": {}}
    for platform, suffix in [("windows", "zip"), ("linux", "tar.gz")]:
        asset = manifest["assets"][f"{platform}-amd64"]
        expected = f"NEU-JWXT-Toolkit-{version}-{platform}-amd64-go.{suffix}"
        if asset["name"] != expected:
            raise ValueError("Unexpected asset name")
        path = output / expected
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != asset["sha256"] or path.stat().st_size != asset["size"]:
            raise ValueError("Asset checksum mismatch")
        evidence["archives"][platform] = inspect_archive(path, version, platform)
    if args.smoke:
        evidence["smoke"] = smoke(output, version)
        if sys.platform == "win32":
            archive = manifest['assets']['windows-amd64']
            test_name = 'TestWindowsReleaseSwitchFromArchive'
            result = subprocess.run(
                ['go', 'test', '-json', '-count=1', '-run', f'^{test_name}$', './cmd/toolbox'],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, 'TOOLBOX_TEST_RELEASE_ZIP': str(output / archive['name']),
                     'TOOLBOX_TEST_RELEASE_SHA256': archive['sha256'], 'TOOLBOX_TEST_RELEASE_VERSION': version},
                text=True, encoding='utf-8', stdout=subprocess.PIPE, timeout=240)
            events = [json.loads(line) for line in result.stdout.splitlines()]
            if result.returncode or not any(event.get('Test') == test_name and event['Action'] == 'pass' for event in events):
                raise ValueError('Windows archive upgrade did not pass:\n' + result.stdout[-4000:])
            evidence['smoke']['windows_archive_upgrade'] = 'passed'
        evidence["smoke"]["conflicting_schema_migration_rollback"] = "passed"
        evidence["smoke"]["invalid_key_migration_rollback_and_retry"] = "passed"
        evidence["smoke"]["remote_mode_mismatch_rejected_and_admission_preserved"] = "passed"
    report = args.report if args.report is not None else output / "verification.json"
    report.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
