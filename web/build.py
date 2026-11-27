#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.parse import quote, unquote, urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = Path(__file__).resolve().parent
DEFAULT_REPOSITORY_URL = "https://github.com/ikarev2ai/worlds"
HOME_TITLE = "Понимание взаимодействия миров"
# Начало вопроса в реестре: «Вопрос N.» в заголовке или жирном абзаце.
QUESTION_LEAD = re.compile(r"Вопрос\s+\d+[.:]")
QUESTION_START = re.compile(r"^(?:#{1,6}\s+|\*\*)?" + QUESTION_LEAD.pattern, re.MULTILINE)
RENDERED_QUESTION = re.compile(r"<h[1-6][^>]*>" + QUESTION_LEAD.pattern + r"|<p><strong>" + QUESTION_LEAD.pattern)


class BuildError(Exception):
    pass


@dataclass
class Document:
    source: Path
    rel_source: str
    kind: str
    route: str
    title: str = ""
    text: str = ""
    content: str = ""
    plain_text: str = ""
    anchors: Set[str] = field(default_factory=set)
    parent: Optional["Document"] = None
    children: List["Document"] = field(default_factory=list)


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "pre", "blockquote"}:
            self.parts.append("\n")


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: Set[str] = set()
        self.references: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        for name in ("href", "src"):
            if values.get(name):
                self.references.append(values[name])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Собрать статический HTML-сайт из Markdown-источников UWI."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Корень репозитория с ontology/, worlds/ и README.md.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Каталог результата (по умолчанию: <root>/dist/html).",
    )
    parser.add_argument(
        "--repository-url",
        default=DEFAULT_REPOSITORY_URL,
        help="Базовый URL ссылок на непубликуемые файлы проекта.",
    )
    return parser.parse_args()


def discover_documents(root: Path) -> List[Document]:
    required = [
        root / "README.md",
        root / "ontology" / "manifest.md",
        root / "ontology" / "questions.md",
        root / "worlds" / "README.md",
        root / "prompts" / "README.md",
        root / "ontology" / "system-prompt.md",
    ]
    missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
    if missing:
        raise BuildError("Отсутствуют обязательные источники: " + ", ".join(missing))

    documents = [
        Document(required[0], "README.md", "home", ""),
        Document(required[1], "ontology/manifest.md", "ontology", "ontology"),
        Document(required[2], "ontology/questions.md", "questions", "ontology/questions"),
        Document(required[3], "worlds/README.md", "worlds_index", "worlds"),
        Document(required[4], "prompts/README.md", "prompts_index", "prompts"),
    ]
    for prompt_source in sorted((root / "prompts").glob("v*.md"), key=lambda path: path.name):
        documents.append(
            Document(
                prompt_source,
                prompt_source.relative_to(root).as_posix(),
                "prompt",
                f"prompts/{prompt_source.stem}",
            )
        )
    worlds_root = root / "worlds"
    for world_dir in sorted(worlds_root.iterdir(), key=lambda path: path.name):
        if not world_dir.is_dir():
            continue
        source = world_dir / "README.md"
        if not source.is_file():
            continue
        world = Document(
            source,
            source.relative_to(root).as_posix(),
            "world",
            f"worlds/{world_dir.name}",
        )
        documents.append(world)
        works_dir = world_dir / "works"
        if not works_dir.is_dir():
            continue
        for work_source in sorted(works_dir.glob("*.md"), key=lambda path: path.name):
            work = Document(
                work_source,
                work_source.relative_to(root).as_posix(),
                "work",
                f"{world.route}/works/{work_source.stem}",
                parent=world,
            )
            world.children.append(work)
            documents.append(work)

    routes: Dict[str, Document] = {}
    sources: Dict[Path, Document] = {}
    for document in documents:
        if document.route in routes:
            raise BuildError(f"Дублируется маршрут: {document.route}")
        resolved_source = document.source.resolve()
        if resolved_source in sources:
            raise BuildError(f"Источник опубликован дважды: {document.rel_source}")
        routes[document.route] = document
        sources[resolved_source] = document
    return documents


def inline_text(token) -> str:
    parts: List[str] = []
    for child in token.children or []:
        if child.type in {"text", "code_inline"}:
            parts.append(child.content)
        elif child.type == "image":
            parts.append(child.content)
    return "".join(parts).strip()


def route_url(current_route: str, target_route: str) -> str:
    relative = posixpath.relpath(target_route or ".", start=current_route or ".")
    return "./" if relative == "." else relative + "/"


def asset_url(current_route: str, asset: str) -> str:
    return posixpath.relpath(asset, start=current_route or ".")


def repository_file_url(repository_url: str, root: Path, target: Path) -> str:
    relative = target.relative_to(root).as_posix()
    return f"{repository_url.rstrip('/')}/blob/main/{quote(relative)}"


