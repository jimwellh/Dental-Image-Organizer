#!/bin/sh
# Dental Image Organizer installer for macOS (spec §7.3). Run it with:
#
#   curl -LsSf https://raw.githubusercontent.com/jimwellh/Dental-Image-Organizer/main/scripts/install.sh | sh
#
# Running it again upgrades the app and rebuilds its launcher. Environment overrides:
#   DIO_WHEEL              install this wheel file instead of the release on PyPI
#   DIO_APPLICATIONS_DIR   put the launcher here instead of ~/Applications
#   DIO_NO_OPEN=1          do not open the app at the end
#
# Marker that tells a saved copy of this script from a piped one: dio-install-script

set -eu

INSTALL_URL="https://raw.githubusercontent.com/jimwellh/Dental-Image-Organizer/main/scripts/install.sh"
UV_INSTALL_URL="https://astral.sh/uv/install.sh"
PACKAGE="dental-image-organizer"
PYTHON_VERSION="3.12"
APP_NAME="Dental Image Organizer"
MIN_MACOS=14

say() {
  printf '%s\n' "$*"
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

check_platform() {
  [ "$(uname -s)" = "Darwin" ] || fail "$APP_NAME installs on macOS only."
  # Not `uname -m`: a Terminal running under Rosetta reports x86_64 on Apple Silicon.
  if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || true)" != "1" ]; then
    fail "$APP_NAME needs a Mac with Apple silicon (M1 or later)."
  fi
}

# Under Rosetta, uv would provision an Intel Python, for which the AI runtime has no wheels.
leave_rosetta() {
  if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || true)" != "1" ]; then
    return 0
  fi
  say "Terminal is running under Rosetta; restarting the installer natively."
  # exec keeps the environment, so overrides such as DIO_WHEEL survive the restart.
  if [ -f "$0" ] && grep -q "dio-install-script" "$0" 2>/dev/null; then
    exec arch -arm64 /bin/sh "$0" "$@"
  fi
  # Piped through sh, there is no file to run again, so fetch the script once more.
  exec arch -arm64 /bin/sh -c "$(curl -LsSf "$INSTALL_URL")"
}

check_macos_version() {
  version=$(sw_vers -productVersion)
  major=${version%%.*}
  case "$major" in
    '' | *[!0-9]*) fail "Could not read the macOS version ($version)." ;;
  esac
  if [ "$major" -lt "$MIN_MACOS" ]; then
    fail "$APP_NAME needs macOS $MIN_MACOS Sonoma or later; this Mac runs macOS $version."
  fi
}

# The absolute path of uv, or nothing. The official installer puts it in ~/.local/bin, which a
# shell started before the install may not have on its PATH yet.
find_uv() {
  found=$(command -v uv 2>/dev/null || true)
  case "$found" in
    /*)
      printf '%s\n' "$found"
      return 0
      ;;
  esac
  for dir in "${XDG_BIN_HOME:-}" "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    if [ -n "$dir" ] && [ -x "$dir/uv" ]; then
      printf '%s\n' "$dir/uv"
      return 0
    fi
  done
  return 0
}

ensure_uv() {
  uv=$(find_uv)
  if [ -n "$uv" ]; then
    return 0
  fi
  say "Installing uv, the tool that installs and updates $APP_NAME, from $UV_INSTALL_URL"
  curl -LsSf "$UV_INSTALL_URL" | sh
  uv=$(find_uv)
  [ -n "$uv" ] || fail "uv was installed, but it could not be found; open a new Terminal window and run this installer again."
}

install_app() {
  if [ -n "${DIO_WHEEL:-}" ]; then
    [ -f "$DIO_WHEEL" ] || fail "DIO_WHEEL names no file: $DIO_WHEEL"
    case "$DIO_WHEEL" in
      /*) wheel=$DIO_WHEEL ;;
      *) wheel="$(pwd)/$DIO_WHEEL" ;;
    esac
    say "Installing $APP_NAME from $wheel"
    "$uv" tool install --python "$PYTHON_VERSION" --reinstall "$wheel"
  else
    say "Installing $APP_NAME from PyPI"
    # --upgrade: a second run brings an installed copy up to date.
    "$uv" tool install --python "$PYTHON_VERSION" --upgrade "$PACKAGE"
  fi
  bin_dir=$("$uv" tool dir --bin)
  dio="$bin_dir/dio"
  [ -x "$dio" ] || fail "The install finished, but $dio is missing."
}

install_launcher() {
  applications_dir=${DIO_APPLICATIONS_DIR:-"$HOME/Applications"}
  if [ -n "${DIO_APPLICATIONS_DIR:-}" ]; then
    "$dio" install-launcher --uv-path "$uv" --applications-dir "$applications_dir"
  else
    "$dio" install-launcher --uv-path "$uv"
  fi
  app="$applications_dir/$APP_NAME.app"
}

finish() {
  if [ "${DIO_NO_OPEN:-}" != "1" ]; then
    # Its icon then shows in the Dock, where the user can keep it.
    open "$app"
  fi
  say ""
  say "$APP_NAME is installed: $app"
  say ""
  say "Keep it in the Dock: while the app is open, right-click its Dock icon, then choose"
  say "Options > Keep in Dock. You can also open it with Spotlight."
  say ""
  say "Update:    the app offers updates itself, or run: \"$uv\" tool upgrade $PACKAGE"
  say "Uninstall: \"$dio\" uninstall-launcher && \"$uv\" tool uninstall $PACKAGE"
  say "           (your photo library in ~/DentalImageOrganizer is kept)"
}

main() {
  check_platform
  leave_rosetta "$@"
  check_macos_version
  ensure_uv
  install_app
  install_launcher
  finish
}

# Everything runs from here, so a download cut short never runs half the script.
main "$@"
