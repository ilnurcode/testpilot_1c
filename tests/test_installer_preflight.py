"""Проверки готовности установки из исходников: без Docker, сети и установок."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PREFLIGHT_PATH = REPO / "installer" / "preflight.py"


def _preflight():
    spec = importlib.util.spec_from_file_location("tp_preflight", PREFLIGHT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass ищет модуль в sys.modules
    spec.loader.exec_module(module)
    return module


preflight = _preflight()

ALL_OK = (
    lambda: preflight.Item("Python 3.12 x64", True, "Python 3.12.10, x64"),
    lambda: preflight.Item("PowerShell", True, "powershell, версия 5"),
    lambda: preflight.Item("Docker Desktop", True, "docker OSType=linux"),
    lambda: preflight.Item("Локальные порты", True, "порты 8000, 8001, 8002: свободны"),
)


def make_package(root: Path, *, venv=True, env=True, runtime=True) -> Path:
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    for name in ("launcher.py", "pyproject.toml", "scripts/install.ps1", "scripts/start.ps1"):
        (root / name).write_text("# package\n", encoding="utf-8")
    if venv:
        (root / ".venv" / "Scripts").mkdir(parents=True, exist_ok=True)
        (root / ".venv" / "Scripts" / "python.exe").write_bytes(b"MZ")
    if env:
        (root / ".env").write_text("TP_EXPERT_PASSWORD=local-only\n", encoding="utf-8")
    if runtime:
        (root / ".runtime").mkdir()
    return root


def evaluate(root, data_dir, **kwargs):
    return preflight.evaluate(
        root, data_dir, protected=(), probes=ALL_OK, disk_free=10 * 1024 ** 3, **kwargs
    )


def missing_names(report) -> set[str]:
    return {item.name for item in report.missing}


def test_ready_requires_venv_env_and_runtime(tmp_path):
    root = make_package(tmp_path / "app")
    report = evaluate(root, tmp_path / "data")
    assert report.prerequisites_met, report.as_dict()

    hints = {item.name: item.hint for item in report.items}
    assert "install.ps1" in hints["Виртуальное окружение .venv"]

    (root / ".venv" / "Scripts" / "python.exe").unlink()
    broken = evaluate(root, tmp_path / "data")
    assert not broken.prerequisites_met
    assert "Виртуальное окружение .venv" in missing_names(broken)

    # .runtime — состояние startup, а не предпосылка установки: его отсутствие не блокирует.
    (root / ".venv" / "Scripts" / "python.exe").write_bytes(b"MZ")
    (root / ".runtime").rmdir()
    assert evaluate(root, tmp_path / "data").prerequisites_met


def test_missing_env_is_reported_and_nothing_is_created(tmp_path):
    root = make_package(tmp_path / "app", env=False)
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    report = evaluate(root, tmp_path / "data")

    assert not report.prerequisites_met
    assert "Локальная конфигурация .env" in missing_names(report)
    assert not (root / ".env").exists()
    after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    assert after == before


def test_environment_inside_program_files_is_blocking(tmp_path):
    program_files = tmp_path / "Program Files"
    root = make_package(program_files / "TestPilot1C")
    report = preflight.evaluate(
        root, tmp_path / "data", protected=(program_files,), probes=ALL_OK, disk_free=10 * 1024 ** 3
    )
    assert not report.prerequisites_met
    item = next(item for item in report.items if item.name == "Расположение приложения")
    assert not item.ok and "Program Files" in item.detail
    assert "Program Files" in item.hint


def test_existing_env_in_target_is_never_moved_or_copied(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    env = data_dir / ".env"
    env.write_bytes(b"TP_EXPERT_PASSWORD=USER-SECRET")

    with pytest.raises(SystemExit) as refused:
        preflight.prepare(data_dir, protected=())
    assert "не переносится" in str(refused.value)
    assert env.read_bytes() == b"TP_EXPERT_PASSWORD=USER-SECRET"
    assert [path.name for path in data_dir.iterdir()] == [".env"]

    report = evaluate(make_package(tmp_path / "app"), data_dir)
    assert "Целевой каталог данных" in missing_names(report)


def test_prepare_creates_only_an_empty_directory(tmp_path):
    target = tmp_path / "новый каталог" / "TestPilot1C"
    created = preflight.prepare(target, protected=())
    assert created == target.resolve()
    assert target.is_dir() and list(target.iterdir()) == []

    target.joinpath("чужой-файл.txt").write_bytes(b"user data")
    with pytest.raises(SystemExit) as refused:
        preflight.prepare(target, protected=())
    assert "не пуст" in str(refused.value)
    assert target.joinpath("чужой-файл.txt").read_bytes() == b"user data"


def test_prepare_refuses_program_files_target(tmp_path):
    program_files = tmp_path / "Program Files"
    with pytest.raises(SystemExit) as refused:
        preflight.prepare(program_files / "TestPilot1C", protected=(program_files,))
    assert "Program Files" in str(refused.value)
    assert not (program_files / "TestPilot1C").exists()


def test_disabled_probes_never_report_ready(tmp_path):
    root = make_package(tmp_path / "app")
    report = preflight.evaluate(root, tmp_path / "data", protected=(), probes=(), disk_free=10 * 1024 ** 3)
    assert not report.prerequisites_met
    assert "Проверки окружения" in missing_names(report)


def test_missing_package_file_is_named(tmp_path):
    root = make_package(tmp_path / "app")
    (root / "scripts" / "install.ps1").unlink()
    report = evaluate(root, tmp_path / "data")
    assert not report.prerequisites_met
    item = next(item for item in report.items if item.name == "Файлы приложения")
    assert "scripts/install.ps1" in item.detail


def test_cli_reports_not_ready_and_writes_nothing(tmp_path):
    root = make_package(tmp_path / "app", venv=False, env=False, runtime=False)
    data_dir = tmp_path / "data"
    result = subprocess.run(
        [sys.executable, str(PREFLIGHT_PATH), "--root", str(root), "--data-dir", str(data_dir), "--no-probes", "--json"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 2, result.stderr
    payload = json.loads(result.stdout)
    assert payload["prerequisites_met"] is False
    assert {"Виртуальное окружение .venv", "Локальная конфигурация .env"} <= set(payload["missing"])
    assert not data_dir.exists()
    assert not (root / ".env").exists()
