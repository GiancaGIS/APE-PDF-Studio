"""Desktop shell: tabbed reading and explicit non-destructive editing."""
from __future__ import annotations

from pathlib import Path
from ..core.user_paths import user_path, same_file
from .. import __version__

from PySide6.QtCore import QSettings, QSignalBlocker, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QColor, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QMenu,
    QProgressDialog,
    QPushButton,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.editor import PDFOperationError, PDFPasswordRequired, parse_page_range
from .document_tab import DocumentTab
from .document_tabs import DocumentTabs
from .exports import DocxExportJob, export_page_image
from .icons import application_icon, icon
from .page_number import PageNumberEdit
from .settings_dialog import SettingsDialog
from .theme import DEFAULT_THEME, apply_theme, normalize_theme, theme_color
from .toast import CompletionToast, UpdateToast
from .content_dialogs import NoteDialog, TextEditDialog
from .split_dialog import SplitDialog
from .document_tools import BlankPageDialog, WatermarkDialog, EditProgress
from .print_dialog import PrintDialog
from .ocr import OcrDialog, OcrJob
from .language import configure_language
from .licenses_dialog import LicensesDialog
from .updates_dialog import UpdatesDialog
from .update_checker import UpdateChecker
from ..core.pdf_links import allowed_url
from ..core.localization import DEFAULT_LANGUAGE, normalize_language, tr


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("APEMainWindow")
        # Keep the original preference namespace across the product rename.
        self.settings = QSettings(QSettings.defaultFormat(), QSettings.Scope.UserScope, "APEX", "PDF Studio")
        self.language_name = configure_language(QApplication.instance(), self.settings.value("ui/language", DEFAULT_LANGUAGE))
        self.theme_name = apply_theme(QApplication.instance(), normalize_theme(self.settings.value("ui/theme", DEFAULT_THEME)))
        self.setWindowTitle("APE PDF Studio")
        self.setWindowIcon(application_icon())
        self.setMinimumSize(900, 620)
        self.resize(1400, 880)
        self.setAcceptDrops(True)
        self._doc_actions: list[QAction] = []
        self._edit_actions: list[QAction] = []
        self._export_job = None
        self._ocr_job = None
        self._ocr_tab = None
        self._page_context = None
        self.close_guard = None

        self.tabs = DocumentTabs()
        self.tabs.currentChanged.connect(self._update_state)
        self.tabs.tabCloseRequested.connect(self.close_tab)

        self.stack = QStackedWidget()
        self.welcome = self._make_welcome()
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.tabs)
        self.setCentralWidget(self.stack)
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_searchbar()
        self._build_navigation()
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_search)
        self.statusBar().showMessage(tr("Pronto — trascina un PDF qui oppure premi Ctrl+O"))
        self._update_state()
        self._restore_window()
        self.toast = CompletionToast(self)
        self.update_toast = UpdateToast(self, self.toast)
        self.update_checker = UpdateChecker(self)
        self.update_checker.completed.connect(self._startup_update_finished)
        self.update_checker.failed.connect(self._startup_update_failed)
        self._startup_requested = False
        self._startup_pending = False
        self._startup_timer = QTimer(self)
        self._startup_timer.setSingleShot(True)
        self._startup_timer.timeout.connect(self._check_startup_update)

    def _make_welcome(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Welcome")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(40, 24, 40, 24)
        outer.addStretch()
        card = QFrame()
        card.setObjectName("StartCard")
        card.setMinimumWidth(440)
        card.setMaximumWidth(520)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(32, 30, 32, 28)
        layout.setSpacing(14)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(40, 52, 70, 24))
        card.setGraphicsEffect(shadow)
        brand = QHBoxLayout()
        badge = QLabel()
        badge.setObjectName("AppBadge")
        badge.setFixedSize(48, 48)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(application_icon().pixmap(48, 48))
        self.welcome_badge = badge
        brand.addWidget(badge)
        brand_name = QLabel("APE PDF STUDIO")
        brand_name.setObjectName("BrandName")
        brand.addWidget(brand_name, 1)
        layout.addLayout(brand)
        title = QLabel(tr("Il tuo spazio PDF"))
        title.setObjectName("StartTitle")
        caption = QLabel(tr("Trascina un documento qui oppure scegli un file."))
        caption.setObjectName("Caption")
        caption.setWordWrap(True)
        button = QPushButton(tr("Scegli documento…"))
        button.setMinimumHeight(40)
        button.setObjectName("Primary")
        button.setIcon(icon("open", "#ffffff"))
        button.clicked.connect(self.open_dialog)
        layout.addWidget(title)
        layout.addWidget(caption)
        layout.addWidget(button)
        self.recent_layout = QVBoxLayout()
        self.recent_layout.setSpacing(4)
        layout.addLayout(self.recent_layout)
        self._refresh_recent()
        outer.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch()
        return page

    def _refresh_recent(self):
        while self.recent_layout.count():
            item = self.recent_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        paths = self.settings.value("recent/files", []) or []
        if isinstance(paths, str):
            paths = [paths]
        self.recent_files = [str(path) for path in paths if Path(path).is_file()][:5]
        if self.recent_files:
            title = QLabel(tr("DOCUMENTI RECENTI"))
            title.setObjectName("Accent")
            self.recent_layout.addWidget(title)
        for path in self.recent_files[:3]:
            button = QPushButton(Path(path).name)
            button.setObjectName("RecentFile")
            button.setIcon(icon("page"))
            button.setProperty("glyph", "page")
            button.setToolTip(path)
            button.clicked.connect(lambda checked=False, value=path: self.open_pdf(Path(value)))
            self.recent_layout.addWidget(button)

    def _remember_file(self, path):
        paths = [str(path)] + [p for p in self.recent_files if p != str(path)]
        self.settings.setValue("recent/files", paths[:5])
        self._refresh_recent()

    def _action(self, name, callback, shortcut=None, *, doc=False, edit=False) -> QAction:
        action = QAction(name, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if edit:
            self._edit_actions.append(action)
        elif doc:
            self._doc_actions.append(action)
        return action

    def _build_actions(self) -> None:
        self.a_settings = self._action(tr("Aspetto e lingua…"), self.show_settings, "Ctrl+,")
        self.a_open = self._action(tr("Apri PDF…"), self.open_dialog, "Ctrl+O")
        self.a_close = self._action(tr("Chiudi scheda"), self.close_current, "Ctrl+W", doc=True)
        self.a_save = self._action(tr("Salva con nome…"), self.save_as, "Ctrl+Shift+S", edit=True)
        self.a_save_current = self._action(tr("Salva"), self.save, "Ctrl+S", edit=True)
        self.a_print = self._action(tr("Stampa…"), self.print_document, "Ctrl+P", doc=True)
        self.a_extract = self._action(tr("Estrai pagine…"), self.extract_pages, "Ctrl+E", edit=True)
        self.a_split = self._action(tr("Dividi PDF…"), self.split_pdf, edit=True)
        self.a_export_image = self._action(tr("Esporta pagina come immagine…"), self.export_image, edit=True)
        self.a_export_docx = self._action(tr("Esporta tutto il PDF in Word…"), self.export_docx, edit=True)
        self.a_exit = self._action(tr("Esci"), self.close, "Ctrl+Q")
        self.a_undo = self._action(tr("Annulla modifica"), self.undo, "Ctrl+Z", edit=True)
        self.a_redo = self._action(tr("Ripristina modifica"), self.redo, "Ctrl+Y", edit=True)
        self.a_rotate = self._action(tr("Ruota pagina +90°"), lambda: self._edit("rotate", self.current.page, 90), "Ctrl+R", edit=True)
        self.a_rotate_ccw = self._action(tr("Ruota pagina -90°"), lambda: self._edit("rotate", self.current.page, -90), edit=True)
        self.a_delete = self._action(tr("Elimina pagina…"), self.delete_page, "Ctrl+Shift+Delete", edit=True)
        self.a_up = self._action(tr("Sposta pagina su"), lambda: self._edit("move", self.current.page, -1), edit=True)
        self.a_down = self._action(tr("Sposta pagina giù"), lambda: self._edit("move", self.current.page, 1), edit=True)
        self.a_insert = self._action(tr("Inserisci altro PDF…"), self.insert_pdf, edit=True)
        self.a_blank = self._action(tr('Inserisci pagina bianca…'), self.insert_blank, edit=True)
        self.a_watermark = self._action(tr('Aggiungi filigrana…'), self.add_watermark, edit=True)
        self.a_draw = self._action(tr('Disegna a mano libera'), lambda: self.current.ink.toggle(), edit=True)
        self.a_draw.setCheckable(True)
        self.a_note = self._action(tr("Aggiungi nota…"), self.add_note, "Ctrl+M", edit=True)
        self.a_edit_text = self._action(tr("Modifica testo…"), lambda: self.current.inline_text.toggle(), edit=True)
        self.a_edit_text.setCheckable(True)
        self.a_ocr = self._action(tr("Riconosci testo (OCR)…"), self.recognize_text, edit=True)
        self.a_form = self._action(tr("Compila modulo"), self.fill_form, "Ctrl+Shift+F", doc=True)
        self.a_prepare_form = self._action(tr("Prepara modulo…"), self.prepare_form, doc=True)
        self.a_prev = self._action(tr("Pagina precedente"), lambda: self._navigate(-1), "PgUp", doc=True)
        self.a_next = self._action(tr("Pagina successiva"), lambda: self._navigate(+1), "PgDown", doc=True)
        self.a_zoom_in = self._action(tr("Ingrandisci"), lambda: self.current.zoom_in(), "Ctrl++", doc=True)
        self.a_zoom_out = self._action(tr("Riduci"), lambda: self.current.zoom_out(), "Ctrl+-", doc=True)
        self.a_fit_width = self._action(tr("Adatta larghezza"), lambda: self.current.fit_width(), doc=True)
        self.a_fit_page = self._action(tr("Adatta pagina"), lambda: self.current.fit_page(), doc=True)
        self.a_vector = self._action(tr("Preferisci resa vettoriale"), self.toggle_vector, doc=True)
        self.a_vector.setCheckable(True)
        self.a_vector.setChecked(True)
        self.a_find = self._action(tr("Cerca testo"), self.focus_search, "Ctrl+F", doc=True)
        self.a_find_next = self._action(tr("Risultato successivo"), lambda: self._search_step(1), "F3", doc=True)
        self.a_find_prev = self._action(tr("Risultato precedente"), lambda: self._search_step(-1), "Shift+F3", doc=True)
        self.a_copy = self._action(tr("Copia testo"), lambda: self.current.viewer.copy_selection(), doc=True)
        self.a_highlight = self._action(tr("Evidenzia"), lambda: self.markup("highlight"), edit=True)
        self.a_underline = self._action(tr("Sottolinea"), lambda: self.markup("underline"), edit=True)
        self.a_sidebar = self._action(tr("Pannello laterale"), self.toggle_sidebar, doc=True)
        self.a_info = self._action(tr("Proprietà documento…"), lambda: self.current.notice.show_details(), "Ctrl+I", doc=True)
        icon_names = {"open": "open", "save_current": "save", "save": "save", "print": "print", "undo": "undo", "redo": "redo",
                      "rotate": "rotate", "delete": "delete", "note": "note", "copy": "copy", "edit_text": "edit_text",
                      "highlight": "highlight", "underline": "underline", "insert": "insert", "extract": "extract", "split": "split",
                      "blank": "insert", "watermark": "watermark", "draw": "draw",
                      "prev": "prev", "next": "next", "zoom_out": "minus", "zoom_in": "plus",
                      "fit_width": "width", "fit_page": "page", "find": "search", "find_prev": "up", "find_next": "down", "sidebar": "sidebar", "info": "info"}
        for name, glyph in icon_names.items():
            action = getattr(self, "a_" + name)
            action.setProperty("glyph", glyph)
            action.setIcon(icon(glyph))
            hint = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            action.setToolTip(action.text().replace("…", "") + (f" ({hint})" if hint else ""))
        for action, text in ((self.a_rotate, tr("Ruota")), (self.a_delete, tr("Elimina")), (self.a_note, tr("Nota")),
                             (self.a_insert, tr("Inserisci PDF")), (self.a_extract, tr("Estrai")), (self.a_save, tr("Salva con nome"))):
            action.setIconText(text)

    def _build_menus(self) -> None:
        menu_file = self.menuBar().addMenu(tr("File"))
        for a in (self.a_open, self.a_close, self.a_save_current, self.a_save, self.a_extract, self.a_split, self.a_print, self.a_info, self.a_exit):
            menu_file.addAction(a)
        exports = QMenu(tr("Esporta"), menu_file)
        exports.addActions([self.a_export_image, self.a_export_docx])
        menu_file.insertMenu(self.a_info, exports)
        menu_edit = self.menuBar().addMenu(tr("Modifica"))
        for a in (self.a_undo, self.a_redo, self.a_rotate, self.a_rotate_ccw,
                  self.a_delete, self.a_up, self.a_down, self.a_insert, self.a_blank, self.a_watermark, self.a_draw, self.a_note,
                  self.a_copy, self.a_highlight, self.a_underline, self.a_edit_text, self.a_ocr, self.a_form,self.a_prepare_form):
            menu_edit.addAction(a)
        menu_view = self.menuBar().addMenu(tr("Visualizza"))
        for a in (self.a_prev, self.a_next, self.a_zoom_in, self.a_zoom_out,
                  self.a_fit_width, self.a_fit_page, self.a_vector, self.a_find, self.a_sidebar,
                  self.a_find_prev, self.a_find_next):
            menu_view.addAction(a)
        settings_menu = self.menuBar().addMenu(tr("Impostazioni"))
        settings_menu.addAction(self.a_settings)
        help_menu = self.menuBar().addMenu(tr("Aiuto"))
        self.a_updates = self._action(tr("Verifica aggiornamenti…"), self.show_updates)
        help_menu.addAction(self.a_updates)
        help_menu.addAction(self._action(tr("Informazioni"), self.about))
        help_menu.addAction(self._action(tr("Licenze e codice sorgente"), self.show_licenses))

    def _build_toolbar(self) -> None:
        toolbar = QToolBar(tr("Documento"), self)
        toolbar.setObjectName("WorkspaceBar")
        self.tools_toolbar = toolbar
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        toolbar.setIconSize(QSize(17, 17))
        self.addToolBar(toolbar)
        toolbar.addAction(self.a_sidebar)
        toolbar.addAction(self.a_open)
        toolbar.widgetForAction(self.a_open).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.a_open.setIconText(tr("Apri"))
        save = QToolButton()
        save.setObjectName("PrimaryTool")
        save.setDefaultAction(self.a_save_current)
        save.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        save.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        save_menu = QMenu(save)
        save_menu.addAction(self.a_save)
        save.setMenu(save_menu)
        toolbar.addWidget(save)
        toolbar.addAction(self.a_print)
        toolbar.addSeparator()
        toolbar.addAction(self.a_undo)
        toolbar.addAction(self.a_redo)
        toolbar.addAction(self.a_edit_text)
        self.a_edit_text.setIconText(tr("Modifica testo"))
        toolbar.widgetForAction(self.a_edit_text).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.addAction(self.a_draw)
        self.a_draw.setIconText(tr('Disegna'))
        toolbar.widgetForAction(self.a_draw).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.annotate_button = QToolButton()
        self.annotate_button.setText(tr("Annota"))
        self.annotate_button.setIcon(icon("highlight"))
        self.annotate_button.setProperty("glyph", "highlight")
        self.annotate_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.annotate_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        annotations = QMenu(self.annotate_button)
        annotations.addActions([self.a_copy, self.a_highlight, self.a_underline, self.a_note, self.a_edit_text, self.a_ocr, self.a_form])
        annotations.addAction(self.a_prepare_form)
        annotations.addActions([self.a_draw, self.a_watermark])
        self.annotate_button.setMenu(annotations)
        self.annotate_control = toolbar.addWidget(self.annotate_button)
        self.pages_button = QToolButton()
        self.pages_button.setText(tr("Pagine"))
        self.pages_button.setIcon(icon("page"))
        self.pages_button.setProperty("glyph", "page")
        self.pages_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.pages_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        pages = QMenu(self.pages_button)
        pages.addActions([self.a_rotate, self.a_rotate_ccw])
        pages.addSeparator()
        pages.addActions([self.a_up, self.a_down, self.a_delete])
        pages.addSeparator()
        pages.addActions([self.a_blank, self.a_insert, self.a_watermark, self.a_extract, self.a_split, self.a_export_image])
        self.pages_button.setMenu(pages)
        self.pages_control = toolbar.addWidget(self.pages_button)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self.document_state = QLabel()
        self.document_state.setObjectName("DocumentState")
        self.statusBar().addPermanentWidget(self.document_state)
        self.render_state = QLabel()
        self.render_state.setObjectName("DocumentState")
        self.render_state.setToolTip(tr("Resa della pagina corrente. Visualizza → Preferisci resa vettoriale per cambiare modalità."))
        self.statusBar().addPermanentWidget(self.render_state)

    def _build_navigation(self):
        footer = QToolBar(tr("Pagine"), self)
        footer.setObjectName("NavigationBar")
        footer.setMovable(False)
        footer.setFloatable(False)
        footer.setAllowedAreas(Qt.ToolBarArea.BottomToolBarArea)
        self.navigation_toolbar = footer
        self.addToolBar(Qt.ToolBarArea.BottomToolBarArea, footer)
        leading_space = QWidget()
        leading_space.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        footer.addWidget(leading_space)
        navigation = QWidget()
        navigation.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.navigation = navigation
        row = QHBoxLayout(navigation)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        def button(action):
            widget = QToolButton()
            widget.setDefaultAction(action)
            widget.setIconSize(QSize(17, 17))
            row.addWidget(widget)
        button(self.a_prev)
        self.page_spin = PageNumberEdit()
        self.page_spin.page_requested.connect(self._page_spin_changed)
        self.page_spin.invalid_input.connect(lambda message: self.statusBar().showMessage(message, 7000))
        row.addWidget(self.page_spin)
        self.page_total = QLabel(" / 0  ")
        row.addWidget(self.page_total)
        button(self.a_next)
        row.addSpacing(8)
        button(self.a_zoom_out)
        self.zoom_combo = QComboBox()
        self.zoom_combo.setEditable(True)
        self.zoom_combo.addItems(["50%", "75%", "100%", "125%", "150%", "200%", "300%"])
        self.zoom_combo.setFixedWidth(76)
        self.zoom_combo.setToolTip(tr("Zoom (25–500%)"))
        self.zoom_combo.activated.connect(lambda _: self._zoom_selected())
        self.zoom_combo.lineEdit().returnPressed.connect(self._zoom_selected)
        row.addWidget(self.zoom_combo)
        button(self.a_zoom_in)
        fit = QToolButton()
        fit.setIcon(icon("width"))
        fit.setProperty("glyph", "width")
        fit.setToolTip(tr("Adatta larghezza o pagina"))
        fit.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(fit)
        menu.addActions([self.a_fit_width, self.a_fit_page])
        fit.setMenu(menu)
        row.addWidget(fit)
        self.navigation_control = footer.addWidget(navigation)
        trailing_space = QWidget()
        trailing_space.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        footer.addWidget(trailing_space)
        self.tools_toolbar.addAction(self.a_find)
        self.tools_toolbar.addAction(self.a_info)

    def _zoom_selected(self):
        if not self.current:
            return
        try:
            percent = int(self.zoom_combo.currentText().strip().rstrip("%"))
            if not 25 <= percent <= 500:
                raise ValueError
        except ValueError:
            self.statusBar().showMessage(tr("Indica uno zoom tra 25% e 500%"), 4000)
            self._update_zoom()
            return
        self.current.set_zoom(percent)

    def _update_zoom(self):
        if self.current:
            mode = self.current.viewer.zoomMode()
            suffix = tr(" · larghezza") if mode.name == "FitToWidth" else tr(" · pagina") if mode.name == "FitInView" else ""
            with QSignalBlocker(self.zoom_combo):
                self.zoom_combo.setCurrentText(f"{round(self.current.effective_zoom() * 100)}%")
            self.zoom_combo.setToolTip("Zoom" + suffix + " (25–500%)")

    def _build_searchbar(self) -> None:
        toolbar = QToolBar(tr("Ricerca"), self)
        toolbar.setObjectName("SearchBar")
        self.search_toolbar = toolbar
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        toolbar.setIconSize(QSize(16, 16))
        self.addToolBar(toolbar)
        self.insertToolBarBreak(toolbar)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Cerca nel documento…"))
        self.search.setMinimumWidth(180)
        self.search.setMaximumWidth(320)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._search_changed)
        self.search.returnPressed.connect(lambda: self._search_step(1))
        toolbar.addWidget(self.search)
        self.search_status = QLabel(tr("Scrivi per cercare"))
        toolbar.addWidget(self.search_status)
        toolbar.addAction(self.a_find_prev)
        toolbar.addAction(self.a_find_next)
        close = QToolButton()
        close.setIcon(icon("close"))
        close.setProperty("glyph", "close")
        close.setToolTip(tr("Chiudi ricerca"))
        close.clicked.connect(self.close_search)
        toolbar.addWidget(close)
        shortcut = QShortcut(QKeySequence("Esc"), toolbar)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.close_search)
        toolbar.hide()

    @property
    def current(self) -> DocumentTab | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, DocumentTab) else None

    def show_settings(self):
        dialog = SettingsDialog(self.theme_name, self,
                                language_name=self.settings.value("ui/language", self.language_name))
        dialog.theme_applied.connect(self._apply_color_theme)
        dialog.language_applied.connect(self._apply_ui_language)
        dialog.exec()
        dialog.deleteLater()

    def _apply_ui_language(self, language):
        language = normalize_language(language)
        self.settings.setValue("ui/language", language)
        self.settings.sync()
        if language != self.language_name:
            self.statusBar().showMessage(tr("Lingua salvata. Riavvia il programma per applicarla."), 10000)

    def _apply_color_theme(self, name):
        self.theme_name = apply_theme(QApplication.instance(), name)
        self.settings.setValue("ui/theme", self.theme_name)
        self.settings.sync()
        # Custom drawings and vector icons share the same palette as Qt widgets.
        for window in QApplication.topLevelWidgets():
            if not isinstance(window, MainWindow):
                continue
            window.theme_name = self.theme_name
            window.tabs.refresh_icons()
            for action in window.findChildren(QAction):
                if glyph := action.property("glyph"):
                    action.setIcon(icon(glyph))
            for button in window.findChildren(QPushButton) + window.findChildren(QToolButton):
                if glyph := button.property("glyph"):
                    button.setIcon(icon(glyph))
            for widget in window.findChildren(QWidget):
                widget.update()

    def _restore_window(self) -> None:
        geometry = self.settings.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def _update_state(self, *_args) -> None:
        tab = self.current
        self.document_state.setVisible(tab is not None)
        self.render_state.setVisible(tab is not None)
        self._update_render_state()
        self.tools_toolbar.setVisible(True)
        self.search_toolbar.setVisible(tab is not None and tab._search_open)
        self.navigation_control.setVisible(tab is not None)
        self.navigation_toolbar.setVisible(tab is not None)
        self.annotate_control.setVisible(tab is not None)
        self.pages_control.setVisible(tab is not None)
        self.stack.setCurrentWidget(self.tabs if tab else self.welcome)
        for action in self._doc_actions:
            action.setEnabled(tab is not None)
        for action in self._edit_actions:
            action.setEnabled(tab is not None and not tab.editor.encrypted)
        self.a_form.setEnabled(tab is not None and bool(tab.form_fields))
        self.a_edit_text.setChecked(tab is not None and tab.inline_text.active)
        self.a_draw.setChecked(tab is not None and tab.ink.active)
        preparing = tab is not None and tab.form_designer.isVisible()
        self.a_prepare_form.setEnabled(tab is not None and not tab.editor.encrypted)
        if preparing:
            for action in self._edit_actions:
                action.setEnabled(False)
            self.a_form.setEnabled(False)
        self.a_export_docx.setEnabled(tab is not None and not tab.editor.encrypted and self._export_job is None and not preparing)
        self.a_ocr.setEnabled(tab is not None and not tab.editor.encrypted and self._ocr_job is None and not preparing)
        self.search.setEnabled(tab is not None)
        self.page_spin.setEnabled(tab is not None)
        self.zoom_combo.setEnabled(tab is not None)
        self.a_copy.setEnabled(tab is not None and tab.viewer.has_selection)
        for action in (self.a_highlight, self.a_underline):
            action.setEnabled(tab is not None and not tab.editor.encrypted and tab.viewer.has_selection and not preparing)
        if tab:
            if tab.search_model.searchString() != tab._query:
                self._search_timer.start(180)
            self.a_undo.setEnabled((tab.editor.can_undo or bool(tab.ink.history) or tab.ink.current is not None) and not preparing)
            self.a_redo.setEnabled((tab.editor.can_redo or bool(tab.ink.future)) and not preparing)
            with QSignalBlocker(self.search):
                self.search.setText(tab._query)
            self._update_page(tab.page, tab.count)
            self._update_search_status(tab._selected_search + 1, tab.search_model.count())
            self._update_zoom()
            self.document_state.setText(tr("Sola lettura") if tab.editor.encrypted else tr("Modifiche da salvare") if tab.dirty else tr("Salvato"))
            self.setWindowTitle(f"{tab.title} — APE PDF Studio")
            self.statusBar().setToolTip(str(tab.editor.output_path))
            self.statusBar().showMessage(tr("Ctrl/⌘ + rotella: zoom · trascina con la rotella premuta: sposta · tasto destro: annota"))
        else:
            self.setWindowTitle("APE PDF Studio")
            self.document_state.clear()
            self._update_page(0, 0)
            with QSignalBlocker(self.search):
                self.search.clear()
            self.search_status.setText(tr("Scrivi per cercare"))
            self.a_find_next.setEnabled(False)
            self.a_find_prev.setEnabled(False)
            self.statusBar().showMessage(tr("Pronto — trascina un PDF qui oppure premi Ctrl+O"))

    def _refresh_titles(self) -> None:
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            self.tabs.setTabText(i, tab.title)
            self.tabs.setTabToolTip(i, str(tab.editor.output_path))
        self._update_state()

    def _update_page(self, page: int, count: int) -> None:
        reset = self._page_context is not self.current
        self._page_context = self.current
        self.page_spin.sync_page(max(1, page + 1), count, reset=reset)
        self.page_total.setText(f" / {count}   ")
        tab = self.current
        self.a_prev.setEnabled(tab is not None and page > 0)
        self.a_next.setEnabled(tab is not None and page < count - 1)
        editable = tab is not None and not tab.editor.encrypted
        self.a_up.setEnabled(editable and page > 0)
        self.a_down.setEnabled(editable and page < count - 1)
        self.a_delete.setEnabled(editable and count > 1)
        self._update_render_state()

    def toggle_vector(self):
        if self.current:
            self.current.viewer.set_vector_enabled(self.a_vector.isChecked())
            self._update_render_state()

    def _update_render_state(self):
        tab = self.current
        if not tab:
            self.render_state.clear()
            return
        vectors = tab.viewer.vectors
        with QSignalBlocker(self.a_vector):
            self.a_vector.setChecked(vectors.enabled)
        if not vectors.enabled or tab.page in vectors.failed:
            text = tr("Compatibilità")
        elif tab.page in vectors.cache:
            text = tr("Vettoriale")
        else:
            text = tr("Preparazione vettoriale…")
        self.render_state.setText(text)

    def _update_search_status(self, index: int, total: int) -> None:
        query = self.current._query if self.current else ""
        self.search_status.setText(tr('  {0} di {1} risultati  ', index if total else 0, total) if total else tr("  Nessun risultato  ") if query else tr("  Scrivi per cercare  "))
        self.a_find_next.setEnabled(total > 0)
        self.a_find_prev.setEnabled(total > 0)

    def _page_spin_changed(self, value: int) -> None:
        if self.current:
            self.current.jump_to_page(value - 1)

    def _navigate(self, delta: int) -> None:
        if self.current:
            self.current.jump_to_page(self.current.page + delta)

    def _search_changed(self, query: str) -> None:
        if self.current:
            # Keep the query with its tab while debouncing the expensive search.
            self.current._query = query.strip()
            if query.strip():
                self.current._search_open = True
                self.search_toolbar.show()
            self._search_timer.start(180)

    def _apply_search(self) -> None:
        if self.current:
            self.current.set_search(self.current._query)

    def _search_step(self, delta: int) -> None:
        if self.current:
            if self._search_timer.isActive():
                self._search_timer.stop()
                self._apply_search()
            self.current.next_result(delta)

    def focus_search(self) -> None:
        if self.current:
            self.current._search_open = True
            self.search_toolbar.show()
            self.search.setFocus()
            self.search.selectAll()

    def close_search(self):
        if self.current:
            self.search.clear()
            self.current._search_open = False
            self.current.sidebar.setCurrentWidget(self.current.thumbs)
            self.current.viewer.setFocus()
        self.search_toolbar.hide()

    def toggle_sidebar(self):
        if self.current:
            self.current.sidebar.setVisible(self.current.sidebar.isHidden())

    def thumbnail_menu(self, tab, position):
        if self.current is not tab:
            return
        item = tab.thumbs.itemAt(position)
        if item is None:
            return
        tab.jump_to_page(tab.thumbs.row(item))
        menu = QMenu(self)
        menu.addActions([self.a_rotate, self.a_rotate_ccw])
        menu.addSeparator()
        menu.addActions([self.a_up, self.a_down, self.a_delete])
        menu.addSeparator()
        menu.addAction(self.a_blank)
        menu.addAction(self.a_extract)
        menu.addAction(self.a_export_image)
        menu.exec(tab.thumbs.viewport().mapToGlobal(position))
        menu.deleteLater()

    def markup(self, kind):
        tab = self.current
        if tab and tab.viewer.has_selection:
            self._edit("add_text_markup", tab.viewer.selection_page, tab.viewer.selection_rectangles(), kind)

    def open_dialog(self) -> None:
        start = str(self.settings.value("recent/directory", str(Path.home())))
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Apri documenti PDF"), start, tr("Documenti PDF (*.pdf)"))
        for path in paths:
            self.open_pdf(Path(path))

    def open_pdf(self, filename: Path) -> None:
        try:
            path = user_path(filename)
            for i in range(self.tabs.count()):
                candidate = self.tabs.widget(i)
                if same_file(candidate.editor.source, path) or same_file(candidate.editor.output_path, path):
                    self.tabs.setCurrentIndex(i)
                    return
            password = None
            while True:
                try:
                    tab = DocumentTab(path, self, password=password)
                    break
                except PDFPasswordRequired:
                    password, ok = QInputDialog.getText(
                        self, tr("Documento protetto"),
                        tr("Inserisci la password del documento PDF:"),
                        QLineEdit.EchoMode.Password,
                    )
                    if not ok:
                        return
        except Exception as exc:
            QMessageBox.critical(self, tr("Apertura PDF"), str(exc))
            return
        tab.thumbs.customContextMenuRequested.connect(lambda pos, owner=tab: self.thumbnail_menu(owner, pos))
        tab.changed.connect(self._refresh_titles)
        tab.selection_changed.connect(lambda _, owner=tab: self._update_state() if self.current is owner else None)
        tab.markup_requested.connect(lambda kind, owner=tab: self.markup(kind) if self.current is owner else None)
        tab.viewer.note_requested.connect(lambda page, point, owner=tab: self.add_note_at(owner, page, point))
        tab.viewer.note_open_requested.connect(lambda page, index, owner=tab: self.open_note(owner, page, index))
        tab.viewer.text_edit_requested.connect(lambda page, point, owner=tab: self.edit_text_at(owner, page, point))
        tab.viewer.link_requested.connect(lambda url, owner=tab: self.open_pdf_link(owner, url))
        tab.notice.form_requested.connect(lambda owner=tab: self.fill_form(owner))
        tab.notice.links_requested.connect(lambda owner=tab: self.toggle_pdf_links(owner))
        tab.message.connect(lambda text, owner=tab: self.statusBar().showMessage(text, 5000) if self.current is owner else None)
        tab.viewer.zoomFactorChanged.connect(lambda _, owner=tab: self._update_zoom() if self.current is owner else None)
        tab.viewer.vectors.changed.connect(lambda owner=tab: self._update_render_state() if self.current is owner else None)
        tab.page_changed.connect(lambda p, n, owner=tab: self._update_page(p, n) if self.current is owner else None)
        tab.search_changed.connect(
            lambda p, n, owner=tab: self._update_search_status(p, n) if self.current is owner else None
        )
        index = self.tabs.addTab(tab, icon("page"), tab.title)
        self.tabs.setTabToolTip(index, str(tab.editor.output_path))
        self.tabs.setCurrentIndex(index)
        self.settings.setValue("recent/directory", str(path.parent))
        self._remember_file(path)
        self._update_state()

    def _confirm_close(self, tab: DocumentTab) -> bool:
        if not tab.form_designer.confirm_discard():
            return False
        if not tab.finish_form():
            return False
        if not tab.editor.dirty:
            return True
        result = QMessageBox.question(
            self, tr("Modifiche non salvate"),
            tr("Salvare le modifiche a '{0}' prima di chiudere?", tab.editor.output_path.name),
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if result == QMessageBox.StandardButton.Save:
            return self._save_tab(tab)
        return result == QMessageBox.StandardButton.Discard

    def close_tab(self, index: int) -> None:
        tab = self.tabs.widget(index)
        if tab is not None and tab is self._ocr_tab:
            self.statusBar().showMessage(tr("Attendi l'OCR oppure premi Annulla nella finestra di avanzamento."))
            return
        if tab is None or not self._confirm_close(tab):
            return
        tab.close_document()
        self.tabs.removeTab(index)
        tab.deleteLater()
        self._update_state()

    def close_current(self) -> None:
        if self.tabs.currentIndex() >= 0:
            self.close_tab(self.tabs.currentIndex())

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._ocr_job is not None:
            event.ignore()
            self.statusBar().showMessage(tr("Attendi l'OCR oppure premi Annulla nella finestra di avanzamento."))
            return
        if self._export_job is not None:
            event.ignore()
            self.statusBar().showMessage(tr("Attendi la conversione Word oppure premi Annulla nella finestra di avanzamento."))
            return
        for index in range(self.tabs.count()):
            tab = self.tabs.widget(index)
            if not self._confirm_close(tab):
                event.ignore()
                return
        if self.close_guard is not None and not self.close_guard():
            event.ignore()
            return
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.sync()
        self._startup_timer.stop()
        self._startup_pending = False
        self.update_checker.cancel()
        self.update_toast.dismiss()
        for index in reversed(range(self.tabs.count())):
            tab = self.tabs.widget(index)
            tab.close_document()
            self.tabs.removeTab(index)
        super().closeEvent(event)

    def _run(self, callable_, success=tr("Operazione completata"), *, output=None, notification=tr("Operazione completata")) -> bool:
        try:
            callable_()
        except PDFOperationError as exc:
            QMessageBox.warning(self, tr("Operazione PDF"), str(exc))
            return False
        except Exception as exc:
            QMessageBox.critical(self, tr("Errore imprevisto"), f"{type(exc).__name__}: {exc}")
            return False
        self._refresh_titles()
        self.statusBar().showMessage(success, 7000)
        if output is not None:
            self.toast.show_completion(notification, output)
        return True

    def _edit(self, method: str, *args) -> None:
        tab = self.current
        if tab is None:
            return
        self._run(lambda: tab.apply_edit(method, *args), tr("Modifica applicata · Usa Ctrl+Z per annullare"))

    def _confirm_pdf_content(self, title, text):
        dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(text)
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        dialog.setEscapeButton(QMessageBox.StandardButton.No)
        result = dialog.exec()
        dialog.deleteLater()
        return result == QMessageBox.StandardButton.Yes

    def toggle_pdf_links(self, tab):
        if self.current is not tab:
            return False
        if tab.links_enabled:
            tab.links_enabled = False
        elif self._confirm_pdf_content(tr("Abilita collegamenti PDF"), tr(
                "Abilitare i collegamenti web e email per questo documento?\n\n"
                "L'abilitazione termina alla chiusura della scheda. Ogni apertura richiede la conferma dell'indirizzo. "
                "Il browser o il programma email possono comunicare con servizi esterni. "
                "Il PDF non può avviare programmi, aprire file locali o inviare automaticamente i dati del modulo.")):
            if self.current is not tab:
                return False
            tab.links_enabled = True
        tab.notice.links_enabled = tab.links_enabled
        tab.notice.refresh()
        return tab.links_enabled

    def open_pdf_link(self, tab, value):
        if self.current is not tab or not tab.finish_form():
            return
        url = allowed_url(value)
        if url is None or not any(entry["url"] == url for entries in tab.links.values() for entry in entries):
            return
        revision = tab.editor.path
        if not tab.links_enabled and not self.toggle_pdf_links(tab):
            return
        target = QUrl(url, QUrl.ParsingMode.StrictMode)
        if not target.isValid() or target.scheme() not in ("http", "https", "mailto"):
            return
        # Show the exact ASCII URL passed to the operating system, including
        # punycode hosts, path and query. PDF text never becomes rich text here.
        address = bytes(target.toEncoded()).decode("ascii")
        confirmed = self._confirm_pdf_content(tr("Apri collegamento PDF"),
            tr("Aprire questo indirizzo con il browser o il programma email?\n\n{0}\n\nDocumento: {1}",
               address, tab.editor.output_path.name))
        if confirmed and self.current is tab and tab.links_enabled and tab.editor.path == revision:
            if not QDesktopServices.openUrl(target):
                QMessageBox.warning(self, tr("Collegamento PDF"), tr("Impossibile aprire il collegamento con l'applicazione predefinita."))

    def fill_form(self, tab=None, name=None):
        # QAction.triggered supplies a boolean rather than a document.
        tab = self.current if tab is None or isinstance(tab, bool) else tab
        if tab is None or self.current is not tab or not tab.form_fields:
            return
        if not tab.inline_text.deactivate():
            return
        tab.inline_form.start(name)

    def prepare_form(self):
        if self.current and not self.current.editor.encrypted:
            self.current.form_designer.open()

    def undo(self) -> None:
        if self.current:
            self._run(self.current.undo, tr("Modifica annullata"))

    def redo(self) -> None:
        if self.current:
            self._run(self.current.redo, tr("Modifica ripristinata"))

    def delete_page(self) -> None:
        tab = self.current
        if not tab:
            return
        confirmed = QMessageBox.question(
            self, tr("Elimina pagina"),
            tr('Eliminare la pagina {0} di {1}?\nPotrai annullare con Ctrl+Z.', tab.page + 1, tab.count),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed == QMessageBox.StandardButton.Yes:
            self._edit("delete", tab.page)

    def _document_tool(self, method, args):
        tab = self.current
        if not tab or not tab.finish_form():
            return
        page = args[0] if method == 'insert_blank' else tab.page
        progress = EditProgress(tab.editor, method, args, self)
        try:
            progress.execute()
        finally:
            progress.deleteLater()
        tab._reload(page)
        tab.changed.emit()

    def insert_blank(self):
        tab = self.current
        if not tab:
            return
        dialog = BlankPageDialog(tab.page, tab.count, self)
        if dialog.exec():
            self._run(lambda: self._document_tool('insert_blank', dialog.arguments()))
        dialog.deleteLater()

    def add_watermark(self):
        if not self.current:
            return
        dialog = WatermarkDialog(self)
        if dialog.exec():
            self._run(lambda: self._document_tool('add_watermark', dialog.arguments()))
        dialog.deleteLater()

    def insert_pdf(self) -> None:
        tab = self.current
        if not tab:
            return
        path, _ = QFileDialog.getOpenFileName(self, tr("Inserisci PDF dopo la pagina corrente"), "", tr("Documenti PDF (*.pdf)"))
        if path:
            self._edit("insert_pdf", path, tab.page)

    def add_note(self) -> None:
        tab = self.current
        if tab and tab.ink.active and not tab.ink.deactivate():
            return
        if not tab:
            return
        tab.viewer.begin_placement("note")

    @staticmethod
    def _position_note_dialog(dialog, tab, page, point):
        dialog.adjustSize()
        position = tab.viewer.page_transform(page).map(point).toPoint()
        position = tab.viewer.viewport().mapToGlobal(position)
        screen = tab.viewer.screen().availableGeometry()
        dialog.move(max(screen.left(), min(position.x() + 24, screen.right() - dialog.width())),
                    max(screen.top(), min(position.y(), screen.bottom() - dialog.height())))

    def add_note_at(self, tab, page, point):
        if tab.editor.encrypted:
            return
        dialog = NoteDialog(save=lambda text: tab.apply_edit("add_note", page, text, (point.x(), point.y())), parent=self)
        self._position_note_dialog(dialog, tab, page, point)
        dialog.exec()
        dialog.deleteLater()

    def open_note(self, tab, page, index):
        entry = next((n for n in tab.viewer.notes.get(page, []) if n["index"] == index), None)
        if entry is None:
            return
        editable = not tab.editor.encrypted
        dialog = NoteDialog(entry["text"],
                            save=(lambda text: tab.apply_edit("update_note", page, index, text)) if editable else None,
                            delete=(lambda: tab.apply_edit("update_note", page, index, None)) if editable else None, parent=self)
        from PySide6.QtCore import QPointF
        self._position_note_dialog(dialog, tab, page, QPointF(entry["rect"][2], entry["rect"][1]))
        dialog.exec()
        dialog.deleteLater()

    def edit_text_at(self, tab, page, point):
        if tab.editor.encrypted:
            return
        tab.inline_text.start(page, point)

    @staticmethod
    def _choose_output_name(source: Path, suffix: str) -> str:
        return str(source.with_name(source.stem + suffix + ".pdf"))

    def _confirm_overwrite(self, path: Path) -> bool:
        if not path.exists():
            return True
        answer = QMessageBox.warning(
            self, tr("Sovrascrittura file"),
            tr('Il file esiste già:\n{0}\n\nSostituirlo definitivamente?', path),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _output_available(self, path: Path, tab: DocumentTab) -> bool:
        for index in range(self.tabs.count()):
            other = self.tabs.widget(index)
            if other is not tab and any(same_file(path, target) for target in (other.editor.source, other.editor.output_path)):
                QMessageBox.warning(self, tr("Documento aperto"), tr("Il file è aperto in un'altra scheda. Scegli un altro nome."))
                return False
        return True

    @staticmethod
    def _pdf_output(destination: str) -> Path:
        path = Path(destination).expanduser()
        if not path.suffix:
            path = path.with_suffix(".pdf")
        return user_path(path)

    def save(self) -> None:
        if self.current:
            self._save_tab(self.current)

    def _save_tab(self, tab: DocumentTab) -> bool:
        if tab.editor.encrypted or not tab.finish_form():
            return False
        if tab.editor.output_path == tab.editor.source:
            return self._save_as_tab(tab)
        path = tab.editor.output_path
        if not self._output_available(path, tab):
            return False
        return self._run(lambda: tab.editor.save_as(path), tr('PDF salvato: {0}', path.name),
                         output=path, notification=tr("PDF salvato"))

    def print_document(self) -> None:
        if self.current and self.current.finish_form():
            def show_print():
                dialog = PrintDialog(self.current, self)
                try:
                    dialog.exec()
                finally:
                    dialog.done(dialog.result())
                    dialog.deleteLater()
            self._run(show_print, tr("Pronto"))

    def save_as(self) -> None:
        if self.current:
            self._save_as_tab(self.current)

    def _save_as_tab(self, tab: DocumentTab) -> bool:
        if not tab.finish_form():
            return False
        proposed = self._choose_output_name(tab.editor.output_path, "_edited")
        destination, _ = QFileDialog.getSaveFileName(self, tr("Salva PDF con nome"), proposed, tr("Documenti PDF (*.pdf)"), options=QFileDialog.Option.DontConfirmOverwrite)
        if not destination:
            return False
        path = self._pdf_output(destination)
        if not self._output_available(path, tab) or not self._confirm_overwrite(path):
            return False
        success = self._run(lambda: tab.editor.save_as(path), tr('PDF salvato in {0}', path),
                            output=path, notification=tr("PDF salvato"))
        if success:
            self._remember_file(path)
        return success

    def extract_pages(self) -> None:
        tab = self.current
        if not tab or not tab.finish_form():
            return
        spec, ok = QInputDialog.getText(
            self, tr("Estrai pagine"), tr('Pagine disponibili: 1–{0}. Inserisci, ad esempio, 1,3-5:', tab.count),
            text=str(tab.page + 1)
        )
        if not ok:
            return
        try:
            pages = parse_page_range(spec, tab.count)
        except PDFOperationError as exc:
            QMessageBox.warning(self, tr("Pagine non valide"), str(exc))
            return
        proposed = self._choose_output_name(tab.editor.output_path, "_pages")
        destination, _ = QFileDialog.getSaveFileName(self, tr("Salva pagine estratte"), proposed, tr("Documenti PDF (*.pdf)"), options=QFileDialog.Option.DontConfirmOverwrite)
        if destination:
            path = self._pdf_output(destination)
            if self._output_available(path, tab) and self._confirm_overwrite(path):
                self._run(lambda: tab.editor.extract(pages, path), tr('Pagine estratte in {0}', path),
                          output=path, notification=tr("Pagine PDF esportate"))

    def split_pdf(self) -> None:
        tab = self.current
        if not tab or tab.editor.encrypted or not tab.finish_form():
            return
        if tab.count < 2:
            QMessageBox.information(self, tr("Dividi PDF"), tr("Servono almeno due pagine per dividere il PDF."))
            return
        dialog = SplitDialog(tab.editor, tab.count, self)
        try:
            if dialog.exec() and dialog.outputs:
                folder = dialog.outputs[0].parent
                self.statusBar().showMessage(tr("Creati {0} PDF in {1}", len(dialog.outputs), folder), 7000)
                self.toast.show_completion(tr("PDF diviso"), folder,
                                           tr("{0} file PDF creati", len(dialog.outputs)))
        finally:
            dialog.deleteLater()

    def export_image(self) -> None:
        tab = self.current
        if tab and not tab.finish_form():
            return
        if not tab or tab.editor.encrypted:
            return
        page = tab.page
        resolution, accepted = QInputDialog.getItem(
            self, tr("Esporta pagina come immagine"), tr('Pagina {0} · Risoluzione:', page + 1),
            [tr("150 dpi — schermo"), tr("300 dpi — stampa"), tr("600 dpi — alta qualità"), tr("72 dpi — file leggero")], 0, False)
        if not accepted:
            return
        dpi = int(resolution.split()[0])
        proposed = str(tab.editor.output_path.with_name(f"{tab.editor.output_path.stem}_pagina_{page + 1}.png"))
        destination, selected_filter = QFileDialog.getSaveFileName(
            self, tr("Salva pagina come immagine"), proposed, tr("Immagine PNG (*.png);;Immagine JPEG (*.jpg *.jpeg)"),
            options=QFileDialog.Option.DontConfirmOverwrite)
        if not destination:
            return
        path = Path(destination).expanduser()
        if not path.suffix:
            path = path.with_suffix(".jpg" if "JPEG" in selected_filter else ".png")
        path = user_path(path)
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            QMessageBox.warning(self, tr("Formato immagine"), tr("Usa l'estensione .png, .jpg oppure .jpeg."))
            return
        if self._output_available(path, tab) and self._confirm_overwrite(path):
            self._run(lambda: export_page_image(tab.pdf, page, path, dpi), tr('Pagina {0} esportata in {1}', page + 1, path),
                      output=path, notification=tr("Immagine esportata"))

    def export_docx(self) -> None:
        tab = self.current
        if tab and not tab.finish_form():
            return
        if not tab or tab.editor.encrypted or self._export_job is not None:
            return
        proposed = str(tab.editor.output_path.with_suffix(".docx"))
        destination, _ = QFileDialog.getSaveFileName(
            self, tr("Esporta tutto il PDF in Word"), proposed, tr("Documento Word (*.docx)"),
            options=QFileDialog.Option.DontConfirmOverwrite)
        if not destination:
            return
        path = Path(destination).expanduser()
        if not path.suffix:
            path = path.with_suffix(".docx")
        path = user_path(path)
        if path.suffix.lower() != ".docx":
            QMessageBox.warning(self, tr("Formato Word"), tr("Usa l'estensione .docx."))
            return
        if not self._output_available(path, tab) or not self._confirm_overwrite(path):
            return
        try:
            job = DocxExportJob(tab.editor.path, path, self, zone=tab.editor._zone)
        except OSError as exc:
            QMessageBox.warning(self, tr("Esportazione Word"), tr('Impossibile preparare il PDF: {0}', exc))
            return
        self._export_job = job
        progress = QProgressDialog(
            tr("Conversione locale. Il testo sarà modificabile; le scansioni saranno incluse come immagini.\n"
            "L'impaginazione può differire dal PDF."), tr("Annulla"), 0, tab.count + 1, self)
        progress.setWindowTitle(tr("Esportazione Word"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumWidth(460)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        progress.canceled.connect(job.cancel)
        job.progress.connect(lambda done, total, label: (
            progress.setMaximum(total + 1), progress.setValue(done),
            progress.setLabelText(label + tr("\nTesto modificabile · scansioni come immagini\nImpaginazione da controllare"))))
        job.succeeded.connect(self._docx_exported)
        job.failed.connect(lambda message: QMessageBox.warning(self, tr("Esportazione Word"), message))
        job.cancelled.connect(lambda: self.statusBar().showMessage(tr("Esportazione Word annullata"), 7000))
        def finish():
            progress.close()
            progress.deleteLater()
            self._export_job = None
            self._update_state()
            job.deleteLater()
        job.finished.connect(finish)
        self._update_state()
        job.start()

    def _docx_exported(self, path, result):
        count = len(result.get("image_pages", []))
        detail = (tr("{0} pagine esportate · Controlla l'impaginazione in Word.", result['pages'])
                  + (tr(' {0} pagine senza testo incluse come immagini; OCR non eseguito.', count) if count else ""))
        self.toast.show_completion(tr("Documento Word esportato"), path, detail)

    def recognize_text(self):
        tab = self.current
        if tab and not tab.finish_form():
            return
        if not tab or tab.editor.encrypted or self._ocr_job is not None:
            return
        dialog = OcrDialog(tab.page, tab.count, self)
        try:
            if not dialog.exec():
                return
            job = OcrJob(tab.editor.path, language=dialog.language.currentData(), pages=dialog.pages,
                         dpi=dialog.dpi.currentData(), download=dialog.download.isChecked(), parent=self)
        except (OSError, PDFOperationError) as exc:
            QMessageBox.warning(self, tr("Riconoscimento OCR"), str(exc))
            return
        finally:
            dialog.deleteLater()
        self._ocr_job, self._ocr_tab = job, tab
        progress = QProgressDialog(tr("Preparazione OCR locale…"), tr("Annulla"), 0, tab.count + 1, self)
        progress.setWindowTitle(tr("Riconoscimento OCR"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumWidth(470)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        progress.canceled.connect(job.cancel)
        job.progress.connect(lambda done, total, label: (
            progress.setMaximum(total + 1), progress.setValue(done), progress.setLabelText(label)))
        job.succeeded.connect(lambda path, result: self._ocr_completed(tab, job.revision, path, result))
        job.failed.connect(lambda message: QMessageBox.warning(self, tr("Riconoscimento OCR"), message))
        job.cancelled.connect(lambda: self.statusBar().showMessage(tr("OCR annullato. Il documento non è stato modificato."), 7000))

        def finish():
            progress.close()
            progress.deleteLater()
            self._ocr_job = self._ocr_tab = None
            self._update_state()
            job.deleteLater()

        job.finished.connect(finish)
        self._update_state()
        job.start()

    def _ocr_completed(self, tab, revision, path, result):
        recognized = len(result.get("recognized", []))
        if recognized:
            if not self._run(lambda: tab.apply_edit("apply_ocr", path, revision), tr("OCR completato · Modifiche da salvare")):
                return
        detail = (tr('{0} pagine riconosciute; {1} già con testo; {2} senza testo riconosciuto.', recognized, len(result.get('skipped', [])), len(result.get('empty', []))))
        if result.get("reduced_dpi"):
            detail += tr(" Risoluzione ridotta per le pagine di grandi dimensioni.")
        if recognized:
            detail += tr(" Controlla il testo. Usa Salva per conservarlo oppure Ctrl+Z per annullare.")
        QMessageBox.information(self, tr("OCR completato"), detail)

    def schedule_update_check(self) -> None:
        """Called once by the entrypoint after showing the application window."""
        if not self._startup_requested:
            self._startup_requested = True
            self._startup_timer.start(800)

    def _check_startup_update(self):
        self._startup_pending = True
        if self.update_checker.checked:
            if self.update_checker.error:
                self._startup_update_failed(self.update_checker.error)
            else:
                self._startup_update_finished(self.update_checker.release)
        else:
            self.update_checker.check()

    def _startup_update_finished(self, release):
        if self._startup_pending:
            self._startup_pending = False
            self.update_toast.show_update(release)

    def _startup_update_failed(self, _message):
        self._startup_pending = False

    def show_updates(self) -> None:
        dialog = UpdatesDialog(self, checker=self.update_checker)
        dialog.check()
        dialog.exec()
        dialog.deleteLater()

    def about(self) -> None:
        QMessageBox.information(
            self, tr("Informazioni su APE PDF Studio"),
            tr("APE PDF Studio {0}\n"
            + "\n" + tr("Autore: {0}", "GiancaGIS")
            + "\n" + "Copyright (C) 2026 GiancaGIS\n"
            + "\n\n" + "Rendering: tracciati MuPDF / Qt SVG; compatibilità QtPdf (PDFium)\nEditing: pypdf\nInterfaccia: PySide6\n\n"
            "Operazioni non distruttive, cronologia Undo/Redo e salvataggio esplicito.\n"
            "Le firme digitali non vengono preservate dalle modifiche ai PDF.", __version__)
            + "\n\n" + tr("Licenza: GNU AGPLv3. Questo programma è distribuito senza garanzie.")
            + "\n" + "Qt/PySide6: LGPLv3 / GPL · PyMuPDF/MuPDF: AGPLv3"
            + "\n" + tr("Testi delle licenze e istruzioni per i sorgenti: Aiuto → Licenze e codice sorgente.")
        )

    def show_licenses(self) -> None:
        LicensesDialog(self).exec()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and any(
            url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf")
            for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf"):
                self.open_pdf(Path(url.toLocalFile()))
        event.acceptProposedAction()
