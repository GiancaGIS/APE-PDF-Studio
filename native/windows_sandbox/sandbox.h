// One-document AppContainer launch, inherited pipes only, bounded job lifetime.
#pragma once
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <userenv.h>
#include <sddl.h>
#include <aclapi.h>
#include <string>
#include <vector>
#include <thread>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <tlhelp32.h>

namespace ape_sandbox {
struct Handle {
    HANDLE value = nullptr;
    ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); }
};
inline std::wstring quoted(const std::wstring& value) {
    std::wstring result = L"\"";
    size_t slashes = 0;
    for (wchar_t ch : value) {
        if (ch == L'\\') { ++slashes; continue; }
        result.append(slashes * (ch == L'\"' ? 2 : 1), L'\\');
        slashes = 0;
        if (ch == L'\"') result += L'\\';
        result += ch;
    }
    result.append(slashes * 2, L'\\');
    return result + L'\"';
}
inline bool grant(const std::wstring& path, PSID sid, DWORD rights) {
    PACL current = nullptr, updated = nullptr;
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    if (GetNamedSecurityInfoW(path.c_str(), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
        nullptr, nullptr, &current, nullptr, &descriptor) != ERROR_SUCCESS) return false;
    bool already = false;
    for (DWORD i = 0; current && i < current->AceCount; ++i) {
        void* entry = nullptr;
        if (GetAce(current, i, &entry) && static_cast<ACE_HEADER*>(entry)->AceType == ACCESS_ALLOWED_ACE_TYPE) {
            auto ace = static_cast<ACCESS_ALLOWED_ACE*>(entry);
            if (EqualSid(sid, &ace->SidStart) && (ace->Mask & rights) == rights) already = true;
        }
    }
    if (already) { LocalFree(descriptor); return true; }
    EXPLICIT_ACCESSW access{};
    TRUSTEE_W trustee{};
    BuildTrusteeWithSidW(&trustee,sid);
    ACCESS_MASK effective=0;
    if (GetEffectiveRightsFromAclW(current,&trustee,&effective)==ERROR_SUCCESS && (effective & rights)==rights) {
        LocalFree(descriptor);
        return true;
    }
    access.grfAccessPermissions = rights;
    access.grfAccessMode = GRANT_ACCESS;
    DWORD attributes = GetFileAttributesW(path.c_str());
    access.grfInheritance = attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_DIRECTORY)
        ? SUB_CONTAINERS_AND_OBJECTS_INHERIT : NO_INHERITANCE;
    BuildTrusteeWithSidW(&access.Trustee, sid);
    DWORD error = SetEntriesInAclW(1, &access, current, &updated);
    if (!error) error = SetNamedSecurityInfoW(const_cast<wchar_t*>(path.c_str()), SE_FILE_OBJECT,
        DACL_SECURITY_INFORMATION, nullptr, nullptr, updated, nullptr);
    if (updated) LocalFree(updated);
    LocalFree(descriptor);
    return error == ERROR_SUCCESS;
}
struct Profile {
    std::wstring name;
    PSID sid = nullptr;
    bool created = false;
    std::wstring marker;
    Handle markerLease;
    ~Profile() {
        if (markerLease.value && markerLease.value!=INVALID_HANDLE_VALUE) {CloseHandle(markerLease.value);markerLease.value=nullptr;}
        if (sid) FreeSid(sid);
        if (created && SUCCEEDED(DeleteAppContainerProfile(name.c_str())) && !marker.empty()) DeleteFileW(marker.c_str());
    }
    bool initialize() {
        GUID guid{};
        if (FAILED(CoCreateGuid(&guid))) return false;
        wchar_t id[40];
        if (!StringFromGUID2(guid, id, 40)) return false;
        name = L"APE.PDF.";
        for (wchar_t ch : std::wstring(id)) if (ch != L'{' && ch != L'}' && ch != L'-') name += ch;
        HRESULT result = CreateAppContainerProfile(name.c_str(), L"APE PDF worker", L"Isolated PDF operation", nullptr, 0, &sid);
        created = SUCCEEDED(result);
        return created;
    }
};

