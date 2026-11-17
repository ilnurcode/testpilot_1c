"""Страж синтаксиса скриптов: `.sh` проверяются `bash -n`, `.ps1` — разбором PowerShell.

Сами скрипты не запускаются: только разбор. В CI на Ubuntu без PowerShell `.ps1` дают skip.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / 'scripts'
SHELL_SCRIPTS = sorted(SCRIPTS.glob('*.sh'))
POWERSHELL_SCRIPTS = sorted(SCRIPTS.glob('*.ps1'))


def _bash_exe() -> str:
    r"""bash из Git for Windows: системный C:\Windows\System32\bash.exe — это WSL и не понимает пути Windows."""
    if os.name != 'nt':
        return shutil.which('bash') or 'bash'
    program_files = os.environ.get('ProgramFiles', r'C:\Program Files')
    for candidate in (
        os.path.join(program_files, 'Git', 'bin', 'bash.exe'),
        os.path.join(program_files, 'Git', 'usr', 'bin', 'bash.exe'),
    ):
        if os.path.isfile(candidate):
            return candidate
    return shutil.which('bash') or 'bash'


def _powershell_exe() -> str | None:
    for name in ('pwsh', 'powershell'):
        found = shutil.which(name)
        if found:
            return found
    return None


PARSE_CODE = (
    '$errors = $null\r\n'
    '$tokens = $null\r\n'
    '[System.Management.Automation.Language.Parser]::ParseFile($env:TP_SYNTAX_FILE, [ref]$tokens, [ref]$errors) | Out-Null\r\n'
    'if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Output $_.Message }; exit 1 }\r\n'
    'exit 0\r\n'
)


def test_scripts_directory_is_not_empty():
    assert SHELL_SCRIPTS, 'в scripts/ нет ни одного .sh'
    assert POWERSHELL_SCRIPTS, 'в scripts/ нет ни одного .ps1'


@pytest.mark.parametrize('script', SHELL_SCRIPTS, ids=lambda path: path.name)
def test_shell_scripts_have_valid_syntax(script: Path):
    result = subprocess.run([_bash_exe(), '-n', str(script)], capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, f'{script.name}: bash -n failed\n{result.stderr}'


@pytest.mark.parametrize('script', POWERSHELL_SCRIPTS, ids=lambda path: path.name)
def test_powershell_scripts_parse(script: Path):
    powershell = _powershell_exe()
    if not powershell:
        pytest.skip('PowerShell не установлен (в CI на Ubuntu его нет) — разбор .ps1 пропущен')

    env = {**os.environ, 'TP_SYNTAX_FILE': str(script)}
    result = subprocess.run(
        [powershell, '-NoProfile', '-NonInteractive', '-Command', PARSE_CODE],
        capture_output=True, text=True, env=env, timeout=120,
    )

    assert result.returncode == 0, f'{script.name}: разбор PowerShell не прошёл\n{result.stdout}{result.stderr}'
