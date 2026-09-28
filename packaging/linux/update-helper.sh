#!/usr/bin/env bash
set -euo pipefail

APP_NAME="neu-jwxt-toolkit"
APP_ROOT="/opt/${APP_NAME}"
DATA_DIR="/var/lib/${APP_NAME}"
REQUEST_FILE="${DATA_DIR}/updates/request.env"
LOCK_FILE="${DATA_DIR}/updates/update.lock"

[[ "${EUID}" -eq 0 ]] || exit 1
install -d -o neu-jwxt -g neu-jwxt -m 0700 "${DATA_DIR}/updates"
exec 9>"${LOCK_FILE}"
flock -n 9 || exit 0
[[ -f "${REQUEST_FILE}" ]] || exit 0
request_owner="$(stat -c '%U' "${REQUEST_FILE}" 2>/dev/null || true)"
request_mode="$(stat -c '%a' "${REQUEST_FILE}" 2>/dev/null || true)"
if [[ "${request_owner}" != "neu-jwxt" || "${request_mode}" != "600" ]]; then
  echo "更新请求文件归属或权限不正确" >&2
  rm -f -- "${REQUEST_FILE}"
  exit 1
fi
claimed_request="${DATA_DIR}/updates/.request.$$.claimed"
mv -T -- "${REQUEST_FILE}" "${claimed_request}"
cleanup() { rm -f -- "${claimed_request}"; }
trap cleanup EXIT

result_file="${DATA_DIR}/updates/update-result.env"
version=""; asset=""; expected=""; job_id=""; install_started_at=""; stage="读取更新请求"
while IFS='=' read -r key value; do
  case "${key}" in
    version) version="${value}" ;;
    asset) asset="${value}" ;;
    sha256) expected="${value}" ;;
    job_id) job_id="${value}" ;;
  esac
done < "${claimed_request}"

if [[ ! "${job_id}" =~ ^[0-9a-f]{32}$ ]]; then
  echo "更新请求任务 ID 无效" >&2
  exit 1
fi
write_result() {
  local state="$1" error="${2:-}" temporary="${result_file}.tmp.$$"
  {
    printf 'job_id=%s\nstate=%s\nversion=%s\nupdated_at=%s\n' \
      "${job_id}" "${state}" "${version}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    [[ -z "${install_started_at}" ]] || printf 'install_started_at=%s\n' "${install_started_at}"
    [[ -z "${error}" ]] || printf 'error=%s\n' "${error}"
  } > "${temporary}"
  chown neu-jwxt:neu-jwxt "${temporary}"
  chmod 0600 "${temporary}"
  mv -f -- "${temporary}" "${result_file}"
}
fail() {
  echo "更新失败：$1" >&2
  write_result failed "$1"
  exit 1
}
on_error() {
  local status=$?
  trap - ERR
  set +e
  write_result failed "${stage}失败，请查看服务端更新器日志"
  exit "${status}"
}
trap on_error ERR

[[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] || fail "更新请求版本无效"
[[ "${asset}" == "NEU-JWXT-Toolkit-${version}-linux-amd64.tar.gz" ]] || fail "更新包名称与版本不一致"
[[ "${expected}" =~ ^[0-9a-fA-F]{64}$ ]] || fail "更新请求校验值无效"
package="${DATA_DIR}/updates/${version}/${asset}"
[[ -f "${package}" ]] || fail "更新包不存在"
stage="校验更新包"
actual="$(sha256sum "${package}" | awk '{print tolower($1)}')"
[[ "${actual}" == "$(printf '%s' "${expected}" | tr '[:upper:]' '[:lower:]')" ]] || fail "更新包 SHA-256 校验失败"

stage="解压更新包"
extract_root="$(mktemp -d "${DATA_DIR}/updates/.extract.XXXXXX")"
cleanup() { rm -rf -- "${extract_root}"; rm -f -- "${claimed_request}"; }
trap cleanup EXIT
tar -xzf "${package}" -C "${extract_root}"
package_root="${extract_root}/neu-jwxt-toolkit"
[[ -x "${package_root}/install.sh" && -x "${package_root}/app/neu-jwxt-server" ]] || fail "更新包结构不完整"
stage="安装或健康检查"
install_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
write_result installing
(cd "${package_root}" && ./install.sh --upgrade)
write_result completed
echo "更新完成：${version}"
