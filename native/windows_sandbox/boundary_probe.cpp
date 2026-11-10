// Test-only probe: uses synthetic resources supplied by the Python test runner.
// It is built beside the launcher, but is never included in the application.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <winioctl.h>
#include <sddl.h>
#include <cstdio>
#include <cwchar>
#include <string>
#include <vector>
#include <cstdint>
#include "sandbox.h"

using ape_sandbox::Handle;

static void jsonString(const wchar_t* value) {
    std::putchar('"');
    for (; *value; ++value) {
        unsigned ch = static_cast<unsigned short>(*value);
        if (ch == '"' || ch == '\\') std::printf("\\%c", static_cast<char>(ch));
        else if (ch < 32 || ch > 126) std::printf("\\u%04x", ch);
        else std::putchar(static_cast<int>(ch));
    }
    std::putchar('"');
}

static bool canOpen(const std::wstring& path, DWORD access, DWORD mode = OPEN_EXISTING) {
    Handle file;
    file.value = CreateFileW(path.c_str(), access, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr, mode, FILE_ATTRIBUTE_NORMAL, nullptr);
    return file.value != INVALID_HANDLE_VALUE;
}

static void ready() {
    std::printf("{\"pid\":%lu}\n", GetCurrentProcessId());
    std::fflush(stdout);
}

static bool sameFileHandle(HANDLE handle, DWORD volume, DWORD high, DWORD low) {
    // Strict handle checks can raise STATUS_INVALID_HANDLE instead of returning
    // an error. This is an expected result for the intentionally absent canary.
    __try {
        BY_HANDLE_FILE_INFORMATION info{};
        return GetFileType(handle) == FILE_TYPE_DISK && GetFileInformationByHandle(handle, &info) &&
            info.dwVolumeSerialNumber == volume && info.nFileIndexHigh == high && info.nFileIndexLow == low;
    } __except (GetExceptionCode() == 0xC0000008u ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH) {
        return false;
    }
}

