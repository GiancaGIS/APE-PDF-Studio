// Native property-sheet integration tests; no registration on the test machine.
#include "metadata_resources.h"

class MetadataDrop final : public IDataObject {
    std::atomic<ULONG> refs{1}; std::wstring path; bool multiple;
public:
    explicit MetadataDrop(const wchar_t* filename, bool many = false) : path(filename), multiple(many) {}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** output) override {
        if (!output) return E_POINTER; *output = nullptr;
        if (iid != IID_IUnknown && iid != IID_IDataObject) return E_NOINTERFACE;
        *output = static_cast<IDataObject*>(this); AddRef(); return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs; }
    ULONG STDMETHODCALLTYPE Release() override { ULONG count = --refs; if (!count) delete this; return count; }
    HRESULT STDMETHODCALLTYPE GetData(FORMATETC* format, STGMEDIUM* medium) override {
        if (!format || !medium) return E_POINTER;
        if (format->cfFormat != CF_HDROP || !(format->tymed & TYMED_HGLOBAL)) return DV_E_FORMATETC;
        std::wstring names = path; names.push_back(L'\0');
        if (multiple) { names += path; names.push_back(L'\0'); }
        names.push_back(L'\0');
        SIZE_T size = sizeof(DROPFILES) + names.size() * sizeof(wchar_t);
        HGLOBAL memory = GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, size); if (!memory) return E_OUTOFMEMORY;
        void* data = GlobalLock(memory); if (!data) { GlobalFree(memory); return E_OUTOFMEMORY; }
        auto drop = static_cast<DROPFILES*>(data); drop->pFiles = sizeof(DROPFILES); drop->fWide = TRUE;
        std::memcpy(static_cast<BYTE*>(data) + sizeof(DROPFILES), names.data(), names.size() * sizeof(wchar_t)); GlobalUnlock(memory);
        *medium = STGMEDIUM{}; medium->tymed = TYMED_HGLOBAL; medium->hGlobal = memory; return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetDataHere(FORMATETC*, STGMEDIUM*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE QueryGetData(FORMATETC*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetCanonicalFormatEtc(FORMATETC*, FORMATETC*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE SetData(FORMATETC*, STGMEDIUM*, BOOL) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE EnumFormatEtc(DWORD, IEnumFORMATETC**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE DAdvise(FORMATETC*, DWORD, IAdviseSink*, DWORD*) override { return OLE_E_ADVISENOTSUPPORTED; }
    HRESULT STDMETHODCALLTYPE DUnadvise(DWORD) override { return OLE_E_ADVISENOTSUPPORTED; }
    HRESULT STDMETHODCALLTYPE EnumDAdvise(IEnumSTATDATA**) override { return OLE_E_ADVISENOTSUPPORTED; }
};
BOOL CALLBACK collectMetadataPage(HPROPSHEETPAGE page, LPARAM parameter) {
    try { reinterpret_cast<std::vector<HPROPSHEETPAGE>*>(parameter)->push_back(page); return TRUE; }
    catch (...) { return FALSE; }
}
BOOL CALLBACK rejectMetadataPage(HPROPSHEETPAGE, LPARAM) { return FALSE; }
using FactoryEntry = HRESULT(__stdcall*)(REFCLSID, REFIID, void**);
using UnloadEntry = HRESULT(__stdcall*)();
void captureMetadataSheet(HWND window) {
    wchar_t path[32768];
    DWORD length = GetEnvironmentVariableW(L"APE_METADATA_CAPTURE_PATH", path, ARRAYSIZE(path));
    if (!length || length >= ARRAYSIZE(path)) return;
    RECT bounds{}; GetWindowRect(window, &bounds);
    LONG width = bounds.right - bounds.left, height = bounds.bottom - bounds.top;
    require(width > 0 && height > 0 && width < 4096 && height < 4096, "capture dimensions");
    BITMAPINFO info{}; info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    info.bmiHeader.biWidth = width; info.bmiHeader.biHeight = -height;
    info.bmiHeader.biPlanes = 1; info.bmiHeader.biBitCount = 32;
    void* pixels = nullptr; HBITMAP bitmap = CreateDIBSection(nullptr, &info, DIB_RGB_COLORS, &pixels, nullptr, 0);
    require(bitmap != nullptr, "capture bitmap");
    HDC dc = CreateCompatibleDC(nullptr); HGDIOBJ old = SelectObject(dc, bitmap);
    BOOL printed = PrintWindow(window, dc, 0);
    HANDLE file = CreateFileW(path, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    BITMAPFILEHEADER header{}; header.bfType = 0x4d42; header.bfOffBits = sizeof(header) + sizeof(info.bmiHeader);
    DWORD pixelBytes = static_cast<DWORD>(width * height * 4); header.bfSize = header.bfOffBits + pixelBytes;
    DWORD written; BOOL saved = file != INVALID_HANDLE_VALUE &&
        WriteFile(file, &header, sizeof(header), &written, nullptr) &&
        WriteFile(file, &info.bmiHeader, sizeof(info.bmiHeader), &written, nullptr) &&
        WriteFile(file, pixels, pixelBytes, &written, nullptr);
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    SelectObject(dc, old); DeleteDC(dc); DeleteObject(bitmap);
    require(printed && saved, "capture native property sheet");
}
void metadataSmoke(FactoryEntry factoryEntry, UnloadEntry unloadEntry, const wchar_t* filename, const wchar_t* mode) {
    {
        Com<IClassFactory> factory;
        require(SUCCEEDED(factoryEntry(CLSID_APEMetadata, IID_PPV_ARGS(&factory.value))), "metadata factory");
        Com<IShellExtInit> initial;
        require(SUCCEEDED(factory->CreateInstance(nullptr, IID_PPV_ARGS(&initial.value))), "metadata instance");
        Com<IShellPropSheetExt> handler;
        require(SUCCEEDED(initial->QueryInterface(IID_PPV_ARGS(&handler.value))), "metadata property-sheet interface");
        require(FAILED(initial->Initialize(nullptr, nullptr, nullptr)), "reject missing selection");
        require(FAILED(handler->AddPages(collectMetadataPage, 0)), "no page before initialization");
        Com<IDataObject> multiple; multiple.value = new MetadataDrop(filename, true);
        require(FAILED(initial->Initialize(nullptr, multiple.value, nullptr)), "reject multiple selected files");
        Com<IDataObject> selection; selection.value = new MetadataDrop(filename);
        require(SUCCEEDED(initial->Initialize(nullptr, selection.value, nullptr)), "metadata CF_HDROP initialization");
        require(FAILED(handler->AddPages(rejectMetadataPage, 0)), "host refusal releases unaccepted page");
        std::vector<HPROPSHEETPAGE> pages;
        DWORD before = GetTickCount();
        require(SUCCEEDED(handler->AddPages(collectMetadataPage, reinterpret_cast<LPARAM>(&pages))) && pages.size() == 1,
            "add exactly one native properties page");
        require(GetTickCount() - before < 1000, "AddPages does not parse PDF or wait for worker");
        PROPSHEETHEADERW header{}; header.dwSize = sizeof(header); header.dwFlags = PSH_MODELESS | PSH_NOAPPLYNOW;
        header.hInstance = GetModuleHandleW(nullptr); header.pszCaption = L"PDF Properties";
        header.nPages = static_cast<UINT>(pages.size()); header.phpage = pages.data();
        HWND sheet = reinterpret_cast<HWND>(PropertySheetW(&header));
        require(sheet != nullptr && sheet != reinterpret_cast<HWND>(-1), "create Windows properties dialog");
        HWND page = PropSheet_GetCurrentPageHwnd(sheet); require(page != nullptr, "metadata page window");
        try {
            bool cancel = wcscmp(mode, L"--metadata-cancel") == 0;
            if (!cancel) {
                DWORD start = GetTickCount();
                while (!GetPropW(page, L"APE.MetadataStatus") && GetTickCount() - start < 20000) pump();
                UINT_PTR status = reinterpret_cast<UINT_PTR>(GetPropW(page, L"APE.MetadataStatus"));
                UINT_PTR expected = wcscmp(mode, L"--metadata-password") == 0 ? 2 : wcscmp(mode, L"--metadata-blocked") == 0 ? 3 : 1;
                require(status == expected, "metadata success/password/blocked status");
                HWND list = GetDlgItem(page, IDC_METADATA_LIST), detail = GetDlgItem(page, IDC_METADATA_VALUE);
                require((GetWindowLongPtrW(detail, GWL_STYLE) & ES_READONLY) != 0, "metadata value is read-only");
                if (expected == 1) {
                    require(ListView_GetItemCount(list) >= 17, "metadata rows populated");
                    wchar_t value[8192]; ListView_GetItemText(list, 0, 1, value, ARRAYSIZE(value));
                    require(wcscmp(value, L"APE metadata smoke: è 日本語 😀") == 0, "metadata Unicode title");
                    GetWindowTextW(detail, value, ARRAYSIZE(value));
                    SendMessageW(detail, WM_CHAR, L'x', 0);
                    wchar_t after[8192]; GetWindowTextW(detail, after, ARRAYSIZE(after));
                    require(wcscmp(value, after) == 0, "typing does not change metadata");
                    ListView_SetItemState(list, 1, LVIS_SELECTED | LVIS_FOCUSED, LVIS_SELECTED | LVIS_FOCUSED);
                    GetWindowTextW(detail, value, ARRAYSIZE(value));
                    require(wcscmp(value, L"GiancaGIS") == 0, "selection displays complete field value");
                    ListView_SetItemState(list, 0, LVIS_SELECTED | LVIS_FOCUSED, LVIS_SELECTED | LVIS_FOCUSED);
                    pump(); captureMetadataSheet(sheet);
                } else require(ListView_GetItemCount(list) == 0, "blocked/password file exposes no partial metadata");
            } else { pump(); Sleep(100); }
            before = GetTickCount(); DestroyWindow(sheet); sheet = nullptr;
            require(GetTickCount() - before < 1000, "closing metadata does not block on parsing");
        } catch (...) { if (sheet) DestroyWindow(sheet); throw; }
        // Destroying an unactivated page must also release its owner.
        pages.clear();
        require(SUCCEEDED(handler->AddPages(collectMetadataPage, reinterpret_cast<LPARAM>(&pages))), "recreate unactivated page");
        require(pages.size() == 1 && DestroyPropertySheetPage(pages[0]), "destroy unused page");
    }
    DWORD start = GetTickCount();
    while (unloadEntry() != S_OK && GetTickCount() - start < 20000) pump();
    require(unloadEntry() == S_OK, "metadata pages, COM objects and asynchronous workers released");
}
