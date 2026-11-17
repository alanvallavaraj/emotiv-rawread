#!/usr/bin/env bash
# Apply this repository's patches to a pristine emotiv v0.0.14 source tree.
#
#   ./apply.sh                 # fetch v0.0.14 from crates.io into ./emotiv-rs
#   ./apply.sh /path/to/tree   # patch a tree you already have
#
# Every patch must apply cleanly; the script stops at the first that does not,
# rather than leaving a half-patched tree behind.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-}"
VERSION=0.0.14

if [ -z "$TARGET" ]; then
  TARGET="$PWD/emotiv-rs"
  if [ -e "$TARGET" ]; then
    echo "error: $TARGET already exists. Remove it, or pass a tree to patch." >&2
    exit 1
  fi
  echo "Fetching emotiv v$VERSION from crates.io …"
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/src.crate" \
    "https://static.crates.io/crates/emotiv/emotiv-$VERSION.crate"
  tar xzf "$tmp/src.crate" -C "$tmp"
  mv "$tmp/emotiv-$VERSION" "$TARGET"
  rm -rf "$tmp"
  echo "  unpacked to $TARGET"
fi

[ -f "$TARGET/Cargo.toml" ] || { echo "error: $TARGET is not a Rust source tree." >&2; exit 1; }

# Dry-run everything first, so a failure costs nothing.
echo "Checking patches against $TARGET …"
for p in "$HERE"/*.patch; do
  if ! patch -p1 --dry-run --silent -d "$TARGET" < "$p" >/dev/null 2>&1; then
    echo "  FAILED  $(basename "$p")" >&2
    echo >&2
    echo "This patch does not apply. The tree is probably not a pristine v$VERSION;" >&2
    echo "nothing has been modified." >&2
    exit 1
  fi
  echo "  ok      $(basename "$p")"
done

echo "Applying …"
for p in "$HERE"/*.patch; do
  patch -p1 --silent -d "$TARGET" < "$p"
done

# src/raw/usb.rs is a new file, not a patch against anything.
install -m 644 "$HERE/src_raw_usb.rs.new" "$TARGET/src/raw/usb.rs"
echo "  added   src/raw/usb.rs"

echo
echo "Done. Build with:"
echo "  cd $TARGET && cargo build --release --features simulate,config-toml,raw,usb"