inline bool diskBudget(const std::wstring& work,unsigned long long* aggregate=nullptr) {
    try {
        ULARGE_INTEGER free{};
        if (!GetDiskFreeSpaceExW(work.c_str(),&free,nullptr,nullptr) || free.QuadPart<128ull*1024*1024) return false;
        size_t count=0;unsigned long long bytes=aggregate?*aggregate:0;
        for (const auto& entry:std::filesystem::recursive_directory_iterator(work)) {
            DWORD attributes=GetFileAttributesW(entry.path().c_str());
            if (attributes==INVALID_FILE_ATTRIBUTES && (GetLastError()==ERROR_FILE_NOT_FOUND || GetLastError()==ERROR_PATH_NOT_FOUND)) continue;
            if (attributes==INVALID_FILE_ATTRIBUTES || attributes & FILE_ATTRIBUTE_REPARSE_POINT || ++count>50000) return false;
            if (!(attributes & FILE_ATTRIBUTE_DIRECTORY)) {
                std::error_code error;auto size=entry.file_size(error);
                if (error==std::errc::no_such_file_or_directory) continue;
                if (error) return false;
                bytes+=size;
            }
            if (bytes>1024ull*1024*1024) return false;
        }
        if (aggregate) *aggregate=bytes;
        return true;
    } catch (...) { return false; }
}

inline HANDLE parentHandle() {
    Handle snapshot;snapshot.value=CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0);
    PROCESSENTRY32W entry{};entry.dwSize=sizeof(entry);
    if (snapshot.value==INVALID_HANDLE_VALUE || !Process32FirstW(snapshot.value,&entry)) return nullptr;
    do {
        if (entry.th32ProcessID==GetCurrentProcessId()) return OpenProcess(SYNCHRONIZE,FALSE,entry.th32ParentProcessID);
    } while (Process32NextW(snapshot.value,&entry));
    return nullptr;
}

inline bool hasLowLabel(const std::wstring& path) {
    PACL sacl=nullptr;PSECURITY_DESCRIPTOR descriptor=nullptr;
    DWORD queried=GetNamedSecurityInfoW(const_cast<wchar_t*>(path.c_str()),SE_FILE_OBJECT,LABEL_SECURITY_INFORMATION,
        nullptr,nullptr,nullptr,&sacl,&descriptor);
    if (queried!=ERROR_SUCCESS) return false;
    bool valid=false;
    if (sacl) for (DWORD i=0;i<sacl->AceCount;++i) {
        void* pointer=nullptr;if (!GetAce(sacl,i,&pointer)) break;
        auto ace=reinterpret_cast<SYSTEM_MANDATORY_LABEL_ACE*>(pointer);
        if (ace->Header.AceType!=SYSTEM_MANDATORY_LABEL_ACE_TYPE) continue;
        PSID sid=&ace->SidStart;SID_IDENTIFIER_AUTHORITY authority=SECURITY_MANDATORY_LABEL_AUTHORITY;
        if (IsValidSid(sid) && !std::memcmp(GetSidIdentifierAuthority(sid),&authority,sizeof(authority)) &&
            *GetSidSubAuthorityCount(sid)==1 && *GetSidSubAuthority(sid,0)<=SECURITY_MANDATORY_LOW_RID &&
            (ace->Header.AceFlags&(OBJECT_INHERIT_ACE|CONTAINER_INHERIT_ACE|NO_PROPAGATE_INHERIT_ACE))==(OBJECT_INHERIT_ACE|CONTAINER_INHERIT_ACE) &&
            (ace->Mask&SYSTEM_MANDATORY_LABEL_NO_WRITE_UP)) valid=true;
    }
    if (descriptor) LocalFree(descriptor);return valid;
}

