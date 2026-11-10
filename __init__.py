"""Linux CUPS printing tools for Hermes.

All print jobs are explicit and must target an installed CUPS queue.
The plugin never runs automatically or updates itself.
"""

import json
import logging
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

logger = logging.getLogger(__name__)
_MAX_COPIES = 5
_MAX_TEXT_CHARS = 5000
_MAX_FILE_BYTES = 20 * 1024 * 1024
_MAX_PAGES = 20
_SAFE_PATH = "/usr/local/bin:/usr/bin:/bin"
_config_getter = None


def _tool_schema(name, description, properties, required=()):
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object", "properties": properties, "required": list(required)
        },
    }


_PRINTER_PROPERTY = {
    "type": "string",
    "description": "Optional installed CUPS printer queue; otherwise use configured/system default.",
}
_COPIES_PROPERTY = {"type": "integer", "description": "Copies (1-5; default 1)."}

SCHEMAS = {
    "printer_status": _tool_schema(
        "printer_status",
        "List installed CUPS queues and the system/plugin default, plus status and queued jobs.",
        {"printer": _PRINTER_PROPERTY},
    ),
    "print_file": _tool_schema(
        "print_file",
        "Submit a local JPEG/PNG/PDF/TXT/MD file to an installed CUPS printer. "
        "PDF/text are converted to JPEG to support printers that reject PDF jobs. "
        "File limit 20MB, 20 pages; files must be inside the user's home or temp dir. "
        "Returns submission job IDs, not proof of physical printing.",
        {
            "path": {"type": "string", "description": "Absolute local path (home or temp)."},
            "printer": _PRINTER_PROPERTY,
            "copies": _COPIES_PROPERTY,
        },
        ("path",),
    ),
    "print_text": _tool_schema(
        "print_text",
        "Print short plain text with automatic word wrapping (maximum 5000 characters), "
        "using CUPS texttopdf and Ghostscript raster conversion.",
        {
            "text": {"type": "string", "description": "Text to print, max 5000 characters."},
            "title": {"type": "string", "description": "Optional CUPS job title."},
            "printer": _PRINTER_PROPERTY,
            "copies": _COPIES_PROPERTY,
        },
        ("text",),
    ),
    "print_letter": _tool_schema(
        "print_letter",
        "Print a beautifully laid-out one-page 300-DPI letter with wrapped body, "
        "styled headline, signature and accents. Never handwrite raw PDF bytes. "
        "Fails before submission if content cannot fit the page. "
        "Reports submission, NOT physical output.",
        {
            "title": {"type": "string", "description": "Headline (1-140 characters)."},
            "body": {"type": "string", "description": "Paragraphs separated by blank lines (max 5000 characters)."},
            "signature": {"type": "string", "description": "Name to sign, defaults to Hermes."},
            "style": {"type": "string", "enum": ["classic", "romantic"],
                      "description": "Letter style: classic (default) or romantic."},
            "printer": _PRINTER_PROPERTY,
            "copies": _COPIES_PROPERTY,
        },
        ("title", "body"),
    ),
}


def register(ctx):
    global _config_getter
    _config_getter = getattr(ctx, "get_config", None)
    for name, handler in (
        ("printer_status", _handle_printer_status),
        ("print_file", _handle_print_file),
        ("print_text", _handle_print_text),
        ("print_letter", _handle_print_letter),
    ):
        ctx.register_tool(name=name, toolset="print-tools", schema=SCHEMAS[name],
                          handler=handler)


def _child_env():
    """Do not pass Hermes credentials or runtime controls to child processes."""
    return {
        "PATH": _SAFE_PATH,
        "HOME": pwd.getpwuid(os.getuid()).pw_dir,
        "LANG": "C.UTF-8",
    }


def _run(args, input_bytes=None, timeout=60):
    return subprocess.run(args, input=input_bytes, capture_output=True,
                          timeout=timeout, check=False, env=_child_env())


