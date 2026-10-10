"""Run the actual Inno Pascal backup logic against an in-memory registry.

The test setup exits from InitializeSetup: no installation or registry writes.
"""
from pathlib import Path
import argparse
import re
import subprocess


FAKES = r'''
var
  Store: TStringList;
  AdminMode: Boolean;
  CLISelected, FailCLIRecord: Boolean;
  AppDirectory: String;

function Entry(Root: Integer; Key, Name: String): String;
begin Result := IntToStr(Root) + '/' + Key + '/' + Name; end;
function FindEntry(Name: String): Integer;
var Index: Integer;
begin
  Result := -1;
  for Index := 0 to Store.Count - 1 do
    if Pos(Name + '=', Store.Strings[Index]) = 1 then begin Result := Index; exit; end;
end;
function FakeIsAdminInstallMode: Boolean;
begin Result := AdminMode; end;
function FakeIsX64: Boolean;
begin Result := True; end;
function FakeWizardIsTaskSelected(Name: String): Boolean;
begin if Name = 'clipath' then Result := CLISelected else Result := True; end;
function FakeExpandConstant(Value: String): String;
begin
  if Value <> '{app}' then RaiseException('Unexpected constant: ' + Value);
  Result := AppDirectory;
end;
function FakeRegQueryStringValue(Root: Integer; Key, Name: String; var Value: String): Boolean;
var Index: Integer; Line: String;
begin
  Index := FindEntry(Entry(Root, Key, Name)); Result := Index >= 0;
  if Result then begin Line := Store.Strings[Index]; Value := Copy(Line, Pos('=', Line) + 1, Length(Line)); end;
end;
function FakeRegWriteStringValue(Root: Integer; Key, Name, Value: String): Boolean;
var Index: Integer;
begin
  if FailCLIRecord and (Key = CLIRegistration) and (Name = 'PathEntry') then begin
    Result := False; exit;
  end;
  Index := FindEntry(Entry(Root, Key, Name)); if Index >= 0 then Store.Delete(Index);
  Store.Add(Entry(Root, Key, Name) + '=' + Value); Result := True;
end;
function FakeRegWriteExpandStringValue(Root: Integer; Key, Name, Value: String): Boolean;
begin Result := FakeRegWriteStringValue(Root, Key, Name, Value); end;
function FakeRegQueryDWordValue(Root: Integer; Key, Name: String; var Value: Cardinal): Boolean;
var Text: String;
begin Result := FakeRegQueryStringValue(Root, Key, Name, Text); if Result then Value := StrToInt(Text); end;
function FakeRegWriteDWordValue(Root: Integer; Key, Name: String; Value: Cardinal): Boolean;
begin Result := FakeRegWriteStringValue(Root, Key, Name, IntToStr(Value)); end;
function FakeRegDeleteValue(Root: Integer; Key, Name: String): Boolean;
var Index: Integer;
begin Index := FindEntry(Entry(Root, Key, Name)); if Index >= 0 then Store.Delete(Index); Result := True; end;
function FakeRegDeleteKeyIfEmpty(Root: Integer; Key: String): Boolean;
begin Result := True; end;
function FakeRegDeleteKeyIncludingSubkeys(Root: Integer; Key: String): Boolean;
var Index: Integer; Prefix: String;
begin
  Prefix := Entry(Root, Key, '');
  for Index := Store.Count - 1 downto 0 do
    if Pos(Prefix, Store.Strings[Index]) = 1 then Store.Delete(Index);
  Result := True;
end;
'''

