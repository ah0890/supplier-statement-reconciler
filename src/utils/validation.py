"""Upload validation and safe file-system helpers.

Uploaded financial documents are untrusted input. Before anything is parsed:

* the extension must be on an allow-list for that upload slot;
* the size must be within limits (and non-zero);
* the content signature must match the extension (a renamed executable is
  rejected even with a ``.csv`` name);
* XLSX archives are checked for excessive decompressed size (zip bombs);
* the stored file name is sanitised and the destination path is confined to
  the upload directory (path traversal protection).
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from pathlib import Path

_EXECUTABLE_SIGNATURES = (b"MZ", b"\x7fELF", b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"#!")
_MAX_XLSX_UNCOMPRESSED = 300 * 1024 * 1024
_MAX_XLSX_RATIO = 200
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


class UploadValidationError(ValueError):
    """User-facing reason why an upload was rejected."""


def sanitize_filename(filename: str, max_length: int = 100) -> str:
    """Return a safe file name: no directories, control characters or odd symbols."""
    name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
    name = name.replace("\\", "/").split("/")[-1]
    name = _SAFE_NAME.sub("_", name).strip(" .")
    if not name or name in {".", ".."}:
        name = "upload"
    stem, dot, suffix = name.rpartition(".")
    if dot and len(name) > max_length:
        name = stem[: max_length - len(suffix) - 1] + "." + suffix
    return name[:max_length]


def safe_join(base: Path, *parts: str) -> Path:
    """Join paths and guarantee the result stays inside ``base``."""
    base_resolved = base.resolve()
    candidate = base_resolved.joinpath(*parts).resolve()
    if not candidate.is_relative_to(base_resolved):
        raise UploadValidationError("Invalid file path.")
    return candidate


def _check_xlsx(data: bytes) -> None:
    if not data.startswith(b"PK\x03\x04"):
        raise UploadValidationError(
            "This file is not a valid .xlsx workbook. Older .xls files must be re-saved as .xlsx."
        )
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names:
                raise UploadValidationError("This file is not a valid .xlsx workbook.")
            if any(n.lower().endswith(("vbaproject.bin", ".exe", ".dll")) for n in names):
                raise UploadValidationError(
                    "Workbooks containing macros or embedded programs are not accepted."
                )
            total = sum(info.file_size for info in archive.infolist())
            if total > _MAX_XLSX_UNCOMPRESSED or total > len(data) * _MAX_XLSX_RATIO:
                raise UploadValidationError("This workbook is too large to process safely.")
    except zipfile.BadZipFile as exc:
        raise UploadValidationError("This .xlsx file is damaged and cannot be opened.") from exc


def _check_csv(data: bytes) -> None:
    if b"\x00" in data[:8192]:
        raise UploadValidationError(
            "This file does not look like a CSV text file. If it is a spreadsheet, "
            "upload it as .xlsx."
        )


def _check_pdf(data: bytes) -> None:
    if not data.lstrip()[:5] == b"%PDF-":
        raise UploadValidationError("This file is not a valid PDF document.")


def validate_upload(
    filename: str,
    data: bytes,
    *,
    allowed_extensions: frozenset[str],
    max_bytes: int,
) -> str:
    """Validate an uploaded file. Returns the normalised lower-case extension."""
    extension = Path(filename).suffix.lower()
    if extension not in allowed_extensions:
        allowed = ", ".join(sorted(e.lstrip(".").upper() for e in allowed_extensions))
        raise UploadValidationError(
            f"'{extension or 'no extension'}' files are not accepted here. Allowed: {allowed}."
        )
    if not data:
        raise UploadValidationError("The file is empty.")
    if len(data) > max_bytes:
        raise UploadValidationError(
            f"The file is {len(data) / 1_048_576:.1f} MB; the maximum is "
            f"{max_bytes / 1_048_576:.0f} MB."
        )
    if data.startswith(_EXECUTABLE_SIGNATURES):
        raise UploadValidationError("Executable files are not accepted.")

    checks = {".xlsx": _check_xlsx, ".csv": _check_csv, ".pdf": _check_pdf}
    checks[extension](data)
    return extension
