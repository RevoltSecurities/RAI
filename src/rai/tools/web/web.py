"""Web security tools — JWT, OAuth, GraphQL analysis.

Ported from Decepticon's web toolkit. Pure Python, no external deps.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar, Iterable
from urllib.parse import parse_qs, urlsplit

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


# ── JWT helpers ────────────────────────────────────────────────────────────


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


@dataclass
class JWTHeader:
    alg: str = "none"
    typ: str = "JWT"
    kid: str | None = None
    jku: str | None = None
    x5u: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JWTHeader:
        known = {"alg", "typ", "kid", "jku", "x5u"}
        return cls(
            alg=data.get("alg", "none"),
            typ=data.get("typ", "JWT"),
            kid=data.get("kid"),
            jku=data.get("jku"),
            x5u=data.get("x5u"),
            extra={k: v for k, v in data.items() if k not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"alg": self.alg, "typ": self.typ}
        for k in ("kid", "jku", "x5u"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        out.update(self.extra)
        return out


@dataclass
class JWTClaims:
    iss: str | None = None
    sub: str | None = None
    aud: Any = None
    exp: int | None = None
    nbf: int | None = None
    iat: int | None = None
    jti: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JWTClaims:
        known = {"iss", "sub", "aud", "exp", "nbf", "iat", "jti"}
        return cls(
            iss=data.get("iss"),
            sub=data.get("sub"),
            aud=data.get("aud"),
            exp=data.get("exp"),
            nbf=data.get("nbf"),
            iat=data.get("iat"),
            jti=data.get("jti"),
            extra={k: v for k, v in data.items() if k not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in ("iss", "sub", "aud", "exp", "nbf", "iat", "jti"):
            val = getattr(self, name)
            if val is not None:
                out[name] = val
        out.update(self.extra)
        return out

    @property
    def expired(self) -> bool:
        return self.exp is not None and self.exp < int(time.time())


@dataclass
class JWTToken:
    header: JWTHeader
    claims: JWTClaims
    signature: bytes
    raw: str
    findings: list[str] = field(default_factory=list)

    def segments(self) -> tuple[str, str, str]:
        parts = self.raw.split(".")
        if len(parts) != 3:
            return (parts[0] if parts else "", "", "")
        return parts[0], parts[1], parts[2]


def _parse_token(token: str) -> JWTToken:
    token = token.strip()
    findings: list[str] = []
    parts = token.split(".")
    if len(parts) != 3:
        findings.append(f"malformed: expected 3 segments, got {len(parts)}")
        parts = (parts + ["", "", ""])[:3]
    try:
        header_data = json.loads(_b64url_decode(parts[0]).decode("utf-8", errors="replace")) if parts[0] else {}
    except (ValueError, json.JSONDecodeError):
        findings.append("header not valid base64url JSON")
        header_data = {}
    try:
        claim_data = json.loads(_b64url_decode(parts[1]).decode("utf-8", errors="replace")) if parts[1] else {}
    except (ValueError, json.JSONDecodeError):
        findings.append("body not valid base64url JSON")
        claim_data = {}
    try:
        sig = _b64url_decode(parts[2]) if parts[2] else b""
    except ValueError:
        findings.append("signature not valid base64url")
        sig = b""
    header = JWTHeader.from_dict(header_data)
    claims = JWTClaims.from_dict(claim_data)
    tok = JWTToken(header=header, claims=claims, signature=sig, raw=token, findings=findings)
    if header.alg.lower() == "none":
        tok.findings.append("alg=none — server MAY accept unsigned tokens")
    if header.alg.lower() == "hs256" and header.jku:
        tok.findings.append("alg=HS256 with jku — key confusion candidate")
    if header.kid and ("../" in header.kid or "%2f" in header.kid.lower()):
        tok.findings.append("kid contains path traversal — file read / SQLi candidate")
    if header.jku and not header.jku.startswith("https://"):
        tok.findings.append("jku over non-HTTPS or attacker-controlled host")
    if claims.expired:
        tok.findings.append("token already expired — test whether server enforces exp")
    if claims.exp is None:
        tok.findings.append("no exp claim — server MAY accept forever-valid tokens")
    return tok


_HS_ALGS = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}


def _sign_hs(alg: str, key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, _HS_ALGS[alg]).digest()


def _forge_token(
    claims: dict[str, Any],
    *,
    alg: str = "none",
    secret: bytes | str | None = None,
    extra_header: dict[str, Any] | None = None,
) -> str:
    alg = alg.upper()
    header_dict: dict[str, Any] = dict(extra_header or {})
    header_dict["alg"] = alg.lower() if alg == "NONE" else alg
    header_dict.setdefault("typ", "JWT")
    header_seg = _b64url_encode(json.dumps(header_dict, separators=(",", ":"), sort_keys=True).encode())
    body_seg = _b64url_encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{header_seg}.{body_seg}".encode("ascii")
    if alg == "NONE":
        sig = b""
    else:
        if secret is None:
            raise ValueError("secret required for HMAC alg")
        key = secret.encode("utf-8") if isinstance(secret, str) else secret
        sig = _sign_hs(alg, key, signing_input)
    return f"{header_seg}.{body_seg}.{_b64url_encode(sig)}"


def _verify_hs(token: JWTToken, secret: bytes | str) -> bool:
    alg = token.header.alg.upper()
    if alg not in _HS_ALGS:
        return False
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    h, b, _ = token.segments()
    signing_input = f"{h}.{b}".encode("ascii")
    expected = _sign_hs(alg, key, signing_input)
    return hmac.compare_digest(expected, token.signature)


_DEFAULT_WEAK_SECRETS: tuple[str, ...] = (
    "", "secret", "password", "admin", "changeme", "token", "jwt", "key",
    "your-256-bit-secret", "mysecret", "supersecret", "secretkey", "default",
    "test", "hello", "123456", "example",
)


# ── OAuth helpers ──────────────────────────────────────────────────────────


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    entropy = 0.0
    for count in freq.values():
        p = count / len(s)
        entropy -= p * math.log2(p)
    return entropy


def _qp(url: str) -> dict[str, list[str]]:
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    fragment = parse_qs(parts.fragment, keep_blank_values=True)
    for k, v in fragment.items():
        query.setdefault(k, []).extend(v)
    return query


@dataclass
class OAuthFinding:
    id: str
    severity: str
    title: str
    detail: str
    recommendation: str = ""


def _analyze_oauth_callback(
    callback_url: str,
    *,
    initial_request_url: str | None = None,
    public_client: bool = False,
) -> list[OAuthFinding]:
    findings: list[OAuthFinding] = []
    params = _qp(callback_url)
    single = {k: v[0] if v else "" for k, v in params.items()}
    initial_params = _qp(initial_request_url) if initial_request_url else {}
    initial_single = {k: v[0] if v else "" for k, v in initial_params.items()}
    response_type = initial_single.get("response_type") or single.get("response_type", "")
    if response_type == "token":
        findings.append(OAuthFinding(
            id="oauth.implicit-flow", severity="high",
            title="Implicit flow in use",
            detail="response_type=token delivers the access token in the URL fragment. Deprecated by RFC 9700.",
            recommendation="Use authorization code + PKCE instead.",
        ))
    state = single.get("state", "")
    if not state:
        findings.append(OAuthFinding(
            id="oauth.state-missing", severity="high",
            title="Callback has no state parameter",
            detail="CSRF protection requires state. RFC 6749 §10.12.",
            recommendation="Always generate and validate a per-session state value.",
        ))
    else:
        if len(state) < 8:
            findings.append(OAuthFinding(
                id="oauth.state-short", severity="medium",
                title="state parameter is too short",
                detail=f"state={state!r} ({len(state)} chars). Below RFC 6819 recommended 128-bit equivalent.",
            ))
        if _shannon_entropy(state) < 2.5:
            findings.append(OAuthFinding(
                id="oauth.state-low-entropy", severity="medium",
                title="state parameter has low Shannon entropy",
                detail=f"entropy={_shannon_entropy(state):.2f} bits/char — may be predictable.",
            ))
        if initial_single.get("state") and initial_single["state"] != state:
            findings.append(OAuthFinding(
                id="oauth.state-mismatch", severity="critical",
                title="state returned by AS does not match initial value",
                detail=f"initial={initial_single['state']!r} callback={state!r}.",
            ))
    scope = initial_single.get("scope", "") or single.get("scope", "")
    if "openid" in scope.split():
        if not initial_single.get("nonce"):
            findings.append(OAuthFinding(
                id="oidc.nonce-missing", severity="high",
                title="OIDC flow without nonce",
                detail="nonce required for OIDC implicit/hybrid flows (OIDC Core §3.1.2).",
                recommendation="Generate a per-request nonce and verify it in the ID token.",
            ))
    code = single.get("code", "")
    if code and "code=" in urlsplit(callback_url).fragment:
        findings.append(OAuthFinding(
            id="oauth.code-in-fragment", severity="high",
            title="Authorization code delivered in URL fragment",
            detail="Fragments leaked via document.location and browser history.",
            recommendation="Use response_mode=query or form_post.",
        ))
    if public_client:
        if not initial_single.get("code_challenge"):
            findings.append(OAuthFinding(
                id="oauth.pkce-missing", severity="high",
                title="Public client without PKCE",
                detail="RFC 9700 requires PKCE for all OAuth clients.",
                recommendation="Send code_challenge + code_challenge_method=S256.",
            ))
        elif initial_single.get("code_challenge_method", "plain").lower() == "plain":
            findings.append(OAuthFinding(
                id="oauth.pkce-plain", severity="medium",
                title="PKCE using plain method",
                detail="code_challenge_method=plain is weaker than S256.",
            ))
    redirect_uri = initial_single.get("redirect_uri", "")
    if redirect_uri:
        if any(frag in redirect_uri for frag in ("%252e%252e", "%2e%2e", "../", "..%2f")):
            findings.append(OAuthFinding(
                id="oauth.redirect-uri-traversal", severity="high",
                title="redirect_uri contains path traversal",
                detail=f"redirect_uri={redirect_uri!r}",
                recommendation="Validate redirect_uri against an exact-match allowlist.",
            ))
        if "@" in redirect_uri:
            findings.append(OAuthFinding(
                id="oauth.redirect-uri-userinfo", severity="medium",
                title="redirect_uri contains userinfo component",
                detail=f"redirect_uri={redirect_uri!r} — URL parser confusion candidate.",
            ))
    if scope and any(s in ("*", "all", "admin") for s in scope.split()):
        findings.append(OAuthFinding(
            id="oauth.scope-wildcard", severity="medium",
            title="Scope contains wildcard / admin token",
            detail=f"scope={scope!r} — least-privilege violation.",
        ))
    return findings


# ── GraphQL helpers ─────────────────────────────────────────────────────────


INTROSPECTION_QUERY = """query IntrospectionQuery { __schema { queryType { name } mutationType { name } subscriptionType { name } types { kind name fields(includeDeprecated: true) { name args { name type { kind name ofType { kind name } } } type { kind name ofType { kind name } } isDeprecated } inputFields { name type { kind name } } enumValues(includeDeprecated: true) { name } } } }"""


def _unwrap_type(type_ref: dict[str, Any] | None) -> tuple[str, bool, bool]:
    if type_ref is None:
        return ("Unknown", False, False)
    is_non_null = False
    is_list = False
    node = type_ref
    if node.get("kind") == "NON_NULL":
        is_non_null = True
        node = node.get("ofType") or {}
    if node.get("kind") == "LIST":
        is_list = True
        node = node.get("ofType") or {}
        if node.get("kind") == "NON_NULL":
            node = node.get("ofType") or {}
    return node.get("name") or "Unknown", is_list, is_non_null


@dataclass
class GraphQLField:
    name: str
    args: dict[str, dict[str, Any]]
    return_type: str
    is_list: bool
    deprecated: bool = False


@dataclass
class GraphQLSchema:
    query_type: str | None
    mutation_type: str | None
    subscription_type: str | None
    types: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_introspection(cls, data: dict[str, Any]) -> GraphQLSchema:
        root = data
        if "data" in data:
            root = data["data"]
        schema = root.get("__schema") or root.get("schema") or {}
        q = (schema.get("queryType") or {}).get("name")
        m = (schema.get("mutationType") or {}).get("name")
        s = (schema.get("subscriptionType") or {}).get("name")
        types: dict[str, dict[str, Any]] = {}
        for t in schema.get("types") or []:
            name = t.get("name")
            if name:
                types[name] = t
        return cls(query_type=q, mutation_type=m, subscription_type=s, types=types)

    def _type(self, name: str) -> dict[str, Any]:
        return self.types.get(name) or {}

    def fields_of(self, type_name: str) -> list[GraphQLField]:
        t = self._type(type_name)
        out: list[GraphQLField] = []
        for f in t.get("fields") or []:
            ret, is_list, _ = _unwrap_type(f.get("type"))
            args: dict[str, dict[str, Any]] = {}
            for a in f.get("args") or []:
                a_type, a_list, a_nn = _unwrap_type(a.get("type"))
                args[a["name"]] = {"type": a_type, "is_list": a_list, "non_null": a_nn}
            out.append(GraphQLField(name=f["name"], args=args, return_type=ret, is_list=is_list, deprecated=bool(f.get("isDeprecated"))))
        return out

    def query_fields(self) -> list[GraphQLField]:
        return self.fields_of(self.query_type) if self.query_type else []

    def mutation_fields(self) -> list[GraphQLField]:
        return self.fields_of(self.mutation_type) if self.mutation_type else []

    def idor_candidates(self) -> list[tuple[str, GraphQLField]]:
        candidates: list[tuple[str, GraphQLField]] = []
        for kind, fields in (("Query", self.query_fields()), ("Mutation", self.mutation_fields())):
            for fld in fields:
                if any(a == "id" or a.lower().endswith("id") for a in fld.args):
                    candidates.append((kind, fld))
        return candidates


# ── Tool definitions ────────────────────────────────────────────────────────


class JWTDecodeInput(BaseModel):
    token: str = Field(description="JWT token string to decode and analyze.")


class JWTDecodeTool(BaseTool):
    """Decode a JWT without verification — shows header, claims, and security findings."""

    name: str = "jwt_decode"
    description: str = (
        "Decode a JWT token without signature verification. "
        "Shows header (alg, kid, jku), claims (sub, exp, roles), expiry status, "
        "and flags known vulnerabilities (alg=none, jku confusion, kid traversal)."
    )
    args_schema: ClassVar[type[BaseModel]] = JWTDecodeInput

    def _run(self, token: str) -> str:
        tok = _parse_token(token)
        lines = [
            "## JWT Analysis",
            f"Algorithm : {tok.header.alg}",
            f"Type      : {tok.header.typ}",
        ]
        if tok.header.kid:
            lines.append(f"kid       : {tok.header.kid}")
        if tok.header.jku:
            lines.append(f"jku       : {tok.header.jku}")
        lines.append("\n### Claims")
        claims_dict = tok.claims.to_dict()
        for k, v in claims_dict.items():
            if k in ("exp", "nbf", "iat") and isinstance(v, int):
                import datetime
                dt = datetime.datetime.fromtimestamp(v, tz=datetime.timezone.utc).isoformat()
                lines.append(f"  {k}: {v} ({dt})")
            else:
                lines.append(f"  {k}: {v}")
        if tok.claims.expired:
            lines.append("  ⚠ Token is EXPIRED")
        if tok.findings:
            lines.append("\n### Security Findings")
            for f in tok.findings:
                lines.append(f"  • {f}")
        else:
            lines.append("\n### Security Findings\n  None detected")
        return "\n".join(lines)


class JWTForgeInput(BaseModel):
    claims: str = Field(description="JSON string of claims to include, e.g. '{\"sub\":\"admin\",\"role\":\"admin\"}'.")
    alg: str = Field(default="none", description="Algorithm: 'none', 'HS256', 'HS384', 'HS512'.")
    secret: str = Field(default="", description="HMAC secret for HS* algorithms. Leave empty for alg=none.")


class JWTForgeTool(BaseTool):
    """Forge a JWT with arbitrary claims and algorithm — for testing alg=none and key confusion."""

    name: str = "jwt_forge"
    description: str = (
        "Forge a JWT with arbitrary claims and a specified algorithm. "
        "Supports alg=none (unsigned), HS256/HS384/HS512. "
        "Use for testing alg=none bypasses and key confusion vulnerabilities."
    )
    args_schema: ClassVar[type[BaseModel]] = JWTForgeInput

    def _run(self, claims: str, alg: str = "none", secret: str = "") -> str:
        try:
            claims_dict = json.loads(claims)
        except json.JSONDecodeError as exc:
            return f"Error: claims must be valid JSON: {exc}"
        try:
            token = _forge_token(claims_dict, alg=alg, secret=secret or None)
        except ValueError as exc:
            return f"Error: {exc}"
        return f"Forged token ({alg}):\n{token}"


class JWTCrackInput(BaseModel):
    token: str = Field(description="HS256/HS384/HS512 JWT token to crack.")
    wordlist_path: str = Field(
        default="",
        description="Path to a newline-delimited wordlist file. "
        "If empty, uses a built-in list of common weak secrets.",
    )


class JWTCrackTool(BaseTool):
    """Dictionary attack on HS256/HS384/HS512 JWT HMAC secret."""

    name: str = "jwt_crack"
    description: str = (
        "Dictionary attack on a HS256/HS384/HS512 JWT to recover the HMAC secret. "
        "Uses a built-in weak-secret list by default; optionally accepts a wordlist file path."
    )
    args_schema: ClassVar[type[BaseModel]] = JWTCrackInput

    def _run(self, token: str, wordlist_path: str = "") -> str:
        tok = _parse_token(token)
        alg = tok.header.alg.upper()
        if alg not in _HS_ALGS:
            return f"Token uses {alg} — only HS256/HS384/HS512 can be cracked with this tool."

        candidates: Iterable[str]
        if wordlist_path:
            from pathlib import Path
            p = Path(wordlist_path)
            if not p.is_file():
                return f"Error: wordlist file not found: {wordlist_path}"
            candidates = p.read_text(encoding="utf-8", errors="replace").splitlines()
        else:
            candidates = _DEFAULT_WEAK_SECRETS

        found = None
        count = 0
        for cand in candidates:
            count += 1
            if _verify_hs(tok, cand):
                found = cand
                break

        if found is not None:
            return f"Secret found after {count} attempts: {found!r}\nForge tokens with: jwt_forge(claims=..., alg={alg!r}, secret={found!r})"
        return f"No secret found after {count} candidate(s). Try a larger wordlist."


class OAuthAuditInput(BaseModel):
    callback_url: str = Field(description="Full OAuth callback URL (the URL the AS redirected the user to).")
    initial_request_url: str = Field(
        default="",
        description="The authorize-endpoint URL the client originally sent (optional — enables state/nonce cross-check).",
    )
    public_client: bool = Field(default=False, description="Set true to enable PKCE checks for public clients.")


class OAuthAuditTool(BaseTool):
    """Audit an OAuth 2.0 / OIDC callback URL for security issues."""

    name: str = "oauth_audit"
    description: str = (
        "Audit an OAuth 2.0 / OIDC callback URL for security issues: "
        "missing/weak state (CSRF), missing nonce (OIDC replay), implicit flow, "
        "PKCE absence, redirect_uri traversal, code in fragment, scope wildcards."
    )
    args_schema: ClassVar[type[BaseModel]] = OAuthAuditInput

    def _run(self, callback_url: str, initial_request_url: str = "", public_client: bool = False) -> str:
        findings = _analyze_oauth_callback(
            callback_url,
            initial_request_url=initial_request_url or None,
            public_client=public_client,
        )
        if not findings:
            return "No OAuth security issues detected."
        lines = [f"OAuth findings ({len(findings)}):\n"]
        for f in findings:
            lines.append(f"[{f.severity.upper()}] {f.id}")
            lines.append(f"  Title : {f.title}")
            lines.append(f"  Detail: {f.detail}")
            if f.recommendation:
                lines.append(f"  Fix   : {f.recommendation}")
            lines.append("")
        return "\n".join(lines)


class GraphQLIntrospectInput(BaseModel):
    introspection_json: str = Field(
        description="Raw JSON response from a GraphQL introspection query. "
        "Paste the full response body."
    )


class GraphQLIntrospectTool(BaseTool):
    """Parse a GraphQL introspection response — enumerate fields, find IDOR candidates, generate queries."""

    name: str = "graphql_introspect"
    description: str = (
        "Parse a GraphQL introspection JSON response. "
        "Lists all Query/Mutation fields, identifies IDOR candidates (ID-shaped args), "
        "and generates sample queries for testing. "
        "Also returns the introspection query to paste into the server."
    )
    args_schema: ClassVar[type[BaseModel]] = GraphQLIntrospectInput

    def _run(self, introspection_json: str) -> str:
        if not introspection_json.strip():
            return f"Paste the introspection result. Query to send:\n\n{INTROSPECTION_QUERY}"
        try:
            data = json.loads(introspection_json)
        except json.JSONDecodeError as exc:
            return f"Error parsing JSON: {exc}"

        schema = GraphQLSchema.from_introspection(data)
        lines = ["## GraphQL Schema Summary"]

        q_fields = schema.query_fields()
        m_fields = schema.mutation_fields()

        lines.append(f"\nQuery type   : {schema.query_type or '(none)'} ({len(q_fields)} fields)")
        lines.append(f"Mutation type: {schema.mutation_type or '(none)'} ({len(m_fields)} fields)")

        if q_fields:
            lines.append("\n### Query Fields")
            for f in q_fields[:20]:
                arg_str = ", ".join(
                    f"{a}:{m['type']}" + ("!" if m.get("non_null") else "")
                    for a, m in f.args.items()
                )
                lines.append(f"  {f.name}({arg_str}) → {f.return_type}" + (" [DEPRECATED]" if f.deprecated else ""))

        if m_fields:
            lines.append("\n### Mutation Fields")
            for f in m_fields[:15]:
                arg_str = ", ".join(f"{a}:{m['type']}" for a, m in f.args.items())
                lines.append(f"  {f.name}({arg_str}) → {f.return_type}" + (" [DEPRECATED]" if f.deprecated else ""))

        idor = schema.idor_candidates()
        if idor:
            lines.append(f"\n### IDOR Candidates ({len(idor)})")
            for kind, f in idor[:10]:
                id_args = [a for a in f.args if a == "id" or a.lower().endswith("id")]
                lines.append(f"  [{kind}] {f.name} — id arg(s): {', '.join(id_args)}")
        else:
            lines.append("\n### IDOR Candidates\n  None detected (no id-shaped arguments found)")

        return "\n".join(lines)


# ── Factory ────────────────────────────────────────────────────────────────


def get_web_tools() -> list[BaseTool]:
    return [
        JWTDecodeTool(),
        JWTForgeTool(),
        JWTCrackTool(),
        OAuthAuditTool(),
        GraphQLIntrospectTool(),
    ]
