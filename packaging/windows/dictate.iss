; Inno Setup script for Dictate on Windows (packaging/build.py passes the defines).
; Installs for the current user only (no admin prompt), adds a Start menu entry and starts Dictate.

#ifndef Version
  #define Version "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\build\dist\Dictate"
#endif
#ifndef IconFile
  #define IconFile "..\..\build\icons\dictate.ico"
#endif

[Setup]
AppId={{8B1E7A4C-2F7D-4C1B-9A57-1D3C5E2B6F10}
AppName=Dictate
AppVersion={#Version}
AppPublisher=Dictate
DefaultDirName={localappdata}\Programs\Dictate
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Dictate.exe
UninstallDisplayName=Dictate
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=no

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; files from an older version don't linger
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{userprograms}\Dictate"; Filename: "{app}\Dictate.exe"

[Run]
Filename: "{app}\Dictate.exe"; Description: "Start Dictate"; Flags: nowait postinstall

[Code]
procedure StopDictate();
var
  ResultCode: Integer;
begin
  { Only the installed copy: process names ignore case, and "dictate.exe" may also be a server
    started from source on this PC. }
  Exec('powershell.exe',
    '-NoProfile -NonInteractive -Command "Get-Process -Name Dictate -ErrorAction SilentlyContinue | ' +
    'Where-Object { $_.Path -eq ''' + ExpandConstant('{app}') + '\Dictate.exe'' } | Stop-Process -Force"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopDictate();  { an update replaces the running copy }
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopDictate();
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Dictate');
end;
