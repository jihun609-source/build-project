#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\AutoSet"
#endif
#ifndef ArtifactDir
  #define ArtifactDir "..\releases\windows"
#endif
[Setup]
AppId={{D6D10F3E-CB72-4A18-9FB7-E1A6DF34E810}
AppName=AutoSet
AppVersion={#AppVersion}
AppPublisher=AutoSet
DefaultDirName={localappdata}\Programs\AutoSet
DefaultGroupName=AutoSet
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#ArtifactDir}
OutputBaseFilename=AutoSet-{#AppVersion}-windows-x64-setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\AutoSet.exe
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AutoSet"; Filename: "{app}\AutoSet.exe"
Name: "{group}\README"; Filename: "{app}\_internal\README.md"
Name: "{autodesktop}\AutoSet"; Filename: "{app}\AutoSet.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AutoSet.exe"; Description: "Launch AutoSet"; Flags: nowait postinstall skipifsilent unchecked
