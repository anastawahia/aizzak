"""Contract-match test — ``docs/design/02-port-contracts.md`` §2 vs the code.

``02-port-contracts.md`` is BINDING: the retrieval plan cites "02 §2" as the
contract ``KnowledgeRetrieval`` implements, and ``ports/inbound.py``'s own
module docstring cites it back. Nothing read it, so it drifted — by the time
the branch review caught it (§4) the document still showed ONE method with a
required ``k``, while the port had grown three, an optional ``k``, seven
``RetrievedChunk`` fields instead of four, and two companion types.

``openapi.yaml`` did not drift over the same commits, and the reason is
``test_retrieved_chunk_contract.py`` (plus ``test_api_conventions.py``): the
wire shape had a guard and the internal contract did not. This is that guard
for the internal one.

Signatures, not just names. A document that listed the three method names but
kept ``k: int`` would be exactly as wrong as the one the review found, so
every parameter is compared: name, order, keyword-only-ness, whether it has a
default, and — where the document annotates it — the annotation itself. The
document deliberately writes ``ctx`` bare (it is ``ExecutionContext``
everywhere in §2 and says so in prose), so an OMITTED annotation is accepted;
a WRONG one is not.

Prose, comments and Arabic commentary around the blocks are untouched by all
of this: only the four declarations §4 names are parsed, and a comment beside
a field or a paragraph rewritten above it changes nothing here.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path
from typing import Any

from app.modules.knowledge.ports.inbound import DocumentNames, KnowledgeRetrieval, RoutedAnswer
from app.modules.knowledge.ports.retrieval import RetrievedChunk

_CONTRACT_DOC = Path(__file__).resolve().parents[2] / "docs" / "design" / "02-port-contracts.md"


def _documented_class(name: str) -> ast.ClassDef:
    """The document's declaration of ``name``, parsed.

    Sliced by indentation rather than by fence: a class line plus every line
    under it that is indented. That handles both shapes §2 writes in — a
    multi-line body (``KnowledgeRetrieval``) and the one-liner it uses for
    thin DTOs (``class DocumentNames: names: ...; total: int``).
    """
    lines = _CONTRACT_DOC.read_text(encoding="utf-8").splitlines()
    headers = [
        i for i, line in enumerate(lines) if line.startswith((f"class {name}:", f"class {name}("))
    ]
    assert len(headers) == 1, (
        f"expected exactly one `class {name}` in {_CONTRACT_DOC.name}: {headers}"
    )

    start = headers[0]
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.startswith((" ", "\t")):
            break
        block.append(line)

    parsed = ast.parse("\n".join(block)).body[0]
    assert isinstance(parsed, ast.ClassDef)
    return parsed


def _documented_methods(node: ast.ClassDef) -> dict[str, ast.AsyncFunctionDef]:
    return {s.name: s for s in node.body if isinstance(s, ast.AsyncFunctionDef)}


def _documented_fields(node: ast.ClassDef) -> list[tuple[str, str, bool]]:
    """``(name, annotation, has_default)`` for every documented field."""
    fields: list[tuple[str, str, bool]] = []
    for statement in node.body:
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            fields.append(
                (
                    statement.target.id,
                    ast.unparse(statement.annotation),
                    statement.value is not None,
                )
            )
    return fields


def _documented_params(fn: ast.AsyncFunctionDef) -> list[tuple[str, str | None, bool, bool]]:
    """``(name, annotation | None, keyword_only, has_default)``, ``self`` dropped."""
    args = fn.args
    positional = args.posonlyargs + args.args
    defaulted = {a.arg for a in positional[len(positional) - len(args.defaults) :]}
    params: list[tuple[str, str | None, bool, bool]] = [
        (
            a.arg,
            None if a.annotation is None else ast.unparse(a.annotation),
            False,
            a.arg in defaulted,
        )
        for a in positional
        if a.arg != "self"
    ]
    params += [
        (a.arg, None if a.annotation is None else ast.unparse(a.annotation), True, d is not None)
        for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True)
    ]
    return params


def _port_params(func: Any) -> list[tuple[str, str, bool, bool]]:
    """The same tuple read off the real Protocol method by reflection."""
    return [
        (
            p.name,
            str(p.annotation),
            p.kind is inspect.Parameter.KEYWORD_ONLY,
            p.default is not inspect.Parameter.empty,
        )
        for p in inspect.signature(func).parameters.values()
        if p.name != "self"
    ]


def test_documented_knowledge_retrieval_methods_match_the_port() -> None:
    documented = set(_documented_methods(_documented_class("KnowledgeRetrieval")))
    implemented = {
        name
        for name, member in vars(KnowledgeRetrieval).items()
        if inspect.isfunction(member) and not name.startswith("_")
    }

    assert documented == implemented
    # Pinned explicitly (not just mutual equality) so a rename applied to BOTH
    # the port and the document still has to be noticed by a human.
    assert documented == {"retrieve", "answer", "list_document_names"}


def test_documented_knowledge_retrieval_signatures_match_the_port() -> None:
    """Parameter for parameter — the check that would have failed on ``k: int``."""
    documented = _documented_methods(_documented_class("KnowledgeRetrieval"))

    for name, fn in documented.items():
        port_method = getattr(KnowledgeRetrieval, name)
        port = _port_params(port_method)
        doc = _documented_params(fn)

        assert [(n, kw, dflt) for n, _, kw, dflt in doc] == [
            (n, kw, dflt) for n, _, kw, dflt in port
        ], f"`{name}` parameters drifted from the port"

        for (_, doc_annotation, _, _), (param, port_annotation, _, _) in zip(
            doc, port, strict=True
        ):
            if doc_annotation is not None:
                assert doc_annotation == port_annotation, f"`{name}({param}: ...)` drifted"

        documented_return = None if fn.returns is None else ast.unparse(fn.returns)
        assert documented_return == str(inspect.signature(port_method).return_annotation)


def test_documented_retrieved_chunk_fields_match_the_port_dataclass() -> None:
    """§4's other half: the document showed four citation-less fields, the
    dataclass has seven (``P-18``). Types and defaults included, because
    ``file_name: str`` would be a different promise from ``str | None``."""
    documented = _documented_fields(_documented_class("RetrievedChunk"))
    implemented = [
        (f.name, str(f.type), f.default is not dataclasses.MISSING)
        for f in dataclasses.fields(RetrievedChunk)
    ]

    assert documented == implemented
    assert [name for name, _, _ in documented] == [
        "document_id",
        "chunk_id",
        "text",
        "score",
        "file_name",
        "page_number",
        "section",
    ]


def test_documented_companion_types_match_the_port_dataclasses() -> None:
    """``RoutedAnswer``/``DocumentNames`` — the two types ``answer`` and
    ``list_document_names`` return, and the half of §4 a method-name check
    alone would miss entirely."""
    for name, dataclass_type in (("DocumentNames", DocumentNames), ("RoutedAnswer", RoutedAnswer)):
        documented = _documented_fields(_documented_class(name))
        implemented = [
            (f.name, str(f.type), f.default is not dataclasses.MISSING)
            for f in dataclasses.fields(dataclass_type)
        ]
        assert documented == implemented, f"`{name}` drifted from {dataclass_type.__module__}"
