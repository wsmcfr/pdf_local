# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Local KB sidecar (run from services/api).

Build with scripts/build-sidecar.ps1, which produces dist/kb-server/ and copies
it to apps/desktop/src-tauri/binaries/kb-server/.
"""
import os
import sys

from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import collect_all, collect_submodules

# Make the local `app` package importable while collecting its submodules.
if SPECPATH not in sys.path:
    sys.path.insert(0, SPECPATH)

datas, binaries, hiddenimports = [], [], []

# Packages that ship data files / native libraries (OCR models, tokenizers, fonts).
for package in [
    "app",
    "rapidocr_onnxruntime",
    "sentence_transformers",
    "jieba",
    "fitz",
    "faiss",
]:
    try:
        package_datas, package_binaries, package_hiddenimports = collect_all(package)
    except Exception as exc:  # noqa: BLE001 - keep building even if an extra is missing
        print(f"[kb-server.spec] collect_all({package}) skipped: {exc}")
        continue
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

# Very large wheels: collect modules only so the sidecar does not bundle test data.
for package in ["torch", "transformers", "onnxruntime"]:
    try:
        hiddenimports += collect_submodules(package)
    except Exception as exc:  # noqa: BLE001
        print(f"[kb-server.spec] collect_submodules({package}) skipped: {exc}")

# Uvicorn selects its protocol/loop implementations dynamically.
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "anyio",
    "h11",
]

a = Analysis(
    ["run_server.py"],
    pathex=[SPECPATH],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # sklearn/scipy are only used by sentence_transformers' evaluation helpers,
    # which the sidecar never calls; dropping them keeps the bundle much smaller.
    excludes=[
        "tkinter",
        "matplotlib",
        "IPython",
        "notebook",
        "pytest",
        "sklearn",
        "scipy",
    ],
    noarchive=False,
)

# Never bundle the Visual C++ runtime. sklearn used to ship an old MSVCP140.dll
# (14.16) that the bootloader loads before Python starts, crashing with
# 0xC0000005. Windows/Python already provide a compatible runtime.
EXCLUDED_RUNTIME_DLLS = {
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "concrt140.dll",
    "ucrtbase.dll",
}
a.binaries = TOC(
    [
        entry
        for entry in a.binaries
        if os.path.basename(entry[0]).lower() not in EXCLUDED_RUNTIME_DLLS
    ]
)

pyz = PYZ(a.pure)

# console=True: Rust reads the "KB_READY {json}" line from stdout and hides the
# window via CREATE_NO_WINDOW (see sidecar.rs).
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="kb-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="kb-server",
)
