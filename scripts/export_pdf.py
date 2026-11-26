#!/usr/bin/env python3
"""Экспорт markdown-документов UIL в PDF (A4) без внешних зависимостей.

Использует PyMuPDF (fitz). Поддерживает: заголовки #..####, абзацы,
списки (-, *, 1.), блок-код ```, цитаты (>), **жирный**, `код`, --- .

Пример:
    python3 scripts/export_pdf.py docs/manifest.md dist/UIL_v1.8_Manifest.pdf
"""

import html
import re
import sys
import textwrap
from pathlib import Path

import fitz

PRE_WRAP_WIDTH = 88

FONT_CANDIDATES = {
    "regular": [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ],
    "bold": [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ],
    "mono": [
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
        "/Library/Fonts/Courier New.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Courier.ttc",
    ],
}


def find_font(kind: str) -> Path:
    for candidate in FONT_CANDIDATES[kind]:
        path = Path(candidate)
        if path.exists():
            return path
    sys.exit(f"Не найден шрифт ({kind}); проверьте FONT_CANDIDATES в {__file__}")


def inline(text: str) -> str:
    """Экранирует текст и размечает **жирный** и `код`."""
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text


def md_to_html(md_text: str) -> str:
    body, paragraph, list_open = [], [], False

    def flush_paragraph():
        if paragraph:
            body.append(f"<p>{inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list():
        nonlocal list_open
        if list_open:
            body.append("</ul>")
            list_open = False

    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.strip().startswith("```"):
            flush_paragraph()
            flush_list()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(html.escape(lines[i]))
                i += 1
            wrapped = []
            for code_line in code_lines:
                if len(code_line) <= PRE_WRAP_WIDTH:
                    wrapped.append(code_line)
                else:
                    indent = " " * (len(code_line) - len(code_line.lstrip()) + 4)
                    wrapped.extend(textwrap.wrap(
                        code_line, width=PRE_WRAP_WIDTH,
                        subsequent_indent=indent,
                        break_long_words=False, break_on_hyphens=False,
                    ) or [""])
            body.append(f"<pre>{'<br>'.join(wrapped)}</pre>")
        elif match := re.match(r"^(#{1,4})\s+(.*)$", line):
            flush_paragraph()
            flush_list()
            level = len(match.group(1))
            body.append(f"<h{level}>{inline(match.group(2))}</h{level}>")
        elif line.strip().startswith(">"):
            flush_paragraph()
            flush_list()
            body.append(f"<blockquote>{inline(line.strip().lstrip('>').strip())}</blockquote>")
        elif re.match(r"^\s*[-*]\s+", line):
            flush_paragraph()
            if not list_open:
                body.append("<ul>")
                list_open = True
            item = re.sub(r"^\s*[-*]\s+", "", line)
            body.append(f"<li>{inline(item)}</li>")
        elif re.match(r"^\s*\d+\.\s+", line):
            flush_paragraph()
            if not list_open:
                body.append("<ul>")
                list_open = True
            item = re.sub(r"^\s*\d+\.\s+", "", line)
            body.append(f"<li>{inline(item)}</li>")
        elif line.strip() == "---":
            flush_paragraph()
            flush_list()
            body.append("<hr>")
        elif line.strip():
            flush_list()
            paragraph.append(line.strip())
        else:
            flush_paragraph()
            flush_list()
        i += 1

    flush_paragraph()
    flush_list()
    return "\n".join(body)


def read_export_markdown(md_path: Path) -> str:
    text = md_path.read_text(encoding="utf-8")
    manifest_path = Path(__file__).resolve().parents[1] / "ontology" / "manifest.md"
    if md_path.resolve() != manifest_path:
        return text

    text, count = re.subn(
        r"^## 4\. Ролевые пресеты.*?(?=^## 5\.)",
        "",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if count != 1:
        raise ValueError(f"Не найден раздел ролевых пресетов в {md_path}")

    questions_path = md_path.with_name("questions.md")
    questions_text = questions_path.read_text(encoding="utf-8")
    match = re.search(
        r"^## 5\. Открытые вопросы\s*$\n(.*?)(?=^## 6\.|\Z)",
        questions_text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise ValueError(f"Не найден раздел открытых вопросов в {questions_path}")

    questions = match.group(1).strip()
    return f"{text.rstrip()}\n\n## Приложение. Открытые вопросы онтологии\n\n{questions}\n"


def export(md_path: Path, pdf_path: Path) -> None:
    fonts = {kind: find_font(kind) for kind in FONT_CANDIDATES}
    archive = fitz.Archive()
    for path in fonts.values():
        archive.add(path.parent)

    user_css = f"""
    @font-face {{ font-family: uil; src: url("{fonts['regular'].name}"); }}
    @font-face {{ font-family: uil; font-weight: bold; src: url("{fonts['bold'].name}"); }}
    @font-face {{ font-family: uilmono; src: url("{fonts['mono'].name}"); }}
    body {{ font-family: uil; font-size: 10.5pt; line-height: 1.45; }}
    h1 {{ font-size: 17pt; margin: 0 0 10pt 0; }}
    h2 {{ font-size: 14pt; margin: 14pt 0 6pt 0; }}
    h3 {{ font-size: 12pt; margin: 12pt 0 5pt 0; }}
    h4 {{ font-size: 11pt; margin: 10pt 0 4pt 0; }}
    p {{ margin: 0 0 6pt 0; }}
    ul {{ margin: 0 0 6pt 0; }}
    li {{ margin: 0 0 3pt 0; }}
    blockquote {{ margin: 4pt 0 8pt 12pt; color: #444; font-style: italic; }}
    pre {{ font-family: uilmono; font-size: 9pt; background-color: #f4f4f4;
          padding: 6pt; margin: 4pt 0 8pt 0; }}
    code {{ font-family: uilmono; font-size: 9.5pt; }}
    b, h1, h2, h3, h4 {{ font-family: uil; font-weight: bold; }}
    """

    story = fitz.Story(html=md_to_html(read_export_markdown(md_path)),
                       user_css=user_css, archive=archive)

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    writer = fitz.DocumentWriter(str(pdf_path))
    mediabox = fitz.paper_rect("a4")
    area = mediabox + (50, 50, -50, -50)
    while True:
        device = writer.begin_page(mediabox)
        more, _ = story.place(area)
        story.draw(device)
        writer.end_page()
        if not more:
            break
    writer.close()


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(f"Использование: {sys.argv[0]} <input.md> <output.pdf>")
    md_path, pdf_path = Path(sys.argv[1]), Path(sys.argv[2])
    if not md_path.exists():
        sys.exit(f"Файл не найден: {md_path}")
    export(md_path, pdf_path)
    print(f"OK: {pdf_path}")


if __name__ == "__main__":
    main()
