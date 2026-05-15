"""LLM-callable tools for drafting and managing report documents."""

from __future__ import annotations

import json
import re
from pathlib import Path

from research_manager.context import get_workspace
from research_manager.tools import external_access
from research_manager.tools.registry import tool

_REPORT_KINDS = {"article", "blog", "book"}
_POLISH_FOCUS = {"abstract", "introduction", "methods", "results", "discussion", "conclusion", "title", "general"}
_CITATION_SCOPE = {"nature_only", "nature_family", "cns", "cns_family", "any_journal"}
_TEXT_EXT = {".md", ".txt", ".rst", ".tex", ".csv", ".tsv", ".log", ".json", ".yaml", ".yml", ".py", ".r", ".R", ".sh"}
_EXTERNAL_TEXT_EXT = _TEXT_EXT | {
    ".ipynb", ".html", ".xml", ".toml", ".cfg", ".ini", ".conf",
    ".bib", ".lock", ".env", "", ".dockerfile", ".gitignore",
}


def _safe_under(workspace: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(workspace.resolve())
        return True
    except ValueError:
        return False


@tool(name="list_results", category="writing")
def list_results(subdir: str) -> str:
    """List files in `res/` (optionally a subdirectory like `fig` or `txt`).

    Args:
        subdir: Subdirectory under `res/` (e.g. "fig", "txt"). Pass empty string for the whole `res/` tree.
    """
    ws = get_workspace()
    base = ws / "res" / subdir if subdir else ws / "res"
    if not base.exists():
        return json.dumps({"error": f"{base.relative_to(ws)} does not exist"}, ensure_ascii=False)
    entries = []
    for p in sorted(base.rglob("*")):
        if p.is_file():
            entries.append({
                "path": str(p.relative_to(ws)),
                "size_bytes": p.stat().st_size,
            })
    return json.dumps({"workspace": str(ws), "count": len(entries), "files": entries[:500]}, ensure_ascii=False)


@tool(name="read_text_file", category="writing")
def read_text_file(path: str, max_chars: int) -> str:
    """Read a text file from the workspace.

    Args:
        path: File path relative to the workspace (or absolute, must still be inside workspace).
        max_chars: Maximum characters to return (truncated with notice if exceeded).
    """
    ws = get_workspace()
    target = (ws / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    if not _safe_under(ws, target):
        return json.dumps({"error": "path escapes workspace"}, ensure_ascii=False)
    if not target.exists():
        return json.dumps({"error": f"file not found: {path}"}, ensure_ascii=False)
    if target.suffix.lower() not in _TEXT_EXT and target.suffix != "":
        return json.dumps({"error": f"refusing to read non-text extension {target.suffix}"}, ensure_ascii=False)
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    return json.dumps({"path": str(target.relative_to(ws)), "content": text, "truncated": truncated}, ensure_ascii=False)


@tool(name="read_external_file", category="writing")
def read_external_file(path: str, max_chars: int) -> str:
    """Read a text file from outside the workspace.

    The path must lie under a directory that has been pre-approved by the user
    (via the `RM_EXTERNAL_READ_PATHS` env var or the REPL `/allow <dir>` command).
    If the path is not approved, this tool returns an error — instruct the user
    to run `/allow <directory>` and then retry.

    Args:
        path: Absolute or user-relative (`~/...`) path to a text file outside the workspace.
        max_chars: Maximum characters to return (truncated with notice if exceeded).
    """
    target = Path(path).expanduser().resolve()
    if not external_access.is_allowed(target):
        return json.dumps(
            {
                "error": "external path not in approved list",
                "path": str(target),
                "approved_dirs": external_access.allowed_dirs(),
                "hint": (
                    "Ask the user to run `/allow <directory>` in the REPL "
                    "(or set RM_EXTERNAL_READ_PATHS) before retrying."
                ),
            },
            ensure_ascii=False,
        )
    if not target.exists():
        return json.dumps({"error": f"file not found: {target}"}, ensure_ascii=False)
    if not target.is_file():
        return json.dumps({"error": f"not a file: {target}"}, ensure_ascii=False)
    if target.suffix.lower() not in _EXTERNAL_TEXT_EXT and target.name.lower() not in {"makefile", "dockerfile"}:
        return json.dumps(
            {"error": f"refusing to read non-text extension {target.suffix!r}"},
            ensure_ascii=False,
        )
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    return json.dumps(
        {"path": str(target), "content": text, "truncated": truncated},
        ensure_ascii=False,
    )


@tool(name="list_external_allowed", category="writing")
def list_external_allowed() -> str:
    """List directories the user has approved for external file reads."""
    return json.dumps({"approved_dirs": external_access.allowed_dirs()}, ensure_ascii=False)


@tool(name="write_report", category="writing")
def write_report(kind: str, filename: str, content: str) -> str:
    """Write a draft document under `report/<kind>/`.

    Args:
        kind: One of "article", "blog", "book".
        filename: File name (e.g. "draft.md"). Subdirectories under the kind are allowed.
        content: Full file contents to write (overwrites existing).
    """
    if kind not in _REPORT_KINDS:
        return json.dumps({"error": f"kind must be one of {sorted(_REPORT_KINDS)}"}, ensure_ascii=False)
    ws = get_workspace()
    target = (ws / "report" / kind / filename).resolve()
    if not _safe_under(ws, target):
        return json.dumps({"error": "path escapes workspace"}, ensure_ascii=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return json.dumps({
        "path": str(target.relative_to(ws)),
        "bytes_written": len(content.encode("utf-8")),
    }, ensure_ascii=False)


@tool(name="append_report", category="writing")
def append_report(kind: str, filename: str, content: str) -> str:
    """Append text to a draft under `report/<kind>/` (creates if missing).

    Args:
        kind: One of "article", "blog", "book".
        filename: File name.
        content: Text to append.
    """
    if kind not in _REPORT_KINDS:
        return json.dumps({"error": f"kind must be one of {sorted(_REPORT_KINDS)}"}, ensure_ascii=False)
    ws = get_workspace()
    target = (ws / "report" / kind / filename).resolve()
    if not _safe_under(ws, target):
        return json.dumps({"error": "path escapes workspace"}, ensure_ascii=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(content)
    return json.dumps({"path": str(target.relative_to(ws)), "bytes_appended": len(content.encode("utf-8"))}, ensure_ascii=False)


_POLISH_RULES = {
    "general": [
        "Language serves the argument; if reasoning is broken, fix logic before wording.",
        "Avoid em dashes; prefer commas, parentheses, or full stops.",
        "Calibrate verbs: show/demonstrate (strong), suggest/indicate (medium), may/could (weak).",
        "Remove unsupported novelty and universal claims (\"the first\", \"completely\", \"all\").",
        "One paragraph, one message. First sentence states the message.",
        "Prefer specific nouns over hedged generalities.",
    ],
    "abstract": [
        "Pattern: context/problem -> gap -> approach -> key result -> implication -> boundary.",
        "150-250 words. No citations. No abbreviations on first use without expansion.",
        "Result sentence must carry a concrete number, comparison, or scope.",
    ],
    "introduction": [
        "Hourglass open: broad relevance -> specific gap -> task framing -> technical challenge -> contribution -> teaser.",
        "End with an explicit contribution list or a single-sentence summary of advance.",
        "Do not preview every result; leave room for Results to do its own work.",
    ],
    "methods": [
        "Module-by-module: motivation -> technical move -> implementation detail -> why this over alternatives.",
        "Reproducibility-first: name versions, parameters, seeds, hardware where load-bearing.",
        "No marketing language; the reader is evaluating, not being sold to.",
    ],
    "results": [
        "Lead each paragraph with a conclusion sentence; figures/statistics support, not lead.",
        "Order: main effect -> comparisons/baselines -> ablations -> robustness/edge cases.",
        "Every quantitative claim attaches to a figure, table, or source-data file.",
    ],
    "discussion": [
        "Hourglass close: interpret -> connect back to gap -> implication -> limitation -> future work.",
        "Distinguish what the evidence shows from what it suggests.",
        "Limitations should be specific (scope, sample, modality), not ritual.",
    ],
    "conclusion": [
        "Bounded restatement: contribution + evidence + impact + limitation, in that order.",
        "No new results, no new citations.",
    ],
    "title": [
        "Concrete subject + concrete advance. Avoid generic verbs (`A study of`, `Towards`).",
        "Front-load the noun phrase that names the contribution.",
    ],
}


@tool(name="polish_text", category="writing")
def polish_text(text: str, focus: str) -> str:
    """Produce a Nature-style polishing brief for an article-mode passage.

    ARTICLE MODE ONLY. This tool is designed for `report/article/` drafts. Do not
    use it for blog or book content — the register and conventions differ. The
    tool itself does not rewrite the text; it returns the input plus a focus-specific
    rules + checklist payload that you, the assistant, must consume in your next
    message to produce the polished version.

    Args:
        text: The passage to polish. Should be a coherent paragraph or short section.
        focus: One of "abstract", "introduction", "methods", "results", "discussion",
            "conclusion", "title", or "general". Selects the rule set applied.
    """
    if focus not in _POLISH_FOCUS:
        return json.dumps(
            {"error": f"focus must be one of {sorted(_POLISH_FOCUS)}"},
            ensure_ascii=False,
        )
    if not text.strip():
        return json.dumps({"error": "text is empty"}, ensure_ascii=False)
    rules = list(_POLISH_RULES["general"])
    if focus != "general":
        rules = _POLISH_RULES[focus] + rules
    checklist = [
        "Does the first sentence state the paragraph's message?",
        "Is every claim either supported by a cited artifact or hedged appropriately?",
        "Are there em dashes, universal claims, or unsupported novelty to remove?",
        "Does the verb register match the strength of the evidence?",
        "Is the sentence-to-sentence relation explicit (cause / contrast / example)?",
    ]
    return json.dumps(
        {
            "mode": "article",
            "focus": focus,
            "input_text": text,
            "rules": rules,
            "self_check": checklist,
            "instruction": (
                "Rewrite `input_text` applying the rules. Preserve all author "
                "evidence and citations. Return the polished prose followed by a "
                "short note listing any unsupported claims you weakened or flagged."
            ),
        },
        ensure_ascii=False,
    )


_CLAIM_VERBS = re.compile(
    r"\b(show|shows|showed|demonstrate|demonstrates|demonstrated|find|finds|found|"
    r"observe|observes|observed|report|reports|reported|prove|proves|proved|"
    r"establish|establishes|established|reveal|reveals|revealed|outperform|"
    r"outperforms|outperformed|improve|improves|improved|reduce|reduces|reduced|"
    r"increase|increases|increased|achieve|achieves|achieved)\b",
    re.IGNORECASE,
)
_NUMBER_TOKEN = re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|×|x|fold|σ|sigma)?\b")


def _segment_sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text.strip())
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z一-鿿])", cleaned)
    return [p.strip() for p in parts if p.strip()]


