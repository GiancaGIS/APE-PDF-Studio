// Exercise the COM factories directly; never register or install on the test PC.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <shobjidl.h>
#include <shlwapi.h>
#include <thumbcache.h>
#include <sddl.h>
#include <commctrl.h>
#include <shellapi.h>
#include <shlobj.h>
#include <atomic>
#include <string>
#include <cstring>
#include <vector>
#include <cstdio>
#include <stdexcept>
#include "shell_ids.h"
#pragma comment(linker, "\"/manifestdependency:type='win32' name='Microsoft.Windows.Common-Controls' version='6.0.0.0' processorArchitecture='amd64' publicKeyToken='6595b64144ccf1df' language='*'\"")

template<class T> struct Com {
    T* value = nullptr;
    ~Com() { if (value) value->Release(); }
    T* operator->() { return value; }
};
void require(bool condition, const char* message) { if (!condition) throw std::runtime_error(message); }
void pump() {
    MSG message;
    while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) { TranslateMessage(&message); DispatchMessageW(&message); }
    Sleep(10);
}
bool waitButton(HWND window, int id) {
    DWORD start = GetTickCount();
    while (GetTickCount() - start < 20000) {
        pump();
        if (IsWindowEnabled(GetDlgItem(window, id))) return true;
    }
    return false;
}
DWORD snapshot(HWND window) {
    BITMAPINFO info{}; info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    info.bmiHeader.biWidth = 400; info.bmiHeader.biHeight = -600;
    info.bmiHeader.biPlanes = 1; info.bmiHeader.biBitCount = 32;
    void* bits = nullptr;
    HBITMAP bitmap = CreateDIBSection(nullptr, &info, DIB_RGB_COLORS, &bits, nullptr, 0);
    require(bitmap != nullptr, "snapshot bitmap");
    HDC dc = CreateCompatibleDC(nullptr); HGDIOBJ old = SelectObject(dc, bitmap);
    SendMessageW(window, WM_PRINTCLIENT, reinterpret_cast<WPARAM>(dc), PRF_CLIENT);
    // The center lies inside both portrait and landscape pages in our fixtures.
    DWORD pixel = static_cast<DWORD*>(bits)[300 * 400 + 200] & 0xffffff;
    SelectObject(dc, old); DeleteDC(dc); DeleteObject(bitmap);
    return pixel;
}
#include "metadata_smoke.h"
int wmain(int argc, wchar_t** argv) {
    // A copy of this test host can stand in for an intentionally stalled helper.
    if (argc == 2 && wcscmp(argv[1], L"shell") == 0) {
        wchar_t fake[2];
        if (GetEnvironmentVariableW(L"APE_FAKE_METADATA_RESPONSE", fake, ARRAYSIZE(fake))) {
            BYTE buffer[65536]; DWORD got;
            while (ReadFile(GetStdHandle(STD_INPUT_HANDLE), buffer, sizeof(buffer), &got, nullptr) && got) {}
            DWORD invalid[] = {0x314d5041, 0, 17, 0xffffffff, 0}, written;
            WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), invalid, sizeof(invalid), &written, nullptr); return 0;
        }
        Sleep(INFINITE); return 1;
    }
    if (argc < 3) return 2;
    bool reject = argc > 3 && wcscmp(argv[3], L"--reject") == 0;
    bool metadata = argc > 3 && wcsncmp(argv[3], L"--metadata", 10) == 0;
    HDESK originalDesktop = GetThreadDesktop(GetCurrentThreadId()), metadataDesktop = nullptr;
    if (metadata) {
        std::wstring name = L"APE.MetadataSmoke." + std::to_wstring(GetCurrentProcessId());
        metadataDesktop = CreateDesktopW(name.c_str(), nullptr, nullptr, 0, GENERIC_ALL, nullptr);
        if (!metadataDesktop || !SetThreadDesktop(metadataDesktop)) { if (metadataDesktop) CloseDesktop(metadataDesktop); return 4; }
    }
    if (argc > 3 && wcscmp(argv[3], L"--low") == 0) {
        HANDLE token = nullptr; PSID sid = nullptr;
        if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_DEFAULT | TOKEN_QUERY, &token)) return 4;
        if (!ConvertStringSidToSidW(L"S-1-16-4096", &sid)) { CloseHandle(token); return 4; }
        TOKEN_MANDATORY_LABEL label{}; label.Label.Attributes = SE_GROUP_INTEGRITY; label.Label.Sid = sid;
        BOOL lowered = SetTokenInformation(token, TokenIntegrityLevel, &label,
            static_cast<DWORD>(sizeof(label) + GetLengthSid(sid)));
        LocalFree(sid); CloseHandle(token); if (!lowered) return 4;
    }
    HRESULT initialized = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (FAILED(initialized)) { if (metadataDesktop) { SetThreadDesktop(originalDesktop); CloseDesktop(metadataDesktop); } return 3; }
    int result = 0;
    HMODULE dll = LoadLibraryExW(argv[1], nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    HWND parent = nullptr;
    try {
        require(dll != nullptr, "load native DLL");
        auto factoryEntry = reinterpret_cast<HRESULT(__stdcall*)(REFCLSID, REFIID, void**)>(GetProcAddress(dll, "DllGetClassObject"));
        auto unloadEntry = reinterpret_cast<HRESULT(__stdcall*)()>(GetProcAddress(dll, "DllCanUnloadNow"));
        require(factoryEntry && unloadEntry, "COM exports");
        if (metadata) {
            metadataSmoke(factoryEntry, unloadEntry, argv[2], argv[3]);
            std::puts("Metadata COM checks passed");
        } else {
        HANDLE input = CreateFileW(argv[2], GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
        require(input != INVALID_HANDLE_VALUE, "open fixture");
        DWORD size = GetFileSize(input, nullptr), got = 0;
        std::vector<BYTE> bytes(size);
        BOOL read = ReadFile(input, bytes.data(), size, &got, nullptr); CloseHandle(input);
        require(read && got == size, "read fixture");
        {
            Com<IClassFactory> factory;
            require(SUCCEEDED(factoryEntry(CLSID_APEThumbnail, IID_PPV_ARGS(&factory.value))), "thumbnail factory");
            Com<IInitializeWithStream> initial;
            require(SUCCEEDED(factory->CreateInstance(nullptr, IID_PPV_ARGS(&initial.value))), "thumbnail instance");
            Com<IStream> stream; stream.value = SHCreateMemStream(bytes.data(), size);
            require(stream.value && SUCCEEDED(initial->Initialize(stream.value, STGM_READ)), "stream initialization");
            require(FAILED(initial->Initialize(stream.value, STGM_READ)), "reject double initialization");
            Com<IThumbnailProvider> thumbnail;
            require(SUCCEEDED(initial->QueryInterface(IID_PPV_ARGS(&thumbnail.value))), "thumbnail interface");
            HBITMAP bitmap = nullptr; WTS_ALPHATYPE alpha = WTSAT_UNKNOWN;
            HRESULT hr = thumbnail->GetThumbnail(256, &bitmap, &alpha);
            if (reject) {
                require(FAILED(hr) && bitmap == nullptr, "reject unsafe/protected/broken PDF");
            } else {
                require(SUCCEEDED(hr) && bitmap && alpha == WTSAT_RGB, "render thumbnail");
                BITMAP details{}; GetObjectW(bitmap, sizeof(details), &details);
                require(details.bmWidth > 0 && details.bmWidth <= 256 && details.bmHeight > 0 && details.bmHeight <= 256, "thumbnail dimensions");
                DeleteObject(bitmap);
            }
        }
        if (!reject) {
            Com<IClassFactory> factory;
            require(SUCCEEDED(factoryEntry(CLSID_APEPreview, IID_PPV_ARGS(&factory.value))), "preview factory");
            Com<IInitializeWithStream> initial;
            require(SUCCEEDED(factory->CreateInstance(nullptr, IID_PPV_ARGS(&initial.value))), "preview instance");
            Com<IStream> stream; stream.value = SHCreateMemStream(bytes.data(), size);
            require(stream.value && SUCCEEDED(initial->Initialize(stream.value, STGM_READ)), "preview initialization");
            Com<IPreviewHandler> preview;
            require(SUCCEEDED(initial->QueryInterface(IID_PPV_ARGS(&preview.value))), "preview interface");
            parent = CreateWindowExW(0, L"STATIC", L"", WS_OVERLAPPED, 0, 0, 400, 600, nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
            require(parent != nullptr, "hidden preview host");
            RECT area{0, 0, 400, 600};
            require(SUCCEEDED(preview->SetWindow(parent, &area)), "set preview parent");
            DWORD before = GetTickCount();
            require(SUCCEEDED(preview->DoPreview()), "start preview");
            require(GetTickCount() - before < 1000, "preview must return promptly");
            Com<IOleWindow> ole;
            require(SUCCEEDED(preview->QueryInterface(IID_PPV_ARGS(&ole.value))), "preview window interface");
            HWND child = nullptr; require(SUCCEEDED(ole->GetWindow(&child)), "preview child");
            require(waitButton(child, 102), "first page rendered and next enabled");
            DWORD first = snapshot(child);
            SendMessageW(child, WM_KEYDOWN, VK_NEXT, 0);
            require(waitButton(child, 101), "second page rendered and previous enabled");
            require(snapshot(child) != first, "second page content differs");
            area.right = 640; area.bottom = 480;
            require(SUCCEEDED(preview->SetRect(&area)), "resize preview");
            RECT resized{}; GetClientRect(child, &resized);
            require(resized.right == 640 && resized.bottom == 480, "preview resize applied");
            require(SUCCEEDED(preview->Unload()) && !IsWindow(child), "release preview window");
            // Unload while rendering must cancel without blocking the host.
            require(SUCCEEDED(initial->Initialize(stream.value, STGM_READ)), "reinitialize preview");
            require(SUCCEEDED(preview->DoPreview()), "restart preview");
            Sleep(250);
            before = GetTickCount(); preview->Unload();
            require(GetTickCount() - before < 1000, "unload must not block");
        }
        DWORD start = GetTickCount();
        while (unloadEntry() != S_OK && GetTickCount() - start < 20000) pump();
        require(unloadEntry() == S_OK, "COM objects and background jobs released");
        std::puts("Shell COM checks passed");
        }
    } catch (const std::exception& error) { std::fprintf(stderr, "%s\n", error.what()); result = 1; }
    if (parent) DestroyWindow(parent);
    // The process ends on a failed test, so do not unload code still in use.
    if (!result && dll) FreeLibrary(dll);
    CoUninitialize();
    if (metadataDesktop) { SetThreadDesktop(originalDesktop); CloseDesktop(metadataDesktop); }
    return result;
}
