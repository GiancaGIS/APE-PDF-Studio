"""Generate a synthetic CJK compatibility PDF without system fonts or network.

Droid Sans Fallback, supplied by MuPDF under Apache-2.0, is embedded together
with its copyright notice and license. No personal data or JavaScript is used.
"""
import argparse
from pathlib import Path

import pymupdf

CHINESE = "中文测试：你好，世界。汉字、标点和数字 12345。"
JAPANESE = "日本語テスト：こんにちは世界。漢字とひらがな、カタカナ。"
TITLE = "中文与日本語 — Complex PDF compatibility sample"
PAGE_TITLES = (
    "01 / Overview — 中文与日本語", "02 / Columns and tables — 分栏与表格",
    "03 / Images and transparency — 画像と透明度", "04 / Geometry — 回転と座標",
    "05 / Fillable form — 入力フォーム", "06 / Dense vectors — 矢量图形",
    "07 / Raster-only scan — スキャン画像", "08 / Columns of glyphs — 縦の文字",
)


def create_example(destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    font = pymupdf.Font("cjk")
    with pymupdf.open() as document:
        def text(page, x, y, value, size=12, color=(.12, .19, .28)):
            if any(not font.has_glyph(ord(char)) for char in value if not char.isspace()):
                raise ValueError("Missing glyph in the embedded example font")
            writer = pymupdf.TextWriter(page.rect)
            writer.append((x, y), value, font=font, fontsize=size)
            writer.write_text(page, color=color)

        def page(number, width=595, height=842):
            result = document.new_page(width=width, height=height)
            result.draw_rect((0, 0, width, 76), color=None, fill=(.07, .15, .26))
            text(result, 28, 47, PAGE_TITLES[number], 17, (.88, .96, 1))
            text(result, 28, height - 24, f"APE PDF Studio / synthetic sample / {number+1} of 8", 9)
            return result

        cover = page(0)
        text(cover, 34, 126, "中文 / 日本語 / Latin", 30)
        text(cover, 34, 177, CHINESE, 15)
        text(cover, 34, 213, JAPANESE, 15)
        text(cover, 34, 257, "繁體中文：臺灣、閱讀、測試與資料。", 16)
        text(cover, 34, 301, "Italiano: città · Français: sécurité · Español: información", 13)
        for index, line in enumerate((
            "Eight pages: Unicode text, tables, vectors, images, forms and scans.",
            "Embedded font: Droid Sans Fallback / Apache-2.0 / supplied by MuPDF.",
            "No system CJK font, JavaScript, external download or personal data required.",
            "Search probes: 中文测试 / 日本語テスト / こんにちは世界",
            "Page 7 is an image without OCR; page 8 uses positioned glyph columns.",
        )):
            text(cover, 34, 363 + index * 27, line, 11)
        cover.draw_rect((34, 551, 558, 700), color=(.1, .55, .58), fill=(.91, .98, .97))
        text(cover, 52, 588, "阅读与编辑 / 表示と編集", 23)
        text(cover, 52, 625, "Document test: bookmarks, links, notes and embedded text files.", 11)
        text(cover, 52, 658, "Use copies for edits. The source sample must stay unchanged.", 11)
        cover.add_text_annot((550, 178), "中文批注 / 日本語の注釈 / Synthetic note").update()

        columns = page(1)
        for column, paragraph in enumerate((CHINESE, JAPANESE)):
            left = 34 + column * 278
            text(columns, left, 115, "中文段落" if column == 0 else "日本語の段落", 17)
            for line in range(16):
                text(columns, left, 148 + line * 19, paragraph[:20] if line % 2 == 0 else paragraph[20:], 11)
        for row in range(6):
            top = 510 + row * 39
            for column in range(3):
                left = 34 + column * 175
                columns.draw_rect((left, top, left + 175, top + 39), color=(.65, .72, .8),
                    fill=(.9, .94, .98) if row == 0 else None, width=.5)
                value = ("项目 / 項目", "数值 / 数値", "说明 / 説明")[column] if row == 0 else (
                    f"测试 {row}", f"{row * 123}.45", "确认 / 確認")[column]
                text(columns, left + 9, top + 25, value, 12)

        images = page(2)
        with pymupdf.open() as artwork_document:
            artwork = artwork_document.new_page(width=600, height=360)
            for row in range(12):
                for column in range(20):
                    artwork.draw_rect((column*30, row*30, column*30+30, row*30+30),
                        fill=(column / 20, .25 + row / 24, .65), color=None)
            pixels = artwork.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        images.insert_image((34, 114, 560, 430), pixmap=pixels)
        images.draw_rect((80, 220, 490, 360), color=None, fill=(1, 1, 1), fill_opacity=.65)
        text(images, 105, 274, "透明叠加 / 透明な重なり", 20)
        text(images, 105, 316, "Image + vector + embedded CJK text", 14)
        for index in range(8):
            images.draw_circle((110 + index*51, 510), 35, color=(.08, .4, .5),
                fill=(.1, .65, .7), fill_opacity=.25 + index*.07)
        ink = images.add_ink_annot([[(45, 613), (140, 590), (225, 626), (340, 595), (540, 614)]])
        ink.set_colors(stroke=(.9, .2, .1))
        ink.set_border(width=3)
        ink.update()
        text(images, 34, 674, "手绘批注 / 手書き注釈 — removable Ink annotation", 13)

        geometry = page(3, 650, 470)
        text(geometry, 75, 135, CHINESE, 17)
        text(geometry, 75, 181, JAPANESE, 17)
        geometry.draw_rect((65, 95, 585, 285), color=(.1, .55, .58), width=2)
        text(geometry, 75, 250, "CropBox + page rotation 90 degrees", 16)
        geometry.set_cropbox(pymupdf.Rect(30, 20, 620, 450))
        geometry.set_rotation(90)
        document.xref_set_key(geometry.xref, "UserUnit", "1.25")

        form = page(4)
        for name, label, top in (("customer", "姓名 / 氏名", 140), ("city", "城市 / 都市", 250)):
            text(form, 34, top - 19, label, 18)
            widget = pymupdf.Widget()
            widget.field_name, widget.field_label = name, label
            widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
            widget.rect = pymupdf.Rect(34, top, 550, top + 45)
            widget.text_font, widget.text_fontsize = "Helv", 16
            widget.field_value = ""
            widget.border_color, widget.border_width = (.2, .4, .6), 1
            form.add_widget(widget)
        text(form, 34, 341, "确认 / 確認", 18)
        checkbox = pymupdf.Widget()
        checkbox.field_name = "confirmed"
        checkbox.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
        checkbox.rect = pymupdf.Rect(35, 364, 60, 389)
        checkbox.field_value = "Off"
        form.add_widget(checkbox)
        text(form, 75, 384, "我同意 / 同意します", 17)
        text(form, 34, 470, "Field names are ASCII; labels are Unicode. Values start empty.", 12)

        vectors = page(5, 842, 595)
        layer = document.add_ocg("图层 / レイヤー / Vector grid", on=True)
        for row in range(15):
            for column in range(24):
                x, y = 34 + column*32, 116 + row*24
                vectors.draw_rect((x, y, x+27, y+19), color=(.2, .4, .6),
                    fill=(.8 + .1*(row % 2), .9, .96), width=.35, oc=layer)
        for index, value in enumerate(("北京 / 東京", "上海 / 大阪", "台北 / 京都", "中文地图 / 日本語の地図")):
            text(vectors, 45 + index*190, 510, value, 13)

        with pymupdf.open() as artwork_document:
            artwork = artwork_document.new_page(width=595, height=842)
            text(artwork, 45, 145, "扫描页面 / スキャンページ", 25)
            text(artwork, 45, 220, CHINESE, 16)
            text(artwork, 45, 270, JAPANESE, 16)
            text(artwork, 45, 340, "Raster-only Chinese/Japanese: intentionally no text layer.", 12)
            scan = artwork.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        scanned = document.new_page(width=595, height=842)
        scanned.insert_image(scanned.rect, pixmap=scan)

        vertical = page(7)
        text(vertical, 34, 117, "Positioned glyph columns (not a full vertical typography engine)", 11)
        for column, value in enumerate(("日本語の縦の文字", "漢字ひらがなカタカナ", "中文直排文字测试")):
            for row, char in enumerate(value):
                text(vertical, 490 - column*120, 186 + row*34, char, 24)
        text(vertical, 34, 667, CHINESE, 13)
        text(vertical, 34, 711, JAPANESE, 13)
        document[0].insert_link(dict(kind=pymupdf.LINK_GOTO,
            **{"from": pymupdf.Rect(34, 551, 558, 700), "page": 4}))
        document[0].insert_link(dict(kind=pymupdf.LINK_URI,
            **{"from": pymupdf.Rect(34, 460, 558, 485), "uri": "https://example.com/"}))
        document.set_toc([[1, title, index + 1] for index, title in enumerate(PAGE_TITLES)])
        document.set_metadata(dict(title=TITLE, author="APE PDF Studio", subject="中文测试 / 日本語テスト",
            keywords="synthetic, CJK, 中文, 日本語, compatibility", creator="generate_complex_cjk_example.py"))
        document.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/">'
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" dc:description="中文与日本語"/>'
            '</rdf:RDF></x:xmpmeta>')
        document.embfile_add("sample-notes.txt", ("Synthetic sample. 中文 / 日本語.\n"
            "Droid Sans Fallback supplied by MuPDF, Apache-2.0.\n"
            "See the embedded font-license.txt for the license.\n").encode("utf-8"))
        license_path = Path(__file__).resolve().parents[1] / "licenses/Apache-2.0.txt"
        document.embfile_add("font-license.txt", ("Copyright (c) 2005-2008, The Android Open Source Project\n\n"
            + license_path.read_text(encoding="utf-8")).encode("utf-8"))
        document.save(destination, garbage=4, deflate=True, use_objstms=1)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "examples/complex_cjk.pdf")
    print(create_example(parser.parse_args().output))
