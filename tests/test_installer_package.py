"""Проверки потребительских свойств Windows-пакета.

Проверяется то, что реально получает установщик: состав staging, безопасность
каталога staging, подтверждение launcher, загрузка приложения из staged-дерева и
разрешимость ссылок установщика. Внутреннее устройство упаковщика не проверяется.
"""

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ISS = REPO / "installer" / "testpilot.iss"
MARKERS = (
    b"oc_sk_MARKER_DO_NOT_SHIP",
    b"TP_EXPERT_PASSWORD=MARKER",
    b"PRIVATE-EVIDENCE-MARKER",
    b"USER-PROJECT-MARKER",
    b"TEST-ONLY-MARKER",
)


def _load_staging_module():
    spec = importlib.util.spec_from_file_location("tp_installer_stage", REPO / "installer" / "stage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


staging = _load_staging_module()


def _write(path: Path, payload: bytes = b"x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _fake_project(
    root: Path,
    *,
    version: str = "9.9.9",
    include_exe: bool = False,
    receipt: bool = False,
    receipt_source_sha: str | None = None,
) -> Path:
    """Временный «репозиторий»: белый список плюс внедрённые приватные данные."""
    for name in staging.ALLOWED_FILES:
        payload = f"{name}\n".encode()
        if name == "pyproject.toml":
            payload = f'[project]\nname = "testpilot-1c"\nversion = "{version}"\n'.encode()
        _write(root / name, payload)
    _write(root / "testpilot" / "__init__.py", b"# package\n")
    _write(root / "testpilot" / "web" / "api.py", b"# routes\n")
    _write(root / "testpilot" / "web" / "static" / "index.html", b"<html></html>\n")
    _write(root / "testpilot" / "web" / "static" / "app.js", b"// ui\n")
    _write(root / "migrations" / "env.py", b"# alembic env\n")
    _write(root / "migrations" / "versions" / "0001_init.py", b"# revision\n")
    _write(root / "scripts" / "start.ps1", b"# start\n")
    _write(root / "scripts" / "install.ps1", b"# install\n")
    _write(root / "scripts" / "acceptance.ps1", b"# qa only, needs samples/\n")
    _write(root / "scripts" / "a2_smoke.py", b"# TEST-ONLY-MARKER\n")
    _write(root / "scripts" / "smoke.py", b"# health check used by start.ps1\n")

    _write(root / ".env", b"TP_LLM_API_KEY=oc_sk_MARKER_DO_NOT_SHIP\nTP_EXPERT_PASSWORD=MARKER\n")
    _write(root / "credentials.json", b'{"token": "oc_sk_MARKER_DO_NOT_SHIP"}\n')
    _write(root / ".runtime" / "ai-acceptance-evidence" / "shot.png", b"PRIVATE-EVIDENCE-MARKER")
    _write(root / "samples" / "demo" / "Module.bsl", b"// USER-PROJECT-MARKER\n")
    _write(root / "tests" / "test_demo.py", b"# TEST-ONLY-MARKER\n")
    _write(root / ".venv" / "Lib" / "site-packages" / "demo.py", b"# TEST-ONLY-MARKER\n")
    _write(root / "testpilot" / "__pycache__" / "api.cpython-312.pyc", b"# TEST-ONLY-MARKER\n")
    _write(root / "dist" / "TestPilot.exe", b"MZ-stale-build")
    _write(root / "docs" / "product-readiness-plan.md", b"# PRIVATE-EVIDENCE-MARKER\n")

    if include_exe:
        exe = root / "TestPilot.exe"
        _write(exe, b"MZ-fake-launcher-build")
    if receipt:
        source = root / "launcher.py"
        exe = root / "TestPilot.exe"
        _write(
            root / "installer" / staging.RECEIPT_NAME,
            json.dumps(
                {
                    "tool": "pyinstaller",
                    "tool_version": "6.11.0",
                    "launcher_source_sha256": receipt_source_sha
                    if receipt_source_sha is not None
                    else hashlib.sha256(source.read_bytes()).hexdigest(),
                    "exe_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
                    "exe_size": exe.stat().st_size,
                    "built_at": "2026-11-17T00:00:00Z",
                }
            ).encode(),
        )
    return root


def _staged_paths(dest: Path) -> list[str]:
    manifest = json.loads((dest / staging.MANIFEST_NAME).read_text(encoding="utf-8"))
    return [entry["path"] for entry in manifest["files"]]


def test_staging_ships_application_package_static_migrations_and_launch_scripts(tmp_path):
    manifest = staging.stage(tmp_path / "staging", REPO)
    files = set(_staged_paths(tmp_path / "staging"))
    for required in (
        "launcher.py",
        "TestPilot.cmd",
        "pyproject.toml",
        "requirements.lock",
        "compose.yaml",
        "alembic.ini",
        ".env.example",
        "testpilot/web/api.py",
        "testpilot/web/static/index.html",
        "testpilot/web/static/app.js",
        "testpilot/web/static/style.css",
        "migrations/env.py",
        "scripts/start.ps1",
        "scripts/install.ps1",
        "scripts/smoke.py",
        "scripts/local_env.py",
    ):
        assert required in files, required
    assert any(path.startswith("migrations/versions/") for path in files)
    assert "scripts/acceptance.ps1" not in files
    assert not any(path.startswith(("tests/", "samples/", "docs/", "contracts/")) for path in files)
    assert manifest["file_count"] == len(files)
    assert manifest["total_bytes"] > 0


def test_staging_excludes_secrets_runtime_private_projects_and_caches(tmp_path):
    root = _fake_project(tmp_path / "src")
    dest = tmp_path / "staging"
    staging.stage(dest, root)
    files = set(_staged_paths(dest))

    for forbidden in (
        ".env",
        "credentials.json",
        "samples/demo/Module.bsl",
        "tests/test_demo.py",
        ".venv/Lib/site-packages/demo.py",
        "testpilot/__pycache__/api.cpython-312.pyc",
        "dist/TestPilot.exe",
        "docs/product-readiness-plan.md",
        ".runtime/ai-acceptance-evidence/shot.png",
        "scripts/acceptance.ps1",
        "scripts/a2_smoke.py",
    ):
        assert forbidden not in files, forbidden

    blob = b"".join(path.read_bytes() for path in dest.rglob("*") if path.is_file())
    for marker in MARKERS:
        assert marker not in blob, marker


def test_unrelated_non_empty_destination_is_refused_and_survives(tmp_path):
    root = _fake_project(tmp_path / "src")
    dest = tmp_path / "user-folder"
    sentinel = dest / "мои-данные.txt"
    _write(sentinel, b"USER-SENTINEL-MUST-SURVIVE")

    with pytest.raises(staging.PackagingError) as refused:
        staging.stage(dest, root)
    assert refused.value.code == "dest_not_owned"
    assert sentinel.read_bytes() == b"USER-SENTINEL-MUST-SURVIVE"
    assert sorted(path.name for path in dest.iterdir()) == ["мои-данные.txt"]
    assert not any(path.name.startswith("user-folder.new-") for path in dest.parent.iterdir())


def test_destination_inside_repository_trees_is_refused(tmp_path):
    root = _fake_project(tmp_path / "src")
    for bad in (root, root / "testpilot" / "staging", root / "scripts" / "staging"):
        with pytest.raises(staging.PackagingError) as refused:
            staging.stage(bad, root)
        assert refused.value.code == "invalid_dest", bad
    assert not (root / "scripts" / "staging").exists()


def test_owned_staging_is_rebuilt_without_touching_neighbours(tmp_path):
    root = _fake_project(tmp_path / "src")
    dest = tmp_path / "staging"
    neighbour = tmp_path / "проекты" / "заказчик.bsl"
    _write(neighbour, b"USER-PROJECT-MARKER")

    staging.stage(dest, root)
    staging.stage(dest, root)

    assert staging.verify(dest, root)["file_count"] > 0
    assert neighbour.read_bytes() == b"USER-PROJECT-MARKER"
    assert not any(".old-" in path.name or ".new-" in path.name for path in tmp_path.iterdir())


def test_package_version_comes_from_pyproject(tmp_path):
    manifest = staging.stage(tmp_path / "staging", REPO)
    declared = staging.project_version(REPO)
    assert manifest["version"] == declared
    assert manifest["version_source"] == "pyproject.toml"
    assert f'#define AppVersion "{declared}"' in (tmp_path / "staging" / staging.VERSION_FILE).read_text(
        encoding="utf-8"
    )
    # Смена версии в pyproject делает прежний staging недействительным.
    root = _fake_project(tmp_path / "src", version="1.2.3")
    staging.stage(tmp_path / "staging-old", root)
    _write(root / "pyproject.toml", b'[project]\nname = "testpilot-1c"\nversion = "1.3.0"\n')
    with pytest.raises(staging.PackagingError) as drifted:
        staging.verify(tmp_path / "staging-old", root)
    assert drifted.value.code == "version_mismatch"


def test_unverified_launcher_is_excluded_and_release_is_refused(tmp_path):
    root = _fake_project(tmp_path / "src", include_exe=True)
    dest = tmp_path / "staging"
    staging.stage(dest, root)
    files = set(_staged_paths(dest))
    assert "TestPilot.exe" not in files
    assert "TestPilot.cmd" in files
    assert staging.launcher_state(root)["trust"] == "unverified"

    with pytest.raises(staging.PackagingError) as refused:
        staging.stage(tmp_path / "release", root, release=True)
    assert refused.value.code == "unverified_launcher"


def test_launcher_receipt_verifies_binary_and_rejects_tampering(tmp_path):
    root = _fake_project(tmp_path / "src", include_exe=True, receipt=True)
    dest = tmp_path / "staging"
    staging.stage(dest, root, release=True)
    assert "TestPilot.exe" in set(_staged_paths(dest))
    assert staging.launcher_state(root)["trust"] == "verified"

    # Подмена байтов exe при прежнем отчёте: выпуск и упаковка подтверждённого
    # launcher больше невозможны.
    _write(root / "TestPilot.exe", b"MZ-replaced-launcher")
    assert staging.launcher_state(root)["trust"] == "unverified"
    with pytest.raises(staging.PackagingError) as replaced:
        staging.stage(tmp_path / "release-after-replace", root, release=True)
    assert replaced.value.code == "unverified_launcher"

    # Отчёт, собранный из другой версии launcher.py, тоже не подтверждает exe.
    untouched = _fake_project(tmp_path / "src2", include_exe=True, receipt=True, receipt_source_sha="0" * 64)
    assert staging.launcher_state(untouched)["trust"] == "unverified"
    with pytest.raises(staging.PackagingError) as stale_source:
        staging.stage(tmp_path / "release2", untouched, release=True)
    assert stale_source.value.code == "unverified_launcher"


def test_verify_detects_changed_files_extra_files_and_digest_drift(tmp_path):
    root = _fake_project(tmp_path / "src", version="2.0.0")
    dest = tmp_path / "staging"
    staging.stage(dest, root)
    staging.verify(dest, root)

    _write(dest / "launcher.py", b"# tampered\n")
    with pytest.raises(staging.PackagingError) as tampered:
        staging.verify(dest, root)
    assert tampered.value.code == "digest_mismatch"
    dest.joinpath("launcher.py").write_bytes((root / "launcher.py").read_bytes())

    _write(dest / "notes.txt", b"leftover\n")
    with pytest.raises(staging.PackagingError) as extra:
        staging.verify(dest, root)
    assert extra.value.code == "unexpected_files"


def test_verify_detects_sources_changed_after_staging(tmp_path):
    root = _fake_project(tmp_path / "src", version="4.0.0")
    dest = tmp_path / "staging"
    staging.stage(dest, root)
    staging.verify(dest, root)

    _write(root / "testpilot" / "web" / "static" / "app.js", b"// newer ui\n")
    with pytest.raises(staging.PackagingError) as changed:
        staging.verify(dest, root)
    assert changed.value.code == "source_drift"

    staging.stage(dest, root)
    _write(root / "testpilot" / "web" / "static" / "extra.js", b"// new file\n")
    with pytest.raises(staging.PackagingError) as added:
        staging.verify(dest, root)
    assert added.value.code == "source_drift"


def test_installer_references_only_staged_package_content(tmp_path):
    staging.stage(tmp_path / "staging", REPO)
    shipped = set(_staged_paths(tmp_path / "staging"))
    text = ISS.read_text(encoding="utf-8")

    block = text.split("[Files]", 1)[1].split("\n[", 1)[0]
    sources = re.findall(r'^Source:\s*"([^"]+)"', block, flags=re.MULTILINE)
    assert sources, "установщик не описывает ни одного источника файлов"
    for source in sources:
        normalized = source.replace("\\", "/")
        assert normalized != "../*", "установщик не должен брать репозиторий целиком"
        assert not normalized.startswith(".."), source
        assert normalized.startswith("staging/"), source

    referenced = set(re.findall(r'Filename:\s*"\{app\}\\([^"]+)"', text))
    assert referenced, "установщик ничего не запускает"
    for name in referenced:
        assert Path(name).as_posix() in shipped, f"установщик ссылается на файл вне пакета: {name}"


def test_installer_is_per_user_and_creates_no_global_artifacts():
    """Установка и удаление не должны требовать администратора.

    Административный режим Inno пишет ключ в HKLM, ярлыки в ProgramData и на
    общий Рабочий стол; такой установщик нельзя удалить без повышения прав
    (unins000.exe возвращал 1). Контракт ниже удерживает установку в профиле
    пользователя.
    """
    text = ISS.read_text(encoding="utf-8")
    setup = text.split("[Setup]", 1)[1].split("\n[", 1)[0]
    icons = text.split("[Icons]", 1)[1].split("\n[", 1)[0]

    assert re.search(r"^PrivilegesRequired=lowest$", setup, re.MULTILINE), "нужен режим без администратора"
    assert re.search(r"^DefaultDirName=\{localappdata\}\\", setup, re.MULTILINE), "каталог программы — в профиле"
    for global_constant in ("{commonpf", "{autopf}", "{commonprograms}", "{autoprograms}", "{commondesktop}", "{autodesktop}", "{group}"):
        assert global_constant not in text, f"общий путь {global_constant} не должен использоваться"

    for line in (entry for entry in icons.splitlines() if entry.startswith("Name:")):
        destination = line.split('Name: "', 1)[1].split('"', 1)[0]
        assert destination.startswith(("{userprograms}\\", "{userdesktop}\\")), destination

    # Ключ удаления пишет сам Inno: в режиме lowest он оказывается в HKCU.
    assert "[Registry]" not in text, "явные записи реестра не нужны и не должны быть глобальными"


def test_launch_chain_references_resolve_inside_package(tmp_path):
    dest = tmp_path / "staging"
    staging.stage(dest, REPO)
    shipped = set(_staged_paths(dest))

    # Цепочка запуска Windows-профиля: TestPilot.cmd → launcher.py → scripts/*.ps1.
    assert {"TestPilot.cmd", "launcher.py", "scripts/start.ps1", "scripts/install.ps1"} <= shipped
    references = set()
    for relative in shipped:
        path = dest / relative
        if path.suffix not in {".ps1", ".py", ".cmd", ".bat"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        references |= {
            found.replace("\\", "/")
            for found in re.findall(r"scripts[\\/][A-Za-z0-9_.-]+\.(?:ps1|py)", text)
        }
        references |= {
            f"{Path(relative).parent.as_posix()}/{name}"
            for name in re.findall(r"Join-Path \$PSScriptRoot '([A-Za-z0-9_.-]+\.ps1)'", text)
        }
    assert references, "в цепочке запуска не найдено ссылок на скрипты"
    dangling = sorted(name for name in references if name not in shipped)
    assert not dangling, f"пакет ссылается на отсутствующие скрипты: {dangling}"


def test_staged_tree_loads_app_static_and_migrations(tmp_path):
    """Smoke без 1С, сети и базы: staged-дерево обязано импортироваться и читаться."""
    dest = tmp_path / "staging"
    staging.stage(dest, REPO)
    survived = staging.verify(dest, REPO)
    assert survived["file_count"] > 0

    code = """
import pathlib, sys
root = pathlib.Path.cwd().resolve()
sys.meta_path[:] = [
    finder for finder in sys.meta_path
    if "editable" not in type(finder).__module__ and "editable" not in getattr(finder, "__module__", "")
]
sys.path.insert(0, str(root))
import testpilot
loaded = pathlib.Path(testpilot.__file__).resolve()
assert loaded.is_relative_to(root), loaded
from testpilot.web import api as web
assert web.STATIC.joinpath("index.html").is_file()
assert web.STATIC.joinpath("style.css").is_file()
import testpilot.impact.api
import testpilot.intelligence.api
assert web.app.openapi()["paths"], "нет маршрутов"
from alembic.config import Config
from alembic.script import ScriptDirectory
cfg = Config(str(root / "alembic.ini"))
cfg.set_main_option("script_location", str(root / "migrations"))
script = ScriptDirectory.from_config(cfg)
heads = script.get_heads()
assert heads, "нет ревизий миграций"
revisions = list(script.walk_revisions())
print("IMPORT-OK", loaded.name, len(revisions), heads)
"""
    env = {
        **os.environ,
        "PYTHONPATH": str(dest),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(dest),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "IMPORT-OK" in result.stdout
