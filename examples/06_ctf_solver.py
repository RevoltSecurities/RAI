"""06_ctf_solver.py — CTF challenge solver with category specialists.

Demonstrates: AsyncSubAgent per CTF category, category auto-detection from
              challenge description, aggressive rate limiting for speed.

Categories: web, pwn, crypto, forensics, reversing

Usage:
    python examples/06_ctf_solver.py --challenge "crack this RSA ciphertext..."
    python examples/06_ctf_solver.py --challenge "buffer overflow binary attached"
    python examples/06_ctf_solver.py --category crypto --challenge "n=..., e=..., c=..."
    python examples/06_ctf_solver.py --help
"""

from __future__ import annotations

import argparse
import asyncio
import re

from rai import DEFAULT_MODEL, AsyncSubAgent
from rai.sdk import (
    RAIAgentBuilder,
    get_builtin_tools,
    get_reversing_tools,
    get_security_tools,
    get_web_tools,
)

COORDINATOR_PROMPT = """\
You are a CTF competition coordinator. Analyze the challenge and delegate to the
appropriate specialist subagent:

- @web-solver  — web challenges (XSS, SQLi, SSRF, LFI, deserialization)
- @pwn-solver  — binary exploitation (buffer overflow, format string, ROP, heap)
- @crypto-solver — cryptography (RSA, AES, hashes, custom cipher analysis)
- @forensics-solver — digital forensics (pcap, steganography, file carving, memory)
- @reversing-solver — reverse engineering (binary analysis, decompilation, patching)

Identify the category from keywords in the challenge description, then delegate.
If uncertain, try web first then escalate.
"""

_CATEGORY_PROMPTS = {
    "web": """\
You are a CTF web exploitation specialist. Solve web challenges using:
- SQL injection (union-based, blind, error-based, OOB)
- XSS (reflected, stored, DOM, CSP bypass)
- SSRF, XXE, LFI/RFI, SSTI
- JWT manipulation, OAuth abuse
- Prototype pollution, deserialization
Extract the flag (format: FLAG{...} or CTF{...}) and explain the vulnerability.
""",
    "pwn": """\
You are a CTF binary exploitation specialist. Solve pwn challenges using:
- Buffer overflows (stack, heap, ret2libc, ROP chains)
- Format string vulnerabilities
- Use-after-free, heap grooming
- ASLR/NX/PIE bypass techniques
Use pwntools patterns, find the flag, and document the exploit chain.
""",
    "crypto": """\
You are a CTF cryptography specialist. Solve crypto challenges:
- RSA (small e, common modulus, Wiener, timing, padding oracle)
- AES (ECB block shuffling, CBC padding oracle, CTR nonce reuse)
- Hash length extension, collision attacks
- Custom cipher cryptanalysis (frequency analysis, differential)
Show all mathematical workings and extract the flag.
""",
    "forensics": """\
You are a CTF digital forensics specialist. Solve forensics challenges:
- PCAP analysis (Wireshark patterns, stream extraction, protocol decoding)
- Steganography (LSB, DCT coefficients, audio, metadata)
- File carving (binwalk, foremost patterns)
- Memory dump analysis (volatility patterns)
Extract hidden data and find the flag.
""",
    "reversing": """\
You are a CTF reverse engineering specialist. Solve reversing challenges:
- Disassemble and decompile binaries (x86/64, ARM, WASM)
- Trace execution flow, identify key comparison routines
- Patch anti-debug, anti-analysis tricks
- Unpack/decrypt custom packers
Find the validation logic and extract or bypass it to get the flag.
""",
}

_CATEGORY_KEYWORDS = {
    "web":      r"web|http|sql|xss|ssrf|lfi|flask|django|cookie|jwt|oauth",
    "pwn":      r"pwn|binary|overflow|rop|heap|shellcode|pwntools|elf|nx|pie|aslr",
    "crypto":   r"crypto|rsa|aes|cipher|hash|encrypt|decrypt|modulus|prime",
    "forensics": r"forensics|pcap|stego|wireshark|memory|dump|carving|wav|png|jpg",
    "reversing": r"revers|disassem|decompil|binary|ida|ghidra|wasm|asm|obfuscat",
}


def auto_detect_category(challenge: str) -> str:
    text = challenge.lower()
    for cat, pattern in _CATEGORY_KEYWORDS.items():
        if re.search(pattern, text):
            return cat
    return "web"


async def run_ctf(challenge: str, category: str | None, model: str, api_key: str, base_url: str) -> None:
    resolved_category = category or auto_detect_category(challenge)
    print(f"Category detected: {resolved_category}")

    all_tools = get_security_tools() + get_builtin_tools() + get_web_tools() + get_reversing_tools()

    subagents = [
        AsyncSubAgent(
            name=f"{cat}-solver",
            model=model,
            system_prompt=_CATEGORY_PROMPTS[cat],
            tools=all_tools,
            auto_approve=True,
        )
        for cat in _CATEGORY_PROMPTS
    ]

    builder = (
        RAIAgentBuilder()
        .model(model)
        .system_prompt(COORDINATOR_PROMPT)
        .add_subagents(subagents)
        .without_hitl()
        .rate_limit("aggressive")
    )
    if api_key:
        builder = builder.api_key(api_key)
    if base_url:
        builder = builder.base_url(base_url)

    async with await builder.build() as agent:
        await agent.run(
            f"Solve this CTF challenge (category hint: {resolved_category}):\n\n{challenge}"
        )
        print(f"\nCTF solver complete. Thread: {agent.thread_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAI CTF Challenge Solver")
    parser.add_argument("--challenge", required=True, help="Challenge description or problem text")
    parser.add_argument("--category", choices=list(_CATEGORY_PROMPTS), default=None,
                        help="Force category (default: auto-detect)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--base-url", default="")
    args = parser.parse_args()
    asyncio.run(run_ctf(args.challenge, args.category, args.model, args.api_key, args.base_url))


if __name__ == "__main__":
    main()