static int information() {
    Handle token;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token.value)) return 3;
    DWORD length = 0, container = 0, elevation = 0, uiAccess = 0;
    TOKEN_MANDATORY_POLICY policy{};
    if (!GetTokenInformation(token.value, TokenIsAppContainer, &container, sizeof(container), &length) ||
        !GetTokenInformation(token.value, TokenElevation, &elevation, sizeof(elevation), &length) ||
        !GetTokenInformation(token.value, TokenUIAccess, &uiAccess, sizeof(uiAccess), &length) ||
        !GetTokenInformation(token.value, TokenMandatoryPolicy, &policy, sizeof(policy), &length)) return 3;
    GetTokenInformation(token.value, TokenPrivileges, nullptr, 0, &length);
    std::vector<BYTE> privileges(length);
    if (!GetTokenInformation(token.value, TokenPrivileges, privileges.data(), length, &length)) return 3;
    auto tokenPrivileges = reinterpret_cast<TOKEN_PRIVILEGES*>(privileges.data());
    // Enumerate disabled privileges too: they must not be available to enable
    // later. TokenElevation alone does not describe AppContainer confinement.
    std::vector<std::pair<std::wstring, DWORD>> privilegeNames;
    for (DWORD i = 0; i < tokenPrivileges->PrivilegeCount; ++i) {
        auto& privilege = tokenPrivileges->Privileges[i];
        wchar_t name[256]{}; DWORD characters = ARRAYSIZE(name);
        if (!LookupPrivilegeNameW(nullptr, &privilege.Luid, name, &characters)) return 3;
        privilegeNames.emplace_back(name, privilege.Attributes);
    }
    GetTokenInformation(token.value, TokenCapabilities, nullptr, 0, &length);
    std::vector<BYTE> capabilities(length);
    if (!GetTokenInformation(token.value, TokenCapabilities, capabilities.data(), length, &length)) return 3;
    DWORD capabilityCount = reinterpret_cast<TOKEN_GROUPS*>(capabilities.data())->GroupCount;
    GetTokenInformation(token.value, TokenIntegrityLevel, nullptr, 0, &length);
    std::vector<BYTE> integrity(length);
    if (!GetTokenInformation(token.value, TokenIntegrityLevel, integrity.data(), length, &length)) return 3;
    PSID label = reinterpret_cast<TOKEN_MANDATORY_LABEL*>(integrity.data())->Label.Sid;
    DWORD integrityLevel = *GetSidSubAuthority(label, *GetSidSubAuthorityCount(label) - 1);
    GetTokenInformation(token.value, TokenAppContainerSid, nullptr, 0, &length);
    std::vector<BYTE> package(length);
    if (!GetTokenInformation(token.value, TokenAppContainerSid, package.data(), length, &length)) return 3;
    PSID sid = reinterpret_cast<TOKEN_APPCONTAINER_INFORMATION*>(package.data())->TokenAppContainer;
    LPWSTR sidText = nullptr;
    if (!sid || !ConvertSidToStringSidW(sid, &sidText)) return 3;
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION job{};
    BOOL inJob = FALSE;
    if (!IsProcessInJob(GetCurrentProcess(), nullptr, &inJob) ||
        !QueryInformationJobObject(nullptr, JobObjectExtendedLimitInformation, &job, sizeof(job), nullptr)) {
        LocalFree(sidText); return 3;
    }
    PROCESS_MITIGATION_DEP_POLICY dep{};
    PROCESS_MITIGATION_EXTENSION_POINT_DISABLE_POLICY extensions{};
    if (!GetProcessMitigationPolicy(GetCurrentProcess(), ProcessDEPPolicy, &dep, sizeof(dep)) ||
        !GetProcessMitigationPolicy(GetCurrentProcess(), ProcessExtensionPointDisablePolicy, &extensions, sizeof(extensions))) {
        LocalFree(sidText); return 3;
    }
    std::printf("{\"appcontainer\":%lu,\"capabilities\":%lu,\"elevated\":%lu,\"integrity\":%lu,"
        "\"in_job\":%d,\"job_flags\":%lu,\"active_process_limit\":%lu,\"process_memory\":%llu,"
        "\"job_memory\":%llu,\"cpu_ticks\":%lld,\"dep\":%u,\"extension_points_disabled\":%u,\"sid\":",
        container, capabilityCount, elevation, integrityLevel, inJob, job.BasicLimitInformation.LimitFlags,
        job.BasicLimitInformation.ActiveProcessLimit, static_cast<unsigned long long>(job.ProcessMemoryLimit),
        static_cast<unsigned long long>(job.JobMemoryLimit), job.BasicLimitInformation.PerJobUserTimeLimit.QuadPart,
        dep.Enable, extensions.DisableExtensionPoints);
    jsonString(sidText); LocalFree(sidText);
    std::printf(",\"ui_access\":%lu,\"mandatory_policy\":%lu,\"privileges\":{", uiAccess, policy.Policy);
    bool first = true;
    for (const auto& privilege : privilegeNames) {
        if (!first) std::putchar(','); first = false;
        jsonString(privilege.first.c_str()); std::printf(":%lu", privilege.second);
    }
    std::puts("}}"); return 0;
}

static int files(const wchar_t* foreign, const wchar_t* code) {
    std::printf("{\"workspace_write\":%d,\"foreign_read\":%d,\"foreign_write\":%d,\"code_read\":%d,\"code_write\":%d}\n",
        canOpen(L"created.txt", GENERIC_WRITE, CREATE_ALWAYS), canOpen(foreign, GENERIC_READ),
        canOpen(foreign, GENERIC_WRITE), canOpen(code, GENERIC_READ), canOpen(code, GENERIC_WRITE));
    return 0;
}

static int network(const wchar_t* family, const wchar_t* transport, const wchar_t* portText) {
    WSADATA data{};
    if (WSAStartup(MAKEWORD(2, 2), &data)) return 3;
    bool ipv6 = std::wstring(family) == L"6", udp = std::wstring(transport) == L"udp";
    SOCKET channel = socket(ipv6 ? AF_INET6 : AF_INET, udp ? SOCK_DGRAM : SOCK_STREAM, udp ? IPPROTO_UDP : IPPROTO_TCP);
    if (channel == INVALID_SOCKET) { WSACleanup(); return 3; }
    u_long nonblocking = 1;
    ioctlsocket(channel, FIONBIO, &nonblocking);
    sockaddr_storage storage{};
    int length = ipv6 ? sizeof(sockaddr_in6) : sizeof(sockaddr_in);
    auto port = htons(static_cast<unsigned short>(std::stoul(portText)));
    if (ipv6) {
        auto address = reinterpret_cast<sockaddr_in6*>(&storage);
        address->sin6_family = AF_INET6; address->sin6_port = port; address->sin6_addr = in6addr_loopback;
    } else {
        auto address = reinterpret_cast<sockaddr_in*>(&storage);
        address->sin_family = AF_INET; address->sin_port = port; address->sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    }
    int status = udp ? sendto(channel, "synthetic", 9, 0, reinterpret_cast<sockaddr*>(&storage), length)
                     : connect(channel, reinterpret_cast<sockaddr*>(&storage), length);
    int error = status == SOCKET_ERROR ? WSAGetLastError() : 0;
    if (!udp && error == WSAEWOULDBLOCK) {
        fd_set writes, errors; FD_ZERO(&writes); FD_ZERO(&errors); FD_SET(channel, &writes); FD_SET(channel, &errors);
        timeval timeout{2, 0};
        if (select(0, nullptr, &writes, &errors, &timeout) > 0) {
            int size = sizeof(error);
            if (getsockopt(channel, SOL_SOCKET, SO_ERROR, reinterpret_cast<char*>(&error), &size)) error = WSAGetLastError();
        }
    }
    std::printf("{\"error\":%d,\"access_denied\":%d}\n", error, error == WSAEACCES);
    closesocket(channel); WSACleanup(); return 0;
}