SCENARIOS = r'''
procedure AssertTrue(Value: Boolean; Detail: String);
begin if not Value then RaiseException(Detail); end;
function SlotKey(Index: Integer): String;
var Prefix, Slot: String;
begin
  if Index < 2 then Slot := '{e357fccd-a995-4576-b01f-234630154e96}'
  else Slot := '{8895b1c6-b41f-4c1c-a562-0d564250836f}';
  if (Index mod 2) = 0 then Prefix := 'Software\Classes\.pdf\ShellEx\'
  else Prefix := 'Software\Classes\SystemFileAssociations\.pdf\ShellEx\';
  Result := Prefix + Slot;
end;
procedure InstallSlots;
var Index: Integer; Guid: String;
begin
  for Index := 0 to 3 do begin
    if Index < 2 then Guid := '{' + '{#ThumbnailGuid}' + '}'
    else Guid := '{' + '{#PreviewGuid}' + '}';
    FakeRegWriteStringValue(ShellRoot, SlotKey(Index), '', Guid);
  end;
end;
procedure VerifySlots(Expected: String; ShouldExist: Boolean);
var Index: Integer; Value: String; Exists: Boolean;
begin
  for Index := 0 to 3 do begin
    Exists := FakeRegQueryStringValue(ShellRoot, SlotKey(Index), '', Value);
    AssertTrue(Exists = ShouldExist, 'slot existence');
    if ShouldExist then AssertTrue(Value = Expected, 'previous/third-party value');
  end;
end;
procedure Seed(Value: String);
var Index: Integer;
begin
  for Index := 0 to 3 do FakeRegWriteStringValue(ShellRoot, SlotKey(Index), '', Value);
end;
procedure TestCLIPath;
var Value, Expected: String; Found, Failed: Boolean;
begin
  AppDirectory := 'C:\Apps con spazi\APE PDF Studio';
  Expected := '%SystemRoot%\system32;C:\Other';
  AssertTrue(FilterPathEntry(Expected + ';"c:\apps con spazi\ape pdf studio\";C:\Later', AppDirectory, Found)
    = Expected + ';C:\Later', 'quoted case-insensitive PATH removal');
  AssertTrue(Found, 'PATH entry matched');
  AssertTrue(FilterPathEntry(Expected + ';C:\Apps con spazi\APE PDF Studio Other', AppDirectory, Found)
    = Expected + ';C:\Apps con spazi\APE PDF Studio Other', 'PATH prefixes preserved');
  AssertTrue(not Found, 'PATH prefix is not a match');

  CLISelected := False; Store.Clear;
  ConfigureCLIPath;
  AssertTrue(not FakeRegQueryStringValue(ShellRoot, EnvironmentKey, 'Path', Value), 'optional PATH task');
  CLISelected := True;
  ConfigureCLIPath; ConfigureCLIPath;
  AssertTrue(FakeRegQueryStringValue(ShellRoot, EnvironmentKey, 'Path', Value)
    and (Value = AppDirectory), 'new PATH and upgrade without duplication');
  RemoveCLIPath;
  AssertTrue(not FakeRegQueryStringValue(ShellRoot, EnvironmentKey, 'Path', Value), 'missing original PATH restored');

  Store.Clear;
  FakeRegWriteStringValue(ShellRoot, EnvironmentKey, 'Path', Expected);
  ConfigureCLIPath;
  FakeRegWriteStringValue(ShellRoot, EnvironmentKey, 'Path', Expected + ';' + AppDirectory + ';C:\Later');
  RemoveCLIPath;
  AssertTrue(FakeRegQueryStringValue(ShellRoot, EnvironmentKey, 'Path', Value)
    and (Value = Expected + ';C:\Later'), 'later PATH edits preserved');

  Store.Clear;
  FakeRegWriteStringValue(ShellRoot, EnvironmentKey, 'Path', Expected + ';"' + AppDirectory + '\"');
  ConfigureCLIPath;
  AssertTrue(not FakeRegQueryStringValue(ShellRoot, CLIRegistration, 'PathEntry', Value), 'pre-existing PATH not owned');
  RemoveCLIPath;
  AssertTrue(FakeRegQueryStringValue(ShellRoot, EnvironmentKey, 'Path', Value)
    and (Value = Expected + ';"' + AppDirectory + '\"'), 'pre-existing PATH retained');

  Store.Clear;
  FakeRegWriteStringValue(ShellRoot, EnvironmentKey, 'Path', Expected + ';');
  ConfigureCLIPath; CLISelected := False; ConfigureCLIPath;
  AssertTrue(FakeRegQueryStringValue(ShellRoot, EnvironmentKey, 'Path', Value)
    and (Value = Expected + ';'), 'deselection and trailing separators preserved');

  Store.Clear; CLISelected := True;
  FakeRegWriteStringValue(ShellRoot, EnvironmentKey, 'Path', '');
  ConfigureCLIPath; RemoveCLIPath;
  AssertTrue(FakeRegQueryStringValue(ShellRoot, EnvironmentKey, 'Path', Value)
    and (Value = ''), 'existing empty PATH retained');

  Store.Clear;
  FakeRegWriteStringValue(ShellRoot, EnvironmentKey, 'Path', Expected);
  ConfigureCLIPath;
  AppDirectory := 'D:\New APE location'; ConfigureCLIPath;
  AssertTrue(FakeRegQueryStringValue(ShellRoot, EnvironmentKey, 'Path', Value)
    and (Value = Expected + ';' + AppDirectory), 'changed install folder replaces owned PATH entry');
  RemoveCLIPath;

  Store.Clear; FailCLIRecord := True; Failed := False;
  FakeRegWriteStringValue(ShellRoot, EnvironmentKey, 'Path', Expected);
  try ConfigureCLIPath; except Failed := True; end;
  FailCLIRecord := False;
  AssertTrue(Failed, 'ownership failure reported');
  AssertTrue(FakeRegQueryStringValue(ShellRoot, EnvironmentKey, 'Path', Value)
    and (Value = Expected), 'ownership failure restores original PATH');
  CLISelected := False; Store.Clear;
end;
procedure Tests;
var Scope: Integer; Value: String;
begin
  for Scope := 0 to 1 do begin
    AdminMode := Scope = 1;
    AssertTrue((AdminMode and (ShellRoot = HKLM)) or ((not AdminMode) and (ShellRoot = HKCU)), 'install scope');
    TestCLIPath;
    Store.Clear; Seed('{PREVIOUS}');
    FakeRegWriteStringValue(ShellRoot, 'Software\Classes\.pdf', '', 'OtherReader.PDF');
    CurStepChanged(ssInstall); InstallSlots;
    // An upgrade while APE owns the slots must keep the original backup.
    CurStepChanged(ssInstall); CurUninstallStepChanged(usUninstall);
    VerifySlots('{PREVIOUS}', True);
    AssertTrue(FakeRegQueryStringValue(ShellRoot, 'Software\Classes\.pdf', '', Value) and (Value = 'OtherReader.PDF'), 'default reader preserved');

    Store.Clear; Seed('{PREVIOUS}'); CurStepChanged(ssInstall); InstallSlots;
    Seed('{THIRD-PARTY}'); CurUninstallStepChanged(usUninstall);
    VerifySlots('{THIRD-PARTY}', True);

    Store.Clear; CurStepChanged(ssInstall); InstallSlots;
    CurUninstallStepChanged(usUninstall); VerifySlots('', False);

    Store.Clear; Seed('{PREVIOUS}'); CurStepChanged(ssInstall); InstallSlots;
    ShellInstallCompleted := False; DeinitializeSetup;
    VerifySlots('{PREVIOUS}', True);

    Store.Clear; Seed('{PREVIOUS}'); CurStepChanged(ssInstall); InstallSlots;
    CurStepChanged(ssPostInstall); DeinitializeSetup;
    // Successful setup must retain APE until uninstall.
    AssertTrue(FakeRegQueryStringValue(ShellRoot, SlotKey(0), '', Value) and
      (Value = '{' + '{#ThumbnailGuid}' + '}'), 'completed install retained');
    CurUninstallStepChanged(usUninstall); VerifySlots('{PREVIOUS}', True);

    Store.Clear; InstallSlots; CurUninstallStepChanged(usUninstall);
    VerifySlots('', False); // Lost/missing backup: remove only our stale slots.
  end;
  AdminMode := False; Store.Clear;
  FakeRegWriteStringValue(HKLM, SlotKey(0), '', '{MACHINE}');
  CurStepChanged(ssInstall); InstallSlots; CurUninstallStepChanged(usUninstall);
  AssertTrue(FakeRegQueryStringValue(HKLM, SlotKey(0), '', Value) and (Value = '{MACHINE}'), 'machine provider preserved during per-user uninstall');
  FakeRegWriteStringValue(HKLM, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'Path', 'C:\Machine');
  CLISelected := True; ConfigureCLIPath; CurUninstallStepChanged(usUninstall);
  AssertTrue(FakeRegQueryStringValue(HKLM, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'Path', Value)
    and (Value = 'C:\Machine'), 'machine PATH preserved during per-user uninstall');
end;
function InitializeSetup: Boolean;
begin
  Store := TStringList.Create;
  try
    try Tests; SaveStringToFile('{#ResultFile}', 'PASS', False);
    except SaveStringToFile('{#ResultFile}', GetExceptionMessage, False); end;
  finally Store.Free; ShellInstallCompleted := True; end;
  Result := False;
end;
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inno-compiler", type=Path, default=Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"))
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    work = root / "build/windows/installer-logic"
    work.mkdir(parents=True, exist_ok=True)
    result_file = work / "result.txt"
    if result_file.exists():
        result_file.unlink()
    source = re.split(r"(?m)^\[Code\]\s*$", (root / "installer/APEPDFStudio.iss").read_text(encoding="utf-8"), maxsplit=1)[1]
    names = ("IsAdminInstallMode", "IsX64", "WizardIsTaskSelected", "ExpandConstant", "RegQueryStringValue", "RegWriteStringValue", "RegWriteExpandStringValue",
             "RegQueryDWordValue", "RegWriteDWordValue", "RegDeleteValue", "RegDeleteKeyIfEmpty", "RegDeleteKeyIncludingSubkeys")
    for name in names:
        source = re.sub(r"\b" + name + r"\b", "Fake" + name, source)
    # Constants/global variables precede the mock functions in Pascal Script.
    globals_, functions = source.split("function ShellRoot", 1)
    fake_globals, fake_functions = FAKES.split("function Entry", 1)
    definitions = '\n'.join(line for line in (root / "installer/APEPDFStudio.iss").read_text(encoding="utf-8").splitlines()
                            if line.startswith(("#define ThumbnailGuid", "#define PreviewGuid")))
    script = definitions + f'''\n#define ResultFile "{result_file.as_posix()}"
[Setup]
AppName=APE shell installer logic test
AppId=APE.Shell.LogicTest
AppVersion=1
DefaultDirName={{tmp}}\\APEUnused
PrivilegesRequired=lowest
Uninstallable=no
CreateAppDir=no
OutputDir={work.as_posix()}
OutputBaseFilename=logic-test
[Code]
''' + globals_ + fake_globals + "function Entry" + fake_functions + "function ShellRoot" + functions + SCENARIOS
    path = work / "logic-test.iss"
    path.write_text(script, encoding="utf-8-sig")
    compiler = arguments.inno_compiler
    subprocess.run([str(compiler), "/Q", str(path)], check=True)
    subprocess.run([str(work / "logic-test.exe"), "/VERYSILENT", "/SP-", "/NORESTART", "/SUPPRESSMSGBOXES"], timeout=20)
    result = result_file.read_text(encoding="utf-8-sig")
    if result != "PASS":
        raise RuntimeError(result)
    print("Inno Pascal: scope, upgrade, rollback, uninstall, providers and CLI PATH ownership/preservation passed.")


if __name__ == "__main__":
    main()
