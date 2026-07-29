#ifndef MyAppVersion
  #error MyAppVersion must be supplied from the repository VERSION file
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\NEU-JWXT-Toolkit"
#endif
#ifndef ChineseMessagesFile
  #error ChineseMessagesFile must be supplied by the release workflow
#endif

[Setup]
AppId={{5EE2B318-8D15-49A5-A2AE-52D9A6613742}
AppName=NEU 教务工具箱
AppVersion={#MyAppVersion}
AppPublisher=NEU-JWXT-Toolkit Contributors
DefaultDirName={localappdata}\Programs\NEU-JWXT-Toolkit
DefaultGroupName=NEU 教务工具箱
PrivilegesRequired=lowest
OutputDir=..\..\release
OutputBaseFilename=NEU-JWXT-Toolkit-{#MyAppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=NEU 教务工具箱
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinesesimp"; MessagesFile: "{#ChineseMessagesFile}"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[InstallDelete]
; 用户数据位于独立的 %LOCALAPPDATA%\NEU-JWXT-Toolkit\data。
; 升级前只清理冻结程序内部目录，避免旧版 Python 模块残留并被新版误加载。
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\NEU-JWXT-Toolkit.exe"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\NEU 教务工具箱"; Filename: "{app}\NEU-JWXT-Toolkit.exe"
Name: "{autodesktop}\NEU 教务工具箱"; Filename: "{app}\NEU-JWXT-Toolkit.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\NEU-JWXT-Toolkit.exe"; Description: "启动 NEU 教务工具箱"; Flags: nowait postinstall skipifsilent