static bool sparseFile(const std::wstring& path, LONGLONG bytes) {
    Handle file;
    file.value=CreateFileW(path.c_str(),GENERIC_WRITE,0,nullptr,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,nullptr);
    DWORD returned=0;LARGE_INTEGER end{};end.QuadPart=bytes;
    return file.value!=INVALID_HANDLE_VALUE &&
        DeviceIoControl(file.value,FSCTL_SET_SPARSE,nullptr,0,nullptr,0,&returned,nullptr) &&
        SetFilePointerEx(file.value,end,nullptr,FILE_BEGIN) && SetEndOfFile(file.value);
}

static std::wstring profileFolder() {
    Handle token;
    if (!OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY,&token.value)) return L"";
    DWORD length=0;GetTokenInformation(token.value,TokenAppContainerSid,nullptr,0,&length);
    std::vector<BYTE> data(length);
    if (!GetTokenInformation(token.value,TokenAppContainerSid,data.data(),length,&length)) return L"";
    PSID sid=reinterpret_cast<TOKEN_APPCONTAINER_INFORMATION*>(data.data())->TokenAppContainer;
    LPWSTR sidText=nullptr;PWSTR path=nullptr;std::wstring result;
    if (!sid || !ConvertSidToStringSidW(sid,&sidText)) return L"";
    if (SUCCEEDED(GetAppContainerFolderPath(sidText,&path))) {result=path;CoTaskMemFree(path);}
    LocalFree(sidText);return result;
}

