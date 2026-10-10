"""Built-in troubleshooting knowledge base: symptom patterns -> probable causes and fix steps.

This is curated, general guidance. It is NOT taken from vendor manuals for a specific model and
firmware: always confirm error codes and procedures in the vendor documentation. Rules match on
the words of the event text (vendors differ in codes), vendor-specific rules win over generic ones.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    key: str
    title: str
    pattern: str                      # regex, matched case-insensitively against "code message source"
    causes: tuple[str, ...]
    steps: tuple[str, ...]
    caution: str = ""
    vendors: tuple[str, ...] = ()     # empty = applies to every vendor
    escalate: bool = False            # involve the vendor / a qualified engineer before acting


def all_rules() -> list[Rule]:
    from .hardware import RULES as hardware
    from .system import RULES as system
    from .vendors_a import RULES as vendors_a
    from .vendors_b import RULES as vendors_b
    # vendor-specific first so they win over the generic rule for the same text
    return [*vendors_a, *vendors_b, *hardware, *system]
