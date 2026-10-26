"""`scripts/install.sh` (spec §7.3) under a POSIX sh, with every macOS and network command faked.

The macOS smoke test runs the real thing; these cover the branches a real Mac cannot reach on
demand: Intel, old macOS, Rosetta, a missing uv, and the overrides.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "install.sh"
INSTALL_URL = (
    "https://raw.githubusercontent.com/jimwellh/Dental-Image-Organizer/main/scripts/install.sh"
)
TIMEOUT_S = 60.0
TOOLS = ("sh", "grep", "cat", "chmod", "mkdir", "cp", "basename")

pytestmark = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in TOOLS), reason="needs a POSIX sh and its tools"
)

# One body for every fake: it logs its name and arguments, then acts by name.
FAKE = """#!/bin/sh
name=$(basename "$0")
echo "$name $*" >> "$FAKE_CALLS"
case "$name" in
  uname) echo "${FAKE_UNAME:-Darwin}" ;;
  sysctl)
    case "$2" in
      hw.optional.arm64) [ -n "${FAKE_ARM64-1}" ] || exit 1; echo "${FAKE_ARM64-1}" ;;
      sysctl.proc_translated) echo "${FAKE_TRANSLATED:-0}" ;;
    esac ;;
  sw_vers) echo "${FAKE_MACOS:-15.1}" ;;
  uv)
    if [ "$1 $2 $3" = "tool dir --bin" ]; then echo "$FAKE_BIN"; fi ;;
  curl)
    # The uv installer: put a uv where the official one does.
    echo "mkdir -p \\"\\$HOME/.local/bin\\""
    echo "cp \\"$FAKE_BIN/uv.spare\\" \\"\\$HOME/.local/bin/uv\\""
    ;;
esac
exit 0
"""


@dataclass(frozen=True)
class Run:
    code: int
    out: str
    err: str
    calls: list[str]


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    for name in ("uname", "sysctl", "sw_vers", "uv", "dio", "open", "curl", "arch", "uv.spare"):
        path = bin_dir / name
        path.write_text(FAKE, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    return bin_dir


def _run(tmp_path: Path, fake_bin: Path, *, stdin: str | None = None, **env: str) -> Run:
    calls = tmp_path / "calls.txt"
    calls.touch()
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    # The fakes first, then only the folders of the shell's own tools, so a uv installed on
    # this machine is never found.
    system = {str(Path(shutil.which(tool) or "").parent) for tool in TOOLS}
    environment = {
        "PATH": os.pathsep.join([str(fake_bin), *sorted(system)]),
        "HOME": str(home),
        "FAKE_CALLS": str(calls),
        "FAKE_BIN": str(fake_bin),
        **env,
    }
    if "SYSTEMROOT" in os.environ:
        # Windows programs, such as the MSYS shell, need it to start.
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    sh = shutil.which("sh")
    assert sh is not None
    args = [sh] if stdin is not None else [sh, str(SCRIPT)]
    done = subprocess.run(
        args,
        input=stdin,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    return Run(done.returncode, done.stdout, done.stderr, calls.read_text().splitlines())


def _named(run: Run, name: str) -> list[str]:
    return [call for call in run.calls if call.split(" ", 1)[0] == name]


def test_installs_from_pypi_then_adds_and_opens_the_launcher(
    tmp_path: Path, fake_bin: Path
) -> None:
    run = _run(tmp_path, fake_bin)

    assert run.code == 0, run.err
    assert "uv tool install --python 3.12 --upgrade dental-image-organizer" in run.calls
    [launcher] = _named(run, "dio")
    assert launcher.startswith("dio install-launcher --uv-path ")
    assert launcher.endswith("/uv")
    [opened] = _named(run, "open")
    assert opened.endswith("/Applications/Dental Image Organizer.app")
    assert "Keep in Dock" in run.out
    assert "uninstall-launcher" in run.out
    assert _named(run, "curl") == []


def test_a_wheel_override_installs_that_file(tmp_path: Path, fake_bin: Path) -> None:
    wheel = tmp_path / "dist" / "dental_image_organizer-1.0.0-py3-none-any.whl"
    wheel.parent.mkdir()
    wheel.write_bytes(b"")

    run = _run(
        tmp_path,
        fake_bin,
        DIO_WHEEL="dist/dental_image_organizer-1.0.0-py3-none-any.whl",
        DIO_NO_OPEN="1",
        DIO_APPLICATIONS_DIR="/tmp/smoke/Applications",
    )

    assert run.code == 0, run.err
    [install] = [call for call in run.calls if call.startswith("uv tool install")]
    assert install.startswith("uv tool install --python 3.12 --reinstall /")
    assert install.endswith("/dist/dental_image_organizer-1.0.0-py3-none-any.whl")
    [launcher] = _named(run, "dio")
    assert launcher.endswith("--applications-dir /tmp/smoke/Applications")
    assert _named(run, "open") == []


def test_a_missing_wheel_is_an_error(tmp_path: Path, fake_bin: Path) -> None:
    run = _run(tmp_path, fake_bin, DIO_WHEEL="nope.whl")

    assert run.code == 1
    assert "DIO_WHEEL names no file" in run.err
    assert _named(run, "uv") == []


def test_installs_uv_first_when_it_is_missing(tmp_path: Path, fake_bin: Path) -> None:
    (fake_bin / "uv").unlink()

    run = _run(tmp_path, fake_bin)

    assert run.code == 0, run.err
    assert _named(run, "curl") == ["curl -LsSf https://astral.sh/uv/install.sh"]
    assert "Installing uv" in run.out
    [launcher] = _named(run, "dio")
    assert launcher.endswith("/.local/bin/uv")


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"FAKE_UNAME": "Linux"}, "installs on macOS only"),
        ({"FAKE_ARM64": ""}, "needs a Mac with Apple silicon"),
        ({"FAKE_MACOS": "13.6.1"}, "needs macOS 14 Sonoma or later; this Mac runs macOS 13.6.1"),
        ({"FAKE_MACOS": "garbage"}, "Could not read the macOS version"),
    ],
)
def test_unsupported_macs_are_refused_before_anything_is_installed(
    tmp_path: Path, fake_bin: Path, env: dict[str, str], message: str
) -> None:
    run = _run(tmp_path, fake_bin, **env)

    assert run.code == 1
    assert message in run.err
    assert _named(run, "uv") == [] and _named(run, "curl") == []


def test_under_rosetta_a_saved_script_runs_itself_again_natively(
    tmp_path: Path, fake_bin: Path
) -> None:
    run = _run(tmp_path, fake_bin, FAKE_TRANSLATED="1")

    assert run.code == 0, run.err
    assert _named(run, "arch") == [f"arch -arm64 /bin/sh {SCRIPT}"]
    assert _named(run, "uv") == []


def test_under_rosetta_a_piped_script_fetches_itself_again(tmp_path: Path, fake_bin: Path) -> None:
    run = _run(tmp_path, fake_bin, stdin=SCRIPT.read_text(encoding="utf-8"), FAKE_TRANSLATED="1")

    assert run.code == 0, run.err
    assert _named(run, "curl") == [f"curl -LsSf {INSTALL_URL}"]
    [arch] = _named(run, "arch")
    assert arch.startswith("arch -arm64 /bin/sh -c ")
    assert _named(run, "uv") == []
