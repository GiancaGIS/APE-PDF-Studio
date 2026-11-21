"""Generate our own paragraph-reflow example; no external content or actions."""
from pathlib import Path
import argparse


def create_example(destination):
    import pymupdf
    destination = Path(destination)
    with pymupdf.open() as document:
        page = document.new_page(width=595, height=842)
        page.insert_text((48, 55), 'Riflusso automatico dei paragrafi', fontsize=21, fontname='hebo', color=(.12, .25, .43))
        page.insert_text((48, 82), 'Modifica testo > Riflusso paragrafi. Invio: nuova riga. Ctrl+Invio: applica.', fontsize=10)
        page.draw_rect((42, 112, 308, 420), color=None, fill=(.92, .96, 1))
        writer = pymupdf.TextWriter(page.rect)
        writer.fill_textbox((54, 130, 296, 320),
            'Questo paragrafo contiene varie righe di testo. Se aggiungi nuove parole, '
            'il motore le dispone automaticamente sulle righe successive, mantenendo '
            'la larghezza della colonna. Puoi cambiare la dimensione dei caratteri e '
            'scegliere un allineamento diverso. Il riquadro cresce soltanto nello spazio libero.',
            font=pymupdf.Font('helv'), fontsize=12, lineheight=1.25, warn=False)
        writer.fill_textbox((338, 130, 547, 320),
            'Questa è una colonna indipendente. Modificare il paragrafo a sinistra '
            'non deve spostare o cancellare queste parole. I riquadri vengono '
            'riconosciuti separatamente per conservare la struttura della pagina.',
            font=pymupdf.Font('helv'), fontsize=12, lineheight=1.25, warn=False)
        writer.write_text(page)
        page.insert_text((48, 462), 'Stili nello stesso paragrafo', fontsize=15, fontname='hebo')
        x = 48
        for text, family, color in [('Una parola ', 'helv', (0, 0, 0)),
                                     ('importante', 'hebo', (.75, .1, .1)),
                                     (' conserva il suo stile originale.', 'helv', (0, 0, 0))]:
            page.insert_text((x, 492), text, fontsize=12, fontname=family, color=color)
            x += pymupdf.Font(family).text_length(text, fontsize=12)
        page.insert_text((48, 508), 'Aggiungi una frase per provare gli a capo e la conservazione del grassetto.', fontsize=12)
        page.draw_rect((48, 650, 547, 715), color=(.25, .45, .7), fill=(.85, .91, .98))
        page.insert_text((64, 680), 'Questo elemento grafico deve rimanere intatto.', fontsize=12)
        page.insert_text((48, 780), 'Se il testo non entra, la bozza resta aperta e il PDF non viene modificato.', fontsize=10)
        document.set_metadata({'title': 'Esempio di riflusso dei paragrafi', 'author': 'APE PDF Studio'})
        document.save(destination, deflate=True)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', nargs='?', default='examples/riflusso_paragrafi.pdf')
    print(create_example(parser.parse_args().output))
