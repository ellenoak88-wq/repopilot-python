"""Line-addressable retrieval units. Structure labels are search metadata, never source text."""
import ast
import re
from bisect import bisect_left, bisect_right
from functools import lru_cache
from importlib import import_module
from pathlib import PurePosixPath

import yaml
from tree_sitter import Language, Parser

CHUNKER_VERSION = "structure-v1"
LANGUAGES = {
    ".go": ("go", "language"), ".sh": ("bash", "language"),
    ".js": ("javascript", "language"), ".jsx": ("javascript", "language"),
    ".mjs": ("javascript", "language"), ".cjs": ("javascript", "language"),
    ".ts": ("typescript", "language_typescript"), ".tsx": ("typescript", "language_tsx"),
    ".json": ("json", "language"), ".css": ("css", "language"), ".html": ("html", "language"),
}
DECLARATIONS = {
    "function_declaration", "generator_function_declaration", "method_declaration", "method_definition",
    "function_definition", "class_declaration", "abstract_class_declaration", "interface_declaration",
    "enum_declaration", "type_alias_declaration", "type_declaration", "case_item",
}


@lru_cache(maxsize=None)
def _language(ext):
    module, entry = LANGUAGES[ext]
    return Language(getattr(import_module("tree_sitter_" + module), entry)())


def _text(node):
    return node.text[:250].decode("utf-8", errors="replace").split("\n")[0].strip() if node else ""


def _tree_structure(ext, content, mark, soft):
    root = Parser(_language(ext)).parse(content.encode("utf-8")).root_node
    stack = [(root, "")]
    while stack:
        node, parent = stack.pop()
        kind, row = node.type, node.start_point.row
        if node.parent and node.parent.type in ("block", "statement_list", "statement_block", "compound_statement",
                                                "switch_body", "object", "array", "class_body"):
            soft.add(row)
        name = ""
        if kind in DECLARATIONS:
            name = _text(node.child_by_field_name("name") or node.child_by_field_name("value"))
            if kind == "method_declaration":
                receiver = _text(node.child_by_field_name("receiver")).strip("()").split()
                if receiver:
                    name = receiver[-1].lstrip("*") + "." + name
            if not name:
                name = _text(node).split("{")[0]
        elif kind == "variable_declarator":
            value = node.child_by_field_name("value")
            if value and value.type in ("arrow_function", "function_expression", "class"):
                name = _text(node.child_by_field_name("name"))
        elif kind == "expression_statement" and node.named_child_count:
            call = node.named_children[0]
            func = _text(call.child_by_field_name("function"))
            args = call.child_by_field_name("arguments")
            if func.split(".")[0] in ("test", "it", "describe") and args and args.named_child_count:
                name = func + " " + _text(args.named_children[0])
        elif kind == "rule_set" and node.named_child_count:
            name = _text(node.named_children[0])
        elif kind == "element" and node.named_child_count:
            tag = _text(node.named_children[0])
            if re.match(r"<(?:main|section|article|form|dialog|nav|header|footer)\b", tag, re.I):
                name = tag.rstrip(">")
        elif kind == "pair" and ext == ".json":
            key = _text(node.child_by_field_name("key")).strip('"')
            if not parent or node.end_point.row > row or parent in ("scripts", "exports"):
                name = key
        elif ext == ".go" and kind in ("var_declaration", "const_declaration") and node.parent.type == "source_file":
            name = _text(node).split("=")[0].strip()
        if name:
            enclosing = parent
            # Multiple declarations on one line share one citable line range.
            parent = " / ".join(filter(None, (parent, name)))[:220]
            first = node
            while first.parent and first.parent.type in ("export_statement", "lexical_declaration", "variable_declaration"):
                first = first.parent
            mark(first.start_point.row, kind, parent, comments=True,
                 end=node.end_point.row + int(node.end_point.column > 0), enclosing=enclosing)
        # Descend only where another unit or a statement boundary can occur.
        if node.named_child_count and (node.end_point.row > row or node.type in
                ("source_file", "program", "document", "export_statement", "lexical_declaration", "variable_declaration")):
            stack.extend((child, parent) for child in reversed(node.named_children))


def _python_structure(content, mark, soft):
    tree = ast.parse(content)
    def visit(node, parent):
        for child in ast.iter_child_nodes(node):
            scope = parent
            if isinstance(child, ast.stmt):
                soft.add(child.lineno - 1)
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = ".".join(filter(None, (parent, child.name)))
                first = min([child.lineno] + [d.lineno for d in child.decorator_list])
                mark(first - 1, type(child).__name__, scope, comments=True,
                     end=child.end_lineno, enclosing=parent)
            visit(child, scope)
    visit(tree, "")


