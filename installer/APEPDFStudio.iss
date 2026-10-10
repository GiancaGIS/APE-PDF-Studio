; Compile via scripts\build_installer.ps1 or open this file in Inno Setup 6.3+.
; /DAppVersion=... /DDistDir=... /DOutputDir=... /DLicensePath=... are optional.
#define AppName "APE PDF Studio"
#define PdfProgID "GiancaGIS.APEPDFStudio.PDF"
#define AppCapabilities "Software\GiancaGIS\APE PDF Studio\Capabilities"
#define ThumbnailGuid "93F94D65-3611-4F88-9485-EFEF3A2E1B57"
#define PreviewGuid "DDBA68CE-5289-4A59-A0A9-F1A3E0267870"
#define ProjectRoot AddBackslash(SourcePath) + ".."
#define AppIcon ProjectRoot + "\src\ape_pdf_studio\assets\app-icon.ico"
#ifndef DistDir
  #define DistDir ProjectRoot + "\dist\APEPDFStudio"
#endif
#ifndef OutputDir
  #define OutputDir ProjectRoot + "\dist\installer"
#endif
#ifndef LicensePath
  #define LicensePath ProjectRoot + "\LICENSE"
#endif
#if !FileExists(DistDir + "\APEPDFStudio.exe") || !FileExists(DistDir + "\APEPDFWorker.exe") || !FileExists(DistDir + "\ape-pdf-cli.exe")
  #error "Build the Windows executables first: scripts\build_installer.ps1"
#endif
#if !FileExists(DistDir + "\APEPDFShell.dll")
  #error "Build the native shell extension and include APEPDFShell.dll beside the executables."
#endif
#if !FileExists(LicensePath)
  #error "LicensePath must point to an existing license file."
#endif
#if !FileExists(DistDir + "\licenses\source-manifest.json") || !FileExists(DistDir + "\licenses\LGPL-3.0-only.txt")
  #error "Prepare and include licenses and verified source manifests before building the installer."
#endif
#ifndef AppVersion
  #define AppVersion GetFileVersion(DistDir + "\APEPDFStudio.exe")
#endif

