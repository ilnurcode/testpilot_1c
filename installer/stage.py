"""Упаковка TestPilot 1C для Windows-установщика по белому списку.

Установщик не должен получать репозиторий целиком: в нём есть `.env`, runtime,
пользовательские проекты, evidence и кэши. Этот модуль копирует только явный
список файлов приложения в отдельный staging-каталог и пишет манифест с
SHA-256, чтобы состав пакета был воспроизводим и проверяем.

Правила:
  * версия берётся только из ``pyproject.toml``;
  * копируются лишь файлы из белого списка и разрешённые деревья исходников;
  * секреты, runtime-состояние, данные проектов, кэши и артефакты сборки
    исключены уже тем, что их нет в списке (плюс вторая проверка запретных путей);
  * каталог staging никогда не перезаписывается, если он не создан этим
    упаковщиком: сборка идёт в новый уникальный каталог и подменяет только
    собственный результат;
  * запускной launcher подтверждается отчётом сборки, а не временем файла;
    неподтверждённый `TestPilot.exe` в пакет не попадает, а release-сборка
    без отчёта завершается отказом.

Использование::

    python installer/stage.py                  # staging в installer/staging
    python installer/stage.py --dest <каталог> # staging в другой каталог
    python installer/stage.py --release        # выпуск: без launcher-отчёта отказ
    python installer/stage.py --verify <каталог>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Iterable
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = ROOT / "installer" / "staging"
STAGING_OWNER = "installer/stage.py"
MANIFEST_NAME = "manifest.json"
VERSION_FILE = "version.iss"
RECEIPT_NAME = "launcher-build.json"
LAUNCHER_EXE = "TestPilot.exe"
LAUNCHER_SOURCE = "launcher.py"

# Точные файлы, которые входят в поставку. Исполняемый launcher добавляется
# отдельно и только при подтверждённом отчёте сборки.
ALLOWED_FILES = (
    LAUNCHER_SOURCE,
    "TestPilot.cmd",
    "Run-TestPilot.bat",
    "Stop-TestPilot.bat",
    "Запустить_в_Docker.cmd",
    "Остановить TestPilot.cmd",
    "Остановить_в_Docker.cmd",
    "pyproject.toml",
    "requirements.lock",
    "alembic.ini",
    "compose.yaml",
    "Dockerfile",
    ".dockerignore",
    ".env.example",
)

# Деревья исходников поставляются целиком с фильтром по расширению.
ALLOWED_TREES = (
    ("testpilot", (".py", ".js", ".css", ".html")),
    ("migrations", (".py", ".mako", ".ini")),
    ("scripts", (".py", ".ps1")),
)

# Скрипты контроля качества и разработки: им нужны tests/ и samples/, которых в
# пакете нет, поэтому в поставку они не входят.
ALWAYS_SHIPPED_SCRIPTS = ("scripts/smoke.py", "scripts/local_env.py", "scripts/pg_backup.py")

EXCLUDED_SCRIPTS = (
    "scripts/acceptance.ps1",
    "scripts/smoke-offline.ps1",
    "scripts/browser-smoke.py",
    "scripts/presentation-browser-smoke.py",
    "scripts/presentation-smoke.py",
    "scripts/projects-browser-smoke.py",
    "scripts/benchmark.py",
    "scripts/export_contracts.py",
    "scripts/a2_smoke.py",
    "scripts/isolation_smoke.py",
)

# Проверочные скрипты называют себя по шаблону; рабочие скрипты в список не попадают.
EXCLUDED_SCRIPT_PATTERNS = ("*_smoke.py", "*-smoke.py")

# Имена, которые не должны попасть в пакет ни при каких условиях.
FORBIDDEN_PARTS = frozenset(
    {
        ".git",
        ".github",
        ".venv",
        ".runtime",
        ".wheelhouse",
        ".pytest_cache",
        "__pycache__",
        "build",
        "dist",
        "dist_setup",
        "node_modules",
        "samples",
        "tests",
        "contracts",
        "docs",
        "installer",
        "testpilot_1c.egg-info",
    }
)
FORBIDDEN_NAMES = frozenset(
    {".env", ".env.local", ".env.production", "credentials.json", "secrets.json", "id_rsa", "id_ed25519"}
)

GATES = (
    "TestPilot.exe не включён: подтверждённого отчёта сборки launcher нет, "
    "штатный запуск идёт через TestPilot.cmd и launcher.py.",
    "Установщик не собран через ISCC и не установлен на чистую Windows x64: "
    "пакет нельзя называть готовым до этой проверки.",
)


class PackagingError(RuntimeError):
    """Упаковка остановлена: пакет получился бы неполным или небезопасным."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def project_version(root: Path = ROOT) -> str:
    """Единственный источник версии: ``[project] version`` в pyproject.toml."""
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(data.get("project", {}).get("version", "")).strip()
    if not re.fullmatch(r"[0-9]+(\.[0-9]+)*([a-zA-Z0-9.+-]*)?", version):
        raise PackagingError("invalid_version", f"в pyproject.toml недопустимая версия: {version!r}")
    return version


