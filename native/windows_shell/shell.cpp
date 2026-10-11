// Native COM adapters only. PDF parsing stays in the bounded, hidden worker.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <shobjidl.h>
#include <thumbcache.h>
#include <shellapi.h>
#include <commctrl.h>
#include <functional>
#include <atomic>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>
#include <string>
#include <algorithm>
#include <cstring>
#include <cstdio>
#include "shell_ids.h"

namespace {
HINSTANCE module;
std::atomic<long> objects{0};
constexpr DWORD maxBytes = 256u * 1024u * 1024u;
constexpr UINT readyMessage = WM_APP + 31;
constexpr wchar_t windowClass[] = L"GiancaGIS.APEPDFStudio.Preview";
bool diagnostics() { wchar_t value[2]; return GetEnvironmentVariableW(L"APE_SHELL_DIAGNOSTICS", value, 2) != 0; }
void diagnostic(const char* stage) { if (diagnostics()) std::fprintf(stderr, "%s: Win32 %lu\n", stage, GetLastError()); }
struct Handle {
    HANDLE value = nullptr;
    Handle() = default;
    explicit Handle(HANDLE h) : value(h) {}
    ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
};
struct Image {
    DWORD width = 0, height = 0, pages = 0, index = 0;
    std::vector<BYTE> pixels;
};
struct RenderState {
    std::mutex mutex;
    std::atomic<bool> cancelled{false};
    HANDLE job = nullptr;
    Image image;
    bool finished = false, success = false;
    void cancel() {
        cancelled = true;
        std::lock_guard<std::mutex> lock(mutex);
        if (job) TerminateJobObject(job, 1);
    }
};
struct PreviewWork {
    std::shared_ptr<RenderState> state;
    std::shared_ptr<std::vector<BYTE>> bytes;
    HWND target;
    UINT page;
    HMODULE library;
};
HRESULT readStream(IStream* stream, std::vector<BYTE>& data) {
    STATSTG stat{};
    HRESULT hr = stream->Stat(&stat, STATFLAG_NONAME);
    if (FAILED(hr)) return hr;
    if (!stat.cbSize.QuadPart || stat.cbSize.QuadPart > maxBytes) return E_FAIL;
    LARGE_INTEGER start{};
    if (FAILED(hr = stream->Seek(start, STREAM_SEEK_SET, nullptr))) return hr;
    data.resize(static_cast<size_t>(stat.cbSize.QuadPart));
    size_t offset = 0;
    while (offset < data.size()) {
        ULONG count = 0;
        hr = stream->Read(data.data() + offset, static_cast<ULONG>(std::min<size_t>(65536, data.size() - offset)), &count);
        if (FAILED(hr) || !count) return E_FAIL;
        offset += count;
    }
    return S_OK;
}
bool transfer(HANDLE pipe, BYTE* buffer, size_t length, bool write) {
    size_t offset = 0;
    while (offset < length) {
        DWORD done = 0;
        DWORD chunk = static_cast<DWORD>(std::min<size_t>(65536, length - offset));
        BOOL ok = write ? WriteFile(pipe, buffer + offset, chunk, &done, nullptr)
                        : ReadFile(pipe, buffer + offset, chunk, &done, nullptr);
        if (!ok || !done) return false;
        offset += done;
    }
    return true;
}
bool runWorker(const std::vector<BYTE>& pdf, const DWORD (&request)[4],
    const std::shared_ptr<RenderState>& state, const std::function<bool(HANDLE)>& receive) {
    if (state->cancelled || pdf.empty() || pdf.size() > maxBytes) return false;
    wchar_t path[32768];
    DWORD length = GetModuleFileNameW(module, path, ARRAYSIZE(path));
    if (!length || length >= ARRAYSIZE(path)) return false;
    std::wstring folder(path, length);
    folder.resize(folder.find_last_of(L"\\/"));
    std::wstring worker = folder + L"\\APEPDFWorker.exe";
    std::wstring command = L"\"" + worker + L"\" shell";
    SECURITY_ATTRIBUTES security{sizeof(security), nullptr, TRUE};
    Handle inputRead, inputWrite, outputRead, outputWrite;
    if (!CreatePipe(&inputRead.value, &inputWrite.value, &security, 0) ||
        !CreatePipe(&outputRead.value, &outputWrite.value, &security, 0)) { diagnostic("CreatePipe"); return false; }
    SetHandleInformation(inputWrite.value, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(outputRead.value, HANDLE_FLAG_INHERIT, 0);
    Handle null(CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &security, OPEN_EXISTING, 0, nullptr));
    if (null.value == INVALID_HANDLE_VALUE) { diagnostic("NUL"); return false; }
    if (diagnostics()) {
        HANDLE inheritedError = nullptr;
        if (DuplicateHandle(GetCurrentProcess(), GetStdHandle(STD_ERROR_HANDLE), GetCurrentProcess(), &inheritedError, 0, TRUE, DUPLICATE_SAME_ACCESS)) {
            CloseHandle(null.value); null.value = inheritedError;
        }
    }
    SIZE_T attrBytes = 0;
    InitializeProcThreadAttributeList(nullptr, 1, 0, &attrBytes);
    std::vector<BYTE> attributes(attrBytes);
    auto attrList = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attributes.data());
    if (!InitializeProcThreadAttributeList(attrList, 1, 0, &attrBytes)) return false;
    HANDLE inherited[] = {inputRead.value, outputWrite.value, null.value};
    bool configured = !!UpdateProcThreadAttribute(attrList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, inherited, sizeof(inherited), nullptr, nullptr);
    STARTUPINFOEXW startup{};
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = inputRead.value;
    startup.StartupInfo.hStdOutput = outputWrite.value;
    startup.StartupInfo.hStdError = null.value;
    startup.lpAttributeList = attrList;
    Handle job(CreateJobObjectW(nullptr, nullptr));
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
    limits.BasicLimitInformation.ActiveProcessLimit = 1;
    limits.ProcessMemoryLimit = 768u * 1024u * 1024u;
    configured = configured && job.value && SetInformationJobObject(job.value, JobObjectExtendedLimitInformation, &limits, sizeof(limits));
    PROCESS_INFORMATION process{};
    BOOL launched = configured && CreateProcessW(worker.c_str(), command.data(), nullptr, nullptr, TRUE,
        CREATE_NO_WINDOW | CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT, nullptr, folder.c_str(), &startup.StartupInfo, &process);
    DeleteProcThreadAttributeList(attrList);
    if (!launched) { diagnostic("CreateProcess/job setup"); return false; }
    Handle processHandle(process.hProcess), threadHandle(process.hThread);
    if (!AssignProcessToJobObject(job.value, process.hProcess)) { diagnostic("AssignProcessToJobObject"); TerminateProcess(process.hProcess, 1); return false; }
    {
        std::lock_guard<std::mutex> lock(state->mutex);
        state->job = job.value;
        if (state->cancelled) TerminateJobObject(job.value, 1);
    }
    struct JobLink {
        std::shared_ptr<RenderState> state;
        ~JobLink() { std::lock_guard<std::mutex> lock(state->mutex); state->job = nullptr; }
    } link{state};
    // Watchdog also breaks stalled pipe I/O by terminating the entire job.
    std::thread watchdog([&] {
        if (WaitForSingleObject(process.hProcess, 15000) != WAIT_OBJECT_0) TerminateJobObject(job.value, 1);
    });
    ResumeThread(process.hThread);
    CloseHandle(inputRead.value); inputRead.value = nullptr;
    CloseHandle(outputWrite.value); outputWrite.value = nullptr;
    bool ok = transfer(inputWrite.value, reinterpret_cast<BYTE*>(const_cast<DWORD*>(request)), sizeof(request), true) &&
        transfer(inputWrite.value, const_cast<BYTE*>(pdf.data()), pdf.size(), true);
    CloseHandle(inputWrite.value); inputWrite.value = nullptr;
    try { ok = ok && receive(outputRead.value); } catch (...) { ok = false; }
    if (!ok) TerminateJobObject(job.value, 1);
    watchdog.join();
    DWORD exitCode = 1;
    GetExitCodeProcess(process.hProcess, &exitCode);
    if (diagnostics() && (!ok || exitCode)) std::fprintf(stderr, "Worker exit=%lu ok=%d cancelled=%d\n", exitCode, ok ? 1 : 0, state->cancelled ? 1 : 0);
    if (!ok || exitCode || state->cancelled) return false;
    return true;
}
bool render(const std::vector<BYTE>& pdf, UINT edge, UINT page, const std::shared_ptr<RenderState>& state, Image& image) {
    DWORD request[] = {0x31525041, std::clamp(edge, 1u, 2048u), page, static_cast<DWORD>(pdf.size())}; // APR1
    return runWorker(pdf, request, state, [&](HANDLE pipe) {
        DWORD response[5]{};
        if (!transfer(pipe, reinterpret_cast<BYTE*>(response), sizeof(response), false) ||
            response[0] != 0x31455041 || !response[1] || !response[2] || response[1] > request[1] ||
            response[2] > request[1] || !response[3] || response[3] > 3000 || response[4] != page || page >= response[3]) return false;
        image.pixels.resize(static_cast<size_t>(response[1]) * response[2] * 4);
        if (!transfer(pipe, image.pixels.data(), image.pixels.size(), false)) return false;
        image.width = response[1]; image.height = response[2]; image.pages = response[3]; image.index = page;
        return true;
    });
}
void CALLBACK previewWork(PTP_CALLBACK_INSTANCE instance, void* context) {
    std::unique_ptr<PreviewWork> work(static_cast<PreviewWork*>(context));
    // The loader keeps this DLL alive until the callback has actually returned.
    FreeLibraryWhenCallbackReturns(instance, work->library);
    Image result;
    bool success = false;
    try { success = render(*work->bytes, 1600, work->page, work->state, result); } catch (...) {}
    {
        std::lock_guard<std::mutex> lock(work->state->mutex);
        work->state->image = std::move(result); work->state->success = success; work->state->finished = true;
    }
    if (!work->state->cancelled) PostMessageW(work->target, readyMessage, 0, 0);
    --objects;
}
BITMAPINFO bitmapInfo(const Image& image) {
    BITMAPINFO info{};
    info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    info.bmiHeader.biWidth = static_cast<LONG>(image.width);
    info.bmiHeader.biHeight = -static_cast<LONG>(image.height);
    info.bmiHeader.biPlanes = 1;
    info.bmiHeader.biBitCount = 32;
    info.bmiHeader.biCompression = BI_RGB;
    return info;
}
const wchar_t* label(bool error) {
    switch (PRIMARYLANGID(GetUserDefaultUILanguage())) {
        case LANG_ITALIAN: return error ? L"Anteprima non disponibile per questo PDF." : L"Caricamento anteprima...";
        case LANG_SPANISH: return error ? L"Vista previa no disponible para este PDF." : L"Cargando vista previa...";
        case LANG_FRENCH: return error ? L"Aperçu indisponible pour ce PDF." : L"Chargement de l'aperçu...";
        default: return error ? L"Preview unavailable for this PDF." : L"Loading preview...";
    }
}

