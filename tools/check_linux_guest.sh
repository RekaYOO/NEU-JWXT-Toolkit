#!/bin/sh
set -eu
[ "$$" -eq 1 ] || { echo 'Run only as init in a disposable test guest' >&2; exit 2; }
[ -d /NEU-JWXT-Toolkit-*-linux-amd64-go ] || exit 2
trap 'poweroff -f' EXIT
mount -t devtmpfs devtmpfs /dev
mount -t proc proc /proc
mount -t sysfs sysfs /sys
ip link set lo up
cd /NEU-JWXT-Toolkit-*-linux-amd64-go
umask 077
addgroup -g 10001 neu-toolbox
adduser -D -H -u 10001 -G neu-toolbox neu-toolbox
export NEU_JWXT_DATA_DIR=/var/lib/neu-toolbox
mkdir "$NEU_JWXT_DATA_DIR"
chown neu-toolbox:neu-toolbox "$NEU_JWXT_DATA_DIR"
start_service() {
    su -p -s /bin/sh -c 'exec ./toolbox --mode remote-multi --listen 127.0.0.1:8000' neu-toolbox &
    pid=$!
}
export NEU_JWXT_PUBLIC_ORIGIN=https://toolbox.example.invalid
export NEU_JWXT_ACCESS_PASSWORD="$(cat /proc/sys/kernel/random/uuid)"
export NEU_JWXT_ADMIN_PASSWORD="$(cat /proc/sys/kernel/random/uuid)"
start_service
for attempt in $(seq 1 90); do
    wget -qO /tmp/health http://127.0.0.1:8000/api/health && break
    kill -0 "$pid"
    sleep 1
done
grep -F '"backend":"go"' /tmp/health
[ "$(awk '/^Uid:/ {print $3}' /proc/$pid/status)" = 10001 ]
[ "$(stat -c '%a:%u:%g' "$NEU_JWXT_DATA_DIR")" = '700:10001:10001' ]
for private_file in toolbox.db vault.key; do
    [ "$(stat -c '%a:%u:%g' "$NEU_JWXT_DATA_DIR/$private_file")" = '600:10001:10001' ]
done
grep -F "\"version\":\"$(tr -d '\r\n' < VERSION)\"" /tmp/health
wget -qO /tmp/home http://127.0.0.1:8000/
grep -q 'id="root"' /tmp/home
wget -SO /dev/null http://127.0.0.1:8000/api/status 2>/tmp/status && exit 1
grep -F '401 Unauthorized' /tmp/status
kill -TERM "$pid"
wait "$pid"
access=$NEU_JWXT_ACCESS_PASSWORD
admin=$NEU_JWXT_ADMIN_PASSWORD
unset NEU_JWXT_ACCESS_PASSWORD NEU_JWXT_ADMIN_PASSWORD
start_service
base=http://127.0.0.1:8000/api
for attempt in $(seq 1 90); do
    wget -qO /dev/null "$base/health" && break
    kill -0 "$pid"
    sleep 1
done
login() {
    wget -SO /tmp/login --header 'Content-Type: application/json' --header "Cookie: $3" \
        --post-data "{\"password\":\"$2\"}" "$base/$1/login" 2>/tmp/login-headers
    grep -q '"success":true' /tmp/login
}
login access "$access" ''
cookie=$(sed -n 's/.*Set-Cookie: \(neu_jwxt_access=[^;]*\);.*/\1/p' /tmp/login-headers)
[ -n "$cookie" ]
wget -SO /dev/null --header "Cookie: $cookie" "$base/admin/accounts" 2>/tmp/status && exit 1
grep -q '401 Unauthorized' /tmp/status
login admin "$admin" "$cookie"
admin_cookie=$(sed -n 's/.*Set-Cookie: \(neu_toolbox_admin=[^;]*\);.*/\1/p' /tmp/login-headers)
[ -n "$admin_cookie" ]
wget -qO /dev/null --header "Cookie: $cookie; $admin_cookie" "$base/admin/accounts"
kill -TERM "$pid"
wait "$pid"
echo TOOLBOX_LINUX_GUEST_PASSED
