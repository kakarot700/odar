"""Trust boundaries, external-content sanitization and prompt-injection defense.

Every byte that arrives from the network is UNTRUSTED DATA.  This module
enforces the separation between:

    SYSTEM POLICY | USER REQUEST | AGENT STATE | TOOL DEFINITIONS |
    UNTRUSTED EXTERNAL CONTENT | MODEL OUTPUT

Concretely it provides:

* control-character and bidi/zero-width Unicode stripping (obfuscation
  defense),
* fenced wrapping so external text is always presented to a model inside an
  explicit, labelled untrusted-data envelope,
* a heuristic prompt-injection detector (direct overrides, role hijacking,
  encoded payloads, tool-call impersonation, fake system blocks),
* secret redaction for logs, traces and serialized state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# --- Unicode hygiene ---------------------------------------------------------#
#: Zero-width / bidi-override / invisible formatting characters used for
#: obfuscation and visual spoofing.
_INVISIBLE_CHARS = (
    "\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
    "\u2066\u2067\u2068\u2069\ufeff\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5"
    "\u180e\u2060\u2061\u2062\u2063\u2064\U000e0000\U000e007f"
)
_INVISIBLE_RE = re.compile(f"[{re.escape(_INVISIBLE_CHARS)}]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# --- Injection heuristics -----------------------------------------------------#
#: (pattern, category) - deliberately conservative, high-signal phrases.
_INJECTION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (
        re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules)", re.I),
        "instruction_override",
    ),
    (
        re.compile(r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules)", re.I),
        "instruction_override",
    ),
    (re.compile(r"you\s+are\s+now\s+(a|an|in)\b", re.I), "role_hijack"),
    (re.compile(r"new\s+system\s+(prompt|message|instruction)", re.I), "fake_system"),
    (re.compile(r"\[system\]|<<\s*sys\s*>>|<\|im_start\|>\s*system", re.I), "fake_system"),
    (re.compile(r"act\s+as\s+if\s+you\s+(have\s+)?no\s+restrictions", re.I), "jailbreak"),
    (re.compile(r"do\s+anything\s+now|\bDAN\s+mode\b", re.I), "jailbreak"),
    (
        re.compile(
            r"(call|invoke|execute|use)\s+the\s+(tool|function)\s+['\"]?(web_search|run_python|extract_page|shell|exec)",
            re.I,
        ),
        "tool_command",
    ),
    (
        re.compile(
            r"(call|invoke|execute|use)\s+the\s+(run_python|web_search|extract_page|shell|exec)\s+tool", re.I
        ),
        "tool_command",
    ),
    (re.compile(r"run_python\s*\(|exec\s*\(\s*['\"]", re.I), "tool_command"),
    (
        re.compile(
            r"\bos\.system\s*\(|subprocess\.(run|call|Popen)|rm\s+-rf\s+/|curl[^\n]{0,80}\|\s*(ba)?sh", re.I
        ),
        "shell_command",
    ),
    (re.compile(r"reveal\s+(your|the)\s+(system\s+prompt|instructions|api\s*key)", re.I), "exfiltration"),
    (
        re.compile(r"(send|post|upload)\s+(this|the|all)\s+(data|text|context)\s+to\s+https?://", re.I),
        "exfiltration",
    ),
    (re.compile(r"base64[:\s]+[A-Za-z0-9+/=]{24,}", re.I), "encoded_payload"),
    (re.compile(r"(decode|decrypt|payload|encoded)[:\s]+[A-Za-z0-9+/]{24,}={0,2}", re.I), "encoded_payload"),
    (
        re.compile(r"from\s+now\s+on\s+(you\s+)?(must|will|shall)\s+(obey|follow|execute)", re.I),
        "instruction_override",
    ),
    (
        re.compile(r"pretend\s+(you\s+are|to\s+be)\s+(an?\s+)?(unrestricted|uncensored|different)\b", re.I),
        "role_hijack",
    ),
    (
        re.compile(r"\bas\s+(the|an)\s+(developer|owner|administrator|admin|root|anthropic|openai)\b", re.I),
        "impersonation",
    ),
    (
        re.compile(r"\bi\s+(authorize|order|instruct|command)\s+(you|the\s+(assistant|agent|model))", re.I),
        "impersonation",
    ),
    (
        re.compile(
            r"(skip|disable|bypass|override)\s+(all\s+)?(validation|safety|security|verification)\b", re.I
        ),
        "policy_evasion",
    ),
]

_UNTRUSTED_LABEL = "UNTRUSTED_EXTERNAL_CONTENT"


@dataclass
class InjectionScan:
    """Result of scanning one untrusted artifact."""

    clean: bool
    findings: List[Dict[str, str]] = field(default_factory=list)
    sanitized: str = ""

    @property
    def detected(self) -> bool:
        return not self.clean


def strip_invisible(text: str) -> str:
    """Remove zero-width, bidi-override and ASCII control characters."""
    text = _INVISIBLE_RE.sub("", text or "")
    text = _CONTROL_RE.sub(" ", text)
    return text


def sanitize_external_text(text: str, max_chars: int = 20000) -> str:
    """Canonicalize untrusted text: strip invisibles, collapse control chars,
    neutralize envelope-escape sequences, cap length."""
    text = strip_invisible(text or "")
    # Prevent the payload from closing our fence and escaping the envelope.
    text = text.replace(f"<{_UNTRUSTED_LABEL}", "<blocked-envelope-open")
    text = text.replace(f"</{_UNTRUSTED_LABEL}>", "<untrusted-content-closed/>")
    if len(text) > max_chars:
        text = text[:max_chars] + " [...truncated by sanitizer]"
    return text


def scan_for_injection(text: str) -> InjectionScan:
    """Scan untrusted text for prompt-injection signatures.

    Detection marks and quarantines; it never *executes* anything from the
    payload.  Matching is performed on the sanitized text.
    """
    sanitized = sanitize_external_text(text)
    findings: List[Dict[str, str]] = []
    for pattern, category in _INJECTION_PATTERNS:
        match = pattern.search(sanitized)
        if match:
            findings.append(
                {
                    "category": category,
                    "pattern": pattern.pattern[:80],
                    "excerpt": sanitized[max(0, match.start() - 40) : match.end() + 40][:200],
                }
            )
    return InjectionScan(clean=not findings, findings=findings, sanitized=sanitized)


def wrap_untrusted(text: str, source_label: str) -> str:
    """Wrap external content in an explicit trust-boundary envelope.

    Anything inside the fence is data, never instructions - the fence is the
    contract presented to any model that later sees this content.
    """
    sanitized = sanitize_external_text(text)
    label = re.sub(r"[^A-Za-z0-9._:/-]", "_", source_label)[:200]
    return f'<{_UNTRUSTED_LABEL} source="{label}">{sanitized}</{_UNTRUSTED_LABEL}>'


def clamp_response_size(text: str, max_chars: int) -> str:
    """Enforce response-size ceilings on anything fed back into the loop."""
    if text is None:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + " [...response-size-capped]"


# --- Secret redaction ---------------------------------------------------------#
_SECRET_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "[REDACTED_ANTHROPIC_KEY]"),
    (re.compile(r"sk-[A-Za-z0-9_-]{24,}"), "[REDACTED_API_KEY]"),
    (
        re.compile(r"\b(?:x-)?api[-_]?key\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}['\"]?", re.I),
        "[REDACTED_API_KEY_ASSIGNMENT]",
    ),
    (re.compile(r"\bBearer\s+[A-Za-z0-9_\-\.]{20,}"), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{4,}\b"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"(?i)\btoken\s+[:=]?\s*[A-Za-z0-9_\-]{10,}\b"), "token=[REDACTED]"),
    (re.compile(r"(?i)(password|passwd|secret|token)\s*[:=]\s*\S+"), r"\1=[REDACTED]"),
]


def redact_secrets(text: str) -> str:
    """Best-effort redaction of credentials before logging/serialization."""
    if not text:
        return text
    out = text
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out