def numeric_version(version: str) -> str:
    """Числовая форма для Inno ``VersionInfoVersion`` (нужны три части)."""
    head = re.match(r"[0-9]+(?:\.[0-9]+){0,3}", version)
    parts = (head.group(0) if head else "0").split(".")
    parts += ["0"] * (3 - len(parts))
    return ".".join(parts[:4])


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _is_owned(dest: Path) -> bool:
    """Каталог создан этим упаковщиком? Тогда его можно пересобрать."""
    manifest = dest / MANIFEST_NAME
    if not manifest.is_file():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("generated_by") == STAGING_OWNER


def _dest_guard(root: Path, dest: Path) -> None:
    """Каталог staging не должен затирать репозиторий или пользовательские данные."""
    if dest == root:
        raise PackagingError("invalid_dest", "каталог staging не может совпадать с корнем репозитория")
    if root.is_relative_to(dest):
        raise PackagingError("invalid_dest", "каталог staging не может содержать репозиторий")
    if dest.is_relative_to(root) and dest != (root / "installer" / "staging").resolve():
        raise PackagingError("invalid_dest", "внутри репозитория разрешён только каталог installer/staging")
    for tree, _ in ALLOWED_TREES:
        if dest.is_relative_to(root / tree):
            raise PackagingError("invalid_dest", f"каталог staging не может лежать внутри дерева {tree}")


def _is_forbidden(relative: Path) -> bool:
    return any(part in FORBIDDEN_PARTS for part in relative.parts[:-1]) or relative.name in FORBIDDEN_NAMES


def _tree_files(root: Path, tree: str, suffixes: Iterable[str]) -> list[Path]:
    base = root / tree
    if not base.is_dir():
        raise PackagingError("missing_tree", f"нет обязательного дерева исходников: {tree}")
    allowed = tuple(suffixes)
    files: list[Path] = []
    for path in sorted(base.rglob("*")):
        if path.is_symlink() or not path.is_file():
            # Ссылки и reparse-точки не копируем: они могут указывать за пределы пакета.
            continue
        if not path.resolve().is_relative_to(root):
            continue
        if path.suffix.lower() not in allowed:
            continue
        relative = path.relative_to(root)
        posix = relative.as_posix()
        if _is_forbidden(relative) or posix in EXCLUDED_SCRIPTS:
            continue
        if (
            tree == "scripts"
            and posix not in ALWAYS_SHIPPED_SCRIPTS
            and any(relative.match(pattern) for pattern in EXCLUDED_SCRIPT_PATTERNS)
        ):
            continue
        files.append(relative)
    if not files:
        raise PackagingError("empty_tree", f"в дереве {tree} нет файлов для поставки")
    return files


def collect_files(root: Path = ROOT, *, with_launcher: bool = False) -> list[Path]:
    """Весь белый список файлов, отсортированный, с повторной проверкой запретов."""
    files: list[Path] = []
    names = ALLOWED_FILES + ((LAUNCHER_EXE,) if with_launcher else ())
    for name in names:
        relative = Path(name)
        target = root / relative
        if target.is_symlink():
            raise PackagingError("symlink_entry", f"файл поставки является ссылкой: {name}")
        if not target.is_file():
            raise PackagingError("missing_required", f"нет обязательного файла пакета: {name}")
        if _is_forbidden(relative):
            raise PackagingError("forbidden_path", f"файл белого списка запрещён к поставке: {name}")
        files.append(relative)
    for tree, suffixes in ALLOWED_TREES:
        files.extend(_tree_files(root, tree, suffixes))
    if len(files) != len(set(files)):
        raise PackagingError("duplicate_entry", "белый список даёт один и тот же файл дважды")
    return sorted(files)


