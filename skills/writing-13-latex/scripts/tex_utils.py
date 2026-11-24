#!/usr/bin/env python3
"""Cross-platform TeX utilities: atomic LF I/O, SHA-256, tool probing, subprocess runner."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


def write_text_lf(path: Path, text: str) -> None:
    """Atomically write UTF-8 text with LF newlines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(norm)
        os.replace(tmp_name, str(path))
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def write_json_atomic(path: Path, data: Any) -> None:
    """Atomically write pretty JSON with LF newlines."""
    write_text_lf(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_tool_version(tool_name: str) -> Dict[str, Any]:
    """Check whether a TeX binary exists on PATH and read its version banner."""
    exe = shutil.which(tool_name)
    if not exe:
        return {"tool": tool_name, "available": False, "path": None, "version": "not_installed"}
    try:
        proc = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        first_line = (proc.stdout or proc.stderr or "").splitlines()[0].strip() if (proc.stdout or proc.stderr) else "unknown"
        return {"tool": tool_name, "available": True, "path": exe, "version": first_line}
    except Exception as exc:
        return {"tool": tool_name, "available": True, "path": exe, "version": f"probe_error: {exc}"}


def run_cmd(cmd: List[str], cwd: Path, timeout_s: int = 60) -> Dict[str, Any]:
    """Run a command inside cwd with strict timeout so TeX builds never hang."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
        return {
            "cmd": " ".join(cmd),
            "returncode": proc.returncode,
            "timed_out": False,
            "stdout_tail": "\n".join((proc.stdout or "").splitlines()[-40:]),
            "stderr_tail": "\n".join((proc.stderr or "").splitlines()[-20:]),
        }
    except subprocess.TimeoutExpired:
        return {
            "cmd": " ".join(cmd),
            "returncode": -1,
            "timed_out": True,
            "stdout_tail": "",
            "stderr_tail": f"Command timed out after {timeout_s}s.",
        }
