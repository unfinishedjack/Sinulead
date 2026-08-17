#!/usr/bin/env python3
# ============================================================
#  build.py — Build .deb + .rpm installers for SinuLead
#  Usage: python3 build.py
#
#  No separate .spec file needed -- this calls the PyInstaller
#  Python API directly with the same options that used to live
#  in sinulead.spec:
#    - onedir (not onefile: core/env.py's frozen-path handling
#      assumes .env sits next to the real executable)
#    - assets/ bundled as data
#    - qtawesome + phonenumbers data files collected (icons /
#      phone parsing render blank/break without them)
#    - keyring backend hiddenimports (keyring probes the
#      platform at runtime, not via a static top-level import,
#      so PyInstaller can't discover these on its own -- without
#      them a frozen build silently falls back to the file-based
#      token store on every platform, even ones with a real
#      keychain)
# ============================================================

import shutil
import subprocess
import sys
from pathlib import Path

# ── CONFIG — edit these ──────────────────────────────────────
APP_NAME = "sinulead"              # package name, no spaces
APP_DISPLAY_NAME = "SinuLead"      # shown in launcher
VERSION = "1.0"
MAINTAINER = "Judel Federigan <judelfederigan1@gmail.com>"
DESCRIPTION = "SinuLead lead-generation dashboard"
MAIN_SCRIPT = "main.py"            # entry point
PYINSTALLER_NAME = "SinuLead"      # PyInstaller build/output name
EXE_ICON = "assets/logo.ico"       # icon baked into the PyInstaller exe
ICON_PATH = "assets/desktop_logo.png"      # 256x256 png for .desktop / icon theme
CATEGORIES = "Utility;"            # freedesktop categories
CLI_NAME = "sinulead"              # command typed in terminal to launch
# ─────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.resolve()


def run(cmd, **kwargs):
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=ROOT, **kwargs)


def which(name):
    return shutil.which(name) is not None


def step(n, total, msg):
    print(f"\n[{n}/{total}] {msg}")


