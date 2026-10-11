#!/usr/bin/env bash
# Upload application sources. Configure .env and run Compose as a separate step.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/deploy.sh --host HOST --key KEY --known-hosts FILE [--user ubuntu] [--directory gastrogid]

Uploads backend sources over SSH with strict host-key verification. Existing
server .env files are preserved. Secrets, Git metadata, local dependencies,
runtime data, caches and iOS sources are excluded. This script does not build
images, start services, create credentials or modify server configuration.

Environment equivalents: DEPLOY_HOST, DEPLOY_SSH_KEY, DEPLOY_KNOWN_HOSTS,
DEPLOY_USER, DEPLOY_DIRECTORY.
USAGE
}

deploy_host="${DEPLOY_HOST:-}"
deploy_key="${DEPLOY_SSH_KEY:-}"
deploy_known_hosts="${DEPLOY_KNOWN_HOSTS:-}"
deploy_user="${DEPLOY_USER:-ubuntu}"
deploy_directory="${DEPLOY_DIRECTORY:-gastrogid}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host|--key|--known-hosts|--user|--directory)
      if [[ $# -lt 2 ]]; then usage >&2; exit 2; fi
      case "$1" in
        --host) deploy_host="$2" ;;
        --key) deploy_key="$2" ;;
        --known-hosts) deploy_known_hosts="$2" ;;
        --user) deploy_user="$2" ;;
        --directory) deploy_directory="$2" ;;
      esac
      shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

if [[ ! "$deploy_host" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] ||
   [[ ! "$deploy_user" =~ ^[a-z_][a-z0-9_-]*$ ]] ||
   [[ ! "$deploy_directory" =~ ^[A-Za-z0-9_./-]+$ ]] ||
   [[ "$deploy_directory" == *".."* ]] ||
   [[ ! -f "$deploy_key" ]] || [[ ! -f "$deploy_known_hosts" ]]; then
  printf '%s\n' 'Supply a valid host, SSH user, private-key file, verified known-hosts file and destination.' >&2
  exit 2
fi

repo_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
deploy_archive="$(mktemp -t gastrogid-deploy.XXXXXX)"
trap 'rm -f "$deploy_archive"' EXIT

python3 - "$repo_directory" "$deploy_archive" <<'PACKAGE'
from pathlib import PurePosixPath
import sys
import tarfile

excluded_parts = {
    ".git", ".venv", "venv", "data", ".data", "ios", "work", "outputs", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    ".codex", ".aws", ".agents", ".secrets", "secrets", ".DS_Store",
}

def permitted(member):
    parts = PurePosixPath(member.name).parts
    if any(part in excluded_parts for part in parts):
        return None
    if member.issym() or member.islnk():
        return None
    if any(part.startswith(".env") and part != ".env.example" for part in parts):
        return None
    if member.name.endswith((".pem", ".key", ".pyc", ".pyo", ".p12", ".pfx")):
        return None
    return member

with tarfile.open(sys.argv[2], "w:gz") as archive:
    archive.add(sys.argv[1], arcname=".", filter=permitted)
PACKAGE

ssh_arguments=(-i "$deploy_key" -o "UserKnownHostsFile=$deploy_known_hosts"
  -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=15)
printf -v quoted_directory '%q' "$deploy_directory"
ssh "${ssh_arguments[@]}" "$deploy_user@$deploy_host" \
  "mkdir -p $quoted_directory && tar -xzf - -C $quoted_directory" < "$deploy_archive"
printf '%s\n' "Uploaded application sources to $deploy_user@$deploy_host:$deploy_directory; existing environment files were preserved."
