"""Отчёт сборки launcher: без PyInstaller отчёт не создаётся, а старый exe не подтверждается."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RECEIPT_PATH = REPO / "installer" / "launcher_receipt.py"


def _receipt_module():
    spec = importlib.util.spec_from_file_location("tp_launcher_receipt", RECEIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


receipt = _receipt_module()


def make_root(root: Path, *, exe: bytes = b"MZ-launcher-build") -> Path:
    (root / "installer").mkdir(parents=True, exist_ok=True)
    (root / "launcher.py").write_text("# bootstrap\n", encoding="utf-8")
    (root / "TestPilot.exe").write_bytes(exe)
    (root / "installer" / "launcher.spec").write_text("# spec\n", encoding="utf-8")
    return root


def test_receipt_records_actual_hashes_and_validates(tmp_path):
    root = make_root(tmp_path / "app")
    data = receipt.build_receipt(root=root, tool_version="6.11.0", built_at="2026-11-17T00:00:00Z")
    assert data["tool"] == "pyinstaller" and data["tool_version"] == "6.11.0"
    assert data["exe_size"] == len(b"MZ-launcher-build")
    assert set(data) >= {"launcher_source_sha256", "exe_sha256", "built_at", "spec_sha256"}

    path = receipt.write_receipt(data, root / "installer" / receipt.RECEIPT_NAME)
    assert receipt.check_receipt(root, path) == []
    assert json.loads(path.read_text(encoding="utf-8"))["exe_sha256"] == data["exe_sha256"]


def test_check_detects_replaced_binary_and_changed_source(tmp_path):
    root = make_root(tmp_path / "app")
    path = receipt.write_receipt(
        receipt.build_receipt(root=root, tool_version="6.11.0"), root / "installer" / receipt.RECEIPT_NAME
    )
    assert receipt.check_receipt(root, path) == []

    (root / "TestPilot.exe").write_bytes(b"MZ-replaced")
    replaced = receipt.check_receipt(root, path)
    assert any("SHA-256 TestPilot.exe" in problem for problem in replaced)

    root = make_root(tmp_path / "app2")
    path = receipt.write_receipt(
        receipt.build_receipt(root=root, tool_version="6.11.0"), root / "installer" / receipt.RECEIPT_NAME
    )
    (root / "launcher.py").write_text("# newer bootstrap\n", encoding="utf-8")
    stale = receipt.check_receipt(root, path)
    assert any("launcher.py изменился" in problem for problem in stale)


def test_missing_receipt_or_empty_tool_version_is_refused(tmp_path):
    root = make_root(tmp_path / "app")
    assert receipt.check_receipt(root, root / "installer" / "none.json"), "нет отчёта — есть расхождение"

    with pytest.raises(SystemExit) as refused:
        receipt.build_receipt(root=root, tool_version="  ")
    assert "версия инструмента" in str(refused.value)

    with pytest.raises(SystemExit) as no_exe:
        receipt.build_receipt(root=tmp_path / "empty", tool_version="6.11.0")
    assert "собранного" in str(no_exe.value)


def test_cli_refuses_without_pyinstaller_and_writes_nothing(tmp_path):
    root = make_root(tmp_path / "app")
    result = subprocess.run(
        [sys.executable, str(RECEIPT_PATH), "--root", str(root), "--write"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode == 0:
        pytest.skip("PyInstaller установлен: отказ по отсутствию инструмента не проверить")
    assert result.returncode == 2
    assert "PyInstaller не установлен" in result.stderr
    assert not (root / "installer" / receipt.RECEIPT_NAME).exists()


def test_receipt_written_by_helper_unlocks_release_staging(tmp_path):
    from test_installer_package import _fake_project, _load_staging_module

    staging = _load_staging_module()
    root = _fake_project(tmp_path / "pkg", include_exe=True)
    data = receipt.build_receipt(root=root, tool_version="6.11.0")
    receipt.write_receipt(data, root / "installer" / receipt.RECEIPT_NAME)

    assert staging.launcher_state(root)["trust"] == "verified"
    manifest = staging.stage(tmp_path / "staging", root, release=True)
    assert "TestPilot.exe" in {entry["path"] for entry in manifest["files"]}
