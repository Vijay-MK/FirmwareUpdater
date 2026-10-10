import re


def parse_version(value: str) -> tuple[int, ...]:
    """'V4.2.1' -> (4, 2, 1). Non-numeric versions give an empty tuple."""
    return tuple(int(x) for x in re.findall(r"\d+", value or ""))


def is_newer(candidate: str, current: str) -> bool:
    c, cur = parse_version(candidate), parse_version(current)
    if not c or not cur:
        return False
    width = max(len(c), len(cur))
    pad = lambda t: t + (0,) * (width - len(t))  # noqa: E731
    return pad(c) > pad(cur)


def same_version(a: str, b: str) -> bool:
    pa, pb = parse_version(a), parse_version(b)
    if not pa or not pb:
        return (a or "").strip().lower() == (b or "").strip().lower()
    width = max(len(pa), len(pb))
    return pa + (0,) * (width - len(pa)) == pb + (0,) * (width - len(pb))
