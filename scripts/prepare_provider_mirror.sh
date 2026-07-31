#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINATION="${IAC_PROVIDER_MIRROR_DEST:-$ROOT_DIR/data/provider_mirror}"
SOURCE="${1:-${IAC_PROVIDER_MIRROR_SOURCE:-}}"

if [[ -z "$SOURCE" ]]; then
  echo "Usage: $0 /path/to/provider_mirror" >&2
  echo "Or set IAC_PROVIDER_MIRROR_SOURCE." >&2
  exit 2
fi
if [[ ! -d "$SOURCE/registry.terraform.io/hashicorp/aws" ]]; then
  echo "Source does not contain registry.terraform.io/hashicorp/aws: $SOURCE" >&2
  exit 2
fi
if [[ -e "$DESTINATION" ]]; then
  echo "Destination already exists: $DESTINATION" >&2
  exit 2
fi

mkdir -p "$(dirname "$DESTINATION")"
cp --reflink=auto -a "$SOURCE" "$DESTINATION"
echo "Provider mirror prepared at: $DESTINATION"
find "$DESTINATION/registry.terraform.io/hashicorp/aws" -maxdepth 3 -type f -name 'terraform-provider-aws*' -print
