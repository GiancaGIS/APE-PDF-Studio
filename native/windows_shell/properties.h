// Included inside shell.cpp's namespace: no PDF parser is loaded into Explorer.
#include "metadata_resources.h"

UINT metadataLanguage() {
    switch (PRIMARYLANGID(GetUserDefaultUILanguage())) {
        case LANG_ITALIAN: return 0;
        case LANG_SPANISH: return 2;
        case LANG_FRENCH: return 3;
        default: return 1;
    }
}
enum MetadataLabel { PageTitle, FieldLabel, ValueLabel, OpenLabel, LoadingLabel, ReadOnlyLabel, PasswordLabel, BlockedLabel, OpenFailedLabel };
const wchar_t* metadataLabel(MetadataLabel index) {
    static const wchar_t* labels[4][9] = {
        {L"Metadati PDF", L"Campo", L"Valore", L"Apri in APE PDF Studio", L"Lettura dei metadati...", L"Sola lettura", L"Password necessaria. Apri il PDF in APE PDF Studio.", L"Metadati non disponibili: PDF non leggibile o contenuti attivi.", L"Impossibile aprire APE PDF Studio."},
        {L"PDF metadata", L"Field", L"Value", L"Open in APE PDF Studio", L"Reading metadata...", L"Read-only", L"Password required. Open the PDF in APE PDF Studio.", L"Metadata unavailable: unreadable PDF or active content.", L"Unable to open APE PDF Studio."},
        {L"Metadatos PDF", L"Campo", L"Valor", L"Abrir en APE PDF Studio", L"Leyendo metadatos...", L"Solo lectura", L"Se requiere una contraseña. Abre el PDF en APE PDF Studio.", L"Metadatos no disponibles: PDF ilegible o contenido activo.", L"No se puede abrir APE PDF Studio."},
        {L"Métadonnées PDF", L"Champ", L"Valeur", L"Ouvrir dans APE PDF Studio", L"Lecture des métadonnées...", L"Lecture seule", L"Mot de passe requis. Ouvrez le PDF dans APE PDF Studio.", L"Métadonnées indisponibles : PDF illisible ou contenu actif.", L"Impossible d'ouvrir APE PDF Studio."},
    };
    return labels[metadataLanguage()][index];
}
struct MetadataResult {
    DWORD status = 2;
    std::vector<std::pair<std::wstring, std::wstring>> rows;
};
struct MetadataState {
    std::mutex mutex;
    std::shared_ptr<RenderState> process = std::make_shared<RenderState>();
    MetadataResult result;
    bool finished = false;
};
struct MetadataWork {
    std::shared_ptr<MetadataState> state;
    std::wstring path;
    UINT language;
    HMODULE library;
};
bool metadataFile(const std::wstring& path, const std::shared_ptr<RenderState>& state, std::vector<BYTE>& bytes) {
    Handle file(CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, nullptr));
    if (file.value == INVALID_HANDLE_VALUE || GetFileType(file.value) != FILE_TYPE_DISK) return false;
    LARGE_INTEGER size{};
    if (!GetFileSizeEx(file.value, &size) || size.QuadPart <= 0 || size.QuadPart > maxBytes) return false;
    bytes.resize(static_cast<size_t>(size.QuadPart));
    size_t offset = 0;
    while (offset < bytes.size()) {
        if (state->cancelled) return false;
        DWORD got = 0;
        if (!ReadFile(file.value, bytes.data() + offset, static_cast<DWORD>(std::min<size_t>(65536, bytes.size() - offset)), &got, nullptr) || !got) return false;
        offset += got;
    }
    return true;
}
bool metadataResponse(HANDLE pipe, MetadataResult& result) {
    DWORD header[5]{};
    if (!transfer(pipe, reinterpret_cast<BYTE*>(header), sizeof(header), false) ||
        header[0] != 0x314d5041 || header[1] > 2 || header[4] || header[3] > 256u * 1024u) return false; // APM1
    if (header[1]) {
        if (header[2] || header[3]) return false;
        result.status = header[1]; return true;
    }
    if (header[2] < 17 || header[2] > 18 || !header[3]) return false;
    std::vector<BYTE> payload(header[3]);
    if (!transfer(pipe, payload.data(), payload.size(), false)) return false;
    size_t offset = 0;
    auto text = [&](std::wstring& value) {
        if (offset + sizeof(DWORD) > payload.size()) return false;
        DWORD length = 0; std::memcpy(&length, payload.data() + offset, sizeof(length)); offset += sizeof(length);
        if (length > 32768 || length % sizeof(wchar_t) || length > payload.size() - offset) return false;
        value.resize(length / sizeof(wchar_t));
        std::memcpy(value.data(), payload.data() + offset, length); offset += length;
        return value.find(L'\0') == std::wstring::npos;
    };
    for (DWORD i = 0; i < header[2]; ++i) {
        std::wstring labelText, value;
        if (!text(labelText) || labelText.empty() || !text(value)) return false;
        result.rows.emplace_back(std::move(labelText), std::move(value));
    }
    if (offset != payload.size()) return false;
    result.status = 0; return true;
}
void CALLBACK metadataWork(PTP_CALLBACK_INSTANCE instance, void* context) {
    std::unique_ptr<MetadataWork> work(static_cast<MetadataWork*>(context));
    FreeLibraryWhenCallbackReturns(instance, work->library);
    MetadataResult result;
    bool success = false;
    try {
        std::vector<BYTE> bytes;
        if (metadataFile(work->path, work->state->process, bytes)) {
            DWORD request[] = {0x31515041, work->language, 0, static_cast<DWORD>(bytes.size())}; // APQ1
            success = runWorker(bytes, request, work->state->process, [&](HANDLE pipe) { return metadataResponse(pipe, result); });
        }
    } catch (...) {}
    if (!success) result = MetadataResult{};
    {
        std::lock_guard<std::mutex> lock(work->state->mutex);
        work->state->result = std::move(result); work->state->finished = true;
    }
    --objects;
}
class MetadataPage {
    std::atomic<ULONG> refs{1};
    std::wstring path;
    std::shared_ptr<MetadataState> state;
    MetadataResult result;
    void load(HWND window) {
        state = std::make_shared<MetadataState>();
        SetTimer(window, 1, 75, nullptr);
        auto work = std::make_unique<MetadataWork>(MetadataWork{state, path, metadataLanguage(), nullptr});
        if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
            reinterpret_cast<LPCWSTR>(metadataWork), &work->library)) { state->finished = true; return; }
        ++objects;
        if (TrySubmitThreadpoolCallback(metadataWork, work.get(), nullptr)) work.release();
        else { --objects; FreeLibrary(work->library); state->finished = true; }
    }
    void update(HWND window) {
        if (!state) return;
        {
            std::lock_guard<std::mutex> lock(state->mutex);
            if (!state->finished) return;
            result = std::move(state->result);
            state->finished = false;
        }
        KillTimer(window, 1);
        HWND list = GetDlgItem(window, IDC_METADATA_LIST);
        for (size_t i = 0; i < result.rows.size(); ++i) {
            LVITEMW item{}; item.mask = LVIF_TEXT; item.iItem = static_cast<int>(i); item.pszText = result.rows[i].first.data();
            ListView_InsertItem(list, &item);
            ListView_SetItemText(list, static_cast<int>(i), 1, result.rows[i].second.data());
        }
        SetDlgItemTextW(window, IDC_METADATA_STATUS, metadataLabel(result.status == 0 ? ReadOnlyLabel : result.status == 1 ? PasswordLabel : BlockedLabel));
        SetPropW(window, L"APE.MetadataStatus", reinterpret_cast<HANDLE>(static_cast<UINT_PTR>(result.status + 1)));
        if (!result.rows.empty()) { ListView_SetItemState(list, 0, LVIS_SELECTED | LVIS_FOCUSED, LVIS_SELECTED | LVIS_FOCUSED); }
        else SetDlgItemTextW(window, IDC_METADATA_VALUE, metadataLabel(result.status == 1 ? PasswordLabel : BlockedLabel));
    }
    void layout(HWND window) {
        RECT bounds{}; GetClientRect(window, &bounds);
        int dpi = static_cast<int>(GetDpiForWindow(window));
        auto scale = [dpi](int value) { return MulDiv(value, dpi ? dpi : 96, 96); };
        int margin = scale(12), gap = scale(8), footer = scale(34), detail = scale(76);
        int width = std::max(1L, bounds.right - margin * 2);
        int listHeight = std::max(1L, bounds.bottom - margin * 2 - footer - detail - gap * 2);
        MoveWindow(GetDlgItem(window, IDC_METADATA_LIST), margin, margin, width, listHeight, TRUE);
        MoveWindow(GetDlgItem(window, IDC_METADATA_VALUE), margin, margin + listHeight + gap, width, detail, TRUE);
        int top = bounds.bottom - margin - footer;
        int buttonWidth = std::min(width, scale(176));
        MoveWindow(GetDlgItem(window, IDC_METADATA_OPEN), margin + width - buttonWidth, top, buttonWidth, footer, TRUE);
        MoveWindow(GetDlgItem(window, IDC_METADATA_STATUS), margin, top, std::max(1, width - buttonWidth - gap), footer, TRUE);
        HWND list = GetDlgItem(window, IDC_METADATA_LIST);
        ListView_SetColumnWidth(list, 0, std::min(width / 2, scale(148)));
        ListView_SetColumnWidth(list, 1, std::max(1, width - std::min(width / 2, scale(148)) - scale(24)));
    }
    void open(HWND window) {
        wchar_t buffer[32768]; DWORD length = GetModuleFileNameW(module, buffer, ARRAYSIZE(buffer));
        if (!length || length >= ARRAYSIZE(buffer)) return;
        std::wstring folder(buffer, length); folder.resize(folder.find_last_of(L"\\/"));
        std::wstring executable = folder + L"\\APEPDFStudio.exe";
        std::wstring command = L"\"" + executable + L"\" \"" + path + L"\"";
        STARTUPINFOW startup{}; startup.cb = sizeof(startup); PROCESS_INFORMATION process{};
        if (CreateProcessW(executable.c_str(), command.data(), nullptr, nullptr, FALSE, 0, nullptr, folder.c_str(), &startup, &process)) {
            CloseHandle(process.hThread); CloseHandle(process.hProcess);
        } else SetDlgItemTextW(window, IDC_METADATA_STATUS, metadataLabel(OpenFailedLabel));
    }