def rewrite_link(
    document: Document,
    href: str,
    root: Path,
    documents_by_source: Dict[Path, Document],
    repository_url: str,
) -> str:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return href
    fragment = unquote(parsed.fragment)
    if not parsed.path:
        if fragment and fragment not in document.anchors:
            raise BuildError(f"{document.rel_source}: не найден якорь #{fragment}")
        return href
    if parsed.path.startswith("/"):
        raise BuildError(f"{document.rel_source}: абсолютный путь не поддерживается: {href}")

    target = (document.source.parent / unquote(parsed.path)).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise BuildError(f"{document.rel_source}: ссылка выходит за корень проекта: {href}") from error
    if not target.exists():
        raise BuildError(f"{document.rel_source}: не найдена цель ссылки: {href}")

    target_document = documents_by_source.get(target)
    suffix = f"#{fragment}" if fragment else ""
    if target_document is None:
        return repository_file_url(repository_url, root, target) + suffix
    if fragment and fragment not in target_document.anchors:
        raise BuildError(f"{document.rel_source}: не найден якорь {target_document.rel_source}#{fragment}")
    if target_document is document and fragment:
        return suffix
    return route_url(document.route, target_document.route) + suffix


def walk_tokens(tokens) -> None:
    for token in tokens:
        yield token
        if token.children:
            yield from walk_tokens(token.children)


def presentation_markdown(document: Document) -> str:
    if document.kind != "world" or not document.children:
        return document.text
    lines = document.text.splitlines()
    header = "| Произведение | Автор | Файл | Версия описания | Вклад в стресс-тест |"
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == header)
    except StopIteration as error:
        raise BuildError(f"{document.rel_source}: не найдена таблица произведений") from error
    end = start + 2
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    rows = lines[start + 2:end]
    children_by_source = {child.source.resolve(): child for child in document.children}
    simplified = [
        "| Произведение | Вклад в стресс-тест |",
        "|--------------|---------------------|",
    ]
    used_sources = set()
    for row in rows:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if len(cells) != 5:
            raise BuildError(f"{document.rel_source}: неожиданная строка таблицы: {row}")
        link_match = re.search(r"\]\(([^)]+\.md)\)", cells[2])
        if not link_match:
            raise BuildError(f"{document.rel_source}: не найдена ссылка произведения: {row}")
        work_source = (document.source.parent / link_match.group(1)).resolve()
        child = children_by_source.get(work_source)
        if child is None:
            raise BuildError(f"{document.rel_source}: произведение не найдено: {link_match.group(1)}")
        relative_source = child.source.relative_to(document.source.parent).as_posix()
        simplified.append(f"| [{cells[0]}]({relative_source}) | {cells[4]} |")
        used_sources.add(work_source)
    if used_sources != set(children_by_source):
        raise BuildError(f"{document.rel_source}: таблица не совпадает с файлами произведений")
    return "\n".join(lines[:start] + simplified + lines[end:]) + "\n"


def is_question_start(inline) -> bool:
    return bool(QUESTION_LEAD.match(inline.content.lstrip().lstrip("*")))


def question_list_tokens(tokens) -> List:
    """Оставляет заголовки разделов и вопросы, убирая вступительные абзацы реестра."""
    kept: List = []
    in_heading = False
    in_question = False
    for index, token in enumerate(tokens):
        if token.type == "heading_close":
            kept.append(token)
            in_heading = False
            continue
        if token.type == "heading_open":
            in_heading = True
            in_question = int(token.tag[1]) > 2 or is_question_start(tokens[index + 1])
        elif token.type == "paragraph_open" and not in_question:
            in_question = is_question_start(tokens[index + 1])
        if in_heading or in_question:
            kept.append(token)
    return kept


def home_tokens(tokens) -> List:
    """Оставляет краткое описание и вводные примеры; служебные разделы не публикуются."""
    start = next(index for index, token in enumerate(tokens) if token.type == "paragraph_open")
    end = next(index for index in range(start, len(tokens)) if tokens[index].type == "paragraph_close")
    # Название уже выведено заголовком, поэтому вводная фраза «Название: » не повторяется.
    lead = HOME_TITLE + ":"
    for child in tokens[start + 1].children:
        if child.type == "text" and child.content.startswith(lead):
            child.content = child.content[len(lead):].lstrip()
            child.content = child.content[0].upper() + child.content[1:]
    example_start = next(
        (
            index
            for index, token in enumerate(tokens)
            if token.type == "heading_open"
            and token.tag == "h2"
            and inline_text(tokens[index + 1]) == "Примеры на естественном языке и в UWI"
        ),
        None,
    )
    if example_start is None:
        raise BuildError("README.md: не найден раздел вводных примеров")
    example_end = next(
        (
            index
            for index in range(example_start + 1, len(tokens))
            if tokens[index].type == "heading_open" and tokens[index].tag == "h2"
        ),
        len(tokens),
    )
    return tokens[start:end + 1] + tokens[example_start:example_end]