def main():
    total = 6
    print("==============================")
    print(f" Building packages for {APP_NAME}")
    print("==============================")

    # ── Step 1: Check dependencies ──────────────────────────
    step(1, total, "Checking dependencies...")
    if not which("pyinstaller"):
        sys.exit("ERROR: pyinstaller not found. Run: pip install pyinstaller")
    if not which("fpm"):
        sys.exit("ERROR: fpm not found. Run: sudo gem install fpm")

    rpm_available = which("rpmbuild")
    if not rpm_available:
        print("WARN: rpmbuild not found — skipping .rpm (run: sudo apt install rpm to enable)")
    print("     OK")

    # ── Step 2: PyInstaller build (via API, no .spec file) ──
    step(2, total, "Running PyInstaller...")
    for d in ("build", "dist"):
        shutil.rmtree(ROOT / d, ignore_errors=True)

    try:
        import PyInstaller.__main__
    except ImportError:
        sys.exit("ERROR: pyinstaller not importable. Run: pip install pyinstaller")

    pyinstaller_args = [
        str(ROOT / MAIN_SCRIPT),
        "--onedir",
        "--name", PYINSTALLER_NAME,
        "--icon", str(ROOT / EXE_ICON),
        "--add-data", f"{ROOT / 'assets'}:assets",
        "--noconsole",
        "--noconfirm",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT),
        # bundled Font Awesome .ttf/.json -- icons render blank without this
        "--collect-data", "qtawesome",
        # bundled phone-number metadata DB -- parsing breaks without this
        "--collect-data", "phonenumbers",
        # keyring picks a backend by probing the platform at runtime, not
        # via a plain top-level import PyInstaller's static analysis can
        # see -- without these, a frozen build silently falls back to
        # token_store.py's file fallback on every platform.
        "--hidden-import", "keyring.backends.Windows",
        "--hidden-import", "keyring.backends.macOS",
        "--hidden-import", "keyring.backends.SecretService",
        "--hidden-import", "keyring.backends.kwallet",
    ]

    print(f"  PyInstaller.__main__.run({pyinstaller_args})")
    PyInstaller.__main__.run(pyinstaller_args)

    dist_dir = ROOT / "dist" / PYINSTALLER_NAME
    if not dist_dir.exists():
        sys.exit(f"ERROR: expected PyInstaller output at {dist_dir}, not found")
    print(f"     Done — output: dist/{PYINSTALLER_NAME}/")

    # ── Step 3: Set up package folder structure ─────────────
    step(3, total, "Setting up package structure...")
    pkgdir = ROOT / "package"
    shutil.rmtree(pkgdir, ignore_errors=True)

    opt_dir = pkgdir / "opt" / APP_NAME
    apps_dir = pkgdir / "usr" / "share" / "applications"
    icons_dir = pkgdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    bin_dir = pkgdir / "usr" / "local" / "bin"
    for d in (opt_dir, apps_dir, icons_dir, bin_dir):
        d.mkdir(parents=True, exist_ok=True)

    cli_wrapper = bin_dir / CLI_NAME
    cli_wrapper.write_text(
        f"#!/bin/bash\nnohup /opt/{APP_NAME}/{PYINSTALLER_NAME} > /dev/null 2>&1 &\n"
    )
    cli_wrapper.chmod(0o755)

    # Copy entire PyInstaller output
    shutil.copytree(dist_dir, opt_dir, dirs_exist_ok=True)

    # Copy icon
    shutil.copy(ROOT / ICON_PATH, icons_dir / f"{APP_NAME}.png")
    print("     Done")

    # ── Step 4: Create .desktop file ────────────────────────
    step(4, total, "Creating .desktop entry...")
    desktop_file = apps_dir / f"{APP_NAME}.desktop"
    desktop_file.write_text(
        "[Desktop Entry]\n"
        f"Name={APP_DISPLAY_NAME}\n"
        f"Exec=/opt/{APP_NAME}/{PYINSTALLER_NAME}\n"
        f"Icon={APP_NAME}\n"
        "Type=Application\n"
        f"Categories={CATEGORIES}\n"
        "Terminal=false\n"
        f"StartupWMClass={PYINSTALLER_NAME}\n"
    )
    print("     Done")

    # ── Step 5: Build .deb AND .rpm ─────────────────────────
    step(5, total, "Building packages with fpm...")

    fpm_common = [
        "fpm", "-s", "dir",
        "-n", APP_NAME,
        "-v", VERSION,
        "-C", str(pkgdir),
        "--description", DESCRIPTION,
        "--maintainer", MAINTAINER,
        "--prefix", "/",
    ]

    print("     Building .deb (Ubuntu/Debian/Mint)...")
    run(fpm_common + ["-t", "deb", "."])
    print("     .deb done")

    if rpm_available:
        print("     Building .rpm (Fedora/CentOS/RHEL)...")
        run(fpm_common + ["-t", "rpm", "."])
        print("     .rpm done")

    # ── Step 6: Cleanup ──────────────────────────────────────
    step(6, total, "Cleaning up temp files...")
    shutil.rmtree(pkgdir, ignore_errors=True)
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    shutil.rmtree(ROOT / "dist", ignore_errors=True)
    shutil.rmtree(ROOT / "__pycache__", ignore_errors=True)
    # PyInstaller writes a throwaway .spec file as a side effect of
    # PyInstaller.__main__.run() -- not needed since build.py owns
    # the build config now.
    (ROOT / f"{PYINSTALLER_NAME}.spec").unlink(missing_ok=True)
    print("     Done")

    # ── Summary ──────────────────────────────────────────────
    deb_file = f"{APP_NAME}_{VERSION}_amd64.deb"
    rpm_file = f"{APP_NAME}-{VERSION}-1.x86_64.rpm"

    print("\n==============================")
    print(" SUCCESS!")
    print("\n Output files:")
    print(f"   \u2714  {deb_file}     \u2192 Ubuntu / Debian / Mint")
    if rpm_available:
        print(f"   \u2714  {rpm_file}  \u2192 Fedora / CentOS / RHEL")
    print("\n Install .deb:")
    print(f"   sudo dpkg -i {deb_file}")
    if rpm_available:
        print("\n Install .rpm:")
        print(f"   sudo rpm -i {rpm_file}")
    print("\n Uninstall:")
    print(f"   sudo apt remove {APP_NAME}      (Debian/Ubuntu)")
    if rpm_available:
        print(f"   sudo rpm -e {APP_NAME}          (Fedora/CentOS)")
    print("\n After install, running the command below in a terminal launches SinuLead:")
    print(f"   {CLI_NAME}")
    print("==============================")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(f"\nERROR: command failed with exit code {e.returncode}")