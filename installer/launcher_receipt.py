"""Отчёт сборки launcher: подтверждение, что TestPilot.exe собран из текущего launcher.py.

Сам отчёт не доказывает корректность сборки — он фиксирует фактические хеши и
инструмент. Отчёт создаётся только при наличии PyInstaller: ручная подстановка
версии инструмента запрещена, иначе отчёт перестаёт что-либо подтверждать.

Использование::

    python installer/launcher_receipt.py --check           # проверить существующий отчёт
    python installer/launcher_receipt.py --write           # отчёт по уже собранному exe
    python installer/launcher_receipt.py --build           # сборка spec + отчёт (запускает PyInstaller)

Ни одна из этих команд не запускается скриптами упаковки автоматически.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECEIPT_NAME = "launcher-build.json"
EXE_NAME = "TestPilot.exe"
SOURCE_NAME = "launcher.py"
SPEC_NAME = "launcher.spec"


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def pyinstaller_version() -> str:
    """Версия реально установленного PyInstaller. Без него отчёт не создаётся."""
    try:
        import PyInstaller  # noqa: PLC0415 - проверяется только при вызове
    except ModuleNotFoundError:
        print(
            "ОТКАЗ: PyInstaller не установлен, поэтому отчёт сборки launcher невозможен.\n"
            "Установите инструмент вручную и повторите: "
            '.venv\\Scripts\\python.exe -m pip install "pyinstaller==6.11.0"',
            file=sys.stderr,
        )
        raise SystemExit(2)
    version = str(getattr(PyInstaller, "__version__", "")).strip()
    if not version:
        raise SystemExit("ОТКАЗ: не удалось определить версию PyInstaller; отчёт не создан.")
    return version


def build_receipt(
    *, root: Path, tool_version: str, built_at: str | None = None
) -> dict[str, object]:
    """Отчёт по фактическим файлам. Версию инструмента передаёт только вызывающий CLI."""
    exe = root / EXE_NAME
    source = root / SOURCE_NAME
    spec = root / "installer" / SPEC_NAME
    if not exe.is_file():
        raise SystemExit(f"ОТКАЗ: нет собранного {exe}; сначала соберите launcher по spec.")
    if not source.is_file():
        raise SystemExit(f"ОТКАЗ: нет исходника {source}")
    if not tool_version.strip():
        raise SystemExit("ОТКАЗ: пустая версия инструмента сборки; отчёт не создан.")
    receipt: dict[str, object] = {
        "tool": "pyinstaller",
        "tool_version": tool_version.strip(),
        "launcher_source": SOURCE_NAME,
        "launcher_source_sha256": _digest(source),
        "exe_sha256": _digest(exe),
        "exe_size": exe.stat().st_size,
        "built_at": built_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if spec.is_file():
        receipt["spec_sha256"] = _digest(spec)
    return receipt


def write_receipt(receipt: dict[str, object], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def check_receipt(root: Path, receipt_path: Path) -> list[str]:
    """Расхождения между отчётом и файлами. Пустой список — отчёт соответствует."""
    if not receipt_path.is_file():
        return [f"нет отчёта {receipt_path}"]
    try:
        stored = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"отчёт не читается: {error}"]
    problems: list[str] = []
    exe = root / EXE_NAME
    source = root / SOURCE_NAME
    if not exe.is_file():
        problems.append(f"нет {exe}")
    if not source.is_file():
        problems.append(f"нет {source}")
    if problems:
        return problems
    if str(stored.get("launcher_source_sha256", "")) != _digest(source):
        problems.append("launcher.py изменился после сборки launcher")
    if str(stored.get("exe_sha256", "")) != _digest(exe):
        problems.append("SHA-256 TestPilot.exe не совпадает с отчётом")
    if int(stored.get("exe_size", -1)) != exe.stat().st_size:
        problems.append("размер TestPilot.exe не совпадает с отчётом")
    if not str(stored.get("tool", "")).strip() or not str(stored.get("tool_version", "")).strip():
        problems.append("в отчёте нет инструмента сборки или его версии")
    return problems


def build(root: Path) -> None:
    """Штатная сборка по spec. Запускается только явной командой владельца."""
    spec = root / "installer" / SPEC_NAME
    if not spec.is_file():
        raise SystemExit(f"ОТКАЗ: нет рецепта сборки {spec}")
    tool_version = pyinstaller_version()
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(root), "--workpath",
         str(root / "build"), "--specpath", str(root / "installer"), str(spec)],
        cwd=str(root),
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ОТКАЗ: PyInstaller завершился с кодом {result.returncode}; отчёт не создан.")
    receipt = build_receipt(root=root, tool_version=tool_version)
    write_receipt(receipt, root / "installer" / RECEIPT_NAME)
    print(f"Отчёт создан: installer/{RECEIPT_NAME} (pyinstaller {tool_version})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Отчёт сборки launcher TestPilot 1C.")
    parser.add_argument("--root", default=str(ROOT), help="каталог приложения")
    parser.add_argument("--check", action="store_true", help="проверить существующий отчёт")
    parser.add_argument("--write", action="store_true", help="записать отчёт по собранному exe")
    parser.add_argument("--build", action="store_true", help="собрать launcher по spec и записать отчёт")
    args = parser.parse_args(argv)
    root = Path(args.root)
    receipt_path = root / "installer" / RECEIPT_NAME

    if args.build:
        build(root)
        return 0
    if args.check:
        problems = check_receipt(root, receipt_path)
        if problems:
            print("ОТЧЁТ НЕ ПОДТВЕРЖДАЕТ СБОРКУ:", file=sys.stderr)
            for problem in problems:
                print(f"- {problem}", file=sys.stderr)
            return 2
        print(f"Отчёт соответствует файлам: {receipt_path}")
        return 0
    if args.write:
        receipt = build_receipt(root=root, tool_version=pyinstaller_version())
        write_receipt(receipt, receipt_path)
        print(f"Отчёт создан: {receipt_path}")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
