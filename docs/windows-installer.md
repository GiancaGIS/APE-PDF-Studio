# Installer Windows con Inno Setup

La procedura crea un pacchetto autonomo con Python, Qt e le dipendenze dell'applicazione, poi lo comprime in un installer Inno Setup. Il PC di destinazione non deve avere Python installato. Il target e Windows x64, Windows 10 1809 o successivo; Windows su ARM puo eseguire il pacchetto tramite compatibilita x64, ma richiede un collaudo sul dispositivo.

## Creare l'installer

Prerequisiti sul PC di build:

- Python x64 nella versione indicata da `requirements-release.txt` (release attuale: 3.14.5).
- Visual Studio o Build Tools con **Desktop development with C++**, compilatore x64 e Windows SDK. Il compilatore viene individuato con `vswhere`; il runtime C++ è collegato staticamente.
- [Inno Setup 6.3 o successivo](https://jrsoftware.org/isdl.php), con Inno Setup Preprocessor e traduzione italiana (inclusi nell'installazione standard).
- Connessione Internet per la prima installazione delle dipendenze Python e la raccolta dei loro sorgenti upstream.

Dalla cartella del progetto:

```powershell
.\scripts\build_installer.bat
```

Oppure:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_installer.ps1
```

Lo script crea un ambiente dedicato `.venv-build`, installa le dipendenze bloccate in `requirements-release.txt` e il progetto, raccoglie licenze e sorgenti upstream, compila la shell extension e prepara gli eseguibili. Verifica i componenti COM, i processi PDF/vettoriale/Word/OCR, la CLI e l'avvio GUI, quindi compila Inno Setup e genera i checksum della release. Legge la versione da `pyproject.toml` e verifica che coincida con `__version__`. La versione viene inserita anche nelle proprieta degli eseguibili. Il compiler viene cercato nel PATH e nelle cartelle standard di Inno Setup 6; per altre posizioni usa `-InnoCompiler`.

Gli artefatti sono:

- `dist\APEPDFStudio\`: pacchetto completo, con eseguibili, `APEPDFShell.dll`, `_internal\`, `licenses\` e documenti di licenza/sorgenti. Distribuisci l'intera cartella.
- `dist\installer\APEPDFStudio-<versione>-Setup-x64.exe`: installer distribuibile.
- `build\windows\smoke-report.json`: risultati della verifica degli eseguibili.
- `dist\release\`: sorgenti dell'applicazione e delle dipendenze, checksum SHA-256 e testo della release.

Per riutilizzare un ambiente gia preparato:

```powershell
.\scripts\build_installer.ps1 -PythonExe .\.venv\Scripts\python.exe -SkipDependencyInstall
```

Questo ambiente deve contenere le versioni di `requirements-release.txt`. Per ottenere solo il pacchetto, aggiungi `-BundleOnly`. Per ricompilare solo l'installer da un pacchetto esistente, usa `-SkipBuild -SkipDependencyInstall -PythonExe .\.venv\Scripts\python.exe`. `-SkipSmokeTest` omette la verifica degli eseguibili; per una build di rilascio lascia attiva la verifica. `-OfflineSources` riutilizza la cache verificata dei sorgenti; `-DistRoot` permette di scrivere gli artefatti in una cartella diversa.

Esempio con compilatore e licenza personalizzati:

```powershell
.\scripts\build_installer.ps1 `
  -InnoCompiler "D:\Tools\Inno Setup 6\ISCC.exe" `
  -LicenseFile ".\docs\licenza.rtf"
```

## Scelte durante l'installazione

All'avvio il setup chiede se installare:

- **Solo per me**: modalita predefinita, senza richiesta di elevazione. Cartella proposta: `%LOCALAPPDATA%\Programs\APE PDF Studio`.
- **Per tutti gli utenti**: richiede elevazione UAC e propone `%ProgramFiles%\APE PDF Studio`.

La pagina della cartella e sempre visibile e permette di scegliere un percorso diverso. In modalita locale scegli una cartella scrivibile dal tuo account. Negli aggiornamenti viene riproposta la cartella precedente della stessa modalita di installazione. La scelta della modalita compare anche negli aggiornamenti.

La procedura mostra la licenza e richiede l'accettazione prima di proseguire. Crea un collegamento nel menu Start e offre, come opzione, quello sul desktop. Aggiunge il disinstallatore all'elenco delle app di Windows.

L'opzione **Associa i file PDF ad APE PDF Studio (conferma nelle impostazioni Windows)** registra il programma tra le applicazioni **Apri con** e **App predefinite**. È facoltativa e inizialmente deselezionata. Le registrazioni seguono la modalità scelta: `HKCU` per **Solo per me**, `HKLM` per **Per tutti gli utenti**. Il comando di apertura racchiude tra virgolette sia l'eseguibile sia il percorso del PDF, supportando le cartelle con spazi.

Se scegli l'associazione, nella pagina finale puoi aprire le impostazioni Windows e confermare APE PDF Studio come app predefinita per `.pdf`. Windows 10 mostra la pagina generale delle app predefinite; Windows 11 aggiornato mostra direttamente la pagina dell'app. La scelta predefinita precedente rimane attiva finché non la cambi nelle impostazioni. L'installer non modifica `UserChoice`; la disinstallazione rimuove soltanto le registrazioni di APE, senza eliminare quelle degli altri lettori PDF. La pagina Windows viene aperta come utente originale anche nelle installazioni con elevazione.

I collegamenti alle impostazioni usano lo schema documentato da [Microsoft](https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-default-apps-settings). La notifica di aggiornamento delle associazioni a Esplora file è gestita da [ChangesAssociations di Inno Setup](https://jrsoftware.org/ishelp/topic_setup_changesassociations.htm).

### CLI e PATH

La CLI è inclusa come **`ape-pdf-cli.exe`** e funziona senza installare Python. L'opzione **Aggiungi APE PDF Studio al PATH** è facoltativa e inizialmente deselezionata. Aggiunge la cartella scelta al PATH dell'utente (`HKCU\Environment`) in modalità locale, oppure al PATH di sistema (`HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment`) per tutti gli utenti. Dopo l'installazione apri un nuovo terminale:

```powershell
ape-pdf-cli --help
ape-pdf-cli "C:\Documenti\input.pdf" info
ape-pdf-cli "C:\Documenti\input.pdf" rotate 1 -o "C:\Documenti\ruotato.pdf"
```

Senza modificare il PATH, puoi usare il percorso completo dell'eseguibile. Per l'installazione locale nella cartella predefinita:

```powershell
& "$env:LOCALAPPDATA\Programs\APE PDF Studio\ape-pdf-cli.exe" "C:\Documenti\input.pdf" info
```

Il setup conserva le altre voci e i riferimenti a variabili d'ambiente, evita duplicati e registra soltanto la voce aggiunta da APE. La disinstallazione rimuove quella voce conservando le modifiche successive al PATH; una voce già presente prima dell'installazione rimane. Negli aggiornamenti, deselezionare l'opzione rimuove la voce precedentemente aggiunta dal setup. La notifica di aggiornamento dell'ambiente è gestita da [ChangesEnvironment di Inno Setup](https://jrsoftware.org/ishelp/topic_setup_changesenvironment.htm).

### Miniature e riquadro Anteprima di Esplora file

Sono disponibili due opzioni indipendenti, inizialmente deselezionate, per Windows x64:

- **Mostra miniature dei PDF in Esplora file**: genera la miniatura della prima pagina.
- **Abilita il riquadro Anteprima dei PDF in Esplora file**: visualizza il documento e permette di sfogliare le pagine con pulsanti, frecce, Page Up/Page Down, Home/End o rotella. La pagina si adatta alle dimensioni del riquadro.

Funzionano anche se il lettore PDF predefinito è un'altra applicazione. Selezionandole, il setup sostituisce il provider corrente nell'ambito scelto (`HKCU` o `HKLM`) e salva le precedenti registrazioni. Gli aggiornamenti conservano il backup originale quando APE è ancora il provider attivo. La disinstallazione ripristina i provider precedenti soltanto se lo slot appartiene ancora ad APE; lascia intatti quelli selezionati successivamente da altri programmi. Anche un'installazione non completata ripristina gli slot modificati durante quel tentativo.

Le preferenze di visualizzazione di Esplora file restano quelle dell'utente: scegli una visualizzazione con miniature e attiva il riquadro Anteprima con **Alt+P**. Chiudi e riapri Esplora file dopo l'installazione. Se Windows mantiene la DLL caricata, un aggiornamento o una disinstallazione possono richiedere un riavvio. Le estensioni native sono x64 e le relative opzioni non sono disponibili su Windows ARM64.

Il motore legge una copia in memoria dello stream e renderizza il PDF in un helper nascosto, con limite di 768 MiB, timeout di 15 secondi e annullamento alla chiusura del riquadro. Non crea copie del PDF su disco. I PDF cifrati, non validi, troppo grandi o bloccati dalla policy dell'app non vengono visualizzati; non vengono eseguite azioni o aperti collegamenti. La DLL implementa `IInitializeWithStream`, mantiene l'isolamento standard delle miniature e usa l'host `Prevhost.exe` per l'anteprima. Riferimenti: [thumbnail handler](https://learn.microsoft.com/en-us/windows/win32/shell/building-thumbnail-providers), [registrazione preview handler](https://learn.microsoft.com/en-us/windows/win32/shell/how-to-register-a-preview-handler).

Per compilare soltanto i componenti nativi:

```powershell
.\scripts\build_windows_shell.ps1
```

Il comando crea `build/windows/shell/APEPDFShell.dll` e `APEPDFShellSmoke.exe`. Il pacchetto distribuibile contiene la DLL accanto a `APEPDFWorker.exe`; l'host di verifica rimane nella cartella di build.

`scripts/test_windows_shell_installer.py` compila ed esegue le effettive funzioni Pascal dell'installer usando un registro simulato in memoria. Verifica entrambe le modalità, aggiornamento, rollback, disinstallazione, backup assente e conservazione degli altri lettori/provider, oltre alla gestione del PATH della CLI nei due ambiti di installazione. Il setup di test termina prima dell'installazione e non scrive nel registro di Windows. La procedura di rilascio esegue anche questa verifica, salvo `-SkipSmokeTest`.

La modalita viene gestita dalle direttive Inno Setup [PrivilegesRequiredOverridesAllowed](https://jrsoftware.org/ishelp/topic_setup_privilegesrequiredoverridesallowed.htm). Cartella e collegamenti usano le [costanti automatiche](https://jrsoftware.org/ishelp/topic_consts.htm) per seguire la scelta utente/tutti gli utenti.

## Licenza

Il file predefinito e `LICENSE` nella radice del progetto. `-LicenseFile` accetta anche un file `.txt` o `.rtf` diverso. Il testo selezionato viene mostrato nel wizard e copiato come `INSTALLATION-LICENSE`. La licenza canonica AGPL rimane sempre presente come `LICENSE`: un testo personalizzato aggiunge informazioni e non sostituisce i diritti concessi dalla AGPL/LGPL. Il pacchetto comprende inoltre i file di licenza e i metadati delle dipendenze disponibili nell'ambiente di build.

Il progetto è **AGPL-3.0-only**, copyright GiancaGIS, con `LICENSE`, metadati e avvisi allineati. Il pacchetto include `NOTICE`, `THIRD_PARTY_NOTICES.md`, `SOURCE_CODE.md` e la cartella `licenses/` con testi completi, avvisi upstream e manifesti delle versioni/sorgenti. Il menu **Aiuto → Licenze e codice sorgente** rende consultabili questi documenti.

La build prepara gli archivi `dist/release/APEPDFStudio-<versione>-Source.zip` e `APEPDFStudio-<versione>-DependencySources.zip`, oltre a checksum e testo della release. Pubblica entrambi gli archivi insieme all'installer sulla stessa pagina di download. Consulta [la procedura GitHub](github-release.md) e [le istruzioni per i sorgenti](../SOURCE_CODE.md).

Le dipendenze della release sono fissate in `requirements-release.txt`. I sorgenti ufficiali vengono scaricati e verificati prima della build; al primo utilizzo il download ? consistente, soprattutto per Qt/PDFium. Le copie verificate rimangono in `.dev-deps/legal/sources/`. Usa `-OfflineSources` per le build successive con cache completa. `-DistRoot` permette una cartella di output diversa. Una build si interrompe se mancano sorgenti esatti, licenze o avvisi verificati.

## Compilazione diretta in Inno Setup

Dopo aver creato il pacchetto, puoi aprire `installer\APEPDFStudio.iss` nell'IDE Inno Setup e premere Compile. Lo script usa i percorsi predefiniti del progetto e legge la versione dall'eseguibile. Il wrapper PowerShell passa invece la versione esatta di `pyproject.toml`.

Per compilare da terminale, puoi personalizzare le definizioni:

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" `
  "/DAppVersion=1.1.1" `
  "/DDistDir=C:\Build\APEPDFStudio" `
  "/DOutputDir=C:\Build\Installers" `
  "/DLicensePath=C:\Build\LICENSE" `
  ".\installer\APEPDFStudio.iss"
```

Il controllo preliminare rifiuta una build senza i tre eseguibili o senza file di licenza.

## Installazione da terminale

Esempi da eseguire sul PC di destinazione:

```powershell
# Locale, cartella personalizzata
.\APEPDFStudio-1.1.1-Setup-x64.exe /CURRENTUSER /DIR="D:\Applicazioni\APE PDF Studio"

# Program Files, tutti gli utenti (richiede amministratore)
.\APEPDFStudio-1.1.1-Setup-x64.exe /ALLUSERS

# Installazione locale automatizzata
.\APEPDFStudio-1.1.1-Setup-x64.exe /CURRENTUSER /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-

# Registra anche APE tra i lettori PDF (la scelta predefinita resta all'utente)
.\APEPDFStudio-1.1.1-Setup-x64.exe /CURRENTUSER /TASKS="pdfassociation"

# Rende disponibile ape-pdf-cli nei nuovi terminali
.\APEPDFStudio-1.1.1-Setup-x64.exe /CURRENTUSER /TASKS="clipath"
```

La modalita silenziosa omette le pagine interattive, compresa la schermata della licenza; usala solo quando l'accettazione e gia gestita dalla tua procedura di distribuzione. I [parametri Inno Setup](https://jrsoftware.org/ishelp/topic_setupcmdline.htm) consentono anche `/LOG="percorso.log"` e `/TASKS="desktopicon,clipath,pdfassociation,pdfthumbnails,pdfpreview"`. La modalità silenziosa registra i PDF se `pdfassociation` è selezionato, ma non apre le impostazioni Windows e non cambia automaticamente il lettore predefinito. `/TASKS=""` deseleziona tutte le opzioni in una nuova installazione.

## Scheda Metadati PDF nelle Proprietà di Windows

Seleziona **Aggiungi la scheda Metadati PDF alle Proprietà dei file PDF (sola lettura)** nella pagina delle opzioni dell'installer. L'opzione è indipendente da miniature, anteprima e associazione del lettore PDF. È disponibile per l'installazione corrente o per tutti gli utenti, tramite lo stesso ambito scelto per il programma.

In Esplora file seleziona un singolo PDF, poi **tasto destro → Proprietà → Metadati PDF**. La scheda mostra titolo, autore, oggetto, parole chiave, applicazione creatrice, produttore, date, versione, pagine, identificazione PDF/A, presenza di firme e restrizioni. Selezionando una riga puoi leggere e copiare il valore nel riquadro sottostante. **Apri in APE PDF Studio** apre il documento nell'app per le operazioni disponibili.

La scheda consulta il file senza modificarlo. I PDF con sole restrizioni sono leggibili senza password; quando serve una password di apertura, la scheda invita ad aprire il documento nell'app. L'identificazione PDF/A e la presenza di firme non costituiscono una verifica della conformità o della validità crittografica. La lingua della scheda segue quella di Windows (italiano, inglese, spagnolo o francese).

L'analisi avviene nel worker separato con controllo preventivo, limite di memoria e timeout. La pagina nativa viene aggiunta tramite [IShellPropSheetExt](https://learn.microsoft.com/en-us/windows/win32/shell/how-to-register-and-implement-a-property-sheet-handler-for-a-file-type). L'installer registra una chiave dedicata sotto `SystemFileAssociations\.pdf\ShellEx\PropertySheetHandlers` e la rimuove in disinstallazione.

Per installazioni automatizzate usa `/TASKS="pdfmetadata"`, eventualmente insieme alle altre opzioni. Dopo l'aggiornamento chiudi e riapri la finestra Proprietà; Windows potrebbe richiedere un riavvio se una versione precedente della DLL è ancora in uso.

## Verifica del pacchetto

L'autore e il publisher sono **GiancaGIS**. I modelli OCR vengono salvati in `%LOCALAPPDATA%\GiancaGIS\APE PDF Studio\ocr\tessdata`; al primo utilizzo dell'OCR, i modelli verificati presenti nelle precedenti cartelle APE o APEX vengono copiati nella nuova posizione senza eliminare gli originali.

Il setup di **APE PDF Studio** mantiene lo stesso AppId della versione APEX: usa la cartella già installata durante un aggiornamento e sostituisce i vecchi eseguibili e collegamenti. Le impostazioni e gli eventuali modelli OCR già scaricati rimangono utilizzabili. La procedura di build usa ora `installer/APEPDFStudio.spec` e `installer/APEPDFStudio.iss`.

L’icona è incorporata negli eseguibili, nel setup e nel programma di disinstallazione; i collegamenti desktop e Start la ricavano dall’eseguibile. Il sorgente modificabile è `src/ape_pdf_studio/assets/app-icon.svg`. Dopo una modifica, rigenera PNG e ICO prima della build:

```powershell
.\.venv\Scripts\python.exe .\scripts\generate_app_icon.py
```

L’ICO contiene dieci misure da 16 a 256 pixel. Il setup usa [SetupIconFile di Inno Setup](https://jrsoftware.org/ishelp/topic_setup_setupiconfile.htm); l’identificativo `APE.PDFStudio` è condiviso dall’applicazione e dai collegamenti per la barra delle applicazioni Windows.

La verifica COM usa le class factory della DLL senza registrarla: controlla miniature, cambio pagina, ridimensionamento, annullamento, rifiuto dei PDF problematici e terminazione di un helper bloccato. Ripete il test in un processo a bassa integrit?, come l?host delle anteprime. Esegui la verifica da un desktop Windows normale: un desktop di automazione ristretto pu? impedire l?inizializzazione del processo a bassa integrit?.

La build controlla gli eseguibili da una cartella temporanea, anche con un modulo `pypdf.py` estraneo e un `PYTHONPATH` estraneo, per verificare che i worker usino il codice incorporato. Non installa il programma, non invia lavori a stampanti e non apre finestre visibili.

Il pacchetto include i cataloghi dell’interfaccia inglese, spagnola e francese e le traduzioni dei pulsanti standard Qt. L’italiano è predefinito; la lingua si sceglie nel programma da **Impostazioni → Aspetto e lingua…** e si applica al riavvio. La lingua del wizard Inno Setup rimane indipendente. La verifica del pacchetto controlla anche i messaggi dei worker nelle tre lingue tradotte.

Se sono presenti entrambi i modelli in `TESSDATA_PREFIX` oppure `.dev-deps\tessdata`, la verifica OCR riconosce una scansione bilingue reale. Altrimenti verifica il processo OCR su una pagina gia testuale, senza scaricare modelli. Per specificare i dati:

```powershell
.\.venv-build\Scripts\python.exe .\scripts\smoke_windows_bundle.py --tessdata "C:\OCR\tessdata"
```

Prima di distribuire il setup, collauda su una macchina di prova entrambe le modalita, una cartella con spazi, accettazione/rifiuto della licenza, aggiornamento e disinstallazione. L'installer compilato non e firmato digitalmente; la procedura non include certificati di firma.
