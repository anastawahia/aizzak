"""Characterize drawings from ele-parse inspection files, off the stack (FR-EVAL-14, INC1-AC-13).

    python -m app.ops.electrical_characterize --inspections <dir> --out <file.json> \\
        [--markdown <file.md>] [--with-names]

Each ``<dir>/*.json`` is one ``python -m services.ele_parse inspect <file.pdf>`` output
(``{"schema": 1, "summary": ..., "pages": [...]}``). A file is identified by the SHA-256 of the PDF
it came from: a top-level ``"sha256"`` key if the file has one, else the file's stem when that is
64 lower-case hexadecimal characters (``sha256sum x.pdf`` names it). Anything else is refused,
because a characterization that cannot say which bytes it describes cannot be matched to the
labels M-32 needs.

The tool runs THE SAME pure functions the worker runs in STG-01 (``classify_page``,
``IdentifierParser.tally``, ``detect_profile``, ``characterize``) and then ``aggregate``, over the
DEFAULT profile and the packaged rules; there is no second pipeline that could drift. It touches
no database and no network, and reads nothing but the inspection files. For a given file the
record is the one the worker stores, except for ``document_id``, which here is the SHA-256 (the
worker's is a UUID) -- ``tests/unit/test_ops_electrical_characterize.py`` pins that equality.

The report names documents by SHA-256 only; ``--with-names`` adds each inspection file's name.
Everything a PDF said about itself (Creator, Producer) and every file name is untrusted text and is
written escaped (``escape_untrusted_text``, PR-B security review R1-4): the output is safe to open
in a terminal or a Markdown viewer. No identifier text is ever written, only counts.

The first run of this tool on the owner's samples is outside INC-1 (LLD section 1.4).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final

from app.framework.observability.untrusted import escape_untrusted_text
from app.modules.electrical.drawings.adapters.ele_parse_client import (
    SCHEMA,
    signals_from_json,
    summary_from_json,
)
from app.modules.electrical.drawings.adapters.rule_files import PackagedRuleSource
from app.modules.electrical.drawings.application.inspection import (
    clip_metadata,
    combine_tallies,
)
from app.modules.electrical.drawings.domain.characterization import (
    Characterization,
    aggregate,
    characterize,
)
from app.modules.electrical.drawings.domain.classification import classify_page
from app.modules.electrical.drawings.domain.pages import PageSignals, PdfSummary
from app.modules.electrical.drawings.domain.profile import DrawingProfile
from app.modules.electrical.drawings.domain.profile_detection import ProfileProposal, detect_profile
from app.modules.electrical.drawings.ports.rules import RuleSource
from app.modules.electrical.shared.parser import IdentifierParser

REPORT_SCHEMA: Final = 1
MAX_INSPECTION_BYTES: Final = 64 * 1024 * 1024
_SHA256: Final = re.compile(r"[0-9a-f]{64}", re.ASCII)
_UNTRUSTED_FIELDS: Final = frozenset({"creator", "producer"})


class InspectionFileError(ValueError):
    """An inspection file the tool cannot use (its message names the file, escaped)."""


@dataclass(frozen=True, slots=True)
class Inspection:
    sha256: str
    file_name: str
    summary: PdfSummary
    pages: tuple[PageSignals, ...]
    content_hash: str  # of the inspection file's own bytes, for the inputs fingerprint


def load_inspection(path: Path) -> Inspection:
    """Read and validate one inspection file."""
    shown = escape_untrusted_text(path.name, limit=80)
    if path.stat().st_size > MAX_INSPECTION_BYTES:
        raise InspectionFileError(f"{shown}: larger than {MAX_INSPECTION_BYTES} bytes")
    raw = path.read_bytes()
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise InspectionFileError(f"{shown}: not an ele-parse inspection (schema {SCHEMA})")
        summary = summary_from_json(data["summary"])
        pages = tuple(
            sorted((signals_from_json(p) for p in data["pages"]), key=lambda s: s.page_no)
        )
    except InspectionFileError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise InspectionFileError(f"{shown}: unreadable inspection ({type(exc).__name__})") from exc
    sha256 = data.get("sha256", path.stem)
    if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
        raise InspectionFileError(
            f"{shown}: name the file <sha256 of the PDF>.json or add a top-level 'sha256' key"
        )
    return Inspection(
        sha256=sha256,
        file_name=path.name,
        summary=summary,
        pages=pages,
        content_hash=hashlib.sha256(raw).hexdigest(),
    )


def load_inspections(directory: Path) -> tuple[Inspection, ...]:
    """Every ``*.json`` of the directory, by file name; a repeated SHA-256 is refused."""
    found = tuple(load_inspection(p) for p in sorted(directory.glob("*.json")) if p.is_file())
    seen: set[str] = set()
    for item in found:
        if item.sha256 in seen:
            raise InspectionFileError(f"two inspection files describe the PDF {item.sha256}")
        seen.add(item.sha256)
    return found


@dataclass(frozen=True, slots=True)
class Analysis:
    """What STG-01 derives from one document: its record and the profile it proposes."""

    record: Characterization
    proposal: ProfileProposal


def analyse_inspection(
    item: Inspection, *, rules: RuleSource, parser: IdentifierParser | None = None
) -> Analysis:
    """What STG-01 computes for the document, over the default profile (see the module doc)."""
    parser = parser or IdentifierParser(rules.identifier_rules())
    base = DrawingProfile.default()
    summary = PdfSummary(
        page_count=item.summary.page_count,
        pdf_version="",
        encrypted=False,
        creator=clip_metadata(item.summary.creator),
        producer=clip_metadata(item.summary.producer),
        outline_count=0,
    )
    classifications = [classify_page(s, base.classification) for s in item.pages]
    tally = combine_tallies(parser.tally(s.tokens) for s in item.pages)
    producers = rules.producer_signatures()
    proposal = detect_profile(
        base=base, summary=summary, pages=classifications, tally=tally, producers=producers
    )
    record = characterize(
        item.sha256,
        item.sha256,
        summary,
        classifications,
        list(item.pages),
        tally,
        proposal,
        producers,
        rules_version=rules.identifier_rules().version,
    )
    return Analysis(record=record, proposal=proposal)


def characterize_inspection(
    item: Inspection, *, rules: RuleSource, parser: IdentifierParser | None = None
) -> Characterization:
    return analyse_inspection(item, rules=rules, parser=parser).record


def to_plain(value: Any) -> Any:
    """Dataclasses, enums and mappings as JSON-ready values with a stable key order."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {
            str(to_plain(key)): to_plain(item)
            for key, item in sorted(value.items(), key=lambda kv: str(to_plain(kv[0])))
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [to_plain(item) for item in value]
    return value


