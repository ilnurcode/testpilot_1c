[Setup]
AppName=TestPilot 1C
AppVersion=1.0
DefaultDirName={autopf}\TestPilot1C
DefaultGroupName=TestPilot 1C
OutputDir=..\dist_setup
OutputBaseFilename=TestPilot_Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на Рабочем столе"; GroupDescription: "Дополнительные значки:"

[Files]
Source: "..\TestPilot.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\Остановить TestPilot.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion; Excludes: ".venv,build,dist,.git,__pycache__"

[Icons]
Name: "{group}\TestPilot 1C"; Filename: "{app}\TestPilot.exe"
Name: "{group}\Остановить TestPilot 1C"; Filename: "{app}\Остановить TestPilot.cmd"
Name: "{autodesktop}\TestPilot 1C"; Filename: "{app}\TestPilot.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TestPilot.exe"; Description: "Запустить TestPilot 1C"; Flags: nowait postinstall skipifsilent