def _yaml_structure(content, mark, soft):
    # Compose only; aliases point back to earlier nodes, which must not be traversed again.
    for doc in yaml.compose_all(content, Loader=yaml.BaseLoader):
        stack = [(doc, (), 0)]
        visited = set()
        while stack:
            node, path, depth = stack.pop()
            if not node or depth > 12 or id(node) in visited:
                continue
            visited.add(id(node))
            if isinstance(node, yaml.MappingNode):
                for key, value in reversed(node.value):
                    if not isinstance(key, yaml.ScalarNode):
                        continue
                    scope = (*path, key.value)
                    row = key.start_mark.line
                    soft.add(row)
                    if depth == 0 or (depth == 1 and isinstance(value, (yaml.MappingNode, yaml.SequenceNode))):
                        mark(row, "config", ".".join(scope))
                    if value.start_mark.index > key.start_mark.index:
                        stack.append((value, scope, depth + 1))
            elif isinstance(node, yaml.SequenceNode):
                for child in node.value:
                    if child.start_mark.index >= node.start_mark.index:
                        soft.add(child.start_mark.line)
                        if child is not node:
                            stack.append((child, path, depth + 1))


def _document_structure(lines, mark, soft):
    fence = None
    headings = []
    for i, line in enumerate(lines):
        marker = re.match(r"^ {0,3}(\x60{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
            soft.add(i)
            continue
        heading = re.match(r"^ {0,3}(#{1,6})\s+(.+)", line)
        if heading:
            level, title, start = len(heading[1]), heading[2], i
        elif re.fullmatch(r"\s*\*\*.+[?？]\*\*\s*", line):
            level, title, start = 7, line.strip().strip("*"), i
        elif i and lines[i-1].strip() and re.fullmatch(r" {0,3}(?:=+|-{3,})\s*", line):
            level, title, start = (1 if line.strip()[0] == "=" else 2), lines[i-1], i-1
        else:
            if line.strip() and i and not lines[i-1].strip():
                soft.add(i)
            continue
        while headings and headings[-1][0] >= level:
            headings.pop()
        headings.append((level, title.strip().rstrip("#").strip()))
        mark(start, "section", " / ".join(h[1] for h in headings))


def chunk_file(path, content, size=65, overlap=12):
    if not 0 <= overlap < size:
        raise ValueError("overlap must be smaller than chunk size")
    lines = content.split("\n")
    anchors = {0: ("text", "")}
    soft = set()
    restores = {}
    def mark(row, kind, scope, comments=False, end=None, enclosing=""):
        if end is not None and end < len(lines):
            restores.setdefault(end, ("context", enclosing))
        if comments:
            while row > 0 and re.match(r"^\s*(?://|#(?![!]))", lines[row-1]):
                row -= 1
        if 0 <= row < len(lines):
            anchors[row] = (kind, scope[:220])

    file = PurePosixPath(path)
    ext = file.suffix.lower()
    try:
        if ext == ".py":
            _python_structure(content, mark, soft)
        elif ext in LANGUAGES:
            _tree_structure(ext, content, mark, soft)
        elif ext in (".yaml", ".yml"):
            _yaml_structure(content, mark, soft)
        elif ext in (".md", ".mdx", ".rst") or file.name.lower() in ("readme", "contributing"):
            _document_structure(lines, mark, soft)
        else:
            pattern = None
            if file.name.lower() == "makefile":
                pattern = r"^([^\s#:=][^:=\s]*(?: +[^:=\s]+)*)\s*:(?![=])"
            elif file.name.lower() == "dockerfile":
                pattern = r"^\s*(FROM\s+.+)"
            elif ext == ".toml":
                pattern = r"^\s*\[{1,2}([^\]]+)\]{1,2}\s*(?:#.*)?$"
            soft.update(i for i in range(1, len(lines)) if lines[i].strip() and not lines[i-1].strip())
            if pattern:
                for i, line in enumerate(lines):
                    match = re.match(pattern, line, re.I)
                    if match:
                        mark(i, "config", match[1], comments=True)
                    if file.name.lower() == "dockerfile" and re.match(r"^[A-Z]+\s", line):
                        soft.add(i)
    except (SyntaxError, ValueError, yaml.YAMLError, RecursionError):
        anchors, soft = {0: ("text", "")}, set()

        restores.clear()

    # Restore the enclosing symbol after a nested method, function or config value.
    # Keep blank separators and lone closing delimiters with their preceding unit.
    starts = sorted(set(anchors) | set(restores) | {len(lines)})
    for start, stop in zip(starts, starts[1:]):
        if start in restores and start not in anchors and any(
                line.strip(" \t\r,;)}]") for line in lines[start:stop]):
            anchors[start] = restores[start]
    empty = not content.strip()
    chunks = []
    starts = sorted(anchors) + [len(lines)]
    breaks = sorted(soft)
    for left, right in zip(starts, starts[1:]):
        kind, scope = anchors[left]
        start = left
        while start < right:
            stop = min(start + size, right)
            natural = False
            if stop < right:
                lo = bisect_left(breaks, start + max(size // 2, 1))
                hi = bisect_right(breaks, stop)
                if hi > lo:
                    stop, natural = breaks[hi - 1], True
            text = "\n".join(lines[start:stop])
            if text.strip() or empty:
                chunks.append(dict(path=path, start=start+1, end=stop, text=text, kind=kind, scope=scope))
            if stop == right:
                break
            start = stop if natural else stop - overlap
    return chunks


def search_text(chunk):
    scope = chunk.get("scope")
    return f"{chunk['path']} · {scope}\n{chunk['text']}" if scope else chunk["text"] or " "