def _binary(name):
    executable = shutil.which(name, path=_SAFE_PATH)
    if not executable:
        raise RuntimeError(
            f"{name} not installed; CUPS clients and Ghostscript are required."
        )
    return executable


def _configured_printer():
    if not _config_getter:
        return ""
    name = _config_getter("default_printer", default="")
    return name.strip() if isinstance(name, str) else ""


def _installed_printers():
    result = _run([_binary("lpstat"), "-p"], timeout=15)
    output = result.stdout.decode("utf-8", errors="replace")
    queues = []
    for line in output.splitlines():
        found = re.match(r"^printer\s+(\S+)", line)
        if found:
            queues.append(found.group(1))
    if result.returncode != 0 and not queues:
        raise RuntimeError("Cannot query CUPS: " +
                           result.stderr.decode("utf-8", errors="replace")[-300:])
    return queues


def _system_default():
    result = _run([_binary("lpstat"), "-d"], timeout=15)
    if result.returncode:
        return ""
    found = re.search(r"system default destination:\s*(\S+)",
                      result.stdout.decode("utf-8", errors="replace"))
    return found.group(1) if found else ""


def _resolve_printer(selected=None):
    queues = _installed_printers()
    if not queues:
        raise ValueError("No installed CUPS printers. Configure a printer first.")
    if selected is not None and not isinstance(selected, str):
        raise ValueError("printer must be a CUPS queue name")
    chosen = (selected or _configured_printer() or _system_default()).strip()
    if not chosen and len(queues) == 1:
        chosen = queues[0]
    if not chosen:
        raise ValueError("Multiple printer queues exist; select one explicitly.")
    if chosen not in queues:
        raise ValueError(f"Unknown CUPS queue {chosen!r}; available: {', '.join(queues)}")
    return chosen


def _copies(value):
    if value is None:
        return 1
    if isinstance(value, bool) or not (
        isinstance(value, int) or (isinstance(value, str) and value.isdigit())
    ):
        raise ValueError("copies must be an integer 1-5")
    value = int(value)
    if not 1 <= value <= _MAX_COPIES:
        raise ValueError("copies must be 1-5")
    return value


def _local_path(value):
    if not isinstance(value, str) or not value:
        raise ValueError("path is required")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("path must be absolute")
    path = path.resolve()
    roots = [Path.home().resolve(), Path(tempfile.gettempdir()).resolve()]
    if not any(path == root or root in path.parents for root in roots):
        raise ValueError("path must reside under the current user's home or temp directory")
    if not path.is_file():
        raise ValueError(f"file not found: {path}")
    if path.stat().st_size > _MAX_FILE_BYTES:
        raise ValueError("file exceeds the 20 MB limit")
    return path


def _printer_status(printer=None):
    selected = _resolve_printer(printer) if printer else None
    args = [_binary("lpstat"), "-p"]
    if selected:
        args.append(selected)
    status = _run(args, timeout=15)
    jobs_args = [_binary("lpstat"), "-o"]
    if selected:
        jobs_args.append(selected)
    jobs = _run(jobs_args, timeout=15)
    return ("Selected default: " + (_configured_printer() or _system_default() or "(none)")
            + "\n" + status.stdout.decode("utf-8", errors="replace").strip()
            + "\nQueued jobs:\n"
            + (jobs.stdout.decode("utf-8", errors="replace").strip() or "(none)"))


def _print_jpeg(path, copies, printer, *, letter=False):
    argv = [_binary("lp"), "-d", printer, "-n", str(copies)]
    if letter:
        argv += ["-o", "media=Letter", "-o", "fit-to-page"]
    argv.append(str(path))
    result = _run(argv, timeout=30)
    out = result.stdout.decode("utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError("CUPS rejected print job: " +
                           result.stderr.decode("utf-8", errors="replace")[-300:])
    found = re.search(r"request id is (\S+)", out)
    if not found:
        raise RuntimeError("CUPS command returned success but no job ID; "
                           "check the queue before retrying to avoid duplicates.")
    return found.group(1)


