"""Party-name intelligence: clean display names + forgiving matching.

Two pure jobs (no DB, no I/O), so both are exhaustively testable:

1. clean_display(raw) - turn a messy Tally ledger name ("M/S RAMESH
   TRADERS-RTE4") into the name the OWNER actually uses ("Ramesh Traders").
   Owner-facing surfaces show this; Tally matching still uses the raw name.

2. resolve(query, names) - resolve a name the owner TYPED on WhatsApp
   ("ramesh", "ramesh tr", a small typo) to a party - forgivingly, but WITHOUT
   over-matching. Returns "one" (confident hit), "many" (a short which-one list),
   or "none".

The old bot did `query.lower() in name.lower()` - a bare substring test that
both OVER-matched (one letter hits everyone) and UNDER-matched (the owner's word
not being a literal substring of an ALL-CAPS, prefixed Tally name). This module
replaces that with token-aware, prefix-stripped, typo-tolerant matching.

Design notes for the 40-70 owner: he types the short name he uses, in any case,
often with a typo, never the full Tally ledger string. Matching must forgive all
of that, and when it is genuinely unsure it must ASK (return "many") rather than
guess - a wrong guess on someone's dues is the exact bad experience we avoid.
"""
from __future__ import annotations

import re

# Honorific / company prefixes an owner never types but Tally often stores.
# Stripped from BOTH the display name and the match key so "M/S Ramesh" and
# "ramesh" line up.
_PREFIX_RE = re.compile(
    r"^(?:m/?s\.?|messrs\.?|shri\.?|sri\.?|smt\.?|mr\.?|mrs\.?|the)\s+", re.IGNORECASE)
# Same prefixes but for an already-normalized string, where "M/S" has become the
# two tokens "m s" (the slash turned into a space), so we must allow that gap.
_CORE_PREFIX_RE = re.compile(r"^(?:m\s?s|messrs|shri|sri|smt|mr|mrs|the)\s+")
# Short ALL-CAPS words that are ordinary words, not acronyms: title-case them
# ("CO" -> "Co", "SONS" -> "Sons") instead of preserving as-is.
_KEEP_CASE_LOWER = {"CO", "SONS", "AND", "THE", "LTD", "PVT", "OF", "IN", "ON"}
# Trailing route / round tags Tally uses to bucket debtors by delivery line
# ("-RTE4", "- ROUTE 12"). This is the ONLY trailing tag we treat as noise: it is
# a delivery sequence, never a customer's identity. Area words (KLP, Aratur),
# "& Hardware", branch parentheticals "(Kovur)", and bare codes are KEPT - they
# are what tells two same-named parties apart, so stripping them would let the
# owner chase the wrong customer.
_ROUTE_RE = re.compile(r"[\s\-,]+(?:rte|route|rout|rt)\.?\s*\d+\w*$", re.IGNORECASE)

STRONG = 0.9      # a confident single hit
WEAK = 0.55       # plausible enough to offer as a choice


