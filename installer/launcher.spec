# -*- mode: python ; coding: utf-8 -*-
"""Рецепт сборки TestPilot.exe (launcher) для PyInstaller.

Сборка выполняется вручную владельцем, скрипты её не запускают:

    .venv\\Scripts\\python.exe -m PyInstaller --noconfirm installer\\launcher.spec

После сборки отчёт `installer/launcher-build.json` создаёт
`installer/launcher_receipt.py`; только с этим отчётом упаковщик включает exe
в пакет.
"""
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH задаёт PyInstaller

analysis = Analysis(  # noqa: F821
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # launcher.py использует только стандартную библиотеку; тяжёлые зависимости не нужны.
    excludes=["tkinter", "pytest", "playwright", "numpy", "sklearn", "fastapi", "sqlalchemy"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="TestPilot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
