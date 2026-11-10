# Hermes Print Tools

Printer-independent printing for [Hermes Agent](https://github.com/NousResearch/hermes-agent) on Linux using CUPS. This plugin works with any printer installed as a CUPS queue, including USB printers and network IPP/AirPrint printers.

It was developed against an image-only printer that rejected PDF submissions. PDF and plain text are therefore rasterized to JPEG before submission, rather than assuming every printer accepts PDF. No special HP driver, fixed network address, cloud account or paid API is needed.

## Tools

| Tool | Action |
|------|--------|
| **printer_status** | List configured printers, the default queue, printer status, and pending jobs |
| **print_file** | Print a local JPEG, PNG, PDF, TXT or Markdown document (20 MiB / 20 page limit) |
| **print_text** | Print up to 5000 characters of word-wrapped plain text |
| **print_letter** | Compose a designed one-page 300 DPI letter, with selectable classic or romantic styling |

Printing tools accept optional **printer** (the exact queue name reported by printer_status) and **copies** (1-5, default 1). A job picks the printer in this order: explicit tool argument; plugin setting default_printer; operating-system default; or the only installed printer. If no unambiguous selection can be made, printing is refused.

The tools return the CUPS job ID after submission. This **does not** guarantee physical output; a printer can still fail or run out of paper.

## Requirements

- Linux with a running CUPS scheduler and installed CUPS queues
- The **lp** and **lpstat** commands (usually from cups-client / cups)
- Ghostscript **gs** for PDF or text conversion
- The CUPS **texttopdf** filter for plain text (usually cups-filters)
- Installed fonts such as DejaVu and URW Base35 (fontconfig recommended)
- Pillow >=11.0,<13, installed automatically by the Hermes plugin manager when the plugin is enabled with Python dependencies approved

Debian/Ubuntu setup:

    sudo apt install cups cups-client cups-filters ghostscript fonts-dejavu-core fonts-urw-base35 fontconfig

Add a CUPS printer using your operating system's printer setup first. Confirm the queues and default:

    lpstat -p -d

## Install

Until this plugin's catalog entry has been reviewed and merged:

    hermes plugins install alxspiker/hermes-print-tools --yes-deps --enable
    hermes plugins doctor print-tools

Optionally set **default_printer** in the plugin's Settings screen in Hermes Desktop. Alternatively, edit Hermes configuration to set the value under plugins.entries.print-tools.settings.default_printer. Leave it empty to use the system default.

The printing commands can be enabled for specific platforms by adding the **print-tools** toolset to that platform's list in the Hermes configuration, if it is not already available there.

## Updates

The plugin intentionally has **no self-updater**.

- For a direct GitHub installation, use **hermes plugins update print-tools** to follow the source's recorded update policy.
- For a catalog installation (once approved), use the same command. Catalog releases are pinned to an exact, human-reviewed commit. A new version requires another pull request to NousResearch/hermes-agent that advances the pinned SHA.
- No plugin process reaches out to GitHub or downloads its own code.

## Security and behavior disclosures

**Physical effects:** Each print call consumes actual paper and ink. The plugin has no automatic printing, cron jobs, background worker, or auto-approval bypass. Only explicit Hermes tool calls initiate printing. Review printed material before sending it through an agent if it comes from an untrusted source.

**Host file reads:** The print_file tool reads a user-specified path and only permits files inside the current user's resolved home directory or the system temporary directory. Symlinks resolving outside these areas are rejected. The size limit is 20 MiB; converted documents may contain up to 20 pages. print_text and print_letter work only from strings passed in their tool arguments.

**Host commands:** The plugin invokes CUPS lpstat, CUPS lp, Ghostscript gs with safer mode, CUPS texttopdf and the current Python interpreter to generate PDF/JPEG pages. Font lookup may invoke fc-match. Commands are passed as argument arrays without shell execution, with an explicit, minimal child environment (trusted system PATH, account HOME, and UTF-8 LANG). No API keys or other ambient Hermes environment variables are forwarded.

**Network:** CUPS may send jobs to whichever local or network printer the user has configured, including IPP endpoints. The plugin does not make independent external HTTP requests, gather usage data, or send telemetry. No credentials are accessed or stored.

**Persistence:** Temporary render files are removed after submission. No plugin-specific daemon or recurring task is created. If an individual page in a multi-page job fails to submit, earlier job IDs may already be accepted; the error identifies them to avoid accidental duplicate printing.

**Input safety:** The renderer measures line widths with installed fonts and refuses to produce a clipped page. Long letters that cannot fit the one-page layout are rejected, not truncated. Letter styles can be **classic** (default) or **romantic**. Emoji that cannot be rendered with installed fonts may be omitted rather than producing black boxes.

## Development

Run tests without printing:

    python3 -m unittest discover -s tests -v

Check the plugin against Hermes' official admission rules:

    hermes plugins validate . --install-deps

Maintained by [alxspiker](https://github.com/alxspiker). MIT-licensed.
