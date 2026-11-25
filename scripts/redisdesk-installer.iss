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
AppComments=Unsigned internal preview. The wizard can choose a data directory (default %LOCALAPPDATA%\RedisDesk-Internal\1.0.0, or {app}, or a custom folder). User data is not bundled or removed.
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
Name: "chinesesimplified"; MessagesFile: "inno-languages\ChineseSimplified.isl"
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

#define MyPointerFile "redisdesk-datadir.txt"

[Code]
var
  DataDirPage: TInputOptionWizardPage;
  CustomDataDirPage: TInputDirWizardPage;

procedure InitializeWizard;
begin
  DataDirPage := CreateInputOptionPage(wpSelectDir,
    '数据目录',
    '选择 RedisDesk 保存连接、凭证和审计数据的位置。',
    '默认使用本机用户目录下的 RedisDesk-Internal\1.0.0，不会与开发版 %LOCALAPPDATA%\RedisDesk 混用。只有你主动选择同一路径时才会共用。',
    True, False);
  DataDirPage.Add('系统默认（%LOCALAPPDATA%\RedisDesk-Internal\1.0.0）');
  DataDirPage.Add('应用根目录（与程序安装在同一目录）');
  DataDirPage.Add('自定义文件夹');
  DataDirPage.SelectedValueIndex := 0;

  CustomDataDirPage := CreateInputDirPage(DataDirPage.ID,
    '自定义数据目录',
    '选择一个可写的文件夹用于保存 RedisDesk 数据。',
    '请选择空文件夹，或你确认要使用的目录。不要把开发版与内部预览数据放到一起，除非你有意合并。',
    False, 'redisdesk-data');
  CustomDataDirPage.Add('');
  CustomDataDirPage.Values[0] := ExpandConstant('{localappdata}\RedisDesk-Data');
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if PageID = CustomDataDirPage.ID then
    Result := DataDirPage.SelectedValueIndex <> 2;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Target: String;
begin
  Result := True;
  if CurPageID = CustomDataDirPage.ID then
  begin
    Target := Trim(CustomDataDirPage.Values[0]);
    if Target = '' then
    begin
      MsgBox('请选择数据目录。', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function ChosenDataDir: String;
begin
  case DataDirPage.SelectedValueIndex of
    1: Result := ExpandConstant('{app}');
    2: Result := Trim(CustomDataDirPage.Values[0]);
  else
    Result := '';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  PointerFile, Target, Content: String;
begin
  if CurStep <> ssPostInstall then
    Exit;
  Target := ChosenDataDir;
  if Target = '' then
    Exit;
  PointerFile := ExpandConstant('{app}\{#MyPointerFile}');
  Content := '# RedisDesk data directory pointer. Do not store secrets in this file.' + #13#10 + Target + #13#10;
  if not SaveStringToFile(PointerFile, Content, False) then
    MsgBox('无法写入数据目录指针：' + PointerFile + #13#10 +
      '安装已完成，但第一次启动仍会使用系统默认数据目录。请检查安装目录是否可写，或在设置页中重新选择。',
      mbError, MB_OK);
end;