def current_prompt(root: Path) -> tuple:
    source = root / "ontology" / "system-prompt.md"
    text = source.read_text(encoding="utf-8")
    block = re.search(r"^```\n(.*?)^```", text, re.MULTILINE | re.DOTALL)
    if block is None:
        raise BuildError("ontology/system-prompt.md: не найден блок промпта")
    prompt_text = block.group(1).rstrip("\n")
    version = re.search(r"^SYSTEM_PROTOCOL: UWI (v\d+\.\d+)\b", prompt_text, re.MULTILINE)
    if version is None:
        raise BuildError("ontology/system-prompt.md: не найдена версия SYSTEM_PROTOCOL")
    prompt_version = version.group(1)
    snapshot = root / "prompts" / f"{prompt_version}.md"
    if not snapshot.is_file():
        raise BuildError(f"Отсутствует текущий снимок промпта: prompts/{prompt_version}.md")
    if snapshot.read_text(encoding="utf-8") != text:
        raise BuildError(f"Текущий снимок prompts/{prompt_version}.md не совпадает с системным промптом")
    return prompt_version, prompt_text


def render_documents(
    documents: List[Document], root: Path, repository_url: str
) -> None:
    documents_by_source = {document.source.resolve(): document for document in documents}
    tokens_by_source = {}
    for document in documents:
        document.text = document.source.read_text(encoding="utf-8")
        markdown = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")
        anchors_plugin(markdown, min_level=1, max_level=6)
        tokens = markdown.parse(presentation_markdown(document))
        tokens_by_source[document.source.resolve()] = tokens

        for index, token in enumerate(tokens):
            if token.type == "heading_open":
                anchor = token.attrs.get("id")
                if anchor:
                    document.anchors.add(anchor)
                if not document.title and index + 1 < len(tokens):
                    document.title = inline_text(tokens[index + 1])
        if not document.title:
            raise BuildError(f"{document.rel_source}: отсутствует заголовок первого уровня")
        if document.kind == "home":
            document.title = HOME_TITLE

    for document in documents:
        markdown = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")
        tokens = tokens_by_source[document.source.resolve()]
        if document.kind == "questions":
            tokens = question_list_tokens(tokens)
        elif document.kind == "home":
            tokens = home_tokens(tokens)
        for token in walk_tokens(tokens):
            if token.type == "link_open" and token.attrs.get("href"):
                token.attrs["href"] = rewrite_link(
                    document,
                    token.attrs["href"],
                    root,
                    documents_by_source,
                    repository_url,
                )
        document.content = markdown.renderer.render(tokens, markdown.options, {})
        document.content = document.content.replace(
            "<table>",
            '<div class="table-scroll" tabindex="0" role="region" aria-label="Таблица"><table>',
        ).replace("</table>", "</table></div>")
        document.content = document.content.replace("<pre>", '<pre tabindex="0">')

        extractor = TextExtractor()
        extractor.feed(document.content)
        document.plain_text = re.sub(r"\s+", " ", " ".join(extractor.parts)).strip()
        if document.kind == "questions" and len(QUESTION_START.findall(document.text)) != len(
            RENDERED_QUESTION.findall(document.content)
        ):
            raise BuildError(f"{document.rel_source}: вопрос потерян при сокращении страницы")


def navigation(document: Document, documents_by_route: Dict[str, Document]) -> dict:
    parent = document.parent
    siblings = parent.children if parent else []
    return {
        "home": route_url(document.route, ""),
        "ontology": route_url(document.route, "ontology"),
        "questions": route_url(document.route, "ontology/questions"),
        "worlds": route_url(document.route, "worlds"),
        "prompts": route_url(document.route, "prompts"),
        "parent": route_url(document.route, parent.route) if parent else None,
        "siblings": [
            {"title": sibling.title, "url": route_url(document.route, sibling.route), "current": sibling is document}
            for sibling in siblings
        ],
        "children": [
            {"title": child.title, "url": route_url(document.route, child.route)}
            for child in document.children
        ],
        "world_list": [
            {"title": world.title, "url": route_url(document.route, world.route)}
            for world in documents_by_route.values()
            if world.kind == "world"
        ],
    }


