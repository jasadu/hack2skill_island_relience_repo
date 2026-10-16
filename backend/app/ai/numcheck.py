import re

ID_PAT = re.compile(r"\b(?:ev-\d+|sop-[a-z]+-\d+(?:#\d+)?|[ah]-\d+)\b", re.I)
NUM_PAT = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def numbers_in(text):
    text = ID_PAT.sub(" ", text)
    out = []
    for m in NUM_PAT.findall(text):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def unsupported_numbers(text, allowed, rel_tol=0.01):
    bad = []
    for n in numbers_in(text):
        if n < 10 and float(n).is_integer():          # small counts such as "3 steps"
            continue
        if not any(abs(n - a) <= max(0.051, rel_tol * abs(a)) for a in allowed):
            bad.append(n)
    return bad
