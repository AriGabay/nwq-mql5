"""EA input extraction and MT5 tester .set files (UTF-16LE with BOM)."""
import dataclasses
import pathlib
import re

INPUT_RE = re.compile(r"^\s*input\s+(\w+)\s+(\w+)\s*=\s*([^;]+);\s*(?://\s*(.*))?$", re.M)
ENUM_RE = re.compile(r"enum\s+(\w+)\s*\{(.*?)\};", re.S)
ENUM_MEMBER_RE = re.compile(r"(\w+)\s*=\s*(-?\d+)")
BUILTIN_ENUMS = {
    "PERIOD_M1": 1, "PERIOD_M5": 5, "PERIOD_M15": 15, "PERIOD_M30": 30, "PERIOD_H1": 16385,
    "PERIOD_H4": 16388, "PERIOD_D1": 16408,
}
NUMERIC = {"int", "uint", "long", "ulong", "short", "ushort", "char", "uchar", "double", "float"}


@dataclasses.dataclass
class InputSpec:
    type: str
    name: str
    default: str   # value as it appears in a .set file
    comment: str


def enum_values(text: str) -> dict:
    out = dict(BUILTIN_ENUMS)
    for _, body in ENUM_RE.findall(text):
        for name, value in ENUM_MEMBER_RE.findall(body):
            out[name] = int(value)
    return out


def _strip_research(text: str) -> str:
    return re.sub(r"#ifdef RESEARCH_LOG.*?#endif[^\n]*\n", "", text, flags=re.S)


def parse_inputs(text: str, research: bool = False) -> list:
    """Inputs in declaration order; research-only inputs are included only when research=True."""
    text = text.replace("\r\n", "\n")
    enums = enum_values(text)
    body = text if research else _strip_research(text)
    specs = []
    for typ, name, raw, comment in INPUT_RE.findall(body):
        specs.append(InputSpec(typ, name, _set_value(typ, raw.strip(), enums), (comment or "").strip()))
    return specs


def _set_value(typ: str, raw: str, enums: dict) -> str:
    if typ == "string":
        return raw.strip('"')
    if typ == "bool":
        return raw.lower()
    if typ in NUMERIC:
        return raw
    if raw in enums:
        return str(enums[raw])
    raise ValueError(f"cannot resolve default {raw!r} for type {typ}")


def render_lines(specs: list, values: dict = None, optimize: dict = None) -> list:
    """[TesterInputs]/.set lines. `values` overrides defaults; `optimize` maps name -> (start, step, stop)."""
    values = dict(values or {})
    optimize = dict(optimize or {})
    unknown = (set(values) | set(optimize)) - {s.name for s in specs}
    if unknown:
        raise KeyError(f"unknown inputs: {sorted(unknown)}")
    lines = []
    for s in specs:
        v = _fmt(values.get(s.name, s.default))
        if s.type == "string":
            lines.append(f"{s.name}={v}")
            continue
        if s.name in optimize:
            start, step, stop = (_fmt(x) for x in optimize[s.name])
            lines.append(f"{s.name}={v}||{start}||{step}||{stop}||Y")
        else:
            lines.append(f"{s.name}={v}||{v}||0||{v}||N")
    return lines


def _fmt(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)
    return str(v)


def write_set(path, lines: list, header: str = "") -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(f"; {h}\r\n" for h in header.splitlines() if h) + "\r\n".join(lines) + "\r\n"
    path.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))


def read_set(path) -> dict:
    """name -> value (first field) from a .set file (UTF-16 or UTF-8)."""
    data = pathlib.Path(path).read_bytes()
    text = data.decode("utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else data.decode("utf-8-sig")
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";") or "=" not in line:
            continue
        name, rest = line.split("=", 1)
        out[name.strip()] = rest.split("||")[0]
    return out