[Setup]
AppId={{F890B690-6157-47AE-94CC-1194928D89EC}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=GiancaGIS
VersionInfoCompany=GiancaGIS
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UsePreviousPrivileges=no
UsePreviousAppDir=yes
DisableDirPage=no
DisableProgramGroupPage=yes
LicenseFile={#LicensePath}
OutputDir={#OutputDir}
OutputBaseFilename=APEPDFStudio-{#AppVersion}-Setup-x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#AppIcon}
UninstallDisplayIcon={app}\APEPDFStudio.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
ChangesAssociations=yes
ChangesEnvironment=yes

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
italian.PdfAssociation=Associa i file PDF ad APE PDF Studio (conferma nelle impostazioni Windows)
english.PdfAssociation=Associate PDF files with APE PDF Studio (confirm in Windows Settings)
italian.FileAssociations=Associazioni dei file:
english.FileAssociations=File associations:
italian.PdfDescription=Documento PDF
english.PdfDescription=PDF document
italian.AppDescription=Lettore ed editor PDF con OCR locale di GiancaGIS
english.AppDescription=PDF reader and editor with local OCR by GiancaGIS
italian.OpenDefaultApps=Scegli APE PDF Studio come app predefinita per i PDF nelle impostazioni Windows
english.OpenDefaultApps=Choose APE PDF Studio as the default PDF app in Windows Settings
italian.PdfThumbnails=Mostra miniature dei PDF in Esplora file (sostituisce il provider corrente)
english.PdfThumbnails=Show PDF thumbnails in File Explorer (replaces the current provider)
italian.PdfPreview=Abilita il riquadro Anteprima dei PDF in Esplora file (sostituisce il provider corrente)
english.PdfPreview=Enable the PDF preview pane in File Explorer (replaces the current provider)
italian.ExplorerIntegration=Integrazione con Esplora file:
english.ExplorerIntegration=File Explorer integration:
italian.CliPath=Aggiungi APE PDF Studio al PATH (comando ape-pdf-cli, riaprire il terminale)
english.CliPath=Add APE PDF Studio to PATH (ape-pdf-cli command, reopen your terminal)
italian.CommandLine=Interfaccia da riga di comando:
english.CommandLine=Command-line interface:

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "clipath"; Description: "{cm:CliPath}"; GroupDescription: "{cm:CommandLine}"; Flags: unchecked
Name: "pdfassociation"; Description: "{cm:PdfAssociation}"; GroupDescription: "{cm:FileAssociations}"; Flags: unchecked
Name: "pdfthumbnails"; Description: "{cm:PdfThumbnails}"; GroupDescription: "{cm:ExplorerIntegration}"; Flags: unchecked; Check: IsX64
Name: "pdfpreview"; Description: "{cm:PdfPreview}"; GroupDescription: "{cm:ExplorerIntegration}"; Flags: unchecked; Check: IsX64

[InstallDelete]
; Remove only the former launchers and shortcuts when upgrading the same AppId.
Type: files; Name: "{app}\APEXPDFStudio.exe"
Type: files; Name: "{app}\APEXPDFWorker.exe"
Type: files; Name: "{autoprograms}\APEX PDF Studio.lnk"
Type: files; Name: "{autodesktop}\APEX PDF Studio.lnk"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Excludes: "APEPDFShell.dll"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#DistDir}\APEPDFShell.dll"; DestDir: "{app}"; Flags: ignoreversion restartreplace uninsrestartdelete; Check: IsX64
; A custom wizard text supplements, rather than replaces, the AGPL license.
Source: "{#ProjectRoot}\LICENSE"; DestDir: "{app}"; DestName: "LICENSE"; Flags: ignoreversion
Source: "{#LicensePath}"; DestDir: "{app}"; DestName: "INSTALLATION-LICENSE"; Flags: ignoreversion

[Registry]
; HKA selects HKCU for a current-user installation, HKLM for all users.
; Register our own handler without overwriting .pdf defaults or UserChoice.
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "{#PdfProgID}"; ValueData: ""; Flags: uninsdeletevalue; Tasks: pdfassociation
Root: HKA; Subkey: "Software\Classes\{#PdfProgID}"; ValueType: string; ValueName: ""; ValueData: "{cm:PdfDescription}"; Flags: uninsdeletekey; Tasks: pdfassociation
Root: HKA; Subkey: "Software\Classes\{#PdfProgID}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\APEPDFStudio.exe"",0"; Tasks: pdfassociation
Root: HKA; Subkey: "Software\Classes\{#PdfProgID}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\APEPDFStudio.exe"" ""%1"""; Tasks: pdfassociation
Root: HKA; Subkey: "Software\Classes\{#PdfProgID}\Application"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#AppName}"; Tasks: pdfassociation
Root: HKA; Subkey: "Software\Classes\{#PdfProgID}\Application"; ValueType: string; ValueName: "ApplicationIcon"; ValueData: """{app}\APEPDFStudio.exe"",0"; Tasks: pdfassociation
Root: HKA; Subkey: "Software\Classes\Applications\APEPDFStudio.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey; Tasks: pdfassociation
Root: HKA; Subkey: "Software\Classes\Applications\APEPDFStudio.exe\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""; Tasks: pdfassociation
Root: HKA; Subkey: "Software\Classes\Applications\APEPDFStudio.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\APEPDFStudio.exe"" ""%1"""; Tasks: pdfassociation
Root: HKA; Subkey: "{#AppCapabilities}"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#AppName}"; Flags: uninsdeletekey; Tasks: pdfassociation
Root: HKA; Subkey: "{#AppCapabilities}"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "{cm:AppDescription}"; Tasks: pdfassociation
Root: HKA; Subkey: "{#AppCapabilities}"; ValueType: string; ValueName: "ApplicationIcon"; ValueData: """{app}\APEPDFStudio.exe"",0"; Tasks: pdfassociation
Root: HKA; Subkey: "{#AppCapabilities}\FileAssociations"; ValueType: string; ValueName: ".pdf"; ValueData: "{#PdfProgID}"; Tasks: pdfassociation
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "{#AppName}"; ValueData: "{#AppCapabilities}"; Flags: uninsdeletevalue; Tasks: pdfassociation

; Stream-based native COM handlers: retain the default process isolation.
Root: HKA; Subkey: "Software\Classes\{#PdfProgID}"; Flags: uninsdeletekey; Tasks: pdfthumbnails or pdfpreview; Check: IsX64
Root: HKA; Subkey: "Software\Classes\{#PdfProgID}\ShellEx\{{e357fccd-a995-4576-b01f-234630154e96}"; ValueType: string; ValueName: ""; ValueData: "{{{#ThumbnailGuid}}"; Tasks: pdfthumbnails; Check: IsX64
Root: HKA; Subkey: "Software\Classes\{#PdfProgID}\ShellEx\{{8895b1c6-b41f-4c1c-a562-0d564250836f}"; ValueType: string; ValueName: ""; ValueData: "{{{#PreviewGuid}}"; Tasks: pdfpreview; Check: IsX64
Root: HKA; Subkey: "Software\Classes\CLSID\{{{#ThumbnailGuid}}"; ValueType: string; ValueName: ""; ValueData: "APE PDF thumbnail provider"; Flags: uninsdeletekey; Tasks: pdfthumbnails; Check: IsX64
Root: HKA; Subkey: "Software\Classes\CLSID\{{{#ThumbnailGuid}}\InprocServer32"; ValueType: string; ValueName: ""; ValueData: "{app}\APEPDFShell.dll"; Tasks: pdfthumbnails; Check: IsX64
Root: HKA; Subkey: "Software\Classes\CLSID\{{{#ThumbnailGuid}}\InprocServer32"; ValueType: string; ValueName: "ThreadingModel"; ValueData: "Apartment"; Tasks: pdfthumbnails; Check: IsX64
Root: HKA; Subkey: "Software\Classes\CLSID\{{{#PreviewGuid}}"; ValueType: string; ValueName: ""; ValueData: "APE PDF preview handler"; Flags: uninsdeletekey; Tasks: pdfpreview; Check: IsX64
Root: HKA; Subkey: "Software\Classes\CLSID\{{{#PreviewGuid}}"; ValueType: string; ValueName: "AppID"; ValueData: "{{6d2b5079-2f0b-48dd-ab7f-97cec514d30b}"; Tasks: pdfpreview; Check: IsX64
Root: HKA; Subkey: "Software\Classes\CLSID\{{{#PreviewGuid}}\InprocServer32"; ValueType: string; ValueName: ""; ValueData: "{app}\APEPDFShell.dll"; Tasks: pdfpreview; Check: IsX64
Root: HKA; Subkey: "Software\Classes\CLSID\{{{#PreviewGuid}}\InprocServer32"; ValueType: string; ValueName: "ThreadingModel"; ValueData: "Apartment"; Tasks: pdfpreview; Check: IsX64
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\PreviewHandlers"; ValueType: string; ValueName: "{{{#PreviewGuid}}"; ValueData: "APE PDF preview handler"; Flags: uninsdeletevalue; Tasks: pdfpreview; Check: IsX64
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\Shell Extensions\Approved"; ValueType: string; ValueName: "{{{#ThumbnailGuid}}"; ValueData: "APE PDF thumbnail provider"; Flags: uninsdeletevalue; Tasks: pdfthumbnails; Check: IsX64
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\Shell Extensions\Approved"; ValueType: string; ValueName: "{{{#PreviewGuid}}"; ValueData: "APE PDF preview handler"; Flags: uninsdeletevalue; Tasks: pdfpreview; Check: IsX64
; Shared ShellEx slots are backed up/restored by [Code], not blindly deleted.
Root: HKA; Subkey: "Software\Classes\.pdf\ShellEx\{{e357fccd-a995-4576-b01f-234630154e96}"; ValueType: string; ValueName: ""; ValueData: "{{{#ThumbnailGuid}}"; Tasks: pdfthumbnails; Check: IsX64
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\ShellEx\{{e357fccd-a995-4576-b01f-234630154e96}"; ValueType: string; ValueName: ""; ValueData: "{{{#ThumbnailGuid}}"; Tasks: pdfthumbnails; Check: IsX64
Root: HKA; Subkey: "Software\Classes\.pdf\ShellEx\{{8895b1c6-b41f-4c1c-a562-0d564250836f}"; ValueType: string; ValueName: ""; ValueData: "{{{#PreviewGuid}}"; Tasks: pdfpreview; Check: IsX64
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\ShellEx\{{8895b1c6-b41f-4c1c-a562-0d564250836f}"; ValueType: string; ValueName: ""; ValueData: "{{{#PreviewGuid}}"; Tasks: pdfpreview; Check: IsX64

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\APEPDFStudio.exe"; WorkingDir: "{app}"; AppUserModelID: "APE.PDFStudio"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\APEPDFStudio.exe"; WorkingDir: "{app}"; AppUserModelID: "APE.PDFStudio"; Tasks: desktopicon

[Run]
Filename: "{app}\APEPDFStudio.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent runasoriginaluser
Filename: "{code:DefaultAppsSettings}"; Description: "{cm:OpenDefaultApps}"; Flags: shellexec postinstall skipifsilent runasoriginaluser; Tasks: pdfassociation

[Code]
const
  ShellBackup = 'Software\GiancaGIS\APE PDF Studio\ShellBackup';
  CLIRegistration = 'Software\GiancaGIS\APE PDF Studio\CLI';
var
  ShellSaved: array[0..3] of Boolean;
  ShellInstallCompleted: Boolean;

function ShellRoot: Integer;
begin
  if IsAdminInstallMode then Result := HKLM else Result := HKCU;
end;

function EnvironmentKey: String;
begin
  if IsAdminInstallMode then
    Result := 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
  else Result := 'Environment';
end;

function NormalizedPathEntry(Value: String): String;
begin
  Result := Trim(Value);
  if (Length(Result) >= 2) and (Result[1] = '"') and
     (Result[Length(Result)] = '"') then
    Result := Copy(Result, 2, Length(Result) - 2);
  while (Length(Result) > 3) and (Result[Length(Result)] = '\') do
    Delete(Result, Length(Result), 1);
end;

function FilterPathEntry(Value, Directory: String; var Found: Boolean): String;
var
  Part: String;
  Separator: Integer;
  Kept, Last: Boolean;
begin
  Result := ''; Found := False; Kept := False;
  repeat
    Separator := Pos(';', Value); Last := Separator = 0;
    if Last then begin Part := Value; Value := ''; end
    else begin
      Part := Copy(Value, 1, Separator - 1);
      Delete(Value, 1, Separator);
    end;
    if CompareText(NormalizedPathEntry(Part), NormalizedPathEntry(Directory)) = 0 then
      Found := True
    else begin
      if Kept then Result := Result + ';';
      Result := Result + Part; Kept := True;
    end;
  until Last;
end;

procedure RemoveCLIPath;
var
  Directory, CurrentPath, UpdatedPath: String;
  Found: Boolean;
  WasPresent: Cardinal;
begin
  { Only remove an entry this installer added; preserve all other PATH entries. }
  if not RegQueryStringValue(ShellRoot, CLIRegistration, 'PathEntry', Directory) then exit;
  if RegQueryStringValue(ShellRoot, EnvironmentKey, 'Path', CurrentPath) then begin
    UpdatedPath := FilterPathEntry(CurrentPath, Directory, Found);
    if Found then begin
      WasPresent := 1;
      RegQueryDWordValue(ShellRoot, CLIRegistration, 'PathWasPresent', WasPresent);
      if (UpdatedPath = '') and (WasPresent = 0) then begin
        if not RegDeleteValue(ShellRoot, EnvironmentKey, 'Path') then
          RaiseException('Unable to remove the APE command-line PATH entry.');
      end else if not RegWriteExpandStringValue(ShellRoot, EnvironmentKey, 'Path', UpdatedPath) then
        RaiseException('Unable to remove the APE command-line PATH entry.');
    end;
  end;
  RegDeleteKeyIncludingSubkeys(ShellRoot, CLIRegistration);
end;

procedure ConfigureCLIPath;
var
  Directory, PreviousDirectory, CurrentPath, UpdatedPath: String;
  WasPresent, Found: Boolean;
begin
  Directory := ExpandConstant('{app}');
  if RegQueryStringValue(ShellRoot, CLIRegistration, 'PathEntry', PreviousDirectory) then
    if (not WizardIsTaskSelected('clipath')) or
       (CompareText(NormalizedPathEntry(Directory), NormalizedPathEntry(PreviousDirectory)) <> 0) then
      RemoveCLIPath;
  if not WizardIsTaskSelected('clipath') then exit;
  WasPresent := RegQueryStringValue(ShellRoot, EnvironmentKey, 'Path', CurrentPath);
  FilterPathEntry(CurrentPath, Directory, Found);
  if Found then exit; { Never take ownership of a pre-existing entry. }
  UpdatedPath := CurrentPath;
  if UpdatedPath <> '' then UpdatedPath := UpdatedPath + ';';
  UpdatedPath := UpdatedPath + Directory;
  if not RegWriteExpandStringValue(ShellRoot, EnvironmentKey, 'Path', UpdatedPath) then
    RaiseException('Unable to add APE PDF Studio to PATH.');
  if (not RegWriteStringValue(ShellRoot, CLIRegistration, 'PathEntry', Directory)) or
     (not RegWriteDWordValue(ShellRoot, CLIRegistration, 'PathWasPresent', Ord(WasPresent))) then begin
    { Revert if ownership cannot be recorded for a safe uninstall. }
    if WasPresent then RegWriteExpandStringValue(ShellRoot, EnvironmentKey, 'Path', CurrentPath)
    else RegDeleteValue(ShellRoot, EnvironmentKey, 'Path');
    RegDeleteKeyIncludingSubkeys(ShellRoot, CLIRegistration);
    RaiseException('Unable to record the APE command-line PATH entry.');
  end;
end;

function SaveShellSlot(Name, Key, OwnGuid: String): Boolean;
var
  OldValue: String;
  HadValue: Boolean;
begin
  Result := False;
  HadValue := RegQueryStringValue(ShellRoot, Key, '', OldValue);
  { On an upgrade, retain the original backup if this slot is already ours. }
  if HadValue and (CompareText(OldValue, OwnGuid) = 0) then exit;
  if HadValue then begin
    if not RegWriteStringValue(ShellRoot, ShellBackup, Name + 'Value', OldValue) then
      RaiseException('Unable to back up the previous PDF shell provider.');
    if not RegWriteDWordValue(ShellRoot, ShellBackup, Name + 'Present', 1) then
      RaiseException('Unable to back up the previous PDF shell provider.');
  end else begin
    if not RegWriteDWordValue(ShellRoot, ShellBackup, Name + 'Present', 0) then
      RaiseException('Unable to back up the previous PDF shell provider.');
  end;
  Result := True;
end;

procedure RestoreShellSlot(Name, Key, OwnGuid: String);
var
  CurrentValue, OldValue: String;
  Present: Cardinal;
begin
  { Preserve providers installed or selected after APE. }
  if not RegQueryStringValue(ShellRoot, Key, '', CurrentValue) then exit;
  if CompareText(CurrentValue, OwnGuid) <> 0 then exit;
  if not RegQueryDWordValue(ShellRoot, ShellBackup, Name + 'Present', Present) then Present := 0;
  if Present = 1 then begin
    if RegQueryStringValue(ShellRoot, ShellBackup, Name + 'Value', OldValue) then
      RegWriteStringValue(ShellRoot, Key, '', OldValue);
  end else begin
    RegDeleteValue(ShellRoot, Key, '');
    RegDeleteKeyIfEmpty(ShellRoot, Key);
  end;
end;

procedure ShellSlots(Restore: Boolean);
var
  Index: Integer;
  Prefix, Slot, Guid, Name: String;
begin
  if not Restore then
    if not IsX64 then exit;
  for Index := 0 to 3 do begin
    if Index < 2 then begin
      Name := 'Thumbnail'; Slot := '{e357fccd-a995-4576-b01f-234630154e96}';
      Guid := '{' + '{#ThumbnailGuid}' + '}';
      if not Restore then
        if not WizardIsTaskSelected('pdfthumbnails') then continue;
    end else begin
      Name := 'Preview'; Slot := '{8895b1c6-b41f-4c1c-a562-0d564250836f}';
      Guid := '{' + '{#PreviewGuid}' + '}';
      if not Restore then
        if not WizardIsTaskSelected('pdfpreview') then continue;
    end;
    if (Index mod 2) = 0 then Prefix := 'Software\Classes\.pdf\ShellEx\'
    else Prefix := 'Software\Classes\SystemFileAssociations\.pdf\ShellEx\';
    Name := Name + IntToStr(Index mod 2);
    if Restore then RestoreShellSlot(Name, Prefix + Slot, Guid)
    else ShellSaved[Index] := SaveShellSlot(Name, Prefix + Slot, Guid);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then ShellSlots(False);
  if CurStep = ssPostInstall then begin
    ShellInstallCompleted := True;
    ConfigureCLIPath;
  end;
end;

procedure DeinitializeSetup;
var
  Index: Integer;
  Prefix, Name, Slot, Guid: String;
begin
  { Undo only slots changed by this attempt if installation did not finish. }
  if ShellInstallCompleted then exit;
  for Index := 0 to 3 do begin
    if not ShellSaved[Index] then continue;
    if Index < 2 then begin
      Name := 'Thumbnail'; Slot := '{e357fccd-a995-4576-b01f-234630154e96}';
      Guid := '{' + '{#ThumbnailGuid}' + '}';
    end else begin
      Name := 'Preview'; Slot := '{8895b1c6-b41f-4c1c-a562-0d564250836f}';
      Guid := '{' + '{#PreviewGuid}' + '}';
    end;
    if (Index mod 2) = 0 then Prefix := 'Software\Classes\.pdf\ShellEx\'
    else Prefix := 'Software\Classes\SystemFileAssociations\.pdf\ShellEx\';
    RestoreShellSlot(Name + IntToStr(Index mod 2), Prefix + Slot, Guid);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then begin
    RemoveCLIPath;
    ShellSlots(True);
    RegDeleteKeyIncludingSubkeys(ShellRoot, ShellBackup);
  end;
end;

function DefaultAppsSettings(Param: String): String;
var
  WindowsVersion: TWindowsVersion;
  RegistrationParameter: String;
begin
  Result := 'ms-settings:defaultapps';
  GetWindowsVersionEx(WindowsVersion);
  { App-specific Settings links are supported on updated Windows 11.
    Windows 10 gets the general Default apps page instead. }
  if WindowsVersion.Build >= 22000 then begin
    if IsAdminInstallMode then
      RegistrationParameter := 'registeredAppMachine'
    else
      RegistrationParameter := 'registeredAppUser';
    Result := Result + '?' + RegistrationParameter + '=APE%20PDF%20Studio';
  end;
end;
