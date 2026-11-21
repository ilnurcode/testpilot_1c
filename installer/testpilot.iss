; TestPilot 1C — установщик для Windows (Inno Setup 6).
;
; На вход идёт только каталог staging, собранный упаковщиком по белому списку.
; Репозиторий целиком в установщик не попадает никогда:
;
;     python installer/stage.py
;     ISCC.exe installer\testpilot.iss
;
; stage.py копирует лишь файлы поставки и пишет version.iss из pyproject.toml,
; поэтому версия установщика имеет один источник.

#define StageDir "staging"
#define StageVersionFile AddBackslash(StageDir) + "version.iss"

#if !FileExists(StageVersionFile)
  #error "Нет каталога staging. Выполните: python installer/stage.py"
#endif
#include StageVersionFile
#if !FileExists(AddBackslash(StageDir) + "manifest.json")
  #error "Нет манифеста staging. Выполните: python installer/stage.py"
#endif

[Setup]
; Постоянный AppId: обновление ставится на место, а не второй копией.
AppId={{6E2F5B7C-8F4A-4F1B-9C6D-2B3E7A1D9F42}
AppName=TestPilot 1C
AppVersion={#AppVersion}
AppPublisher=TestPilot 1C
; Установка только для текущего пользователя: без UAC и без администратора.
; Каталог программы — %LOCALAPPDATA%\Programs\TestPilot1C, ярлыки — в личных
; Start Menu и на личном Рабочем столе, ключ удаления — в HKCU. Глобальных
; артефактов (HKLM, ProgramData, общий Рабочий стол) установка не создаёт,
; поэтому и удаление не требует повышения прав.
; Каталог данных first-run.ps1 (-Packaged) держит отдельно, в %LOCALAPPDATA%\TestPilot1C.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\TestPilot1C
DefaultGroupName=TestPilot 1C
DisableProgramGroupPage=yes
OutputDir=..\dist_setup
OutputBaseFilename=TestPilot_Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64
; Обновление ставится в тот же каталог: настройки и .runtime пользователя живут.
UsePreviousAppDir=yes
; Состав пакета описан в installer/stage.py: без .env, .runtime, .venv, .git,
; пользовательских проектов, evidence, кэшей и артефактов сборки.
VersionInfoVersion={#AppVersionNumeric}

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на Рабочем столе"; GroupDescription: "Дополнительные значки:"

[Files]
; staging/ — весь состав поставки: пакет приложения, статика, миграции,
; скрипты запуска и остановки, compose/requirements и шаблон .env.example.
; version.iss нужен только при сборке; manifest.json остаётся для проверки состава.
; Существующий .env пользователя не входит в пакет и не перезаписывается.
Source: "staging\*"; DestDir: "{app}"; Excludes: "version.iss"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
; Единая точка входа A3: first-run.ps1 ставит окружение в каталог данных
; (%LOCALAPPDATA%\TestPilot1C), создаёт схему (init.ps1) и запускает сервисы.
; Пакет в {app} не изменяется; Python и Docker установщик не ставит.
; {userprograms} — личная папка Start Menu пользователя (не общая ProgramData),
; {userdesktop} — его Рабочий стол. Ярлыки лежат в собственной группе, как раньше,
; но внутри профиля пользователя.
Name: "{userprograms}\TestPilot 1C\TestPilot 1C"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\first-run.ps1"" -Packaged"
Name: "{userprograms}\TestPilot 1C\Остановить TestPilot 1C"; Filename: "{app}\Остановить TestPilot.cmd"
Name: "{userdesktop}\TestPilot 1C"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\first-run.ps1"" -Packaged"; Tasks: desktopicon

[Run]
; Первый запуск после установки. Требует Python 3.12 x64 и Docker Desktop Linux —
; установщик их не ставит, а first-run.ps1 сообщает, чего не хватает.
; Внимание: сборка ISCC и установка на чистую Windows в этой среде НЕ проверялись.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\first-run.ps1"" -Packaged"; Description: "Запустить TestPilot 1C (первый запуск)"; Flags: postinstall skipifsilent nowait
