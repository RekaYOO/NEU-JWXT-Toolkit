#!/bin/sh
set -eu
stage=engine-archive
trap 'status=$?; if [ "$status" -ne 0 ]; then printf "stage=%s exit=%s TOOLBOX_BUILD_FAILED\n" "$stage" "$status"; fi' EXIT
test -f /mnt/bundle/engine.tgz
tar xzf /mnt/bundle/engine.tgz --strip-components=1 -C /usr/local/bin
driver=vfs
if [ -f /mnt/bundle/bases.tar ]; then driver=overlay2; fi
if [ -f /mnt/bundle/source.tar ]; then
    stage=scratch-disk
    disk=/dev/disk/by-id/virtio-toolboxscratch
    test -b "$disk"
    test "$(blockdev --getsize64 "$disk")" = 25769803776
    test -z "$(wipefs -n --noheadings "$disk")"
    mkfs.ext4 -q "$disk"
    mkdir -p /var/lib/docker
    mount "$disk" /var/lib/docker
fi
stage=docker-daemon
dockerd --bridge=none --iptables=false --ip6tables=false --storage-driver="$driver" >/tmp/docker.log 2>&1 &
for attempt in $(seq 1 60); do
    docker info >/dev/null 2>&1 && break
    sleep 1
done
docker info >/dev/null || { tail -40 /tmp/docker.log; exit 1; }
if [ -f /mnt/bundle/bases.tar ]; then
    stage=base-images
    docker load -i /mnt/bundle/bases.tar
    for image in debian:bookworm-slim node:22-bookworm-slim golang:1.24-bookworm; do
        test "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")" = linux/amd64
    done
fi
if [ -f /mnt/bundle/source.tar ]; then
    stage=source-build
    mkdir /tmp/toolbox-source
    tar xf /mnt/bundle/source.tar -C /tmp/toolbox-source
    cd /tmp/toolbox-source
    if DOCKER_BUILDKIT=0 timeout 45m docker build --network=host --pull=false -f go.Dockerfile -t toolbox-source:test . >/tmp/source-build.log 2>&1; then
        docker image inspect toolbox-source:test >/dev/null
        echo TOOLBOX_BUILD_OK
    else
        tail -35 /tmp/source-build.log
        exit 1
    fi
    exit 0
fi
stage=container-recreation
mkdir -p /tmp/image/app /tmp/image/data
chown 10001:10001 /tmp/image/data
chmod 700 /tmp/image/data
tar xzf /mnt/bundle/bundle.tgz --strip-components=1 -C /tmp/image/app
tar -C /tmp/image -cf - . | docker import - toolbox-candidate:test
docker volume create toolbox-test
access=$(cat /proc/sys/kernel/random/uuid)
for pass in 1 2; do
    if [ "$pass" = 1 ]; then
        set -- -e "NEU_JWXT_ACCESS_PASSWORD=$access"
    else
        set --
    fi
    docker run -d --name toolbox-test --user 10001:10001 --network host -v toolbox-test:/data -w /app -e NEU_JWXT_ADMIN_ACCOUNT=20990001 -e NEU_JWXT_PUBLIC_ORIGIN=https://toolbox.example.invalid "$@" toolbox-candidate:test /app/toolbox --mode remote-multi --listen 127.0.0.1:8000 --data /data
    base=http://localhost:8000/api
    curl -4fsS --retry 30 --retry-connrefused --retry-delay 1 "$base/health" | grep -q '"backend":"go"'
    jar=$(mktemp)
    login() {
        printf '{"password":"%s"}' "$2" | curl -4fsS -b "$jar" -c "$jar" -H 'Content-Type: application/json' -d @- "$base/$1/login" | grep -q '"success":true'
    }
    login access "$access"
    test "$(curl -4s -b "$jar" -o /dev/null -w '%{http_code}' "$base/admin/accounts")" = 401
    test "$(curl -4s -o /dev/null -w '%{http_code}' "${base%/api}/admin")" = 404
    rm -f "$jar"
    docker stop toolbox-test
    docker rm toolbox-test
done
echo TOOLBOX_CONTAINER_OK
