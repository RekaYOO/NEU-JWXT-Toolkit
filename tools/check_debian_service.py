"""Boot a disposable Debian nocloud guest and exercise the packaged systemd unit."""

import argparse
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import tempfile
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('qemu', 'image', 'archive'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--docker-engine', type=Path)
    parser.add_argument('--kvm', action='store_true')
    parser.add_argument('--selection-test', type=Path)
    parser.add_argument('--docker-bases', type=Path)
    parser.add_argument('--docker-source-tar', type=Path)
    args = parser.parse_args()
    if args.docker_bases and not args.docker_engine:
        parser.error('--docker-bases requires --docker-engine')
    if args.docker_source_tar and not args.docker_bases:
        parser.error('--docker-source-tar requires --docker-bases')
    with tempfile.TemporaryDirectory() as directory, socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(30)
        shutil.copyfile(args.archive, Path(directory) / 'bundle.tgz')
        if args.selection_test:
            shutil.copyfile(args.selection_test, Path(directory) / 'selection.test')
        if args.docker_engine:
            if args.docker_source_tar:
                shutil.copyfile(args.docker_source_tar, Path(directory) / 'source.tar')
            if args.docker_bases:
                shutil.copyfile(args.docker_bases, Path(directory) / 'bases.tar')
            shutil.copyfile(args.docker_engine, Path(directory) / 'engine.tgz')
            shutil.copyfile(Path(__file__).with_name('check_docker_guest.sh'), Path(directory) / 'docker.sh')
        launch = [str(args.qemu), '-accel', 'kvm' if args.kvm else 'tcg', '-cpu', 'host' if args.kvm else 'max', '-m', '3072' if args.docker_source_tar else '1024',
                                    '-drive', f'file={args.image},format=qcow2,if=none,id=guestos,snapshot=on',
                                    '-device', 'virtio-blk-pci,drive=guestos,bootindex=1',
                                    '-drive', f'file=fat:ro:{directory},format=raw,if=none,id=bundle,readonly=on',
                                    '-device', 'virtio-blk-pci,drive=bundle,serial=toolboxbundle',
                                    '-nic', 'user,model=virtio' if args.docker_source_tar else 'none', '-display', 'none', '-serial', f'tcp:127.0.0.1:{listener.getsockname()[1]}', '-monitor', 'none']
        process = None
        scratch = None
        if args.docker_source_tar:
            scratch = tempfile.TemporaryDirectory()
            disk = Path(scratch.name) / 'docker.qcow2'
            subprocess.run([str(args.qemu.with_name('qemu-img' + args.qemu.suffix)), 'create', '-f', 'qcow2', str(disk), '24G'], check=True)
            launch.extend(['-drive', f'file={disk},format=qcow2,if=none,id=dockerdata', '-device', 'virtio-blk-pci,drive=dockerdata,serial=toolboxscratch'])
        output = queue.Queue()

        def reader():
            try:
                while chunk := console.recv(4096):
                    output.put(chunk)
            except ConnectionResetError:
                pass
            finally:
                output.put(None)

        def wait_for(marker, timeout=180):
            deadline = time.monotonic() + timeout
            recent = b''
            while marker not in recent:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'Timed out waiting for {marker!r}: {recent[-1500:].decode(errors="replace")}')
                try:
                    chunk = output.get(timeout=1)
                except queue.Empty:
                    continue
                if chunk is None:
                    raise RuntimeError('guest exited unexpectedly')
                recent = (recent + chunk)[-8192:]
                if b'TOOLBOX_BUILD_FAILED' in recent:
                    raise RuntimeError(recent[-4000:].decode(errors='replace'))
                if b'TOOLBOX_DIAG_END' in recent:
                    print(recent.decode(errors='replace'), flush=True)
                    recent = recent.split(b'TOOLBOX_DIAG_END', 1)[1]

        def send(line):
            console.sendall(line.encode() + b'\r')

        try:
            commands = [
                'set -e', 'mkdir -p /opt/neu-toolbox',
                'tar xzf /mnt/bundle/bundle.tgz --strip-components=1 -C /opt/neu-toolbox',
                'useradd --system --user-group --no-create-home neu-toolbox',
                'cp /opt/neu-toolbox/neu-toolbox.service /etc/systemd/system/',
                'umask 077', 'echo NEU_JWXT_PUBLIC_ORIGIN=https://toolbox.example.invalid >/etc/neu-toolbox.env',
                'for role in ACCESS ADMIN; do echo NEU_JWXT_${role}_PASSWORD=$(cat /proc/sys/kernel/random/uuid) >>/etc/neu-toolbox.env; done',
                'cp /etc/neu-toolbox.env /root/test-login.env',
                'cat /proc/sys/kernel/random/boot_id >/root/previous-boot',
                'cp /mnt/bundle/reboot.sh /root/reboot-check.sh',
                'systemctl daemon-reload', 'systemctl enable --now neu-toolbox',
                'for attempt in $(seq 1 30); do curl -fsS 127.0.0.1:8000/api/health >/tmp/health && break; sleep 1; done',
                '''grep -q '"backend":"go"' /tmp/health''',
                '''test "$(stat -c '%a %U' /var/lib/neu-toolbox)" = '700 neu-toolbox' ''',
                "sed -i '/_PASSWORD=/d' /etc/neu-toolbox.env", 'systemctl restart neu-toolbox',
                'curl --retry 15 --retry-connrefused --retry-delay 1 -fsS 127.0.0.1:8000/api/health >/dev/null',
                'test "$(curl -so /dev/null -w %{http_code} 127.0.0.1:8000/api/status)" = 401',
                'systemctl is-active --quiet neu-toolbox', 'echo TOOLBOX_SYSTEMD_OK',
            ]
            fixture_command = 'TOOLBOX_TEST_REBOOT_DIR=/root/selection-fixture /root/selection.test -test.run=^TestSelectionTaskUnknownSubmissionSurvivesRestartAndRoundEnd$ -test.v'
            if args.selection_test:
                commands[-1:-1] = ['install -m 0700 /mnt/bundle/selection.test /root/selection.test',
                                    'TOOLBOX_TEST_REBOOT_PHASE=prepare ' + fixture_command]
            Path(directory, 'verify.sh').write_bytes(('\n'.join(commands) + '\n').encode())
            reboot_check = r'''set -eu
test "$(cat /root/previous-boot)" != "$(cat /proc/sys/kernel/random/boot_id)"
if grep -q _PASSWORD /etc/neu-toolbox.env; then exit 1; fi
systemctl is-enabled --quiet neu-toolbox
base=http://localhost:8000/api
if ! curl -4fsS --retry 30 --retry-connrefused --retry-delay 1 "$base/health" >/dev/null 2>&1; then
    systemctl list-jobs --no-pager
    systemctl show neu-toolbox -p ActiveState -p SubState -p Result -p ExecMainStatus
    journalctl -b -u neu-toolbox --no-pager -n 10
    echo TOOLBOX_DIAG_END
    curl -4fsS --retry 150 --retry-connrefused --retry-delay 1 "$base/health" >/dev/null 2>&1
fi
systemctl is-active --quiet neu-toolbox
. /root/test-login.env
umask 077
jar=$(mktemp)
trap 'rm -f "$jar"' EXIT
login() {
    printf '{"password":"%s"}' "$2" | curl -4fsS -b "$jar" -c "$jar" -H 'Content-Type: application/json' -d @- "$base/$1/login" | grep -q '"success":true'
}
login access "$NEU_JWXT_ACCESS_PASSWORD"
test "$(curl -4s -b "$jar" -o /dev/null -w '%{http_code}' "$base/admin/accounts")" = 401
login admin "$NEU_JWXT_ADMIN_PASSWORD"
curl -4fsS -b "$jar" "$base/admin/accounts" >/dev/null
echo TOOLBOX_REBOOT_OK
'''
            if args.selection_test:
                reboot_check = reboot_check.replace('echo TOOLBOX_REBOOT_OK', 'TOOLBOX_TEST_REBOOT_PHASE=recover ' + fixture_command + '\necho TOOLBOX_REBOOT_OK')
            Path(directory, 'reboot.sh').write_bytes(reboot_check.encode())
            process = subprocess.Popen(launch, stdin=subprocess.DEVNULL)
            console, _ = listener.accept()
            console.settimeout(None)
            threading.Thread(target=reader, daemon=True).start()
            wait_for(b'login:')
            send('root')
            wait_for(b'# ')
            send('mkdir -p /mnt/bundle; disk=/dev/disk/by-id/virtio-toolboxbundle; (mount -o ro "$disk-part1" /mnt/bundle || mount -o ro "$disk" /mnt/bundle) && test -f /mnt/bundle/bundle.tgz && echo BUNDLE_""READY')
            wait_for(b'BUNDLE_READY', timeout=30)
            if args.docker_engine:
                send('sh /mnt/bundle/docker.sh')
                if args.docker_source_tar:
                    wait_for(b'TOOLBOX_BUILD_OK', timeout=3600)
                    print('Source Dockerfile build passed')
                    return
                wait_for(b'TOOLBOX_CONTAINER_OK', timeout=900 if args.docker_bases else 240)
                print('Offline candidate image: Docker container recreation and persistent credentials passed')
                return
            send('sh /mnt/bundle/verify.sh')
            wait_for(b'TOOLBOX_SYSTEMD_OK')
            print('Debian systemd start, private state directory and restart passed')
            send('systemctl reboot')
            wait_for(b'login:')
            send('root')
            wait_for(b'# ')
            send('sh /root/reboot-check.sh')
            wait_for(b'TOOLBOX_REBOOT_OK', timeout=360)
            print('Debian guest reboot, automatic service startup and original credentials passed')
        finally:
            if process is not None:
                process.kill()
                process.wait()
            if scratch is not None:
                scratch.cleanup()


if __name__ == '__main__':
    main()