class Handler final : public IInitializeWithStream, public IThumbnailProvider, public IPreviewHandler,
    public IOleWindow, public IObjectWithSite, public IPreviewHandlerVisuals {
    std::atomic<ULONG> refs{1};
    bool thumbnail;
    IStream* stream = nullptr;
    IUnknown* site = nullptr;
    IPreviewHandlerFrame* frame = nullptr;
    HWND parent = nullptr, window = nullptr, previous = nullptr, next = nullptr;
    RECT rect{};
    COLORREF background = GetSysColor(COLOR_WINDOW), foreground = GetSysColor(COLOR_WINDOWTEXT);
    HFONT font = nullptr;
    Image image;
    UINT requestedPage = 0;
    bool failed = false;
    std::shared_ptr<std::vector<BYTE>> data;
    std::shared_ptr<RenderState> state;
    void cancel() { if (state) state->cancel(); state.reset(); }
    void load(UINT page) {
        cancel(); failed = false; requestedPage = page;
        state = std::make_shared<RenderState>();
        auto work = std::make_unique<PreviewWork>(PreviewWork{state, data, window, page, nullptr});
        if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
            reinterpret_cast<LPCWSTR>(previewWork), &work->library)) { failed = true; state.reset(); return; }
        ++objects;
        if (TrySubmitThreadpoolCallback(previewWork, work.get(), nullptr)) work.release();
        else { --objects; FreeLibrary(work->library); failed = true; state.reset(); }
        InvalidateRect(window, nullptr, TRUE);
    }
    bool key(UINT key) {
        if (!image.pages) return false;
        UINT page = requestedPage;
        if (key == VK_RIGHT || key == VK_DOWN || key == VK_NEXT) { if (page + 1 < image.pages) ++page; }
        else if (key == VK_LEFT || key == VK_UP || key == VK_PRIOR) { if (page) --page; }
        else if (key == VK_HOME) page = 0;
        else if (key == VK_END) page = image.pages - 1;
        else return false;
        if (page != requestedPage) load(page);
        return true;
    }
    void paint(HDC dc) {
        RECT bounds{}; GetClientRect(window, &bounds);
        HBRUSH brush = CreateSolidBrush(background);
        FillRect(dc, &bounds, brush); DeleteObject(brush);
        SetBkMode(dc, TRANSPARENT); ::SetTextColor(dc, foreground);
        auto oldFont = SelectObject(dc, font ? font : GetStockObject(DEFAULT_GUI_FONT));
        RECT content = bounds; content.bottom = std::max<LONG>(0, content.bottom - 38);
        if (!image.pixels.empty() && !failed) {
            double scale = std::min(std::max(1L, content.right - 16) / double(image.width),
                                    std::max(1L, content.bottom - 16) / double(image.height));
            int width = static_cast<int>(image.width * scale), height = static_cast<int>(image.height * scale);
            auto info = bitmapInfo(image);
            SetStretchBltMode(dc, HALFTONE); SetBrushOrgEx(dc, 0, 0, nullptr);
            StretchDIBits(dc, (content.right - width) / 2, (content.bottom - height) / 2, width, height,
                0, 0, image.width, image.height, image.pixels.data(), &info, DIB_RGB_COLORS, SRCCOPY);
        } else DrawTextW(dc, label(failed), -1, &content, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
        RECT counter = bounds; counter.top = content.bottom; counter.left = 48; counter.right -= 48;
        std::wstring text = image.pages ? std::to_wstring(requestedPage + 1) + L" / " + std::to_wstring(image.pages) : L"APE PDF Studio";
        DrawTextW(dc, text.c_str(), -1, &counter, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        SelectObject(dc, oldFont);
    }
    static LRESULT CALLBACK procedure(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
        auto self = reinterpret_cast<Handler*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
        if (message == WM_NCCREATE) {
            self = static_cast<Handler*>(reinterpret_cast<CREATESTRUCTW*>(lparam)->lpCreateParams);
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
        }
        if (!self) return DefWindowProcW(hwnd, message, wparam, lparam);
        try {
            switch (message) {
                case readyMessage:
                    if (self->state) {
                        std::lock_guard<std::mutex> lock(self->state->mutex);
                        if (self->state->finished) {
                            self->state->finished = false;
                            self->failed = !self->state->success;
                            if (!self->failed) self->image = std::move(self->state->image);
                            EnableWindow(self->previous, !self->failed && self->requestedPage > 0);
                            EnableWindow(self->next, !self->failed && self->requestedPage + 1 < self->image.pages);
                            InvalidateRect(hwnd, nullptr, TRUE);
                        }
                    }
                    return 0;
                case WM_SIZE: {
                    RECT r{}; GetClientRect(hwnd, &r);
                    MoveWindow(self->previous, 8, std::max(0L, r.bottom - 34), 32, 28, TRUE);
                    MoveWindow(self->next, std::max(0L, r.right - 40), std::max(0L, r.bottom - 34), 32, 28, TRUE);
                    InvalidateRect(hwnd, nullptr, TRUE); return 0;
                }
                case WM_COMMAND:
                    if (HIWORD(wparam) == BN_CLICKED) self->key(LOWORD(wparam) == 101 ? VK_PRIOR : VK_NEXT);
                    return 0;
                case WM_KEYDOWN: if (self->key(static_cast<UINT>(wparam))) return 0; break;
                case WM_MOUSEWHEEL: self->key(GET_WHEEL_DELTA_WPARAM(wparam) > 0 ? VK_PRIOR : VK_NEXT); return 0;
                case WM_LBUTTONDOWN: ::SetFocus(hwnd); return 0;
                case WM_GETDLGCODE: return DLGC_WANTARROWS;
                case WM_ERASEBKGND: return 1;
                case WM_PRINTCLIENT: self->paint(reinterpret_cast<HDC>(wparam)); return 0;
                case WM_PAINT: {
                    PAINTSTRUCT ps{}; HDC dc = BeginPaint(hwnd, &ps); self->paint(dc); EndPaint(hwnd, &ps); return 0;
                }
                case WM_NCDESTROY: SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0); break;
            }
        } catch (...) { return 0; }
        return DefWindowProcW(hwnd, message, wparam, lparam);
    }
public:
    explicit Handler(bool thumb) : thumbnail(thumb) { ++objects; }
    ~Handler() { Unload(); SetSite(nullptr); if (font) DeleteObject(font); --objects; }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** result) override {
        if (!result) return E_POINTER;
        *result = nullptr;
        if (iid == IID_IUnknown || iid == __uuidof(IInitializeWithStream)) *result = static_cast<IInitializeWithStream*>(this);
        else if (thumbnail && iid == __uuidof(IThumbnailProvider)) *result = static_cast<IThumbnailProvider*>(this);
        else if (!thumbnail && iid == __uuidof(IPreviewHandler)) *result = static_cast<IPreviewHandler*>(this);
        else if (!thumbnail && iid == IID_IOleWindow) *result = static_cast<IOleWindow*>(this);
        else if (!thumbnail && iid == IID_IObjectWithSite) *result = static_cast<IObjectWithSite*>(this);
        else if (!thumbnail && iid == __uuidof(IPreviewHandlerVisuals)) *result = static_cast<IPreviewHandlerVisuals*>(this);
        else return E_NOINTERFACE;
        AddRef(); return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs; }
    ULONG STDMETHODCALLTYPE Release() override { ULONG count = --refs; if (!count) delete this; return count; }
    HRESULT STDMETHODCALLTYPE Initialize(IStream* input, DWORD mode) override {
        if (!input) return E_POINTER;
        if (stream) return HRESULT_FROM_WIN32(ERROR_ALREADY_INITIALIZED);
        if (mode & (STGM_WRITE | STGM_READWRITE)) return E_ACCESSDENIED;
        stream = input; stream->AddRef(); return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetThumbnail(UINT edge, HBITMAP* bitmap, WTS_ALPHATYPE* alpha) override {
        if (!bitmap || !alpha) return E_POINTER;
        *bitmap = nullptr; *alpha = WTSAT_UNKNOWN;
        if (!stream || !edge) return E_INVALIDARG;
        try {
            std::vector<BYTE> bytes; HRESULT hr = readStream(stream, bytes); if (FAILED(hr)) return hr;
            Image result;
            if (!render(bytes, edge, 0, std::make_shared<RenderState>(), result)) return E_FAIL;
            auto info = bitmapInfo(result); void* bits = nullptr;
            *bitmap = CreateDIBSection(nullptr, &info, DIB_RGB_COLORS, &bits, nullptr, 0);
            if (!*bitmap) return E_OUTOFMEMORY;
            std::memcpy(bits, result.pixels.data(), result.pixels.size()); *alpha = WTSAT_RGB; return S_OK;
        } catch (...) { return E_OUTOFMEMORY; }
    }
    HRESULT STDMETHODCALLTYPE SetWindow(HWND owner, const RECT* area) override {
        if (!area) return E_POINTER;
        parent = owner; rect = *area;
        if (window) { SetParent(window, parent); MoveWindow(window, rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top, TRUE); }
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE SetRect(const RECT* area) override { return SetWindow(parent, area); }
    HRESULT STDMETHODCALLTYPE DoPreview() override {
        if (!stream || !parent || window) return E_UNEXPECTED;
        try {
            data = std::make_shared<std::vector<BYTE>>(); HRESULT hr = readStream(stream, *data); if (FAILED(hr)) return hr;
            WNDCLASSEXW wc{}; wc.cbSize = sizeof(wc); wc.hInstance = module; wc.lpfnWndProc = procedure;
            wc.lpszClassName = windowClass; wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
            if (!RegisterClassExW(&wc) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) return E_FAIL;
            window = CreateWindowExW(0, windowClass, L"APE PDF Studio", WS_CHILD | WS_VISIBLE | WS_CLIPCHILDREN,
                rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top, parent, nullptr, module, this);
            if (!window) return E_FAIL;
            previous = CreateWindowExW(0, L"BUTTON", L"‹", WS_CHILD | WS_VISIBLE | WS_TABSTOP, 8, 0, 32, 28, window, reinterpret_cast<HMENU>(101), module, nullptr);
            next = CreateWindowExW(0, L"BUTTON", L"›", WS_CHILD | WS_VISIBLE | WS_TABSTOP, 48, 0, 32, 28, window, reinterpret_cast<HMENU>(102), module, nullptr);
            EnableWindow(previous, FALSE); EnableWindow(next, FALSE);
            SendMessageW(window, WM_SIZE, 0, 0); load(0); return S_OK;
        } catch (...) { return E_OUTOFMEMORY; }
    }
    HRESULT STDMETHODCALLTYPE Unload() override {
        cancel(); if (window) DestroyWindow(window);
        window = previous = next = nullptr;
        if (stream) { stream->Release(); stream = nullptr; }
        data.reset(); image = {}; requestedPage = 0; failed = false; return S_OK;
    }
    HRESULT STDMETHODCALLTYPE SetFocus() override { if (window) ::SetFocus(window); return S_OK; }
    HRESULT STDMETHODCALLTYPE QueryFocus(HWND* output) override { if (!output) return E_POINTER; *output = GetFocus(); return S_OK; }
    HRESULT STDMETHODCALLTYPE TranslateAccelerator(MSG* message) override {
        if (!message) return E_POINTER;
        try { if (message->message == WM_KEYDOWN && key(static_cast<UINT>(message->wParam))) return S_OK; } catch (...) { return E_FAIL; }
        return frame ? frame->TranslateAccelerator(message) : S_FALSE;
    }
    HRESULT STDMETHODCALLTYPE GetWindow(HWND* output) override { if (!output) return E_POINTER; *output = window; return window ? S_OK : E_FAIL; }
    HRESULT STDMETHODCALLTYPE ContextSensitiveHelp(BOOL) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE SetSite(IUnknown* value) override {
        if (value) value->AddRef();
        if (frame) frame->Release(); frame = nullptr;
        if (site) site->Release(); site = value;
        if (site) site->QueryInterface(IID_PPV_ARGS(&frame)); return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetSite(REFIID iid, void** output) override {
        if (!output) return E_POINTER; *output = nullptr;
        return site ? site->QueryInterface(iid, output) : E_FAIL;
    }
    HRESULT STDMETHODCALLTYPE SetBackgroundColor(COLORREF color) override { background = color; if (window) InvalidateRect(window, nullptr, TRUE); return S_OK; }
    HRESULT STDMETHODCALLTYPE SetTextColor(COLORREF color) override { foreground = color; if (window) InvalidateRect(window, nullptr, TRUE); return S_OK; }
    HRESULT STDMETHODCALLTYPE SetFont(const LOGFONTW* value) override {
        if (!value) return E_POINTER;
        HFONT replacement = CreateFontIndirectW(value); if (!replacement) return E_OUTOFMEMORY;
        if (font) DeleteObject(font); font = replacement; if (window) InvalidateRect(window, nullptr, TRUE); return S_OK;
    }
};
#include "properties.h"
class Factory final : public IClassFactory {
    std::atomic<ULONG> refs{1}; CLSID kind;
public:
    explicit Factory(REFCLSID value) : kind(value) { ++objects; }
    ~Factory() { --objects; }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** output) override {
        if (!output) return E_POINTER; *output = nullptr;
        if (iid != IID_IUnknown && iid != IID_IClassFactory) return E_NOINTERFACE;
        *output = static_cast<IClassFactory*>(this); AddRef(); return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs; }
    ULONG STDMETHODCALLTYPE Release() override { ULONG count = --refs; if (!count) delete this; return count; }
    HRESULT STDMETHODCALLTYPE CreateInstance(IUnknown* outer, REFIID iid, void** output) override {
        if (!output) return E_POINTER; *output = nullptr; if (outer) return CLASS_E_NOAGGREGATION;
        try {
            if (kind == CLSID_APEMetadata) {
                auto handler = new PropertyHandler(); HRESULT hr = handler->QueryInterface(iid, output); handler->Release(); return hr;
            }
            auto handler = new Handler(kind == CLSID_APEThumbnail); HRESULT hr = handler->QueryInterface(iid, output); handler->Release(); return hr;
        }
        catch (...) { return E_OUTOFMEMORY; }
    }
    HRESULT STDMETHODCALLTYPE LockServer(BOOL lock) override { if (lock) ++objects; else --objects; return S_OK; }
};
} // namespace
extern "C" HRESULT __stdcall DllGetClassObject(REFCLSID clsid, REFIID iid, void** output) {
    if (!output) return E_POINTER; *output = nullptr;
    if (clsid != CLSID_APEThumbnail && clsid != CLSID_APEPreview && clsid != CLSID_APEMetadata) return CLASS_E_CLASSNOTAVAILABLE;
    try { auto factory = new Factory(clsid); HRESULT hr = factory->QueryInterface(iid, output); factory->Release(); return hr; }
    catch (...) { return E_OUTOFMEMORY; }
}
extern "C" HRESULT __stdcall DllCanUnloadNow() {
    if (objects.load()) return S_FALSE;
    UnregisterClassW(windowClass, module); return S_OK;
}
BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) module = instance;
    return TRUE;
}
