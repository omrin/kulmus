; Inno Setup script: per-user install (no administrator rights), Hebrew wizard, Start-menu and optional
; desktop shortcut. Built by the release workflow:  ISCC.exe /DAppVersion=v1.0.0 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "dev"
#endif

[Setup]
AppId={{CA947904-1747-5D80-AC84-9FA26D572872}
AppName=קולמוס
AppVersion={#AppVersion}
AppPublisher=קולמוס (קוד פתוח)
DefaultDirName={localappdata}\Programs\Kulmus
DefaultGroupName=קולמוס
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=Kulmus-Windows-Setup
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\Kulmus.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "hebrew"; MessagesFile: "compiler:Languages\Hebrew.isl"

[Tasks]
Name: "desktopicon"; Description: "יצירת קיצור דרך בשולחן העבודה"; GroupDescription: "קיצורי דרך:"

[Files]
Source: "..\dist\Kulmus\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\קולמוס"; Filename: "{app}\Kulmus.exe"
Name: "{group}\הסרת קולמוס"; Filename: "{uninstallexe}"
Name: "{userdesktop}\קולמוס"; Filename: "{app}\Kulmus.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Kulmus.exe"; Description: "הפעלת קולמוס"; Flags: nowait postinstall skipifsilent
