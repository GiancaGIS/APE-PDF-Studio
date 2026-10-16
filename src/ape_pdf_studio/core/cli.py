"""Optional headless PDF commands: useful for automation and integration testing."""
from __future__ import annotations
from .localization import tr

import argparse
import sys
from pathlib import Path

from .editor import parse_page_range
from .errors import PDFOperationError
from .broker_editor import BrokerEditor as PDFEditor


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ape-pdf-cli", description=tr("APE PDF Studio — motore di editing da terminale"))
    parser.add_argument("input", type=Path, help=tr("Documento PDF di partenza"))
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("info", help=tr("Visualizza informazioni sul PDF"))

    rotate = subs.add_parser("rotate", help=tr("Ruota una pagina"))
    rotate.add_argument("page", type=int, help=tr("Numero di pagina (da 1)"))
    rotate.add_argument("--degrees", type=int, default=90, choices=[90, 180, 270, -90, -180, -270])

    delete = subs.add_parser("delete", help=tr("Elimina una pagina"))
    delete.add_argument("page", type=int)

    move = subs.add_parser("move", help=tr("Sposta la pagina di una posizione"))
    move.add_argument("page", type=int)
    move.add_argument("direction", choices=["up", "down"])

    insert = subs.add_parser("insert", help=tr("Inserisci un PDF dopo una pagina"))
    insert.add_argument("--after", type=int, required=True)
    insert.add_argument("--file", type=Path, required=True)

    note = subs.add_parser("note", help=tr("Aggiungi una nota alla pagina"))
    note.add_argument("page", type=int)
    note.add_argument("text")

    extract = subs.add_parser("extract", help=tr("Estrai un intervallo, ad es. 1,3-5"))
    extract.add_argument("pages")

    for name, child in subs.choices.items():
        if name != "info":
            child.add_argument("-o", "--output", required=True, type=Path, help=tr("Percorso PDF di output"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    editor = None
    try:
        editor = PDFEditor(args.input)
        if args.command == "info":
            print(tr('File: {0}', args.input.resolve()))
            print(tr('Protetto: {0}', tr('Sì') if editor.encrypted else tr('No')))
            if not editor.encrypted:
                print(tr('Pagine: {0}', editor.metadata().page_count))
            return 0
        if args.command == "rotate":
            editor.rotate(args.page - 1, args.degrees)
        elif args.command == "delete":
            editor.delete(args.page - 1)
        elif args.command == "move":
            editor.move(args.page - 1, -1 if args.direction == "up" else 1)
        elif args.command == "insert":
            editor.insert_pdf(args.file, args.after - 1)
        elif args.command == "note":
            editor.add_note(args.page - 1, args.text)
        elif args.command == "extract":
            pages = parse_page_range(args.pages, editor.metadata().page_count)
            editor.extract(pages, args.output)
            print(tr('Creato: {0}', args.output.resolve()))
            return 0
        editor.save_as(args.output)
        print(tr('Creato: {0}', args.output.resolve()))
        return 0
    except (PDFOperationError, OSError) as exc:
        print(tr('ERRORE: {0}', exc), file=sys.stderr)
        return 2
    finally:
        if editor:
            editor.close()


if __name__ == "__main__":
    raise SystemExit(main())
