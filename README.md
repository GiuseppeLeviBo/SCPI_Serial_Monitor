# SCPI Serial & Combined Monitor 📟

![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

A desktop toolkit for communicating with and automating SCPI-compatible instruments.

This repository currently ships two applications:

- **SCPI Serial Monitor** (`SCPI_serial_monitor.py`)
- **SCPI Combined Monitor V2** (`SCPI_combined_monitor_V2.py`)
- **SCPI Combined Monitor V3 (plugin-ready)** (`SCPI_combined_monitor_V3.py`)

![Serial Monitor screenshot](Screenshot.png)
![Combined Monitor V2 screenshot](Screenshot2.png)

---

## What's new

- Updated both applications:
  - `SCPI_serial_monitor.py`
  - `SCPI_combined_monitor_V2.py`
- Added Windows-ready binaries:
  - `pyinstaller/SCPI_serial_monitor.exe`
  - `pyinstaller/SCPI_combined_monitor_V2.exe`
- Legacy Combined Monitor removed (V2 is the supported Combined Monitor).
- **Serial Monitor:** import/export workflow for scripts/macros shared with Combined Monitor.
- **Combined Monitor V2:** DSL command autocomplete in the editor.

---

## 1) SCPI Serial Monitor

Run:

```bash
python SCPI_serial_monitor.py
```

### Main features

- Serial / VISA / Socket transport support
- Thread-safe I/O and async RX log
- Macro editor and macro execution
- Command history
- Recent connection profiles

### Import / Export interoperability

From the Macro panel:

- **Export** writes macros as JSON.
- **Import** accepts:
  - `.json` macro collections
  - `.scpi` script files (imported as macro commands, one line per command)

The **Copia @conn** button copies a DSL-ready connection snippet (`@conn ...` + `@target ...`) to the clipboard so it can be pasted directly into Combined Monitor scripts.

---

## 2) SCPI Combined Monitor V2

Run:

```bash
python SCPI_combined_monitor_V2.py
```

### Main features

- Workspace-based `.scpi` scripts
- Multi-tab editor
- Runtime log
- Step-by-step debugger
- Full DSL engine (flow control, variables, calls, binary/CSV helpers)

### Autocomplete (new)

The editor now supports autocomplete for:

- DSL meta-commands (`@...`)
- Built-in names

Keys:

- **Tab / Enter**: accept suggestion
- **Up / Down**: navigate suggestions
- **Esc**: close suggestion popup

---

## 3) SCPI Combined Monitor V3 (Advanced / Plugin)

Run:

```bash
python SCPI_combined_monitor_V3.py
```

V3 preserves all V2 behavior and adds a startup plugin loader:

- At launch, V3 scans `plugins/*.py`
- Each plugin module can expose `register(engine)`
- Plugin commands are callable in DSL (`@plot`, `@fft`, `@filter`, ...)
- Built-in DSL commands cannot be overridden
- Plugin specs can be injected at runtime for autocomplete via `engine.register_dsl_spec(...)`

See:

- `plugins/README.md`
- `plugins/_example_plugin.py` (template, disabled by default)

---

## DSL command documentation

The full DSL language manual (commands, behavior, examples, and test checklist) is preserved in:

- **[`DSL_REFERENCE.md`](DSL_REFERENCE.md)**

---

## Requirements

- Python 3.8+
- Dependencies from `requirements.txt`

Install:

```bash
pip install -r requirements.txt
```

For VISA with NI backend, install NI-VISA Runtime.

---

## Installation

```bash
git clone https://github.com/GiuseppeLeviBo/SCPI_Serial_Monitor.git
cd SCPI_Serial_Monitor
pip install -r requirements.txt
```

Linux users may need Tkinter from system packages (for example `python3-tk`).

---

## Windows binaries

Prebuilt executables are available in `pyinstaller/`:

- `SCPI_serial_monitor.exe`
- `SCPI_combined_monitor_V2.exe`

---

## License

MIT License. See [LICENSE](LICENSE).
