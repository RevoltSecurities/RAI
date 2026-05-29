# Skill Template

Copy this file to `~/.rai/skills/<name>/SKILL.md` and fill in the TODOs.

---

```markdown
---
name: <name>
description: "<one-sentence description>"
license: MIT
compatibility: RAI
metadata:
  author: <your-handle>
  version: "1.0"
# allowed-tools:
#   - bash
#   - http_request
#   - web_search
#   - findings_add
#   - write_file
#   - read_file
---

# <Title>

## Overview

[1-2 sentences: what this skill enables and why it exists]

## When to Use

- Invoked via: `/<name> [target or args]`
- Trigger phrases: [describe natural-language phrases that should activate this skill]
- Context: [pentest / CTF / OSINT / audit / red-team]

## Instructions

### Step 1: Scope Confirmation

> Always confirm authorization before any active technique.

Ask: "Is `<target>` in scope for this engagement? Do you have written permission?"

### Step 2: [Primary Action]

[Describe the main action. Be specific — tool name, flags, expected output.]

```bash
# Replace with actual command
nmap -sV -sC -p- --open -T4 <target> -oN scan.txt
```

### Step 3: [Analysis / Interpretation]

[What to look for in the output. How to identify real findings vs noise.]

Key indicators:
- [Indicator 1]
- [Indicator 2]

### Step 4: Record Findings

For each confirmed finding, call `findings_add`:

| Field | Value |
|---|---|
| title | Short descriptive title |
| severity | critical / high / medium / low / info |
| description | What was found, where, and why it matters |
| evidence | Relevant output snippet, request/response, or file path |

## Output Format

[Describe the expected output structure]

Example:
```
Finding: Open redirect on /redirect?url=
Severity: Medium
Evidence: GET /redirect?url=https://evil.com → 302 Location: https://evil.com
```

## Security Notes

- **Authorized use only.** This skill is for penetration testing engagements
  with explicit written permission.
- [List any rate limits, legal constraints, or scope restrictions]
- [Mention if any technique could be disruptive (DoS risk, etc.)]

## Examples

### Example: Basic scan

**User:** `/<name> https://target.example.com`

**Agent actions:**
1. Confirms target is in scope
2. [Action 2]
3. Records any findings with `findings_add`

**Output:**
```
[summary of expected results]
```

### Example: With custom options

**User:** `/<name> 10.0.0.0/24 --ports 80,443,8080`

**Agent actions:**
[...]
```
