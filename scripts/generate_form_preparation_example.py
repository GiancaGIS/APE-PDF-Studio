"""Create flat digital and scanned forms for testing Prepare form."""
from pathlib import Path
import pymupdf


def create_examples(folder):
    folder = Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    digital = folder/"modulo_da_preparare.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=595,height=842)
        page.draw_rect((0,0,595,105),color=None,fill=(.10,.20,.37))
        page.insert_text((40,44),"APE PDF STUDIO / PREPARA MODULO",fontsize=10,color=(.7,.82,1))
        page.insert_text((40,79),"Da foglio a modulo compilabile",fontsize=22,color=(1,1,1))
        page.insert_text((40,127),"Questo documento non contiene campi interattivi.",fontsize=11)
        for label,y,rect in (
                ("Nome e cognome",164,(40,177,555,208)),
                ("Email",244,(40,257,555,288)),
                ("Note",454,(40,467,555,585))):
            page.insert_text((40,y),label,fontsize=11)
            page.draw_rect(rect,color=(.4,.5,.6),width=.8)
        page.insert_text((40,329),"Data",fontsize=11)
        page.draw_line((40,359),(250,359),color=(.4,.5,.6),width=.8)
        page.draw_rect((40,394,55,409),color=(.4,.5,.6),width=.8)
        page.insert_text((65,406),"Accetto le condizioni",fontsize=11)
        page.insert_text((40,644),"1. Apri Annota > Prepara modulo e premi Riconosci campi.",fontsize=10)
        page.insert_text((40,668),"2. Controlla le proposte. Disegna o correggi i riquadri sulla pagina.",fontsize=10)
        page.insert_text((40,692),"3. Conferma Crea campi, chiudi la preparazione e compila il PDF.",fontsize=10)
        page.insert_text((40,716),"4. Usa Salva con nome per conservare il modulo.",fontsize=10)
        document.set_metadata(dict(title="Esempio di preparazione modulo",author="APE PDF Studio"))
        document.save(digital)
        pixels = page.get_pixmap(matrix=pymupdf.Matrix(2,2))
    scanned = folder/"modulo_da_preparare_scansione.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=595,height=842)
        page.insert_image(page.rect,pixmap=pixels)
        document.set_metadata(dict(title="Scansione di esempio da preparare",author="APE PDF Studio"))
        document.save(scanned)
    return digital,scanned


if __name__ == "__main__":
    for path in create_examples(Path(__file__).resolve().parents[1]/"examples"):
        print(path)
