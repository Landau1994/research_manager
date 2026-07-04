"""LLM-callable tools for authoring, executing, and persisting project-specific scripts.

Workflow:
1. `propose_script` writes a snippet to `res/_proposals/`, optionally executes it,
   and returns the result tagged with a `proposal_id`.
2. The REPL intercepts the tool call and asks the user whether to promote the
   snippet to `code/<language>/`. The user's choice is enacted via `save_proposed_script`.
3. `revise_script` rewrites an existing script, backing up the previous version
   into `res/_proposals/`.
4. `run_saved_script` invokes a previously saved script by name.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from research_manager.context import get_workspace
from research_manager.executor.runner import ScriptRunner
from research_manager.tools.registry import tool

_PROPOSALS_SUBDIR = "res/_proposals"
_LANG_EXT = {"python": ".py", "py": ".py", "r": ".R", "R": ".R", "shell": ".sh", "sh": ".sh"}
_LANG_NORMALIZE = {"py": "python", "python": "python", "r": "r", "R": "r", "sh": "shell", "shell": "shell"}
_LANG_CODE_DIR = {"python": "code/python", "r": "code/r", "shell": "code/bash"}


def _runner() -> ScriptRunner:
    timeout = int(os.environ.get("RM_TOOL_TIMEOUT", "300"))
    return ScriptRunner(workspace=get_workspace(), default_timeout=timeout)


def _proposals_dir() -> Path:
    d = get_workspace() / _PROPOSALS_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_proposal_id(name: str, code: str) -> str:
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()[:8]
    ts = int(time.time())
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return f"{safe_name}_{ts}_{digest}"


def _safe_under(workspace: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(workspace.resolve())
        return True
    except ValueError:
        return False


def _script_search_dirs(language: str | None = None) -> list[Path]:
    ws = get_workspace()
    dirs: list[Path] = []
    if language:
        code_dir = _LANG_CODE_DIR.get(language)
        if code_dir:
            dirs.append(ws / code_dir)
    else:
        dirs.extend(ws / d for d in _LANG_CODE_DIR.values())
    dirs.append(ws / "script")
    return dirs


def _target_dir_for_language(language: str) -> Path:
    return get_workspace() / _LANG_CODE_DIR[language]


def _resolve_saved_script(name: str, language: str | None = None) -> Path | None:
    ws = get_workspace()
    p = Path(name)
    candidates: list[Path] = []
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(ws / p)
        for base in _script_search_dirs(language):
            candidates.append(base / p)
            if not p.suffix:
                for ext in (".py", ".R", ".r", ".sh"):
                    candidates.append(base / f"{name}{ext}")
    for cand in candidates:
        if cand.exists() and _safe_under(ws, cand):
            return cand
    return None


def _execute_proposal(path: Path, language: str, conda_env: str, timeout: int) -> dict:
    r = _runner()
    if language == "python":
        result = r.run_python(script=str(path), env=conda_env or None, timeout=timeout)
    elif language == "r":
        result = r.run_r(script=str(path), env=conda_env or None, timeout=timeout)
    else:
        result = r.run_shell(command=f"bash {path}", env=conda_env or None, timeout=timeout)
    return result.summary()


@tool(name="propose_script", category="dynamic")
def propose_script_tool(
    name: str,
    language: str,
    code: str,
    description: str,
    run: bool,
    conda_env: str,
    timeout: int,
) -> str:
    """Propose a project-specific script. Writes to res/_proposals/ and (optionally) executes it.

    The user will be prompted in the REPL to decide whether to save this proposal
    into `code/python`, `code/r`, or `code/bash` based on language. Do not save anywhere else; let the REPL handle promotion.

    Args:
        name: Logical name without extension (e.g. "normalize_qc"). Used for the final filename.
        language: One of "python", "r", "shell".
        code: Full source text of the script.
        description: One-sentence purpose of the script.
        run: If true, execute the proposal immediately so the user can review its result.
        conda_env: Conda environment name. Pass empty string to use the default environment.
        timeout: Wall-clock execution timeout in seconds.
    """
    lang = _LANG_NORMALIZE.get(language.lower())
    if lang is None:
        return json.dumps(
            {"error": f"language must be one of python/r/shell, got {language!r}"},
            ensure_ascii=False,
        )

    ext = _LANG_EXT[lang]
    proposal_id = _make_proposal_id(name, code)
    proposal_path = _proposals_dir() / f"{proposal_id}{ext}"
    proposal_path.write_text(code, encoding="utf-8")

    meta_path = proposal_path.with_suffix(proposal_path.suffix + ".json")
    meta = {
        "proposal_id": proposal_id,
        "name": name,
        "language": lang,
        "description": description,
        "created_at": time.time(),
        "proposal_path": str(proposal_path.relative_to(get_workspace())),
        "target_filename": f"{name}{ext}",
        "target_dir": _LANG_CODE_DIR[lang],
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    response: dict = {
        "proposal_id": proposal_id,
        "proposal_path": meta["proposal_path"],
        "target_filename": meta["target_filename"],
        "target_dir": meta["target_dir"],
        "language": lang,
        "needs_user_confirmation": True,
        "hint": "Ask the user (via the REPL prompt) whether to save this proposal to code/<language>/.",
    }
    if run:
        response["execution"] = _execute_proposal(
            proposal_path, lang, conda_env, timeout
        )
    return json.dumps(response, ensure_ascii=False)


@tool(name="save_proposed_script", category="dynamic")
def save_proposed_script_tool(
    proposal_id: str,
    target_name: str,
    overwrite: bool,
) -> str:
    """Promote a previously proposed script from res/_proposals/ to code/<language>/.

    Args:
        proposal_id: The id returned by propose_script.
        target_name: Final filename (with or without extension). Pass empty string to use the proposed name.
        overwrite: If true, overwrite an existing code/<language>/<target_name> instead of refusing.
    """
    ws = get_workspace()
    proposals = _proposals_dir()

    matches = list(proposals.glob(f"{proposal_id}.*"))
    matches = [m for m in matches if not m.name.endswith(".json")]
    if not matches:
        return json.dumps({"error": f"no proposal found with id {proposal_id}"}, ensure_ascii=False)
    src = matches[0]

    meta_path = src.with_suffix(src.suffix + ".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    final_name = target_name.strip() or meta.get("target_filename") or src.name
    if not Path(final_name).suffix:
        final_name = final_name + src.suffix

    lang = meta.get("language", "")
    if lang not in _LANG_CODE_DIR:
        return json.dumps({"error": f"unknown proposal language: {lang!r}"}, ensure_ascii=False)

    target_root = _target_dir_for_language(lang)
    dest = target_root / final_name
    if not _safe_under(target_root, dest):
        return json.dumps({"error": "target path escapes language code directory"}, ensure_ascii=False)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and not overwrite:
        return json.dumps(
            {"error": f"{dest.relative_to(ws)} already exists; pass overwrite=true to replace"},
            ensure_ascii=False,
        )

    if dest.exists() and overwrite:
        backup = proposals / f"{dest.stem}_backup_{int(time.time())}{dest.suffix}"
        shutil.copy2(dest, backup)

    shutil.copy2(src, dest)
    src.unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)

    return json.dumps(
        {
            "ok": True,
            "saved_to": str(dest.relative_to(ws)),
            "language": meta.get("language", ""),
        },
        ensure_ascii=False,
    )


@tool(name="revise_script", category="dynamic")
def revise_script_tool(
    name: str,
    new_code: str,
    description: str,
    run: bool,
    conda_env: str,
    timeout: int,
) -> str:
    """Rewrite an existing script under code/ or legacy script/. The previous version is backed up to res/_proposals/.

    Args:
        name: Filename of the existing script (with or without extension), e.g. "normalize_qc.py".
        new_code: Full replacement source.
        description: One-sentence explanation of what changed and why.
        run: If true, execute the revised script immediately.
        conda_env: Conda environment name. Pass empty string to use the default environment.
        timeout: Wall-clock execution timeout in seconds.
    """
    ws = get_workspace()
    target = _resolve_saved_script(name)
    if target is None:
        return json.dumps({"error": f"saved script {name!r} does not exist"}, ensure_ascii=False)
    if not _safe_under(ws, target):
        return json.dumps({"error": "target path escapes workspace"}, ensure_ascii=False)

    ext = target.suffix.lower()
    lang = {".py": "python", ".r": "r", ".sh": "shell"}.get(ext)
    if lang is None:
        return json.dumps({"error": f"unsupported script extension {ext!r}"}, ensure_ascii=False)

    proposals = _proposals_dir()
    backup = proposals / f"{target.stem}_rev_{int(time.time())}{target.suffix}"
    shutil.copy2(target, backup)

    target.write_text(new_code, encoding="utf-8")

    response: dict = {
        "ok": True,
        "revised": str(target.relative_to(ws)),
        "backup": str(backup.relative_to(ws)),
        "description": description,
    }
    if run:
        response["execution"] = _execute_proposal(target, lang, conda_env, timeout)
    return json.dumps(response, ensure_ascii=False)


@tool(name="run_saved_script", category="dynamic")
def run_saved_script_tool(
    name: str,
    conda_env: str,
    timeout: int,
) -> str:
    """Run a previously saved script under code/ or legacy script/ by filename.

    Args:
        name: Filename under code/<language>/ or legacy script/ (with or without extension).
        conda_env: Conda environment name. Pass empty string to use the default environment.
        timeout: Wall-clock execution timeout in seconds.
    """
    ws = get_workspace()
    target = _resolve_saved_script(name)
    if target is None:
        return json.dumps({"error": f"saved script {name!r} does not exist"}, ensure_ascii=False)

    ext = target.suffix.lower()
    lang = {".py": "python", ".r": "r", ".sh": "shell"}.get(ext)
    if lang is None:
        return json.dumps({"error": f"unsupported script extension {ext!r}"}, ensure_ascii=False)

    result = _execute_proposal(target, lang, conda_env, timeout)
    return json.dumps({"script": str(target.relative_to(ws)), **result}, ensure_ascii=False)


@tool(name="list_proposals", category="dynamic")
def list_proposals_tool() -> str:
    """List all pending (unpromoted) script proposals under res/_proposals/."""
    proposals = _proposals_dir()
    items = []
    for meta_file in sorted(proposals.glob("*.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items.append({
            "proposal_id": meta.get("proposal_id"),
            "name": meta.get("name"),
            "language": meta.get("language"),
            "description": meta.get("description"),
            "target_filename": meta.get("target_filename"),
            "target_dir": meta.get("target_dir"),
        })
    return json.dumps({"proposals": items}, ensure_ascii=False)