def _pdf_images(pdf, directory):
    pattern = str(directory / "page-%d.jpg")
    proc = _run([_binary("gs"), "-dSAFER", "-dBATCH", "-dNOPAUSE",
                 "-sDEVICE=jpeg", "-r150", f"-sOutputFile={pattern}",
                 str(pdf)], timeout=120)
    jpegs = sorted(directory.glob("page-*.jpg"),
                   key=lambda path: int(path.stem.rsplit("-", 1)[-1]))
    if proc.returncode or not jpegs:
        raise RuntimeError("PDF rasterization failed: " +
                           proc.stderr.decode("utf-8", errors="replace")[-350:])
    if len(jpegs) > _MAX_PAGES:
        raise ValueError("Document has more than 20 pages; no jobs submitted")
    return jpegs


def _plain_text(text):
    paragraphs = []
    for line in text.splitlines():
        # The CUPS texttopdf filter uses a legacy font encoding.
        cleaned = "".join(
            ch if ord(ch) < 256 else "?" if ch.isprintable() else ""
            for ch in line if not (0x1F000 <= ord(ch) <= 0x1FAFF or
                                  ord(ch) in (0xFE0F, 0x200D))
        )
        paragraphs.extend(textwrap.wrap(cleaned, width=78,
                                         break_long_words=False) or [""])
    return "\n".join(paragraphs)


def _text_pdf(text, pdf, title):
    filter_paths = (
        Path("/usr/lib/cups/filter/texttopdf"),
        Path("/usr/libexec/cups/filter/texttopdf"),
    )
    texttopdf = next((str(path) for path in filter_paths if path.is_file()), None)
    if not texttopdf:
        raise RuntimeError("CUPS texttopdf filter missing (install cups-filters)")
    output = _run([texttopdf, "1", "hermes", title, "1", ""],
                  input_bytes=_plain_text(text).encode("utf-8", errors="replace"),
                  timeout=30)
    if output.returncode or not output.stdout.startswith(b"%PDF"):
        raise RuntimeError("CUPS texttopdf failed: " +
                           output.stderr.decode("utf-8", errors="replace")[-350:])
    pdf.write_bytes(output.stdout)


def _result(label, printer, jobs):
    suffix = ""
    try:
        suffix = "\n" + _printer_status(printer)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        suffix = f"\nQueue status unavailable: {exc}"
    return (f"Submitted {label} to CUPS queue {printer}: "
            + ", ".join(jobs)
            + ". CUPS acceptance is not confirmation of physical printing."
            + suffix)


def _print_file(args):
    path = _local_path(args.get("path"))
    copies = _copies(args.get("copies"))
    printer = _resolve_printer(args.get("printer"))
    with tempfile.TemporaryDirectory(prefix="hermes-print-") as folder:
        temp = Path(folder)
        suffix = path.suffix.lower()
        if suffix in (".jpg", ".jpeg", ".png"):
            images = [path]
        elif suffix == ".pdf":
            images = _pdf_images(path, temp)
        elif suffix in (".txt", ".text", ".md"):
            contents = path.read_text(encoding="utf-8", errors="replace")
            if len(contents) > _MAX_TEXT_CHARS * 4:
                raise ValueError("Text file is too long (max 20000 characters)")
            pdf = temp / "input.pdf"
            _text_pdf(contents, pdf, path.stem[:100])
            images = _pdf_images(pdf, temp)
        else:
            raise ValueError("Unsupported file type (expected JPEG, PNG, PDF, TXT, MD)")
        # Multi-page submission cannot be atomic: if later pages fail,
        # report already-submitted job IDs rather than claiming no printing.
        jobs = []
        for image in images:
            try:
                jobs.append(_print_jpeg(image, copies, printer))
            except Exception as exc:
                raise RuntimeError(f"{exc}. Already submitted: {', '.join(jobs) or 'none'}") from exc
        return _result(path.name, printer, jobs)


