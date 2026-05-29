"""Binary reversing tools — strings, symbols, ROP gadgets, packer detection, disassembly."""

from __future__ import annotations

import subprocess
from typing import ClassVar

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


def _run_cmd(cmd: list[str], timeout: int = 60, input_data: str | None = None) -> str:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_data,
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as exc:
        return f"Error: {exc}"


# ── Binary Info ────────────────────────────────────────────────────────────


class BinaryInfoInput(BaseModel):
    path: str = Field(description="Path to the binary file to analyze.")


class BinaryInfoTool(BaseTool):
    """Extract architecture, OS, and binary protection information from a binary."""

    name: str = "binary_info"
    description: str = (
        "Extract binary metadata: architecture, OS type, ELF/PE/Mach-O info, "
        "and security protections (NX/DEP, PIE, RELRO, stack canary). "
        "Uses file, readelf, and checksec."
    )
    args_schema: ClassVar[type[BaseModel]] = BinaryInfoInput

    def _run(self, path: str) -> str:
        import shutil
        sections: list[str] = []

        if shutil.which("file"):
            sections.append(f"[file]\n{_run_cmd(['file', path])}")

        if shutil.which("readelf"):
            elf_out = _run_cmd(["readelf", "-h", path])
            sections.append(f"[readelf -h]\n{elf_out[:1000]}")

        if shutil.which("checksec"):
            sections.append(f"[checksec]\n{_run_cmd(['checksec', '--file', path])}")
        elif shutil.which("pwn"):
            script = "from pwn import *; e=ELF('" + path + "',checksec=True)"
            sections.append(f"[pwntools checksec]\n{_run_cmd(['python3', '-c', script])}")

        if not sections:
            return "Error: none of file/readelf/checksec found. Install: apt install binutils file checksec"
        return "\n\n".join(sections)


# ── Strings Extract ────────────────────────────────────────────────────────


class StringsExtractInput(BaseModel):
    path: str = Field(description="Path to the binary file.")
    min_len: int = Field(default=8, description="Minimum string length to extract.")
    filter: str = Field(
        default="",
        description="Keyword filter — only return strings containing this substring (case-insensitive).",
    )
    limit: int = Field(default=100, description="Maximum number of strings to return.")


class StringsExtractTool(BaseTool):
    """Extract printable strings from a binary file."""

    name: str = "strings_extract"
    description: str = (
        "Extract printable strings from a binary. Useful for finding hardcoded credentials, "
        "URLs, error messages, function names, and config values. "
        "Requires strings (binutils)."
    )
    args_schema: ClassVar[type[BaseModel]] = StringsExtractInput

    def _run(self, path: str, min_len: int = 8, filter: str = "", limit: int = 100) -> str:
        import shutil
        if not shutil.which("strings"):
            return "Error: strings not found. Install: apt install binutils"
        out = _run_cmd(["strings", f"-n{min_len}", path])
        lines = out.splitlines()
        if filter:
            needle = filter.lower()
            lines = [l for l in lines if needle in l.lower()]
        total = len(lines)
        lines = lines[:limit]
        result = "\n".join(lines)
        if total > limit:
            result += f"\n\n... {total - limit} more strings (use filter to narrow)"
        return result or "(no strings found)"


# ── Symbols Extract ────────────────────────────────────────────────────────


class SymbolsExtractInput(BaseModel):
    path: str = Field(description="Path to the binary file.")
    type: str = Field(
        default="all",
        description="Symbol type: 'all', 'defined' (local symbols), 'undefined' (external deps).",
    )
    filter: str = Field(default="", description="Keyword filter for symbol names.")
    limit: int = Field(default=100, description="Maximum number of symbols to return.")


class SymbolsExtractTool(BaseTool):
    """Extract symbol table from a binary using nm or objdump."""

    name: str = "symbols_extract"
    description: str = (
        "Extract the symbol table from a binary. Reveals function names, global variables, "
        "and external library dependencies. Uses nm or objdump."
    )
    args_schema: ClassVar[type[BaseModel]] = SymbolsExtractInput

    def _run(self, path: str, type: str = "all", filter: str = "", limit: int = 100) -> str:
        import shutil
        if shutil.which("nm"):
            flags = ["-D"] if type == "undefined" else ["--defined-only"] if type == "defined" else ["-D", "--defined-only" if type == "defined" else ""]
            flags = [f for f in flags if f]
            out = _run_cmd(["nm"] + flags + [path])
        elif shutil.which("objdump"):
            out = _run_cmd(["objdump", "-t", path])
        else:
            return "Error: neither nm nor objdump found. Install: apt install binutils"

        lines = out.splitlines()
        if filter:
            needle = filter.lower()
            lines = [l for l in lines if needle in l.lower()]
        total = len(lines)
        lines = lines[:limit]
        result = "\n".join(lines)
        if total > limit:
            result += f"\n\n... {total - limit} more symbols"
        return result or "(no symbols found)"


# ── Packer Detect ──────────────────────────────────────────────────────────


class PackerDetectInput(BaseModel):
    path: str = Field(description="Path to the binary file.")


