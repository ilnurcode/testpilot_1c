"""Проверка готовности установки TestPilot 1C из исходников.

Проверка ничего не устанавливает и не копирует: она только сообщает, собраны ли
предпосылки установки и чего не хватает. Это НЕ проверка готовности сервиса:
единственный механизм готовности — запуск `scripts/start.ps1` и его health-check,
владелец A3. Наличие файлов здесь не выдаётся за «сервис готов».

Использование::

    python installer/preflight.py                       # каталог приложения = installer/..
    python installer/preflight.py --root <каталог> --data-dir <каталог>
    python installer/preflight.py --prepare             # создать новый пустой target
    python installer/preflight.py --no-probes            # без docker/портов/PowerShell
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_FILES = ("launcher.py", "pyproject.toml", "scripts/install.ps1", "scripts/start.ps1")
REQUIRED_PYTHON = (3, 12)
PORTS = (8000, 8001, 8002)
DISK_MIN_BYTES = 2 * 1024 ** 3


@dataclass(frozen=True)
class Item:
    """Одна проверка: что проверяли, результат и что делать."""

    name: str
    ok: bool
    detail: str
    blocking: bool = True
    hint: str = ""


@dataclass
class Report:
    root: Path
    data_dir: Path
    items: list[Item] = field(default_factory=list)

    @property
    def missing(self) -> list[Item]:
        return [item for item in self.items if item.blocking and not item.ok]

    @property
    def prerequisites_met(self) -> bool:
        """Собраны ли предпосылки. Готовность сервиса проверяет startup, не этот модуль."""
        return not self.missing

    def as_dict(self) -> dict[str, object]:
        return {
            "prerequisites_met": self.prerequisites_met,
            "root": str(self.root),
            "data_dir": str(self.data_dir),
            "items": [
                {"name": item.name, "ok": item.ok, "blocking": item.blocking, "detail": item.detail, "hint": item.hint}
                for item in self.items
            ],
            "missing": [item.name for item in self.missing],
        }


def _protected_roots() -> tuple[Path, ...]:
    """Каталоги, внутри которых нельзя держать приложение: там нет записи для runtime."""
    roots = []
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "windir"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value))
    return tuple(roots)


def _inside(path: Path, parents: tuple[Path, ...]) -> Path | None:
    for parent in parents:
        try:
            if path == parent or path.is_relative_to(parent):
                return parent
        except (OSError, ValueError):
            continue
    return None


def probe_python() -> Item:
    version = sys.version_info
    ok = version[:2] >= REQUIRED_PYTHON and sys.maxsize > 2 ** 32
    detail = f"Python {version.major}.{version.minor}.{version.micro}, {'x64' if sys.maxsize > 2 ** 32 else 'x86'}"
    return Item(
        "Python 3.12 x64",
        ok,
        detail if ok else detail + " — нужен Python 3.12 x64",
        hint="Установите Python 3.12 x64 вручную и повторите проверку.",
    )


def probe_powershell() -> Item:
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if not executable:
        return Item("PowerShell", False, "PowerShell не найден в PATH", hint="Установите PowerShell 5.1+ вручную.")
    try:
        result = subprocess.run(
            [executable, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.Major"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return Item("PowerShell", False, f"не удалось запустить {executable}: {error}")
    major = result.stdout.strip()
    ok = result.returncode == 0 and major.isdigit() and int(major) >= 5
    return Item("PowerShell", ok, f"{executable}, версия {major or '?'}")


def probe_docker() -> Item:
    if not shutil.which("docker"):
        return Item(
            "Docker Desktop",
            False,
            "docker не найден в PATH",
            hint="Установите Docker Desktop вручную и включите Linux-контейнеры.",
        )
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.OSType}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return Item("Docker Desktop", False, f"docker info не выполнился: {error}")
    ostype = result.stdout.strip()
    ok = result.returncode == 0 and ostype == "linux"
    detail = f"docker OSType={ostype or '?'}"
    if result.returncode != 0:
        detail += f", код {result.returncode}"
    return Item(
        "Docker Desktop",
        ok,
        detail if ok else detail + " — нужен запущенный Docker Desktop в режиме Linux-контейнеров",
        hint="Запустите Docker Desktop и выберите Linux-контейнеры.",
    )


def probe_ports(ports: tuple[int, ...] = PORTS) -> Item:
    busy = []
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(1.0)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                busy.append(port)
    detail = f"порты {', '.join(map(str, ports))}: " + ("свободны" if not busy else f"заняты {busy}")
    return Item("Локальные порты", not busy, detail, hint="Остановите процесс, занимающий порт, и повторите.")


DEFAULT_PROBES = (probe_python, probe_powershell, probe_docker, probe_ports)


def _skipped_probes() -> list[Item]:
    return [
        Item("Проверки окружения", False, "пропущены (--no-probes): предпосылки не подтверждены",
             hint="Запустите без --no-probes на целевой машине.")
    ]


def evaluate(
    root: Path,
    data_dir: Path,
    *,
    protected: tuple[Path, ...] | None = None,
    probes: tuple = DEFAULT_PROBES,
    disk_free: int | None = None,
) -> Report:
    """Собрать отчёт о готовности каталога приложения к запуску."""
    root = Path(root).resolve()
    data_dir = Path(data_dir).resolve()
    protected = _protected_roots() if protected is None else tuple(Path(p) for p in protected)
    report = Report(root=root, data_dir=data_dir)

    report.items.extend([probe() for probe in probes] if probes else _skipped_probes())

    missing_files = [name for name in PACKAGE_FILES if not (root / name).is_file()]
    report.items.append(
        Item(
            "Файлы приложения",
            not missing_files,
            "все файлы на месте" if not missing_files else f"нет: {', '.join(missing_files)}",
            hint="Распакуйте пакет полностью: launcher.py, pyproject.toml, scripts/*.ps1.",
        )
    )

    hit = _inside(root, protected)
    report.items.append(
        Item(
            "Расположение приложения",
            hit is None,
            f"{root}" if hit is None else f"{root} находится внутри {hit}: там нет записи для .env и .runtime",
            hint="Выберите каталог вне Program Files и системных каталогов Windows.",
        )
    )

    writable = os.access(root, os.W_OK)
    report.items.append(
        Item("Запись в каталог приложения", writable, "каталог доступен для записи" if writable else "нет прав на запись",
             hint="Дайте права на запись или выберите каталог пользователя.")
    )

    venv_python = root / ".venv" / "Scripts" / "python.exe"
    report.items.append(
        Item(
            "Виртуальное окружение .venv",
            venv_python.is_file(),
            f"{venv_python}" if venv_python.is_file() else "нет .venv\\Scripts\\python.exe",
            hint="Выполните scripts\\install.ps1 (он создаёт .venv и ставит зависимости).",
        )
    )

    env_file = root / ".env"
    report.items.append(
        Item(
            "Локальная конфигурация .env",
            env_file.is_file(),
            ".env на месте" if env_file.is_file() else "нет .env",
            hint="Выполните scripts\\install.ps1: он вызывает scripts\\local_env.py и создаёт новый .env.",
        )
    )

    # .runtime не предпосылка установки: его создаёт и проверяет startup (A3).
    runtime_dir = root / ".runtime"
    runtime_ok = runtime_dir.is_dir() and os.access(runtime_dir, os.W_OK)
    report.items.append(
        Item(
            "Runtime-каталог .runtime",
            runtime_ok,
            str(runtime_dir) if runtime_ok else "ещё не создан — это делает scripts\\start.ps1",
            blocking=False,
            hint="Готовность сервиса проверяет startup (scripts\\start.ps1 и health-check), не эта проверка.",
        )
    )

    data_hit = _inside(data_dir, protected)
    data_env = data_dir / ".env"
    if data_hit is not None:
        report.items.append(
            Item("Целевой каталог данных", False, f"{data_dir} внутри {data_hit}", hint="Выберите каталог вне Program Files.")
        )
    elif data_env.exists():
        report.items.append(
            Item(
                "Целевой каталог данных",
                False,
                f"в {data_dir} уже есть .env: он не переносится и не изменяется",
                hint="Укажите новый пустой каталог: существующая конфигурация не копируется без согласия.",
            )
        )
    else:
        report.items.append(
            Item("Целевой каталог данных", True, f"{data_dir} (существующий .env не переносится)")
        )

    free = shutil.disk_usage(root).free if disk_free is None else disk_free
    report.items.append(
        Item("Свободное место", free >= DISK_MIN_BYTES, f"{free // (1024 ** 2)} МБ свободно", blocking=False,
             hint="Освободите место: нужно не меньше 2 ГБ.")
    )
    return report


def prepare(data_dir: Path, *, protected: tuple[Path, ...] | None = None) -> Path:
    """Создать новый пустой целевой каталог. Ничего не копирует и не переносит."""
    data_dir = Path(data_dir).resolve()
    protected = _protected_roots() if protected is None else tuple(Path(p) for p in protected)
    hit = _inside(data_dir, protected)
    if hit is not None:
        raise SystemExit(f"ОТКАЗ: {data_dir} находится внутри {hit}; выберите каталог вне Program Files")
    if (data_dir / ".env").exists():
        raise SystemExit(
            f"ОТКАЗ: в {data_dir} уже есть .env; существующая конфигурация не переносится без согласия"
        )
    if data_dir.exists() and any(data_dir.iterdir()):
        raise SystemExit(f"ОТКАЗ: {data_dir} не пуст; укажите новый каталог для установки")
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _print_human(report: Report) -> None:
    for item in report.items:
        mark = "OK  " if item.ok else ("НЕТ " if item.blocking else "ВНИМ")
        print(f"[{mark}] {item.name}: {item.detail}")
    if report.prerequisites_met:
        print("\nПРЕДУСЛОВИЯ СОБРАНЫ:", report.root)
        print("Это не готовность сервиса: её проверяет scripts\\start.ps1 (health-check).")
        print("Далее: powershell -ExecutionPolicy Bypass -File scripts\\install.ps1, затем scripts\\start.ps1")
        return
    print("\nПРЕДУСЛОВИЯ НЕ СОБРАНЫ. Чего не хватает:")
    for item in report.missing:
        print(f"- {item.name}: {item.detail}")
        if item.hint:
            print(f"  что делать: {item.hint}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверить готовность установки TestPilot 1C из исходников.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]), help="каталог приложения")
    parser.add_argument("--data-dir", default="", help="целевой каталог данных вне Program Files")
    parser.add_argument("--prepare", action="store_true", help="создать новый пустой целевой каталог")
    parser.add_argument("--no-probes", action="store_true", help="не проверять Docker, PowerShell и порты")
    parser.add_argument("--json", action="store_true", help="вывести отчёт в JSON")
    args = parser.parse_args(argv)

    root = Path(args.root)
    data_dir = Path(args.data_dir) if args.data_dir else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TestPilot1C"
    if args.prepare:
        data_dir = prepare(data_dir)
        print(f"Создан пустой целевой каталог: {data_dir} (без .env, без копирования данных)")
    report = evaluate(root, data_dir, probes=() if args.no_probes else DEFAULT_PROBES)
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return 0 if report.prerequisites_met else 2


if __name__ == "__main__":
    raise SystemExit(main())
