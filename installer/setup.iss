; Inno Setup 6.3+ — установщик Windows-версии.
; Сборка: build.ps1 (или ISCC.exe installer\setup.iss после PyInstaller).

#define AppName "Music"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppExe "Music.exe"
#define AppPublisher "Music"

[Setup]
AppId={{3709B09A-5496-4939-B670-7B3D8EE0E505}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=Music-Setup-{#AppVersion}
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
AppMutex=Local\MusicPlayer.SingleInstance.v1
VersionInfoVersion={#AppVersion}.0
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "associate"; Description: "Сопоставить аудиофайлы с Music"; GroupDescription: "Интеграция с Windows:"; Flags: checkedonce

[Files]
Source: "..\dist\Music\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "Music.Player"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "Music.Player"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "--register"; Flags: runhidden waituntilterminated; Tasks: associate
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#AppExe}"; Parameters: "--unregister"; Flags: runhidden waituntilterminated; RunOnceId: "UnregisterAssociations"

[Code]
const
  WebView2Client = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebView2Installed(): Boolean;
var
  Version: String;
begin
  Result :=
    RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\' + WebView2Client, 'pv', Version) or
    RegQueryStringValue(HKLM, 'Software\Microsoft\EdgeUpdate\Clients\' + WebView2Client, 'pv', Version) or
    RegQueryStringValue(HKLM, 'Software\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WebView2Client, 'pv', Version);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not WebView2Installed() then
    MsgBox('Не найден Microsoft Edge WebView2 Runtime.' + #13#10#13#10 +
           'Приложение использует его для отображения интерфейса. ' +
           'Установите WebView2 по ссылке:' + #13#10 +
           'https://go.microsoft.com/fwlink/p/?LinkId=2124703' + #13#10#13#10 +
           'Установка продолжится, но без WebView2 приложение не запустится.',
           mbInformation, MB_OK);
end;
