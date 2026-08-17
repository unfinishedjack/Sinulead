# sinulead.spec
#
# Build with:  pyinstaller sinulead.spec
# (run from inside client/, with the client's venv active)
#
# --onedir, not --onefile: --onefile re-extracts everything to a temp
# dir on *every launch*, which is slow and pointless for a real desktop
# app the user installs once. --onedir starts instantly and is what
# core/env.py's frozen-path handling assumes (.env sits next to the
# actual executable, not a throwaway temp copy of it).

import qtawesome
import phonenumbers
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

datas = [
    ("assets", "assets"),  # logo, avatars, icons -- core/config.py finds these via __file__
]
datas += collect_data_files("qtawesome")   # bundled Font Awesome .ttf/.json -- icons render blank without this
datas += collect_data_files("phonenumbers")  # bundled metadata DB -- phone parsing breaks without this

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "keyring.backends.Windows",
        "keyring.backends.macOS",
        "keyring.backends.SecretService",
        "keyring.backends.kwallet",
        # keyring picks a backend by probing the platform at runtime,
        # not via a plain top-level import PyInstaller's static
        # analysis can see -- without these listed explicitly, a
        # frozen build silently falls back to token_store.py's file
        # fallback on every platform, even ones with a real keychain.
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SinuLead",
    debug=False,
    strip=False,
    upx=True,
    console=False,  # GUI app -- no terminal window
    icon="assets/logo.ico",  # see note below: needs converting from logo.png first
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="SinuLead",
)
