#!/usr/bin/env bash
set -euo pipefail

installer=$1
archive=$2
manifest=$3
version=$(jq -er '.version' "$manifest")
bundle="NEU-JWXT-Toolkit-${version}-linux-amd64-go"
base=http://localhost:18000/api
maintenance=/opt/.neu-toolbox-upgrades
workdir=$(mktemp -d)

make_bundle() {
  local new_version=$1
  local name="NEU-JWXT-Toolkit-${new_version}-linux-amd64-go"
  mkdir -p "$workdir/$new_version"
  tar -xzf "$archive" -C "$workdir/$new_version"
  mv -- "$workdir/$new_version/$bundle" "$workdir/$new_version/$name"
  printf '%s\n' "$new_version" > "$workdir/$new_version/$name/VERSION"
  if [[ ${2:-} == broken ]]; then
    rm -- "$workdir/$new_version/$name/frontend/build/asset-manifest.json"
  fi
  location="$workdir/$new_version/$name.tar.gz"
  tar -czf "$location" -C "$workdir/$new_version" "$name"
  metadata="$workdir/$new_version/go-release-manifest.json"
  jq --arg ver "$new_version" --arg name "$name.tar.gz" --arg digest "$(sha256sum "$location" | cut -d ' ' -f 1)" \
    --argjson size "$(stat -c '%s' "$location")" \
    '.version = $ver | .assets["linux-amd64"] = {name: $name, sha256: $digest, size: $size}' "$manifest" > "$metadata"
}

health() {
  curl -4fsS --retry 20 --retry-delay 1 --retry-connrefused "$base/health" | jq -e --arg ver "$1" '.backend == "go" and .version == $ver'
  test "$(curl -4s -o /dev/null -w '%{http_code}' "$base/status")" = 401
}

admin_login() {
  local cookies=$1
  jq -nc '{password:env.TEST_ACCESS_PASSWORD}' | curl -4fsS -c "$cookies" -H 'Content-Type: application/json' -d @- "$base/access/login" | jq -e '.success == true'
  jq -nc '{password:env.TEST_ADMIN_PASSWORD}' | curl -4fsS -b "$cookies" -c "$cookies" -H 'Content-Type: application/json' -d @- "$base/admin/login" | jq -e '.success == true'
}

if bash "$installer" rollback --restore-data; then
  exit 1
fi
health "$version"
unit_checksum=$(sha256sum /etc/systemd/system/neu-toolbox.service)
config_checksum=$(sudo sha256sum /etc/neu-toolbox.env)
key_checksum=$(sudo sha256sum /var/lib/neu-toolbox/vault.key)
previous_version="${version%%.*}.999999.1"
next_version="${version%%.*}.999999.2"
make_bundle "$previous_version"
good_archive=$location
good_manifest=$metadata
jq '.assets["linux-amd64"].sha256 = ("0" * 64)' "$good_manifest" > "$workdir/bad-digest.json"
old_pid=$(systemctl show --property=MainPID --value neu-toolbox)
if bash "$installer" update --archive "$good_archive" --manifest "$workdir/bad-digest.json"; then
  exit 1
fi
test "$old_pid" = "$(systemctl show --property=MainPID --value neu-toolbox)"

admin_login "$workdir/before-update"
bash "$installer" update --archive "$good_archive" --manifest "$good_manifest"
health "$previous_version"
curl -4fsS -b "$workdir/before-update" "$base/admin/accounts" | jq -e '.accounts | any(.account == "20990001")'
curl -4fsS -b "$workdir/before-update" -H 'Content-Type: application/json' -d '{"account":"20990002"}' "$base/admin/accounts" >/dev/null
bash "$installer" status

make_bundle "$next_version" broken
if bash "$installer" update --archive "$location" --manifest "$metadata"; then
  exit 1
fi
health "$previous_version"
curl -4fsS -b "$workdir/before-update" "$base/admin/accounts" | jq -e '.accounts | any(.account == "20990002")'
sudo jq -e '.pending == null and .activate == null' "$maintenance/state.json"

if bash "$installer" rollback; then
  exit 1
fi
bash "$installer" rollback --restore-data
health "$version"
test "$(curl -4s -b "$workdir/before-update" -o /dev/null -w '%{http_code}' "$base/admin/accounts")" = 401
admin_login "$workdir/after-rollback"
accounts=$(curl -4fsS -b "$workdir/after-rollback" "$base/admin/accounts")
jq -e '(.accounts | any(.account == "20990001")) and (.accounts | all(.account != "20990002"))' <<< "$accounts"
bash "$installer" rollback --restore-data
health "$previous_version"
admin_login "$workdir/after-second-rollback"
curl -4fsS -b "$workdir/after-second-rollback" "$base/admin/accounts" | jq -e '.accounts | any(.account == "20990002")'
bash "$installer" recover
test "$(stat -c '%a %U %G' "$maintenance")" = '700 root root'
test "$unit_checksum" = "$(sha256sum /etc/systemd/system/neu-toolbox.service)"
test "$config_checksum" = "$(sudo sha256sum /etc/neu-toolbox.env)"
test "$key_checksum" = "$(sudo sha256sum /var/lib/neu-toolbox/vault.key)"
test "$(systemctl is-enabled neu-toolbox || true)" = disabled
sudo systemctl is-active --quiet neu-jwxt-toolkit
