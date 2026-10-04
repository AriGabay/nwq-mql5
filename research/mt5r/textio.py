"""Text I/O for MT5 files: UTF-16 with a byte-order mark, otherwise an 8-bit fallback codec."""
import hashlib
import pathlib

BOMS = (b"\xff\xfe", b"\xfe\xff")


def read_text(path, fallback: str = "utf-8", errors: str = "strict", utf16_errors: str = "strict") -> str:
    data = pathlib.Path(path).read_bytes()
    if data[:2] in BOMS:
        return data.decode("utf-16", utf16_errors)
    return data.decode(fallback, errors)


def write_utf16(path, text: str) -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))


def sha256(path) -> str:
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()