def normalize(s: str) -> str:
    """Lowercase, punctuation -> spaces, whitespace collapsed. The common base
    for every comparison so case and stray dots/commas never matter."""
    s = re.sub(r"[^a-z0-9]+", " ", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def core(s: str) -> str:
    """Normalized name with leading honorific/company prefixes removed - the key
    we actually match on. 'M/S RAMESH TRADERS' and 'ramesh traders' share a core."""
    n = normalize(s)
    prev = None
    while prev != n:                       # peel stacked prefixes ("the m/s ...")
        prev = n
        n = _CORE_PREFIX_RE.sub("", n).strip()
    return n


def tokens(s: str) -> list[str]:
    c = core(s)
    return c.split() if c else []


def _cap(w: str) -> str:
    # Keep short ALL-CAPS acronyms (ABC, HDFC, SKF) but title-case ordinary short
    # words even when Tally stored them shouting ("CO" -> "Co", "SONS" -> "Sons").
    if w.isupper() and len(w) <= 4 and w.isalpha() and w not in _KEEP_CASE_LOWER:
        return w
    m = re.search(r"[a-zA-Z]", w)          # capitalize the first LETTER, so a
    if not m:                              # leading "(" ("(kovur)" -> "(Kovur)")
        return w                           # or "&" is preserved
    i = m.start()
    return w[:i] + w[i].upper() + w[i + 1:].lower()


_FY_SUFFIX_RE = re.compile(
    r"(?:\s*[-–]\s*\((?:from\s+[^)]*|as\s+at[^)]*|\d{1,4}\s*[-/]\s*\d{1,4})\))+\s*$",
    re.IGNORECASE,
)


def clean_business(raw: str) -> str:
    """A Tally COMPANY name -> the clean shop name a customer should see.

    Tally company names often carry financial-year / date markers that must never
    reach a customer's WhatsApp (fictional examples):
      'ACME TRADERS - (from 1-Apr-2019) - (from 1-Apr-2020)' -> 'ACME TRADERS'
      'EXAMPLE AGENCIES - (from 1-Apr-24)'                   -> 'EXAMPLE AGENCIES'
      'SAMPLE CHEMICALS - (25-26)'                           -> 'SAMPLE CHEMICALS'
    Conservative: only strips trailing ' - (from ...)' / ' - (as at ...)' /
    ' - (YY-YY)' markers, so a legitimate name like 'ACME (India)' is untouched,
    and never returns empty (falls back to the trimmed original)."""
    s = (raw or "").strip()
    if not s:
        return s
    cleaned = _FY_SUFFIX_RE.sub("", s).strip(" -–,")
    return cleaned or s


def clean_display(raw: str) -> str:
    """A messy Tally ledger name -> the human name the owner recognizes.

    'M/S RAMESH TRADERS-RTE4' -> 'Ramesh Traders'. Conservative: only strips
    tags that clearly are prefixes/route-codes, and never returns empty (falls
    back to the trimmed original) so a display name is always something."""
    s = (raw or "").strip()
    if not s:
        return ""
    original = s
    s = _ROUTE_RE.sub("", s)               # drop ONLY the delivery-round tag
    s = _PREFIX_RE.sub("", s)              # drop a leading "M/S " / "Shri "
    s = re.sub(r"\s+", " ", s).strip(" -,.")
    if not s:
        return original
    return " ".join(_cap(w) for w in s.split())


def _lev(a: str, b: str, cap: int = 2) -> int:
    """Levenshtein distance, short-circuited at `cap` (we only care about <=1)."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            best = min(best, cur[-1])
        if best > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _tok_score(q: str, c: str) -> float:
    """How well one typed token `q` matches one candidate token `c` (0..1).

    A 3+ char prefix is a strong signal; a 2-char prefix is only a weak hint (so
    typing "in" never confidently lands on "India"); typos are forgiven by edit
    distance, allowing a transposition (distance 2) once the token is long."""
    if q == c:
        return 1.0
    if len(q) >= 3 and c.startswith(q):
        return 0.9
    if len(q) == 2 and c.startswith(q):
        return 0.5
    if len(q) >= 3 and q in c:
        return 0.75
    if len(q) >= 4 and _lev(q, c, 1) <= 1:
        return 0.7
    if len(q) >= 5 and _lev(q, c, 2) <= 2:      # forgive a transposition/typo
        return 0.6
    return 0.0


def score(query: str, name: str) -> float:
    """0..1 match strength of a typed `query` against a candidate `name`."""
    qt, ct = tokens(query), tokens(name)
    if not qt or not ct:
        return 0.0
    cq, cn = core(query), core(name)
    if cq == cn:
        return 1.0
    # Average how well each typed token finds a home in the name. An exact token
    # ("ramesh" in "Ramesh Traders") outscores a mere prefix ("ramesh" starting
    # "Rameshwar"), so the closer party ranks first.
    base = sum(max((_tok_score(q, c) for c in ct), default=0.0) for q in qt) / len(qt)
    # Floor: the whole typed phrase is a leading fragment of the name.
    if len(cq) >= 3 and cn.startswith(cq):
        base = max(base, 0.85)
    return base


def resolve(query: str, names: list[str], limit: int = 6) -> dict:
    """Resolve a typed name against `names`.

    Returns {"status": "one"|"many"|"none", "index": int, "indices": [int],
    "scores": [float]}. "one" carries the winning index; "many" carries a short
    ranked shortlist for a which-one prompt; "none" means nothing plausible.

    Never guesses when unsure: two plausible parties come back as "many" so the
    caller asks the owner to pick, rather than silently acting on the wrong one."""
    scores = [score(query, n) for n in names]
    order = sorted(range(len(names)), key=lambda i: (-scores[i], len(core(names[i]))))
    strong = [i for i in order if scores[i] >= STRONG]
    weak = [i for i in order if scores[i] >= WEAK]
    cq = core(query)

    if len(cq) < 2:                        # too little typed to trust a single hit
        return {"status": "many" if weak else "none",
                "indices": weak[:limit], "scores": scores}
    if len(strong) == 1:
        return {"status": "one", "index": strong[0], "scores": scores}
    if not strong and len(weak) == 1:
        return {"status": "one", "index": weak[0], "scores": scores}
    if len(weak) >= 1:                     # 2+ plausible (or 2+ strong) -> ask
        return {"status": "many", "indices": weak[:limit], "scores": scores}
    return {"status": "none", "indices": [], "scores": scores}


def distinguisher(names: list[str]) -> list[str]:
    """Given similar names, return each one's DISTINGUISHING part - the area or
    branch that tells them apart - so a which-one prompt can highlight it.

    ["Meenakshi Electricals KLP", "Meenakshi Electricals & Hardware",
     "Meenakshi Electrical Aratur"] -> ["Electricals KLP",
     "Electricals & Hardware", "Electrical Aratur"]. When names share no leading
    stem (or are identical), it falls back to the full display name."""
    disp = [clean_display(n) for n in names]
    norm = [[normalize(w) for w in d.split()] for d in disp]
    common = 0
    if norm and all(norm):
        for col in range(min(len(t) for t in norm)):
            if len({t[col] for t in norm}) == 1:
                common += 1
            else:
                break
    out = []
    for d in disp:
        tail = d.split()[common:]
        out.append(" ".join(tail) if tail else d)
    return out


def duplicate_groups(names: list[str]) -> list[list[int]]:
    """Indices of names that are the SAME party written more than once - only an
    exact core match (identical after prefix/case/spacing normalization), so
    'Meenakshi Electricals KLP' and 'Meenakshi Electricals Aratur' (different
    areas) are NEVER grouped. This only FLAGS candidates; a real link is always
    owner-confirmed, because wrongly merging two different customers is worse
    than leaving a duplicate."""
    seen: dict[str, list[int]] = {}
    for i, n in enumerate(names):
        seen.setdefault(core(n), []).append(i)
    return [idxs for idxs in seen.values() if len(idxs) > 1]