def _claim_grade(sentence: str) -> str:
    has_verb = bool(_CLAIM_VERBS.search(sentence))
    has_number = bool(_NUMBER_TOKEN.search(sentence))
    if has_verb and has_number:
        return "primary_claim"
    if has_verb:
        return "qualitative_claim"
    if has_number:
        return "quantitative_statement"
    return "background_or_transition"


_CITATION_SCOPE_HELP = {
    "nature_only": "Restrict to Nature flagship only.",
    "nature_family": "Nature Portfolio: Nature, Nature [field], Nature Communications, Communications [field], Scientific Reports, npj titles.",
    "cns": "Flagships only: Cell, Nature, Science.",
    "cns_family": "Cell Press, Nature Portfolio, AAAS Science family (flagships + accepted subjournals).",
    "any_journal": "No publisher restriction; still prefer reputable peer-reviewed sources.",
}


@tool(name="add_citations", category="writing")
def add_citations(text: str, scope: str) -> str:
    """Segment article-mode prose into citable claims and produce a citation worksheet.

    ARTICLE MODE ONLY. The tool does not search the web; it deterministically
    segments the passage, grades each sentence's citation need, and emits an
    English search query per primary claim plus the scope rules from the
    nature-citation playbook. You, the assistant, then use this worksheet to
    propose citations using your own knowledge or a separate search tool, and
    must verify any DOI/title before inserting it into the draft.

    Args:
        text: The passage to annotate. Typically introduction or discussion prose.
        scope: One of "nature_only", "nature_family", "cns", "cns_family",
            "any_journal". Controls the journal whitelist hint emitted per segment.
    """
    if scope not in _CITATION_SCOPE:
        return json.dumps(
            {"error": f"scope must be one of {sorted(_CITATION_SCOPE)}"},
            ensure_ascii=False,
        )
    sentences = _segment_sentences(text)
    if not sentences:
        return json.dumps({"error": "text is empty after segmentation"}, ensure_ascii=False)
    segments = []
    for idx, sent in enumerate(sentences, start=1):
        grade = _claim_grade(sent)
        segments.append({
            "id": f"S{idx:03d}",
            "text": sent,
            "claim_grade": grade,
            "needs_citation": grade in {"primary_claim", "qualitative_claim", "quantitative_statement"},
            "search_query_hint": sent if grade != "background_or_transition" else "",
        })
    return json.dumps(
        {
            "mode": "article",
            "scope": scope,
            "scope_rule": _CITATION_SCOPE_HELP[scope],
            "segments": segments,
            "guidance": [
                "Do not insert a citation merely because a title is topically related.",
                "Grade each candidate: strong_support / partial_support / background_only / not_supporting.",
                "Verify DOI and journal-family membership before adding to the draft.",
                "Prefer structured metadata (Crossref / PubMed / publisher pages) over Google Scholar snippets.",
                "If no in-scope citation exists for a claim, weaken the claim rather than citing out-of-scope.",
            ],
        },
        ensure_ascii=False,
    )