int wmain(int argc, wchar_t** argv) {
    if (argc < 2) return 2;
    const std::wstring mode(argv[1]);
    if (mode == L"info") return information();
    if ((mode == L"files" || mode == L"cross") && argc == 4) {
        if (mode == L"cross") { if (information()) return 3; std::fflush(stdout); if (std::getchar() == EOF) return 3; }
        return files(argv[2], argv[3]);
    }
    if (mode == L"environment") {
        std::printf("{"); bool first = true;
        for (const auto* key : {L"GITHUB_TOKEN", L"AWS_SECRET_ACCESS_KEY", L"PYTHONPATH", L"PYTHONHOME",
                 L"QT_PLUGIN_PATH", L"HTTP_PROXY", L"SSLKEYLOGFILE", L"APE_TEST_SECRET", L"APE_UI_LANGUAGE"}) {
            if (!first) std::putchar(','); first = false;
            jsonString(key); std::printf(":%d", GetEnvironmentVariableW(key, nullptr, 0) != 0);
        }
        wchar_t platform[128]{}; GetEnvironmentVariableW(L"QT_QPA_PLATFORM", platform, 128);
        wchar_t workspace[32768]{}, current[32768]{};
        GetEnvironmentVariableW(L"APE_WORKSPACE", workspace, 32768); GetCurrentDirectoryW(32768, current);
        std::printf(",\"offscreen\":%d,\"workspace_matches\":%d}\n",
            std::wstring(platform) == L"offscreen", _wcsicmp(workspace, current) == 0);
        return 0;
    }
    if (mode == L"arguments") {
        std::putchar('[');
        for (int i = 2; i < argc; ++i) { if (i != 2) std::putchar(','); jsonString(argv[i]); }
        std::puts("]"); return 0;
    }
    if (mode == L"network" && argc == 5) return network(argv[2], argv[3], argv[4]);
    if (mode == L"child") { return canOpen(L"child-created.txt", GENERIC_WRITE, CREATE_ALWAYS) ? 0 : 3; }
    if (mode == L"spawn" && argc == 3) {
        wchar_t executable[32768]{}; GetModuleFileNameW(nullptr, executable, 32768);
        std::wstring command = ape_sandbox::quoted(executable) + L" child";
        STARTUPINFOW startup{}; startup.cb = sizeof(startup); PROCESS_INFORMATION child{};
        BOOL launched = CreateProcessW(executable, command.data(), nullptr, nullptr, FALSE,
            CREATE_NO_WINDOW | (std::wstring(argv[2]) == L"breakaway" ? CREATE_BREAKAWAY_FROM_JOB : 0),
            nullptr, nullptr, &startup, &child);
        DWORD error = launched ? 0 : GetLastError();
        if (launched) { WaitForSingleObject(child.hProcess, 5000); CloseHandle(child.hThread); CloseHandle(child.hProcess); }
        std::printf("{\"launched\":%d,\"error\":%lu}\n", launched, error); return 0;
    }
    if (mode == L"process" && argc == 3) {
        DWORD pid = std::stoul(argv[2]);
        std::printf("{"); bool first = true;
        for (DWORD rights : {PROCESS_VM_READ, PROCESS_VM_WRITE | PROCESS_VM_OPERATION, PROCESS_DUP_HANDLE}) {
            Handle handle; handle.value = OpenProcess(rights, FALSE, pid);
            if (!first) std::putchar(','); first = false;
            std::printf("\"%lu\":%d", rights, handle.value != nullptr);
        }
        std::puts("}"); return 0;
    }
    if (mode == L"registry" && argc == 3) {
        HKEY key = nullptr;
        bool read = RegOpenKeyExW(HKEY_CURRENT_USER, argv[2], 0, KEY_READ, &key) == ERROR_SUCCESS;
        if (read) RegCloseKey(key);
        bool write = RegOpenKeyExW(HKEY_CURRENT_USER, argv[2], 0, KEY_SET_VALUE, &key) == ERROR_SUCCESS;
        if (write) RegCloseKey(key);
        std::printf("{\"read\":%d,\"write\":%d}\n", read, write); return 0;
    }
    if (mode == L"handle" && argc == 6) {
        auto handle = reinterpret_cast<HANDLE>(static_cast<uintptr_t>(std::stoull(argv[2])));
        bool match = sameFileHandle(handle, std::stoul(argv[3]), std::stoul(argv[4]), std::stoul(argv[5]));
        std::printf("{\"canary_inherited\":%d}\n", match); return 0;
    }
    if (mode == L"memory") {
        void* smallAllocation = VirtualAlloc(nullptr, 8ull*1024*1024, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        bool smallAllowed = smallAllocation != nullptr;
        if (smallAllocation) VirtualFree(smallAllocation, 0, MEM_RELEASE);
        void* large = VirtualAlloc(nullptr, 800ull*1024*1024, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        bool denied = large == nullptr;
        if (large) VirtualFree(large, 0, MEM_RELEASE);
        std::printf("{\"small_allowed\":%d,\"large_denied\":%d}\n", smallAllowed, denied); return 0;
    }
    if (mode == L"disk" || mode == L"profile_disk" || mode == L"split_disk") {
        LONGLONG workBytes=mode==L"disk" ? 1024ll*1024*1024+4096 : mode==L"split_disk" ? 640ll*1024*1024 : 0;
        LONGLONG profileBytes=mode==L"profile_disk" ? 1024ll*1024*1024+4096 : mode==L"split_disk" ? 448ll*1024*1024 : 0;
        if (workBytes && !sparseFile(L"sparse-quota.bin",workBytes)) return 3;
        if (profileBytes) {
            std::wstring folder=profileFolder();
            if (folder.empty() || !sparseFile(folder+L"\\sparse-quota.bin",profileBytes)) return 3;
        }
        std::printf("{\"pid\":%lu,\"work_bytes\":%lld,\"profile_bytes\":%lld}\n",
            GetCurrentProcessId(),workBytes,profileBytes);std::fflush(stdout);
        Sleep(INFINITE); return 4;
    }
    if (mode == L"hang" || mode == L"spin") {
        ready();
        if (mode == L"hang") Sleep(INFINITE);
        else { for (;;) YieldProcessor(); }
        return 4;
    }
    if (mode == L"flood") {
        char data[8192]; std::memset(data, 'X', sizeof(data));
        for (int i = 0; i < 128; ++i) { if (std::fwrite(data, 1, sizeof(data), stdout) != sizeof(data)) return 3; }
        std::fflush(stdout); return 0;
    }
    if (mode == L"crash") { TerminateProcess(GetCurrentProcess(), 37); return 4; }
    return 2;
}