def _print_text(args):
    text = args.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > _MAX_TEXT_CHARS:
        raise ValueError("text must be nonempty and no longer than 5000 characters")
    title = args.get("title") or "Hermes text"
    if not isinstance(title, str):
        raise ValueError("title must be a string")
    title = title.replace("\n", " ").replace("\r", " ")[:100]
    printer, copies = _resolve_printer(args.get("printer")), _copies(args.get("copies"))
    with tempfile.TemporaryDirectory(prefix="hermes-print-") as folder:
        directory = Path(folder)
        pdf = directory / "text.pdf"
        _text_pdf(text, pdf, title)
        images = _pdf_images(pdf, directory)
        jobs = []
        for image in images:
            try:
                jobs.append(_print_jpeg(image, copies, printer))
            except Exception as exc:
                raise RuntimeError(f"{exc}. Already submitted: {', '.join(jobs) or 'none'}") from exc
        return _result(title, printer, jobs)


def _print_letter(args):
    title, body = args.get("title"), args.get("body")
    signature = args.get("signature", "Hermes")
    if not isinstance(title, str) or not title.strip() or len(title) > 140:
        raise ValueError("title must be 1-140 characters")
    if not isinstance(body, str) or not body.strip() or len(body) > _MAX_TEXT_CHARS:
        raise ValueError("body must be 1-5000 characters")
    if not isinstance(signature, str) or not signature.strip() or len(signature) > 90:
        raise ValueError("signature must be 1-90 characters")
    style = args.get("style", "classic")
    if style not in ("classic", "romantic"):
        raise ValueError("style must be classic or romantic")
    copies = _copies(args.get("copies"))
    printer = _resolve_printer(args.get("printer"))
    with tempfile.TemporaryDirectory(prefix="hermes-print-") as folder:
        directory = Path(folder)
        jpg, pdf = directory / "letter.jpg", directory / "letter.pdf"
        renderer = Path(__file__).resolve().parent / "letter_renderer.py"
        payload = json.dumps({"title": title, "body": body, "signature": signature,
                              "style": style})
        proc = _run([sys.executable, str(renderer), "--jpeg", str(jpg),
                     "--pdf", str(pdf)],
                    input_bytes=payload.encode("utf-8"), timeout=100)
        if proc.returncode:
            raise RuntimeError("Letter layout failed before printing: " +
                               proc.stderr.decode("utf-8", errors="replace")[-400:])
        info = json.loads(proc.stdout.decode("utf-8", errors="replace"))
        if info.get("pages") != 1 or info.get("body_lines", 0) < 2:
            raise RuntimeError("Invalid one-page letter rendering")
        if not jpg.is_file() or jpg.stat().st_size < 15000:
            raise RuntimeError("Rendered letter is missing or blank")
        job = _print_jpeg(jpg, copies, printer, letter=True)
    return _result("one styled US Letter page", printer, [job])


def _call(name, action, args):
    try:
        return action(args or {})
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired,
            json.JSONDecodeError) as exc:
        logger.warning("%s: %s", name, exc)
        return f"{name}: error: {exc}"
    except Exception:
        logger.exception("%s: unexpected printing failure", name)
        return f"{name}: error: unexpected failure; verify queue before retrying"


def _handle_printer_status(args, session_id="", **kwargs):
    return _call("printer_status", lambda a: _printer_status(a.get("printer")), args)


def _handle_print_file(args, session_id="", **kwargs):
    return _call("print_file", _print_file, args)


def _handle_print_text(args, session_id="", **kwargs):
    return _call("print_text", _print_text, args)


def _handle_print_letter(args, session_id="", **kwargs):
    return _call("print_letter", _print_letter, args)
