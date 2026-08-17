#!/usr/bin/env python3
# ============================================================
#  build_windows.py — Build a Windows installer (.exe) for SinuLead
#  Usage (on WINDOWS, from inside client/, with the venv active):
#      python build_windows.py
#
#  Mirrors build.py's PyInstaller step exactly (same --onedir,
#  same qtawesome/phonenumbers data collection, same keyring
#  hidden-imports -- see build.py's comments for *why* each of
#  those exists, that reasoning is unchanged here).
#
#  Where this diverges from build.py: instead of fpm building
#  .deb/.rpm, this script writes an Inno Setup .iss script next
#  to itself and compiles it with ISCC.exe to produce a single
#  SinuLead-Setup-<version>.exe installer.
#
#  PyInstaller CANNOT cross-compile: a Windows .exe can only be
#  built by running PyInstaller ON Windows. Running this on
#  Linux/macOS will fail at the dependency check below.
#
#  Requirements on the Windows build machine:
#    - Python 3.x + this project's requirements.txt installed
#      (pip install -r requirements.txt)
#    - pip install pyinstaller
#    - Inno Setup 6 installed: https://jrsoftware.org/isdl.php
#      (default install adds ISCC.exe to
#       C:\Program Files (x86)\Inno Setup 6\ISCC.exe -- this
#       script looks there first, then falls back to PATH)
# ============================================================

import shutil
import subprocess
import sys
from pathlib import Path

# ── CONFIG — edit these (kept in sync with build.py's config) ─
APP_NAME = "sinulead"              # package name, no spaces
APP_DISPLAY_NAME = "SinuLead"      # shown in Start Menu / installer UI
VERSION = "1.0"
PUBLISHER = "Judel Federigan"
DESCRIPTION = "SinuLead lead-generation dashboard"
MAIN_SCRIPT = "main.py"            # entry point
PYINSTALLER_NAME = "SinuLead"      # PyInstaller build/output name (== .exe stem)
EXE_ICON = "assets/logo.ico"       # icon baked into the PyInstaller exe + installer
# ─────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.resolve()
ISCC_CANDIDATES = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def run(cmd, **kwargs):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, check=True, cwd=ROOT, **kwargs)


def which(name):
    return shutil.which(name) is not None


def find_iscc():
    for c in ISCC_CANDIDATES:
        if Path(c).exists():
            return c
    found = shutil.which("ISCC.exe") or shutil.which("iscc")
    return found


def step(n, total, msg):
    print(f"\n[{n}/{total}] {msg}")


def main():
    if sys.platform != "win32":
        sys.exit(
            "ERROR: build_windows.py must be run ON Windows.\n"
            "PyInstaller cannot cross-compile a Windows .exe from "
            f"{sys.platform}. Copy this project onto a Windows machine "
            "(or Windows VM) with Python + your requirements.txt + "
            "pyinstaller installed, and run it there."
        )

    total = 5
    print("==============================")
    print(f" Building Windows installer for {APP_NAME}")
    print("==============================")

    # ── Step 1: Check dependencies ──────────────────────────
    step(1, total, "Checking dependencies...")
    if not which("pyinstaller"):
        sys.exit("ERROR: pyinstaller not found. Run: pip install pyinstaller")

    iscc = find_iscc()
    if iscc is None:
        sys.exit(
            "ERROR: Inno Setup's ISCC.exe not found.\n"
            "Install Inno Setup 6 from https://jrsoftware.org/isdl.php "
            "(default settings are fine), then re-run this script."
        )
    print(f"     OK — using ISCC at: {iscc}")

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
        "--add-data", f"{ROOT / 'assets'};assets",  # ';' separator on Windows, not ':'
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
        # token_store.py's file fallback instead of the real Windows
        # Credential Manager backend.
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
    print(f"     Done — output: dist\\{PYINSTALLER_NAME}\\")

    # ── Step 3: Generate the Inno Setup script ──────────────
    step(3, total, "Generating installer.iss...")
    installer_out_dir = ROOT / "installer_output"
    installer_out_dir.mkdir(exist_ok=True)

    iss_path = ROOT / "installer.iss"
    iss_contents = f"""; Auto-generated by build_windows.py -- do not edit by hand,
; edit the CONFIG section at the top of build_windows.py instead
; and re-run it, so this file and the PyInstaller build stay in sync.

#define MyAppName "{APP_DISPLAY_NAME}"
#define MyAppVersion "{VERSION}"
#define MyAppPublisher "{PUBLISHER}"
#define MyAppExeName "{PYINSTALLER_NAME}.exe"

[Setup]
AppId={{{{B5C6C7D2-{APP_NAME.upper()}-0001-0001-SINULEADAPP01}}}}
AppName={{#MyAppName}}
AppVersion={{#MyAppVersion}}
AppPublisher={{#MyAppPublisher}}
DefaultDirName={{autopf}}\\{{#MyAppName}}
DefaultGroupName={{#MyAppName}}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename={APP_NAME}-setup-{VERSION}
Compression=lzma2
SolidCompression=yes
SetupIconFile={EXE_ICON.replace('/', '\\\\')}
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={{app}}\\{{#MyAppExeName}}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{{cm:CreateDesktopIcon}}"; GroupDescription: "{{cm:AdditionalIcons}}"; Flags: unchecked

[Files]
; Grabs the ENTIRE PyInstaller onedir output (exe + all its dependent
; DLLs/data) recursively -- this has to be a folder copy, not just the
; .exe, or the app won't launch (onedir mode, see build.py's comments).
Source: "dist\\{PYINSTALLER_NAME}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{group}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"
Name: "{{group}}\\{{cm:UninstallProgram,{{#MyAppName}}}}"; Filename: "{{uninstallexe}}"
Name: "{{autodesktop}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\{{#MyAppExeName}}"; Description: "{{cm:LaunchProgram,{{#MyAppName}}}}"; Flags: nowait postinstall skipifsilent
"""
    iss_path.write_text(iss_contents, encoding="utf-8")
    print(f"     Done — wrote {iss_path.name}")

    # ── Step 4: Compile the installer with Inno Setup ───────
    step(4, total, "Compiling installer with ISCC...")
    run([iscc, str(iss_path)])
    print("     Done")

    # ── Step 5: Cleanup ──────────────────────────────────────
    step(5, total, "Cleaning up temp files...")
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    (ROOT / f"{PYINSTALLER_NAME}.spec").unlink(missing_ok=True)
    print("     Done")

    setup_file = installer_out_dir / f"{APP_NAME}-setup-{VERSION}.exe"
    print("\n==============================")
    print(" SUCCESS!")
    print(f"\n Installer: installer_output\\{setup_file.name}")
    print("\n Give that single .exe to Windows users -- running it")
    print(f" installs {APP_DISPLAY_NAME} into Program Files, adds a Start")
    print(" Menu entry + optional desktop icon, and registers an uninstaller")
    print(" in Add/Remove Programs.")
    print("==============================")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(f"\nERROR: command failed with exit code {e.returncode}")