@tool(name="data_availability", category="writing")
def data_availability(notes: str, journal: str) -> str:
    """Draft a Nature-style Data Availability statement scaffold from author notes.

    ARTICLE MODE ONLY. Returns a structured scaffold plus the FAIR/Nature-policy
    checklist; you, the assistant, fill in the scaffold from `notes` and from
    artifacts in the workspace, then write the final statement (typically into
    `report/article/data_availability.md` via `write_report`).

    The tool will not invent DOIs, accession numbers, or repository names. If
    the notes do not specify them, the scaffold preserves explicit `[TODO: ...]`
    placeholders so missing fields are visible to the user.

    Args:
        notes: Free-form author notes describing datasets, repositories, access
            constraints, third-party data, source data for figures, etc. Chinese
            input is acceptable; the final statement should be drafted in English
            unless the user asks otherwise.
        journal: Target journal (e.g. "Nature", "Nature Communications",
            "Science"). Pass empty string if unknown.
    """
    if not notes.strip():
        return json.dumps({"error": "notes is empty"}, ensure_ascii=False)
    classes = [
        "public repository (DOI / accession)",
        "controlled access repository",
        "within paper or supplementary information",
        "reused public source (cite original)",
        "third-party restricted (named owner, access route)",
        "available on justified request (specify reason + reviewer)",
        "not applicable",
    ]
    checklist = [
        "Every main-figure and supplementary-figure dataset has an access route.",
        "Source data for quantitative panels is named explicitly.",
        "Reused third-party data has its original citation, not just a URL.",
        "`available upon request` is only used with a specific legal/ethical/commercial reason.",
        "Restricted data names the controller and the request-evaluation process.",
        "No invented DOIs, accession numbers, or repository names — placeholders only.",
        "Code, materials, and protocols are kept separate from Data unless the journal merges them.",
    ]
    scaffold = (
        "Data Availability\n\n"
        "The data that support the findings of this study are described below.\n\n"
        "Newly generated data:\n"
        "- [TODO: dataset name] is available at [TODO: repository] under accession "
        "[TODO: accession / DOI].\n\n"
        "Source data for figures:\n"
        "- Source data for Figures [TODO: list] are provided with this paper.\n\n"
        "Reused third-party data:\n"
        "- [TODO: dataset name] was obtained from [TODO: repository / publication] "
        "(ref. [TODO: citation]).\n\n"
        "Restricted data (if applicable):\n"
        "- [TODO: dataset] cannot be shared publicly because [TODO: reason]. Access "
        "requests should be directed to [TODO: controller], who will respond within "
        "[TODO: timeframe].\n"
    )
    return json.dumps(
        {
            "mode": "article",
            "journal": journal or "unspecified",
            "author_notes": notes,
            "access_classes": classes,
            "scaffold": scaffold,
            "fair_checklist": checklist,
            "instruction": (
                "Fill the scaffold from `author_notes` and any workspace artifacts. "
                "Preserve `[TODO: ...]` markers for fields the author has not "
                "supplied; do not invent identifiers. Output the final statement in "
                "English unless the user asked for Chinese."
            ),
        },
        ensure_ascii=False,
    )


@tool(name="list_reports", category="writing")
def list_reports(kind: str) -> str:
    """List existing drafts under `report/<kind>/`.

    Args:
        kind: One of "article", "blog", "book". Pass empty string to list all kinds.
    """
    ws = get_workspace()
    kinds = [kind] if kind else sorted(_REPORT_KINDS)
    if kind and kind not in _REPORT_KINDS:
        return json.dumps({"error": f"kind must be one of {sorted(_REPORT_KINDS)} or empty"}, ensure_ascii=False)
    out = {}
    for k in kinds:
        base = ws / "report" / k
        if not base.exists():
            out[k] = []
            continue
        out[k] = [str(p.relative_to(ws)) for p in sorted(base.rglob("*")) if p.is_file()]
    return json.dumps({"workspace": str(ws), "reports": out}, ensure_ascii=False)
