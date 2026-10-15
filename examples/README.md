# PDF di esempio

[sample_document.pdf](sample_document.pdf) è il documento di esempio generale.

## Modulo PDF compilabile

Apri [modulo_compilabile.pdf](modulo_compilabile.pdf) in APE PDF Studio.

- Fai clic su un campo evidenziato e compilalo direttamente sulla pagina. **Compila modulo** (`Ctrl+Shift+F`) porta al primo campo disponibile.
- **Tab / Shift+Tab** passano al campo successivo o precedente. **Invio** conferma un campo di testo; nelle note multilinea usa **Ctrl+Invio**. **Esc** annulla il campo in corso.
- Il valore viene applicato quando lasci il campo. Usa **Salva** o **Salva con nome…** per conservarlo nel PDF; anche il campo ancora attivo viene incluso.
- Prova testo, note multilinea, casella di spunta, pulsanti radio, menu a scelta e selezione multipla. Il riferimento finale è in sola lettura.
- I due collegamenti in fondo richiedono l'abilitazione per documento e la conferma dell'indirizzo. L'indirizzo email usa il dominio riservato `example.com`.

È un documento sintetico AcroForm, senza JavaScript né invio automatico di dati. I campi rimangono compilabili dopo il salvataggio. Si rigenera dalla radice del repository con `python scripts/generate_form_example.py`.

## Riconoscere e creare i campi

Apri [modulo_da_preparare.pdf](modulo_da_preparare.pdf) oppure la [versione scansionata](modulo_da_preparare_scansione.pdf): questi due documenti non hanno campi compilabili.

1. **Annota → Prepara modulo… → Riconosci campi**.
2. Controlla le proposte sulla pagina. Puoi rinominarle, spostarle, ridimensionarle, cambiarne il tipo o eliminarle. Per aggiungere un campo, scegli uno strumento **Disegna…** e trascina un rettangolo sul PDF.
3. Premi **Crea campi…**, conferma e chiudi la preparazione.
4. Compila direttamente sulla pagina e usa **Salva con nome…**.

L'OCR per le etichette usa le lingue già installate. Senza dati OCR, le scansioni vengono comunque analizzate per righe e riquadri e puoi rinominare i campi manualmente. Il riconoscimento è assistito: controlla sempre i risultati.

Rigenera entrambi gli esempi con `python scripts/generate_form_preparation_example.py`.