public:
    explicit MetadataPage(const std::wstring& filename) : path(filename) { ++objects; }
    ~MetadataPage() { if (state) state->process->cancel(); --objects; }
    void addRef() { ++refs; }
    void release() { if (!--refs) delete this; }
    static UINT CALLBACK callback(HWND, UINT message, PROPSHEETPAGEW* page) {
        auto self = reinterpret_cast<MetadataPage*>(page->lParam);
        if (message == PSPCB_ADDREF) self->addRef();
        else if (message == PSPCB_RELEASE) self->release();
        return 1;
    }
    static INT_PTR CALLBACK procedure(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
        auto self = reinterpret_cast<MetadataPage*>(GetWindowLongPtrW(window, GWLP_USERDATA));
        if (message == WM_INITDIALOG) {
            self = reinterpret_cast<MetadataPage*>(reinterpret_cast<PROPSHEETPAGEW*>(lparam)->lParam);
            SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
        }
        if (!self) return FALSE;
        try {
            switch (message) {
                case WM_INITDIALOG: {
                    HWND list = GetDlgItem(window, IDC_METADATA_LIST);
                    ListView_SetExtendedListViewStyle(list, LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER);
                    LVCOLUMNW column{}; column.mask = LVCF_TEXT | LVCF_WIDTH; column.cx = 148;
                    column.pszText = const_cast<wchar_t*>(metadataLabel(FieldLabel)); ListView_InsertColumn(list, 0, &column);
                    column.pszText = const_cast<wchar_t*>(metadataLabel(ValueLabel)); ListView_InsertColumn(list, 1, &column);
                    SetDlgItemTextW(window, IDC_METADATA_OPEN, metadataLabel(OpenLabel));
                    SetDlgItemTextW(window, IDC_METADATA_STATUS, metadataLabel(LoadingLabel));
                    self->layout(window); self->load(window); return TRUE;
                }
                case WM_SIZE: self->layout(window); return TRUE;
                case WM_TIMER: if (wparam == 1) self->update(window); return TRUE;
                case WM_NOTIFY: {
                    auto header = reinterpret_cast<NMHDR*>(lparam);
                    if (header->idFrom == IDC_METADATA_LIST && header->code == LVN_ITEMCHANGED) {
                        auto item = reinterpret_cast<NMLISTVIEW*>(lparam);
                        if ((item->uNewState & LVIS_SELECTED) && item->iItem >= 0 && static_cast<size_t>(item->iItem) < self->result.rows.size())
                            SetDlgItemTextW(window, IDC_METADATA_VALUE, self->result.rows[item->iItem].second.c_str());
                    }
                    return FALSE;
                }
                case WM_COMMAND: if (LOWORD(wparam) == IDC_METADATA_OPEN && HIWORD(wparam) == BN_CLICKED) { self->open(window); return TRUE; } break;
                case WM_DESTROY: KillTimer(window, 1); if (self->state) self->state->process->cancel(); RemovePropW(window, L"APE.MetadataStatus"); return TRUE;
                case WM_NCDESTROY: SetWindowLongPtrW(window, GWLP_USERDATA, 0); break;
            }
        } catch (...) { SetDlgItemTextW(window, IDC_METADATA_STATUS, metadataLabel(BlockedLabel)); }
        return FALSE;
    }
};
class PropertyHandler final : public IShellExtInit, public IShellPropSheetExt {
    std::atomic<ULONG> refs{1};
    std::wstring path;
public:
    PropertyHandler() { ++objects; }
    ~PropertyHandler() { --objects; }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** output) override {
        if (!output) return E_POINTER; *output = nullptr;
        if (iid == IID_IUnknown || iid == IID_IShellExtInit) *output = static_cast<IShellExtInit*>(this);
        else if (iid == IID_IShellPropSheetExt) *output = static_cast<IShellPropSheetExt*>(this);
        else return E_NOINTERFACE;
        AddRef(); return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs; }
    ULONG STDMETHODCALLTYPE Release() override { ULONG count = --refs; if (!count) delete this; return count; }
    HRESULT STDMETHODCALLTYPE Initialize(PCIDLIST_ABSOLUTE, IDataObject* data, HKEY) override {
        path.clear(); if (!data) return E_INVALIDARG;
        FORMATETC format{CF_HDROP, nullptr, DVASPECT_CONTENT, -1, TYMED_HGLOBAL}; STGMEDIUM medium{};
        HRESULT hr = data->GetData(&format, &medium); if (FAILED(hr)) return hr;
        struct ReleaseMedium { STGMEDIUM* value; ~ReleaseMedium() { ReleaseStgMedium(value); } } release{&medium};
        if (medium.tymed != TYMED_HGLOBAL || !medium.hGlobal) return E_INVALIDARG;
        auto drop = static_cast<HDROP>(medium.hGlobal);
        if (DragQueryFileW(drop, 0xffffffff, nullptr, 0) != 1) return E_INVALIDARG;
        UINT length = DragQueryFileW(drop, 0, nullptr, 0); if (length < 5 || length >= 32768) return E_INVALIDARG;
        try {
            std::vector<wchar_t> filename(length + 1);
            if (DragQueryFileW(drop, 0, filename.data(), length + 1) != length) return E_FAIL;
            if (std::find(filename.begin(), filename.end(), L'"') != filename.end()) return E_INVALIDARG;
            if (_wcsicmp(filename.data() + length - 4, L".pdf") != 0) return E_INVALIDARG;
            path.assign(filename.data(), length); return S_OK;
        } catch (...) { return E_OUTOFMEMORY; }
    }
    HRESULT STDMETHODCALLTYPE AddPages(LPFNSVADDPROPSHEETPAGE add, LPARAM parameter) override {
        if (!add) return E_POINTER; if (path.empty()) return E_UNEXPECTED;
        try {
            INITCOMMONCONTROLSEX controls{sizeof(controls), ICC_LISTVIEW_CLASSES}; InitCommonControlsEx(&controls);
            auto context = new MetadataPage(path);
            PROPSHEETPAGEW page{}; page.dwSize = sizeof(page); page.dwFlags = PSP_USETITLE | PSP_USECALLBACK;
            page.hInstance = module; page.pszTemplate = MAKEINTRESOURCEW(IDD_PDF_METADATA); page.pszTitle = metadataLabel(PageTitle);
            page.pfnDlgProc = MetadataPage::procedure; page.pfnCallback = MetadataPage::callback; page.lParam = reinterpret_cast<LPARAM>(context);
            HPROPSHEETPAGE handle = CreatePropertySheetPageW(&page); context->release();
            if (!handle) return E_OUTOFMEMORY;
            if (!add(handle, parameter)) { DestroyPropertySheetPage(handle); return E_FAIL; }
            return S_OK;
        } catch (...) { return E_OUTOFMEMORY; }
    }
    HRESULT STDMETHODCALLTYPE ReplacePage(UINT, LPFNSVADDPROPSHEETPAGE, LPARAM) override { return E_NOTIMPL; }
};