def _read_receipt(root: Path) -> dict | None:
    path = root / "installer" / RECEIPT_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def launcher_state(root: Path = ROOT) -> dict[str, object]:
    """Состояние launcher: подтверждён отчётом сборки или нет.

    Время файла ничего не доказывает, поэтому соответствие `TestPilot.exe`
    исходнику проверяется только по отчёту сборки: он фиксирует SHA-256 и
    размер exe, SHA-256 `launcher.py` и версию инструмента сборки.
    """
    source = root / LAUNCHER_SOURCE
    if not source.is_file():
        raise PackagingError("missing_launcher_source", f"нет исходника launcher: {LAUNCHER_SOURCE}")
    source_digest = _digest(source)
    exe = root / LAUNCHER_EXE
    state: dict[str, object] = {
        "source": LAUNCHER_SOURCE,
        "source_sha256": source_digest,
        "executable": None,
        "trust": "absent",
        "reason": "исполняемого launcher нет; штатный запуск идёт через TestPilot.cmd",
    }
    if not exe.is_file():
        return state
    exe_digest = _digest(exe)
    exe_size = exe.stat().st_size
    state["executable"] = {"name": LAUNCHER_EXE, "sha256": exe_digest, "size": exe_size}
    receipt = _read_receipt(root)
    if receipt is None:
        state["trust"] = "unverified"
        state["reason"] = f"нет отчёта сборки installer/{RECEIPT_NAME}; exe в пакет не включается"
        return state
    problems = []
    if str(receipt.get("launcher_source_sha256", "")) != source_digest:
        problems.append("отчёт собран из другой версии launcher.py")
    if str(receipt.get("exe_sha256", "")) != exe_digest:
        problems.append("SHA-256 TestPilot.exe не совпадает с отчётом")
    if int(receipt.get("exe_size", -1)) != exe_size:
        problems.append("размер TestPilot.exe не совпадает с отчётом")
    if not str(receipt.get("tool", "")).strip() or not str(receipt.get("tool_version", "")).strip():
        problems.append("в отчёте не указан инструмент сборки и его версия")
    if problems:
        state["trust"] = "unverified"
        state["reason"] = "; ".join(problems)
        return state
    state["trust"] = "verified"
    state["reason"] = f"подтверждено отчётом сборки: {receipt.get('tool')} {receipt.get('tool_version')}"
    return state


def version_iss(version: str) -> str:
    return (
        "; Сгенерировано installer/stage.py из pyproject.toml — не редактировать.\n"
        f'#define AppVersion "{version}"\n'
        f'#define AppVersionNumeric "{numeric_version(version)}"\n'
    )


def _unique_dir(parent: Path, prefix: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent)))


def _rename_dir(source: Path, target: Path, attempts: int = 6) -> None:
    """Переименовать каталог, переживая кратковременную блокировку Windows."""
    last: OSError | None = None
    for attempt in range(attempts):
        try:
            source.rename(target)
            return
        except OSError as error:
            last = error
            time.sleep(0.2 * (attempt + 1))
    raise PackagingError(
        "dest_busy",
        f"каталог {target} не удалось заменить: {last}. Закройте программы, читающие staging, и повторите",
    )


def _prepare_dest(dest: Path) -> None:
    if dest.exists() and not dest.is_dir():
        raise PackagingError("dest_not_directory", f"путь staging занят файлом: {dest}")
    if not dest.exists():
        return
    entries = list(dest.iterdir())
    if entries and not _is_owned(dest):
        raise PackagingError(
            "dest_not_owned",
            f"каталог {dest} не создан этим упаковщиком; содержимое не тронуто",
        )