def render_site(
    staging: Path,
    documents: List[Document],
    prompt_version: str,
    prompt_text: str,
) -> None:
    environment = Environment(
        loader=FileSystemLoader(str(WEB_ROOT / "templates")),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = environment.get_template("base.html")
    documents_by_route = {document.route: document for document in documents}
    worlds_index = documents_by_route["worlds"]
    world_list = sorted(
        [document for document in documents if document.kind == "world"],
        key=lambda document: document.title.casefold(),
    )
    documents_by_route["worlds"] = worlds_index

    for document in sorted(documents, key=lambda item: item.route):
        nav = navigation(document, documents_by_route)
        nav["world_list"] = [
            {"title": world.title.split(" — ", 1)[0], "url": route_url(document.route, world.route)}
            for world in world_list
        ]
        html = template.render(
            document=document,
            content=document.content,
            nav=nav,
            prompt_version=prompt_version,
            prompt_text=prompt_text,
            prompt_raw=asset_url(document.route, f"prompts/{prompt_version}.md"),
            raw_source=(
                asset_url(document.route, f"prompts/{document.source.name}")
                if document.kind == "prompt"
                else None
            ),
            root_prefix=route_url(document.route, ""),
            stylesheet=asset_url(document.route, "assets/site.css"),
            script=asset_url(document.route, "assets/search.js"),
            prompt_script=asset_url(document.route, "assets/prompt.js"),
        )
        destination = staging / document.route / "index.html"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(html, encoding="utf-8")

    search_index = [
        {
            "kind": document.kind,
            "title": document.title,
            "url": f"{document.route}/" if document.route else "",
            "parent": document.parent.title if document.parent else "",
            "text": document.plain_text,
        }
        for document in sorted(documents, key=lambda item: item.route)
    ]
    (staging / "search-index.json").write_text(
        json.dumps(search_index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (staging / ".nojekyll").write_text("", encoding="utf-8")


def check_generated_site(staging: Path) -> None:
    pages: Dict[Path, PageParser] = {}
    for html_path in sorted(staging.rglob("*.html")):
        parser = PageParser()
        parser.feed(html_path.read_text(encoding="utf-8"))
        pages[html_path.resolve()] = parser

    for html_path, parser in pages.items():
        for reference in parser.references:
            parsed = urlsplit(reference)
            if parsed.scheme or parsed.netloc or reference.startswith("data:"):
                continue
            fragment = unquote(parsed.fragment)
            if not parsed.path:
                target = html_path
            else:
                target = (html_path.parent / unquote(parsed.path)).resolve()
                if target.is_dir():
                    target = target / "index.html"
            if not target.is_file():
                relative_source = html_path.relative_to(staging)
                raise BuildError(f"{relative_source}: не найдена сгенерированная цель: {reference}")
            if fragment and target.suffix == ".html":
                target_parser = pages.get(target.resolve())
                if target_parser is None or fragment not in target_parser.ids:
                    relative_source = html_path.relative_to(staging)
                    raise BuildError(f"{relative_source}: не найден сгенерированный якорь: {reference}")


def write_manifest(staging: Path, documents: List[Document]) -> None:
    manifest = {
        "documents": [
            {
                "route": document.route,
                "source": document.rel_source,
                "source_sha256": hashlib.sha256(document.text.encode("utf-8")).hexdigest(),
                "type": document.kind,
            }
            for document in sorted(documents, key=lambda item: item.route)
        ]
    }
    (staging / "build-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def publish(staging: Path, output: Path) -> None:
    backup = output.parent / f".{output.name}-backup-{os.getpid()}"
    try:
        if output.exists():
            if not output.is_dir():
                raise BuildError(f"Результат не является каталогом: {output}")
            output.rename(backup)
        staging.rename(output)
    except Exception:
        if backup.exists() and not output.exists():
            backup.rename(output)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if backup.exists():
            shutil.rmtree(backup)


def build(root: Path, output: Path, repository_url: str) -> List[Document]:
    root = root.resolve()
    output = output.resolve()
    if not root.is_dir():
        raise BuildError(f"Корень исходников не найден: {root}")
    documents = discover_documents(root)
    prompt_version, prompt_text = current_prompt(root)
    render_documents(documents, root, repository_url)

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        shutil.copytree(WEB_ROOT / "assets", staging / "assets")
        for source in sorted((root / "prompts").glob("*.md"), key=lambda path: path.name):
            destination = staging / source.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        render_site(staging, documents, prompt_version, prompt_text)
        write_manifest(staging, documents)
        check_generated_site(staging)
        publish(staging, output)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return documents


def main() -> int:
    args = parse_args()
    output = args.output or args.root / "dist" / "html"
    try:
        documents = build(args.root, output, args.repository_url)
    except (BuildError, OSError) as error:
        print(f"Ошибка сборки: {error}", file=sys.stderr)
        return 1
    worlds = sum(document.kind == "world" for document in documents)
    works = sum(document.kind == "work" for document in documents)
    print(f"Готово: {output.resolve()}")
    print(f"Документы: {len(documents)}; миры: {worlds}; произведения: {works}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
