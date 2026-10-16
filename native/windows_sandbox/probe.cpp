#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#include <cstdio>
#include <string>

int wmain(int argc, wchar_t** argv) {
    if (argc != 4) return 2;
    std::puts("probe started"); std::fflush(stdout);
    auto allowed = [](const std::wstring& path, DWORD access, DWORD mode) {
        HANDLE file = CreateFileW(path.c_str(), access, FILE_SHARE_READ, nullptr, mode, 0, nullptr);
        if (file == INVALID_HANDLE_VALUE) return false;
        CloseHandle(file); return true;
    };
    if (!allowed(std::wstring(argv[1])+L"\\allowed.txt", GENERIC_WRITE, CREATE_ALWAYS)) return 3;
    std::puts("workspace writable"); std::fflush(stdout);
    if (allowed(argv[2], GENERIC_READ, OPEN_EXISTING)) return 4;
    if (allowed(std::wstring(argv[2])+L".write", GENERIC_WRITE, CREATE_ALWAYS)) return 5;
    std::puts("private paths denied"); std::fflush(stdout);
    WSADATA data{};
    if (WSAStartup(MAKEWORD(2,2), &data)) return 6;
    SOCKET channel = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    u_long nonblocking = 1;
    ioctlsocket(channel, FIONBIO, &nonblocking);
    sockaddr_in address{}; address.sin_family = AF_INET; address.sin_port = htons(static_cast<u_short>(std::stoul(argv[3])));
    address.sin_addr.s_addr = htonl(0x7f000001);
    int connected = connect(channel, reinterpret_cast<sockaddr*>(&address), sizeof(address));
    int error = WSAGetLastError();
    bool denied = error == WSAEACCES;
    if (connected != 0 && error == WSAEWOULDBLOCK) {
        fd_set writable, errors;
        FD_ZERO(&writable); FD_ZERO(&errors);
        FD_SET(channel, &writable); FD_SET(channel, &errors);
        timeval timeout{2,0};
        int ready = select(0, nullptr, &writable, &errors, &timeout);
        if (!ready) denied = true;
        else if (ready > 0) {
            int status = 0, length = sizeof(status);
            getsockopt(channel, SOL_SOCKET, SO_ERROR, reinterpret_cast<char*>(&status), &length);
            denied = status == WSAEACCES;
        }
    }
    closesocket(channel); WSACleanup();
    if (connected == 0 || !denied) return 7;
    wchar_t command[] = L"C:\\Windows\\System32\\cmd.exe /c exit 0";
    STARTUPINFOW startup{}; startup.cb = sizeof(startup);
    PROCESS_INFORMATION child{};
    if (CreateProcessW(nullptr, command, nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, nullptr, &startup, &child)) {
        CloseHandle(child.hThread); CloseHandle(child.hProcess); return 8;
    }
    std::puts("AppContainer: private reads, outside writes, network and child processes denied");
    return 0;
}
