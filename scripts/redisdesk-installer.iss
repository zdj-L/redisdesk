; RedisDesk 1.0.0 unsigned internal preview installer.
; Built from the already-staged standalone folder, not from user data.
; Compile with: scripts\build_installer.ps1

#define MyAppName "RedisDesk"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "RedisDesk"
#define MyAppExeName "RedisDesk.exe"
#define MySourceDir AddBackslash(SourcePath) + "..\release\RedisDesk-1.0.0-internal-win64-portable"

[Setup]
AppId={{8F3A1C2E-6B47-4D19-A5E2-91C0D4B7E836}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion} (internal preview)
AppPublisher={#MyAppPublisher}
AppComments=Unsigned internal preview. User data stays in %LOCALAPPDATA%\RedisDesk-Internal\1.0.0 and is not bundled or removed.
VersionInfoVersion=1.0.0.0
VersionInfoProductName=RedisDesk
VersionInfoProductVersion=1.0.0
VersionInfoDescription=RedisDesk 1.0.0 internal preview setup (unsigned)
DefaultDirName={autopf}\RedisDesk
DefaultGroupName=RedisDesk
DisableProgramGroupPage=yes
AllowNoIcons=yes
OutputDir={#SourcePath}..\release
OutputBaseFilename=RedisDesk-1.0.0-internal-win64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName=RedisDesk 1.0.0
; Do not close or kill a running RedisDesk instance.
CloseApplications=no
RestartApplications=no
UsedUserAreasWarning=no
InfoBeforeFile={#SourcePath}..\docs\INTERNAL_INSTALLER-wizard.txt
SetupLogging=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Program files and bundled licenses/notices only. The source folder is the reviewed portable payload and contains no user connections, passwords, .env, or logs.
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "RedisDesk 1.0.0 internal preview (unsigned)"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "RedisDesk 1.0.0 internal preview (unsigned)"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

; Intentionally no [UninstallDelete] for %LOCALAPPDATA%\RedisDesk-Internal or %LOCALAPPDATA%\RedisDesk.