def _present(plain: dict[str, Any]) -> dict[str, Any]:
    """A record's plain form with its untrusted PDF text escaped."""
    return {
        key: (
            escape_untrusted_text(value)
            if key in _UNTRUSTED_FIELDS and isinstance(value, str)
            else value
        )
        for key, value in plain.items()
    }


def build_report(
    items: Sequence[Inspection], *, rules: RuleSource, with_names: bool = False
) -> dict[str, Any]:
    parser = IdentifierParser(rules.identifier_rules())
    records = [characterize_inspection(i, rules=rules, parser=parser) for i in items]
    documents: list[dict[str, Any]] = []
    for item, record in zip(items, records, strict=True):
        entry = _present(to_plain(record))
        if with_names:
            entry["file"] = escape_untrusted_text(item.file_name, limit=120)
        documents.append(entry)
    inputs = hashlib.sha256()
    for digest in sorted(i.content_hash for i in items):
        inputs.update(digest.encode("ascii") + b"\n")
    return {
        "schema": REPORT_SCHEMA,
        "inspections": len(items),
        "inputs_fingerprint": inputs.hexdigest(),
        "report": to_plain(aggregate(records)),
        "documents": documents,
    }


def to_json(report: Mapping[str, Any]) -> str:
    """Sorted keys, so the same inputs are the same bytes."""
    return json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _lines(title: str, counts: Mapping[str, Any]) -> list[str]:
    if not counts:
        return [f"- {title}: none"]
    return [f"- {title}: " + ", ".join(f"{k} {v}" for k, v in counts.items())]


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def render_markdown(report: Mapping[str, Any]) -> str:
    summary: Mapping[str, Any] = report["report"]
    out = [
        "# WireTwin characterization",
        "",
        f"- inspections: {report['inspections']}",
        f"- documents: {summary['documents']}, pages: {summary['pages']}",
        f"- OCR needed ratio: {summary['ocr_needed_ratio']:.4f}",
        f"- mixed-standard documents: {summary['mixed_standard_documents']}",
        *_lines("producers", summary["producers"]),
        *_lines("page kinds", summary["text_kinds"]),
        *_lines("document languages", summary["document_languages"]),
        *_lines("conventions", summary["conventions"]),
        *_lines("symbol standards", summary["symbol_standards"]),
        *_lines("PLC families", summary["plc_families"]),
        "- versions: "
        + "; ".join(f"{name} {', '.join(found)}" for name, found in summary["versions"].items()),
        f"- inputs fingerprint: {report['inputs_fingerprint']}",
        "",
        "## Documents",
        "",
        "| sha256 | pages | producer | language | convention | symbols |",
        "|---|---|---|---|---|---|",
    ]
    for doc in report["documents"]:
        conventions = doc["conventions"]
        top = max(conventions, key=lambda c: conventions[c]) if conventions else "-"
        label = _cell(doc.get("file", doc["content_sha256"][:16]))
        producer = _cell(doc["producer_family"] or doc["producer"] or "-")
        out.append(
            f"| {label} | {doc['page_count']} | {producer} | "
            f"{doc['document_language']} | {top} | {doc['symbol_standard_guess']} |"
        )
    return "\n".join(out) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.electrical_characterize",
        description="Characterize drawings from ele-parse inspection files (no DB, no network).",
    )
    parser.add_argument("--inspections", type=Path, required=True, help="folder of <sha256>.json")
    parser.add_argument("--out", type=Path, required=True, help="the JSON report to write")
    parser.add_argument("--markdown", type=Path, help="also write a Markdown summary here")
    parser.add_argument(
        "--with-names", action="store_true", help="name each document by its inspection file"
    )
    args = parser.parse_args(argv)
    if not args.inspections.is_dir():
        sys.stderr.write("--inspections is not a folder\n")
        return 2
    try:
        items = load_inspections(args.inspections)
    except InspectionFileError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    report = build_report(items, rules=PackagedRuleSource(), with_names=args.with_names)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(to_json(report), encoding="utf-8")
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(report), encoding="utf-8")
    sys.stdout.write(f"characterized {len(items)} inspection file(s) -> {args.out.name}\n")
    return 0


__all__ = [
    "Analysis",
    "Inspection",
    "InspectionFileError",
    "analyse_inspection",
    "build_report",
    "characterize_inspection",
    "load_inspection",
    "load_inspections",
    "main",
    "render_markdown",
    "to_json",
    "to_plain",
]


if __name__ == "__main__":
    sys.exit(main())