class PackerDetectTool(BaseTool):
    """Detect binary packers and protectors (UPX, MPRESS, custom)."""

    name: str = "packer_detect"
    description: str = (
        "Detect if a binary is packed or obfuscated. "
        "Checks for UPX, MPRESS, and common packer signatures. "
        "Uses die (Detect-It-Easy) if available, falls back to file + strings."
    )
    args_schema: ClassVar[type[BaseModel]] = PackerDetectInput

    def _run(self, path: str) -> str:
        import shutil
        sections: list[str] = []

        # Detect-It-Easy (preferred)
        for die_cmd in ("die", "diec"):
            if shutil.which(die_cmd):
                sections.append(f"[die]\n{_run_cmd([die_cmd, path])}")
                break

        # UPX check
        if shutil.which("upx"):
            upx_out = _run_cmd(["upx", "-l", path])
            sections.append(f"[upx -l]\n{upx_out}")

        # Fallback: file + strings grep
        if shutil.which("file"):
            sections.append(f"[file]\n{_run_cmd(['file', path])}")

        if shutil.which("strings"):
            strings_out = _run_cmd(["strings", "-n6", path])
            upx_sig = "UPX" in strings_out
            mpress_sig = "MPRESS" in strings_out
            themida_sig = "Themida" in strings_out or "WinLicense" in strings_out
            packer_hints: list[str] = []
            if upx_sig:
                packer_hints.append("UPX signature found in strings")
            if mpress_sig:
                packer_hints.append("MPRESS signature found in strings")
            if themida_sig:
                packer_hints.append("Themida/WinLicense signature found in strings")
            if packer_hints:
                sections.append("[string signatures]\n" + "\n".join(packer_hints))

        if not sections:
            return "Error: no packer detection tools found. Install: die (Detect-It-Easy) or upx"
        return "\n\n".join(sections)


# ── ROP Gadgets ────────────────────────────────────────────────────────────


class ROPGadgetsInput(BaseModel):
    path: str = Field(description="Path to the binary file.")
    query: str = Field(default="", description="Gadget filter query, e.g. 'pop rdi' or 'ret'.")
    arch: str = Field(default="x86_64", description="Architecture: x86_64, x86, arm, arm64.")
    limit: int = Field(default=50, description="Maximum number of gadgets to return.")


class ROPGadgetsTool(BaseTool):
    """Find ROP gadgets in a binary for return-oriented programming exploit development."""

    name: str = "rop_gadgets"
    description: str = (
        "Find ROP (Return-Oriented Programming) gadgets in a binary. "
        "Uses ROPgadget or ropper. Filter by gadget type (e.g. 'pop rdi', 'ret'). "
        "Requires: pip install ROPgadget  OR  pip install ropper"
    )
    args_schema: ClassVar[type[BaseModel]] = ROPGadgetsInput

    def _run(self, path: str, query: str = "", arch: str = "x86_64", limit: int = 50) -> str:
        import shutil
        if shutil.which("ROPgadget"):
            cmd = ["ROPgadget", "--binary", path]
            if query:
                cmd.extend(["--filter", query])
            out = _run_cmd(cmd, timeout=120)
        elif shutil.which("ropper"):
            cmd = ["ropper", "-f", path]
            if query:
                cmd.extend(["--search", query])
            out = _run_cmd(cmd, timeout=120)
        else:
            return "Error: ROPgadget or ropper not found. Install: pip install ROPgadget"

        lines = out.splitlines()
        gadget_lines = [l for l in lines if "0x" in l]
        total = len(gadget_lines)
        if query:
            needle = query.lower()
            gadget_lines = [l for l in gadget_lines if needle in l.lower()]
        result_lines = gadget_lines[:limit]
        summary = f"Found {total} gadgets total"
        if query:
            summary += f", {len(gadget_lines)} matching '{query}'"
        return summary + "\n\n" + "\n".join(result_lines)


# ── Disassemble ────────────────────────────────────────────────────────────


class DisassembleInput(BaseModel):
    path: str = Field(description="Path to the binary file.")
    function: str = Field(default="main", description="Function name to disassemble.")
    lines: int = Field(default=60, description="Maximum disassembly lines to return.")


class DisassembleTool(BaseTool):
    """Disassemble a function from a binary using objdump or radare2."""

    name: str = "disassemble"
    description: str = (
        "Disassemble a function from a binary. "
        "Uses radare2 (r2) for precise function-level disassembly if available, "
        "falls back to objdump. Returns assembly instructions."
    )
    args_schema: ClassVar[type[BaseModel]] = DisassembleInput

    def _run(self, path: str, function: str = "main", lines: int = 60) -> str:
        import shutil
        if shutil.which("r2"):
            script = f"aaa; pdf @ sym.{function}~{lines}[0]"
            out = _run_cmd(["r2", "-q", "-c", script, path], timeout=60)
            if out and "Cannot" not in out:
                return out
        if shutil.which("objdump"):
            out = _run_cmd(["objdump", "-d", "--no-show-raw-insn", path], timeout=60)
            # Find the function and extract lines
            found = False
            result_lines: list[str] = []
            for line in out.splitlines():
                if f"<{function}>:" in line or f"<{function}@" in line:
                    found = True
                if found:
                    result_lines.append(line)
                    if len(result_lines) >= lines:
                        break
                    if found and len(result_lines) > 3 and line.strip() == "":
                        break
            if result_lines:
                return "\n".join(result_lines)
            return f"Function '{function}' not found in objdump output. Try: strings_extract to find function names."
        return "Error: neither r2 nor objdump found. Install: apt install radare2 binutils"


# ── Factory ────────────────────────────────────────────────────────────────


def get_reversing_tools() -> list[BaseTool]:
    return [
        BinaryInfoTool(),
        StringsExtractTool(),
        SymbolsExtractTool(),
        PackerDetectTool(),
        ROPGadgetsTool(),
        DisassembleTool(),
    ]