// The caller supplies only trusted code directories, never document locations.
// Each private workspace receives its own distinct package SID.
inline int run(const std::wstring& executable, const std::vector<std::wstring>& arguments,
               const std::wstring& work, const std::vector<std::wstring>& code,
               DWORD timeout, unsigned long long maxOutput) {
    if (timeout < 100 || timeout > 3600000) return 120;
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    Profile profile;
    if (!profile.initialize()) return 121;
    PSID applications = nullptr;
    if (!ConvertStringSidToSidW(L"S-1-15-2-1", &applications)) return 122;
    bool granted = true;
    for (const auto& path : code) granted = granted && grant(path, applications, FILE_GENERIC_READ | FILE_GENERIC_EXECUTE);
    LocalFree(applications);
    if (!granted) {std::fprintf(stderr,"Sandbox runtime ACL unavailable\n");return 123;}
    if (!grant(work, profile.sid, FILE_ALL_ACCESS)) {std::fprintf(stderr,"Sandbox workspace ACL unavailable: %lu\n",GetLastError());return 123;}
    auto workspace=std::filesystem::path(work);
    // The worker has no capability for this parent directory. Keep the lease
    // outside its writable workspace so compromised code cannot erase it.
    profile.marker=(workspace.parent_path()/(L"."+workspace.filename().wstring()+L".profile-"+profile.name.substr(8))).wstring();
    {
        profile.markerLease.value=CreateFileW(profile.marker.c_str(),GENERIC_READ|GENERIC_WRITE,0,nullptr,CREATE_NEW,FILE_ATTRIBUTE_HIDDEN,nullptr);
        if (profile.markerLease.value==INVALID_HANDLE_VALUE) return 123;
    }
    PSECURITY_DESCRIPTOR label = nullptr;
    PACL sacl = nullptr;
    BOOL present = FALSE, defaulted = FALSE;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(L"S:(ML;OICI;NW;;;LW)", SDDL_REVISION_1, &label, nullptr)) return 123;
    bool labelled = hasLowLabel(work) || (GetSecurityDescriptorSacl(label, &present, &sacl, &defaulted) && present &&
        SetNamedSecurityInfoW(const_cast<wchar_t*>(work.c_str()), SE_FILE_OBJECT, LABEL_SECURITY_INFORMATION, nullptr, nullptr, nullptr, sacl) == ERROR_SUCCESS);
    LocalFree(label);
    if (!labelled) {std::fprintf(stderr,"Sandbox workspace label unavailable: %lu\n",GetLastError());return 123;}
    SECURITY_ATTRIBUTES security{sizeof(security), nullptr, TRUE};
    Handle outputRead, outputWrite, input, error;
    if (!CreatePipe(&outputRead.value, &outputWrite.value, &security, 65536) ||
        !SetHandleInformation(outputRead.value, HANDLE_FLAG_INHERIT, 0)) return 124;
    if (!DuplicateHandle(GetCurrentProcess(), GetStdHandle(STD_INPUT_HANDLE), GetCurrentProcess(),
        &input.value, 0, TRUE, DUPLICATE_SAME_ACCESS)) return 124;
    error.value = CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &security, OPEN_EXISTING, 0, nullptr);
    if (error.value == INVALID_HANDLE_VALUE) return 124;
    SIZE_T size = 0;
    InitializeProcThreadAttributeList(nullptr, 3, 0, &size);
    std::vector<BYTE> storage(size);
    auto list = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(storage.data());
    if (!InitializeProcThreadAttributeList(list, 3, 0, &size)) return 125;
    SECURITY_CAPABILITIES capabilities{};
    capabilities.AppContainerSid = profile.sid;  // No network or device capabilities.
    HANDLE handles[] = {input.value, outputWrite.value, error.value};
    DWORD64 mitigation = PROCESS_CREATION_MITIGATION_POLICY_DEP_ENABLE |
        PROCESS_CREATION_MITIGATION_POLICY_SEHOP_ENABLE |
        PROCESS_CREATION_MITIGATION_POLICY_EXTENSION_POINT_DISABLE_ALWAYS_ON;
    bool configured = !!UpdateProcThreadAttribute(list, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
        &capabilities, sizeof(capabilities), nullptr, nullptr);
    configured = configured && UpdateProcThreadAttribute(list, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, handles, sizeof(handles), nullptr, nullptr);
    configured = configured && UpdateProcThreadAttribute(list, 0, PROC_THREAD_ATTRIBUTE_MITIGATION_POLICY, &mitigation, sizeof(mitigation), nullptr, nullptr);
    Handle job;
    job.value = CreateJobObjectW(nullptr, nullptr);
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_PROCESS_MEMORY |
        JOB_OBJECT_LIMIT_JOB_MEMORY | JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_JOB_TIME;
    limits.BasicLimitInformation.ActiveProcessLimit = 1;
    limits.BasicLimitInformation.PerJobUserTimeLimit.QuadPart = static_cast<LONGLONG>(timeout) * 10000;
    limits.ProcessMemoryLimit = limits.JobMemoryLimit = 768ull * 1024 * 1024;
    configured = configured && job.value && SetInformationJobObject(job.value, JobObjectExtendedLimitInformation, &limits, sizeof(limits));
    std::wstring command = quoted(executable);
    for (const auto& argument : arguments) command += L" " + quoted(argument);
    // Use an explicit, minimal environment. Never forward Python import overrides,
    // proxy credentials, GitHub tokens or the user's inherited application state.
    std::vector<std::wstring> variables;
    wchar_t system[32768];
    UINT systemLength = GetWindowsDirectoryW(system, ARRAYSIZE(system));
    if (!systemLength || systemLength >= ARRAYSIZE(system)) { DeleteProcThreadAttributeList(list); return 125; }
    std::wstring windows(system);
    variables = {L"SystemRoot="+windows, L"WINDIR="+windows, L"PATH="+windows+L"\\System32",
        L"TEMP="+work, L"TMP="+work, L"QT_QPA_PLATFORM=offscreen", L"APE_WORKER_SANDBOX=1", L"APE_WORKSPACE="+work};
    for (const auto* key : {L"USERPROFILE", L"LOCALAPPDATA", L"APPDATA"}) {
        wchar_t value[32768];
        DWORD length = GetEnvironmentVariableW(key, value, ARRAYSIZE(value));
        if (length && length < ARRAYSIZE(value)) variables.push_back(std::wstring(key)+L"="+value);
    }
    wchar_t language[8]{};
    if (GetEnvironmentVariableW(L"APE_UI_LANGUAGE", language, 8)) {
        std::wstring value(language);
        if (value == L"it" || value == L"en" || value == L"fr" || value == L"es") variables.push_back(L"APE_UI_LANGUAGE="+value);
    }
    std::sort(variables.begin(), variables.end());
    std::vector<wchar_t> environment;
    for (const auto& variable : variables) { environment.insert(environment.end(), variable.begin(), variable.end()); environment.push_back(0); }
    environment.push_back(0);
    STARTUPINFOEXW startup{};
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = input.value;
    startup.StartupInfo.hStdOutput = outputWrite.value;
    startup.StartupInfo.hStdError = error.value;
    startup.lpAttributeList = list;
    PROCESS_INFORMATION process{};
    BOOL launched = configured && CreateProcessW(executable.c_str(), command.data(), nullptr, nullptr, TRUE,
        CREATE_NO_WINDOW | CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT,
        environment.data(), work.c_str(), &startup.StartupInfo, &process);
    DWORD launchError = GetLastError();
    DeleteProcThreadAttributeList(list);
    if (!launched) { std::fprintf(stderr, "Sandbox CreateProcess failed: %lu\n", launchError); return 126; }
    Handle child, thread;
    child.value = process.hProcess;
    thread.value = process.hThread;
    if (!AssignProcessToJobObject(job.value, child.value)) { TerminateProcess(child.value, 1); return 127; }
    CloseHandle(outputWrite.value); outputWrite.value = nullptr;
    Handle parent;parent.value=parentHandle();
    LPWSTR sidText=nullptr;PWSTR profileFolder=nullptr;std::wstring storageFolder;
    if (ConvertSidToStringSidW(profile.sid,&sidText)) {
        if (SUCCEEDED(GetAppContainerFolderPath(sidText,&profileFolder))) {storageFolder=profileFolder;CoTaskMemFree(profileFolder);}
        LocalFree(sidText);
    }
    std::thread watchdog([&] {
        ULONGLONG deadline=GetTickCount64()+timeout;
        while (WaitForSingleObject(child.value,500)==WAIT_TIMEOUT) {
            unsigned long long diskBytes=0;
            if (GetTickCount64()>deadline || (parent.value && WaitForSingleObject(parent.value,0)==WAIT_OBJECT_0) ||
                storageFolder.empty() || !diskBudget(work,&diskBytes) || !diskBudget(storageFolder,&diskBytes)) {
                TerminateJobObject(job.value,1);break;
            }
        }
    });
    ResumeThread(thread.value);
    BYTE buffer[65536];
    DWORD received = 0;
    unsigned long long total = 0;
    bool valid = true;
    while (ReadFile(outputRead.value, buffer, sizeof(buffer), &received, nullptr) && received) {
        total += received;
        if (maxOutput && total > maxOutput) { valid = false; break; }
        DWORD offset = 0;
        while (offset < received) {
            DWORD written = 0;
            if (!WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), buffer+offset, received-offset, &written, nullptr) || !written) { valid = false; break; }
            offset += written;
        }
        if (!valid) break;
    }
    if (!valid) TerminateJobObject(job.value, 1);
    watchdog.join();
    DWORD status = 1;
    GetExitCodeProcess(child.value, &status);
    return valid ? static_cast<int>(status) : 128;
}
}