def stage(dest: Path | str = DEFAULT_DEST, root: Path | str = ROOT, *, release: bool = False) -> dict[str, object]:
    """Собрать staging в новом каталоге и вернуть записанный манифест."""
    root = Path(root).resolve()
    dest = Path(dest).resolve()
    _dest_guard(root, dest)
    _prepare_dest(dest)

    version = project_version(root)
    launcher = launcher_state(root)
    if release and launcher["trust"] != "verified":
        raise PackagingError(
            "unverified_launcher",
            f"выпуск без подтверждённого launcher невозможен: {launcher['reason']}",
        )
    with_launcher = launcher["trust"] == "verified"
    files = collect_files(root, with_launcher=with_launcher)

    work = _unique_dir(dest.parent, f"{dest.name}.new-")
    try:
        entries = []
        for relative in files:
            source = root / relative
            target = work / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            entries.append({"path": relative.as_posix(), "sha256": _digest(target), "size": target.stat().st_size})

        gates = list(GATES) if not with_launcher else [GATES[1]]
        manifest: dict[str, object] = {
            "generated_by": STAGING_OWNER,
            "package": "testpilot-1c",
            "version": version,
            "version_source": "pyproject.toml",
            "launcher": launcher,
            "file_count": len(entries),
            "total_bytes": sum(int(entry["size"]) for entry in entries),
            "files": entries,
            "excluded": [
                ".env* (кроме .env.example)",
                ".runtime, .venv, .git, .github, .wheelhouse, .pytest_cache, __pycache__",
                "dist, dist_setup, build, testpilot_1c.egg-info",
                "samples, tests, contracts, docs, installer",
                "скрипты контроля качества: " + ", ".join(EXCLUDED_SCRIPTS),
                "проверочные скрипты scripts/*_smoke.py, кроме scripts/smoke.py",
            ],
            "gates": gates,
        }
        (work / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (work / VERSION_FILE).write_text(version_iss(version), encoding="utf-8")

        previous: Path | None = None
        if dest.exists():
            previous = dest.with_name(f"{dest.name}.old-{uuid4().hex[:8]}")
            _rename_dir(dest, previous)
        try:
            _rename_dir(work, dest)
        except BaseException:
            # Прежний каталог возвращаем на место: подмена не должна терять прошлый staging.
            if previous is not None and not dest.exists():
                _rename_dir(previous, dest)
            raise
        if previous is not None:
            shutil.rmtree(previous, ignore_errors=True)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return manifest


def verify(dest: Path | str = DEFAULT_DEST, root: Path | str = ROOT) -> dict[str, object]:
    """Перепроверить staging: digests, отсутствие лишнего, версия, актуальность исходников."""
    root = Path(root).resolve()
    dest = Path(dest).resolve()
    if not (dest / MANIFEST_NAME).is_file():
        raise PackagingError("missing_manifest", f"нет {MANIFEST_NAME} в {dest}")
    if not _is_owned(dest):
        raise PackagingError("dest_not_owned", f"{dest} создан не этим упаковщиком")
    manifest = json.loads((dest / MANIFEST_NAME).read_text(encoding="utf-8"))
    expected_version = project_version(root)
    if manifest.get("version") != expected_version:
        raise PackagingError(
            "version_mismatch",
            f"версия staging {manifest.get('version')!r} не совпадает с pyproject {expected_version!r}; пересоберите staging",
        )
    listed = {str(entry["path"]) for entry in manifest.get("files", [])}
    actual = {
        path.relative_to(dest).as_posix()
        for path in dest.rglob("*")
        if path.is_file() and path.relative_to(dest).as_posix() not in {MANIFEST_NAME, VERSION_FILE}
    }
    missing = sorted(listed - actual)
    extra = sorted(actual - listed)
    if missing:
        raise PackagingError("missing_files", f"в staging нет файлов: {missing[:5]}")
    if extra:
        raise PackagingError("unexpected_files", f"в staging есть файлы вне белого списка: {extra[:5]}")
    for entry in manifest.get("files", []):
        if _digest(dest / str(entry["path"])) != entry["sha256"]:
            raise PackagingError("digest_mismatch", f"файл {entry['path']} не совпадает с манифестом")
    launcher = launcher_state(root)
    if launcher["trust"] == "verified":
        if launcher["executable"]["sha256"] != (manifest.get("launcher") or {}).get("executable", {}).get("sha256"):
            raise PackagingError("launcher_mismatch", "TestPilot.exe изменился после staging; пересоберите staging")
        if LAUNCHER_EXE not in listed:
            raise PackagingError("launcher_mismatch", "launcher подтверждён, но отсутствует в staging")
    elif LAUNCHER_EXE in listed:
        raise PackagingError("unverified_launcher", "в staging лежит неподтверждённый TestPilot.exe")
    if f'#define AppVersion "{expected_version}"' not in (dest / VERSION_FILE).read_text(encoding="utf-8"):
        raise PackagingError("version_file", f"{VERSION_FILE} не содержит версию {expected_version}")
    current = {relative.as_posix(): _digest(root / relative) for relative in collect_files(root, with_launcher=LAUNCHER_EXE in listed)}
    staged = {str(entry["path"]): str(entry["sha256"]) for entry in manifest.get("files", [])}
    drift = sorted(set(current) ^ set(staged)) or sorted(
        path for path, digest in current.items() if staged.get(path) != digest
    )
    if drift:
        raise PackagingError("source_drift", f"исходники изменились после staging; пересоберите staging: {drift[:5]}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Собрать staging-каталог пакета TestPilot 1C по белому списку.")
    parser.add_argument("--dest", default=str(DEFAULT_DEST), help="каталог staging")
    parser.add_argument("--release", action="store_true", help="выпуск: требовать подтверждённый launcher")
    parser.add_argument("--verify", metavar="DIR", help="проверить существующий каталог staging")
    parser.add_argument("--json", action="store_true", help="напечатать манифест целиком")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            manifest = verify(args.verify)
        else:
            stage(args.dest, release=args.release)
            manifest = verify(args.dest)
    except PackagingError as error:
        print(f"УПАКОВКА-ОТКАЗ {error.code}: {error.detail}", file=sys.stderr)
        return 1
    summary = {
        "version": manifest["version"],
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "launcher": manifest["launcher"],
        "gates": manifest["gates"],
    }
    print(json.dumps(manifest if args.json else summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
