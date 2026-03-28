# SCPI Serial Monitor 📟

![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

**A robust, thread-safe desktop application to communicate with and debug SCPI-compatible programmable instruments.**

Whether you are controlling a professional oscilloscope, a bench multimeter, or debugging a custom Arduino-based SCPI instrument, this tool provides a clean GUI, macro management, and asynchronous reading across multiple communication protocols.



 ![Example screenshot](Screenshot.png)


## ✨ Features

- 🔌 **Multi-Protocol Support:**
  - **Serial (COM/USB):** Direct RS232/USB communication with baud rate selection.
  - **VISA:** Support for professional instruments via PyVISA (auto, NI, or Py backend).
  - **Raw Socket:** Direct TCP/IP connection to Ethernet-enabled instruments (usually port 5025).
- 🛡️ **Thread-Safe I/O:** Built-in lock mechanisms prevent race conditions between asynchronous unrequested output (e.g., a noisy serial line) and user queries.
- 🤖 **Macro Editor:** Create, edit, and save sequences of SCPI commands. Macros handle automatic delays between commands and wait for queries to complete.
- 📜 **Command History:** Use Up/Down arrows to navigate previously sent commands, or create a new Macro directly from your current history.
- 💾 **Session Memory:** The app remembers your recent connections (up to 12 profiles) and your last used UI settings, saving them in local `.json` files.
- ⚡ **Smart UI:** Buttons and inputs are dynamically enabled/disabled during command execution to prevent flooding the instrument with overlapping commands.

## 🛠️ Requirements

The application relies on Python's built-in `tkinter` for the GUI.
Depending on the protocols you plan to use, you will need the following Python packages:

Depending on the protocols you plan to use, you will need the following Python packages:

    pyserial>=3.5     # Required for Serial communication
    pyvisa>=1.13.0    # Required for VISA communication

*Note for VISA users: To use the `ni` backend, ensure you have the [NI-VISA Runtime](https://www.ni.com/en/support/downloads/software-products/download.ni-visa.html) installed on your system.*

## 🚀 Installation & Usage

1. **Clone the repository:**
       git clone https://github.com/GiuseppeLeviBo/SCPI_Serial_Monitor.git
       cd SCPI_Serial_Monitor

1. **Install dependencies:**
       pip install -r requirements.txt

   *(Note: Linux users might need to install Tkinter via their package manager, e.g., `sudo apt-get install python3-tk`).*

2. **Run the application:**
       python SCPI_serial_monitor.py
   
## 📖 How it Works

### Sending Commands
Type your SCPI command in the bottom input field and press `Enter` or click **Send**. 
- If the command ends with a `?` (e.g., `*IDN?`), the app will automatically treat it as a Query, waiting for a response and displaying it in the log.
- You can force a Query for commands that don't end in `?` by clicking the **Query** button.

### Macros
Macros allow you to automate testing sequences. 
- Click **New** to open the Macro Editor. 
- Write one command per line. 
- During execution, the application will pause for `0.1s` after standard write commands to allow slow instruments (like custom Arduino boards) to process the data, while it will actively wait for responses for query commands (`?`).
- You can Export/Import Macros to share them across different setups.

### Technical Detail: Thread Safety
When interfacing with embedded devices, an active background reader thread can accidentally "steal" bytes meant for a synchronous `query()`. This application solves this by wrapping I/O operations and the background reader loop in a `threading.Lock()`. This guarantees that when a query is sent, the UI waits safely, the background reader is paused, and the instrument's response is captured correctly without data corruption.


## 🧩 Combined Monitor (NEW)

The repository also includes `SCPI_combined_monitor.py`, a hybrid monitor that combines:
- the live monitoring logic from the serial monitor,
- the scripting/meta-command language from the core engine (`@conn`, `@target`, `@wait`, `@if`, `@halt`, `@call`, `@rts`, `@store`, `@readbin`, `@savebin`) with extensions `@startstore`, `@stopstore`, `@comment`.

Quick start:

```bash
python SCPI_combined_monitor.py
```

Example script:

```text
@conn gen serial COM3 115200
@target gen
@wait 1
*IDN?
MEAS:VOLT?
@if last < 1 @halt
```
 ![Example screenshot](Screenshot2.png)
## 🧠 Meta-command Syntax (`SCPI_combined_monitor.py`)

In the Combined Monitor, each line can be:
- a **meta-command** (starts with `@`), or
- a regular **SCPI command** (e.g., `*IDN?`, `MEAS:VOLT?`).

### Available Commands

#### `@conn`
Creates a connection and registers it with a name (target).

```text
@conn <name> serial <port> [baud=9600] [timeout_s=2.0] [terminator=\n]
@conn <name> visa <resource> [timeout_s=2.0] [backend=auto] [terminator=\n]
@conn <name> socket <host:port | host> [port=5025] [timeout_s=2.0] [terminator=\n]
```

Examples:
```text
@conn gen serial COM3 115200
@conn dmm visa USB0::0x0957::0x1798::MY12345678::INSTR 3 auto \n
@conn psu socket 192.168.1.55:5025 2 \n
```

#### `@target`
Selects the active target where subsequent SCPI commands are sent.

```text
@target <name>
```

Example:
```text
@target gen
@wait 1
*IDN?
```

#### `@wait`
Waits for a specified number of seconds.

```text
@wait <seconds>
```

Example:
```text
@wait 0.5
```

#### `@if`
Evaluates a condition and, if true, executes a supported action.

```text
@if <left> <op> <right> <action>
```

- `<left>` can be `last` (last query response) or a literal value.
- Supported `<op>` operators: `==`, `!=`, `>`, `<`, `>=`, `<=`.
- Supported actions:
  - `@halt`
  - `@wait <seconds>`

Examples:
```text
MEAS:VOLT?
@if last < 1 @halt
@if last >= 5 @wait 1
```

#### `@halt`
Stops script execution.

```text
@halt
```

#### `@call` / `@script`
Calls a saved script and creates a new frame in the execution stack.

```text
@call <script_name>
@script <script_name>
```

Notes:
- `@script` is an alias of `@call`.
- The script is searched in the Combined Monitor JSON index (`~/.scpi_combined_scripts.json`) and in the `~/.scpi_macros` folder.
- When the called script ends, execution automatically resumes in the caller.
- Script names with spaces are supported both without quotes (`@call final calibration`) and with quotes (`@call "final calibration"`).

Example:
```text
@call calibration
MEAS:VOLT?
```

#### `@rts`
Return To Script: forces an immediate return to the caller script (early exit from the current script).

```text
@rts
```

Typical use:
```text
MEAS:STAT?
@if last != OK @rts
```

#### `@store`
Saves the last textual value (`last`) in `lastres.csv` with timestamp, target, command, and measurement name.

```text
@store <label>
```

`<label>` can contain spaces (e.g., `@store channel 1 test` or `@store "channel 1 test"`).

CSV row format:
```text
DDMMYYYY HH:MM; <target>; <last_command>; <label>; <value|NOVAL>
```

Example:
```text
MEAS:VOLT?
@store volt
```

#### `@readbin`
Arms binary reading: the **next SCPI command** is sent as a write and the response is captured as raw bytes in `last_bin`.

```text
@readbin
```

Example:
```text
@readbin
WAV:DATA?
```

#### `@savebin`
Saves the binary content read by `@readbin` to a file.

```text
@savebin <filename>
```

Behavior:
- If no binary data is available, it raises an error.
- The filename is automatically enriched as:
  - `<stem>_<YYYYMMDD_HHMMSS>_<target><suffix>`

Example:
```text
@readbin
WAV:DATA?
@savebin wave.bin
```

#### `@startstore`
Enables automatic saving to `lastres.csv` of **every ASCII query response** from that point onward.

```text
@startstore [label]
```

Details:
- If `label` is omitted, `AUTO` is used.
- Saving occurs after every SCPI query that produces `last`.
- `label` can contain spaces (e.g., `@startstore burn in` or `@startstore "burn in"`).

Example:
```text
@startstore trend
MEAS:VOLT?
MEAS:CURR?
```

#### `@stopstore`
Disables automatic saving enabled by `@startstore`.

```text
@stopstore
```

#### `@comment`
Saves a comment row in `lastres.csv` (useful for annotating events, test steps, conditions).

```text
@comment <free text>
```

Example:
```text
@comment Start channel 1 sweep
```

### Useful Notes

- An SCPI query (command ending with `?`) stores the response in `last`.
- A binary read with `@readbin` stores bytes in `last_bin` (and clears `last`).
- Multi-line ASCII responses are correctly saved in `lastres.csv` using CSV quoting (they remain in the same record even if they contain newlines).
- On serial transport, the Combined Monitor applies a short buffering window after a query to reassemble additional ASCII lines that arrive right after the first response.
- In the Combined Monitor, if a serial query times out, a retry is automatically performed after 1 second.
- If you paste a script in one line with literal `\n` sequences (e.g., `@conn ...\n@target ...\n*IDN?`), the monitor automatically converts them into real new lines before execution.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
