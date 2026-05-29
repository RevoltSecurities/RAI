# Example: web-recon skill

This is a complete, ready-to-use example showing what a well-formed RAI
skill looks like.  Save it as `~/.rai/skills/web-recon/SKILL.md` or let
the skill-creator generate it with:

```
/skill-creator build a web recon skill
```

---

```markdown
---
name: web-recon
description: "Passive and active web reconnaissance: subdomain enum, tech fingerprinting, endpoint discovery, and screenshot capture."
license: MIT
compatibility: RAI
metadata:
  author: rai
  version: "1.0"
---

# Web Recon

## Overview

Comprehensive web reconnaissance covering passive OSINT, DNS enumeration,
subdomain discovery, technology fingerprinting, and active endpoint probing.
Use before any exploitation phase.

## When to Use

- Invoked via: `/web-recon <domain>`
- Trigger phrases: "recon", "enumerate subdomains", "fingerprint", "map the attack surface"
- Context: any engagement where the target is a web application or domain

## Instructions

### Step 1: Scope Confirmation

> "Is `<domain>` in scope? Do you have written authorisation for active probing?"

### Step 2: Passive OSINT

Use `web_search` for each:
- `site:<domain>` — indexed pages and subdomains
- `"<domain>" filetype:pdf` — exposed documents
- GitHub / GitLab search: `"<domain>" password OR secret OR api_key`
- crt.sh: `https://crt.sh/?q=%25.<domain>&output=json` → subdomain list
- Shodan / Censys: search for IPs and open services

### Step 3: DNS Enumeration

```bash
dig <domain> ANY +short
dig _dmarc.<domain> TXT +short
subfinder -d <domain> -silent -o /tmp/subs.txt
cat /tmp/subs.txt | httpx -silent -status-code -title -tech-detect -o /tmp/live.txt
cat /tmp/live.txt
```

### Step 4: Technology Fingerprinting

```bash
# For each live host from step 3
whatweb -a 3 <url>
curl -sI <url> | grep -iE "server:|x-powered-by:|x-generator:"
```

Key technologies to note: CMS (WordPress, Drupal), frameworks (Laravel, Django,
Rails, Express), CDN/WAF (Cloudflare, Akamai), cloud provider.

### Step 5: Endpoint Discovery

```bash
ffuf -u https://<target>/FUZZ \
  -w /usr/share/wordlists/dirb/common.txt \
  -mc 200,201,301,302,403 \
  -t 40 \
  -o /tmp/ffuf.json
```

Look for: admin panels (`/admin`, `/wp-admin`, `/manage`), API endpoints
(`/api/`, `/v1/`, `/graphql`), backup files (`.bak`, `.old`, `.zip`),
dev endpoints (`/debug`, `/swagger`, `/.env`).

### Step 6: Record Findings

For each interesting discovery, call `findings_add`:
- Admin panel exposed → high severity
- `.env` or config file exposed → critical
- Outdated CMS version with known CVE → medium-high
- Unexpected open port/service → medium

## Output Format

Summary table:
| Asset | Type | Technology | Notes |
|-------|------|------------|-------|
| sub.example.com | subdomain | Nginx 1.18 | 403 on / |
| admin.example.com | admin panel | WordPress 5.8 | Login exposed |

Then a findings list with severity.

## Security Notes

- `ffuf` directory brute-forcing is **active probing** — confirm scope first.
- Keep request rate reasonable (`-t 40` threads).
- Do not screenshot or crawl assets outside the defined scope.

## Examples

### Example: Full recon on a bug bounty target

**User:** `/web-recon example.com`

**Agent actions:**
1. Confirms scope
2. Runs passive OSINT — finds 3 subdomains via crt.sh
3. Runs subfinder + httpx — finds 12 live hosts
4. Fingerprints each — identifies WordPress 5.8 on blog.example.com
5. Runs ffuf on the WordPress site — finds /wp-content/uploads/ directory listing
6. Calls `findings_add` for directory listing (medium) and outdated WP (medium)

**Output:** Summary table + 2 findings recorded
```
