#!/bin/sh
# macOS smoke test (spec §7.2 step 3, §7.5): install a built wheel with install.sh, then check
# the CLI, the detached server, the launcher, a real launch through the applet, and the restart
# that the in-app update performs.
#
#   sh scripts/smoke_macos.sh dist/dental_image_organizer-<version>-py3-none-any.whl
#
# Everything goes to a temporary folder (DIO_APP_DIR and a private Applications folder), so a
# run on a developer's Mac touches neither the real library nor ~/Applications.

set -eu

[ "$#" -eq 1 ] || {
  printf 'usage: %s <wheel>\n' "$0" >&2
  exit 2
}
here=$(cd "$(dirname "$0")" && pwd)
case "$1" in
  /*) wheel=$1 ;;
  *) wheel="$(pwd)/$1" ;;
esac
[ -f "$wheel" ] || {
  printf 'error: no wheel at %s\n' "$wheel" >&2
  exit 2
}

LAUNCH_TIMEOUT_S=60
APPLET_EXIT_S=30
UPDATE_TIMEOUT_S=300

root=$(mktemp -d)
export DIO_APP_DIR="$root/app"
export DIO_APPLICATIONS_DIR="$root/Applications"
export DIO_WHEEL="$wheel"
export DIO_NO_OPEN=1
app="$DIO_APPLICATIONS_DIR/Dental Image Organizer.app"
instance="$DIO_APP_DIR/data/instance.json"

step() {
  printf '\n== %s\n' "$*"
}

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  for log in "$DIO_APP_DIR/log/dio.log" "$DIO_APP_DIR/log/update.log"; do
    if [ -f "$log" ]; then
      printf -- '--- %s\n' "$log" >&2
      tail -n 50 "$log" >&2
    fi
  done
  exit 1
}

# instance.json is written by json.dumps, so each field reads as `"name": value`.
field() {
  [ -f "$instance" ] || return 0
  sed -n "s/.*\"$1\": *\"\{0,1\}\([^,\"}]*\).*/\1/p" "$instance"
}

health() {
  port=$(field port)
  [ -n "$port" ] || return 1
  curl -fsS --max-time 2 "http://127.0.0.1:$port/api/health" 2>/dev/null
}

post() {
  port=$(field port)
  curl -fsS --max-time 10 -X POST -H "Origin: http://127.0.0.1:$port" \
    "http://127.0.0.1:$port/api/$1"
}

get() {
  port=$(field port)
  curl -fsS --max-time 10 "http://127.0.0.1:$port/api/$1"
}

wait_for() {
  # $1: seconds; the rest: a command that succeeds once the condition holds.
  limit=$1
  shift
  waited=0
  until "$@" >/dev/null 2>&1; do
    [ "$waited" -lt "$limit" ] || return 1
    sleep 1
    waited=$((waited + 1))
  done
}

down() {
  ! health
}

stop_server() {
  if health >/dev/null 2>&1; then
    post system/shutdown >/dev/null
    wait_for 30 down || fail "the server did not stop"
  fi
}

cleanup() {
  stop_server >/dev/null 2>&1 || true
}
trap cleanup EXIT

step "install.sh with $wheel"
sh "$here/install.sh"
uv=$(command -v uv)
dio="$("$uv" tool dir --bin)/dio"

step "dio --version"
version=$("$dio" --version)
expected=$(basename "$wheel" | cut -d- -f2)
[ "$version" = "$expected" ] || fail "dio --version printed $version, not $expected"
printf '%s\n' "$version"

step "dio serve --detach --no-browser, then /api/health"
"$dio" serve --detach --no-browser
health | grep -q "\"version\":\"$version\"" || fail "health did not report $version"
stop_server

step "the launcher bundle"
[ -x "$app/Contents/MacOS/applet" ] || fail "no applet in $app"
[ -f "$app/Contents/Resources/applet.icns" ] || fail "no icon in $app"
if xattr -p com.apple.quarantine "$app" >/dev/null 2>&1; then
  fail "the launcher carries a quarantine attribute"
fi
codesign --verify --strict "$app" || fail "the launcher's signature does not verify"

step "launch through the applet"
open "$app"
wait_for "$LAUNCH_TIMEOUT_S" health || fail "the applet did not start the server"
# Only a real launch proves that `do shell script` returned (spec §4.3, §7.2).
applet_running() {
  pgrep -f "$app/Contents/MacOS/applet"
}
applet_gone() {
  ! applet_running
}
wait_for "$APPLET_EXIT_S" applet_gone || fail "the applet was still running after ${APPLET_EXIT_S} s"

step "in-app update: the server stops, upgrades, and starts again"
get system/update | grep -q '"can_update":true' || fail "in-app update is not offered: $(get system/update)"
old_pid=$(field pid)
post system/update | grep -q '"updating"' || fail "POST /system/update did not start the update"
restarted() {
  pid=$(field pid)
  [ -n "$pid" ] && [ "$pid" != "$old_pid" ] && health
}
wait_for "$UPDATE_TIMEOUT_S" restarted || fail "the server did not come back after the update"
printf '%s\n' "$(get system/update)"
cat "$DIO_APP_DIR/log/update.log"

step "dio uninstall-launcher"
stop_server
"$dio" uninstall-launcher --applications-dir "$DIO_APPLICATIONS_DIR"
[ ! -e "$app" ] || fail "the launcher is still there"

printf '\nmacOS smoke test passed (%s)\n' "$version"
