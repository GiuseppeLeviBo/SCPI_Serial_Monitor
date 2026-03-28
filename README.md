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
       cd SCPI_Serial_Monitorr

1. **Install dependencies:**
       pip install -r requirements.txt

   *(Note: Linux users might need to install Tkinter via their package manager, e.g., `sudo apt-get install python3-tk`).*

2. **Run the application:**
       python SCPI_serial_monitor.py
   
## 📖 How it Works

### Sending Commands
Type your SCPI command in the bottom input field and press `Enter` or click **Invia**. 
- If the command ends with a `?` (e.g., `*IDN?`), the app will automatically treat it as a Query, waiting for a response and displaying it in the log.
- You can force a Query for commands that don't end in `?` by clicking the **Query** button.

### Macros
Macros allow you to automate testing sequences. 
- Click **Nuova** to open the Macro Editor. 
- Write one command per line. 
- During execution, the application will pause for `0.1s` after standard write commands to allow slow instruments (like custom Arduino boards) to process the data, while it will actively wait for responses for query commands (`?`).
- You can Export/Import Macros to share them across different setups.

### Technical Detail: Thread Safety
When interfacing with embedded devices, an active background reader thread can accidentally "steal" bytes meant for a synchronous `query()`. This application solves this by wrapping I/O operations and the background reader loop in a `threading.Lock()`. This guarantees that when a query is sent, the UI waits safely, the background reader is paused, and the instrument's response is captured correctly without data corruption.


## 🧩 Combined Monitor (NEW)

Ora il repository include anche `SCPI_combined_monitor.py`, un monitor ibrido che unisce:
- la logica di monitor live del serial monitor,
- il linguaggio di scripting/meta-comandi del core engine (`@conn`, `@target`, `@wait`, `@if`, `@halt`, `@call`, `@rts`, `@store`, `@readbin`, `@savebin`) con estensioni `@startstore`, `@stopstore`, `@comment`.

Avvio rapido:

```bash
python SCPI_combined_monitor.py
```

Esempio script:

```text
@conn gen serial COM3 115200
@target gen
@wait 1
*IDN?
MEAS:VOLT?
@if last < 1 @halt
```

## 🧠 Sintassi meta-comandi (`SCPI_combined_monitor.py`)

Nel Combined Monitor ogni riga può essere:
- un **meta-comando** (inizia con `@`), oppure
- un **comando SCPI** normale (es. `*IDN?`, `MEAS:VOLT?`).

### Comandi disponibili

#### `@conn`
Crea una connessione e la registra con un nome (target).

```text
@conn <nome> serial <porta> [baud=9600] [timeout_s=2.0] [terminatore=\n]
@conn <nome> visa <resource> [timeout_s=2.0] [backend=auto] [terminatore=\n]
@conn <nome> socket <host:port | host> [port=5025] [timeout_s=2.0] [terminatore=\n]
```

Esempi:
```text
@conn gen serial COM3 115200
@conn dmm visa USB0::0x0957::0x1798::MY12345678::INSTR 3 auto \n
@conn psu socket 192.168.1.55:5025 2 \n
```

#### `@target`
Seleziona il target attivo su cui inviare i comandi SCPI successivi.

```text
@target <nome>
```

Esempio:
```text
@target gen
@wait 1
*IDN?
```

#### `@wait`
Attende un certo numero di secondi.

```text
@wait <secondi>
```

Esempio:
```text
@wait 0.5
```

#### `@if`
Valuta una condizione e, se vera, esegue un'azione supportata.

```text
@if <left> <op> <right> <azione>
```

- `<left>` può essere `last` (ultima risposta query) o un valore letterale.
- `<op>` supportati: `==`, `!=`, `>`, `<`, `>=`, `<=`.
- Azioni supportate:
  - `@halt`
  - `@wait <secondi>`

Esempi:
```text
MEAS:VOLT?
@if last < 1 @halt
@if last >= 5 @wait 1
```

#### `@halt`
Ferma l'esecuzione dello script.

```text
@halt
```

#### `@call` / `@script`
Chiama uno script salvato e crea un nuovo frame nello stack di esecuzione.

```text
@call <nome_script>
@script <nome_script>
```

Note:
- `@script` è un alias di `@call`.
- Lo script viene cercato nell'indice JSON del Combined Monitor (`~/.scpi_combined_scripts.json`) e nella cartella `~/.scpi_macros`.
- Quando lo script chiamato termina, l'esecuzione riprende automaticamente dal chiamante.

Esempio:
```text
@call calibrazione
MEAS:VOLT?
```

#### `@rts`
Return To Script: forza il ritorno immediato allo script chiamante (uscita anticipata dallo script corrente).

```text
@rts
```

Uso tipico:
```text
MEAS:STAT?
@if last != OK @rts
```

#### `@store`
Salva l'ultimo valore testuale (`last`) in `lastres.csv` con timestamp, target, comando e nome misura.

```text
@store <label>
```

Formato riga CSV:
```text
DDMMYYYY HH:MM; <target>; <last_command>; <label>; <value|NOVAL>
```

Esempio:
```text
MEAS:VOLT?
@store volt
```

#### `@readbin`
Arma la lettura binaria: il **prossimo comando SCPI** viene inviato come write e la risposta viene acquisita come byte raw in `last_bin`.

```text
@readbin
```

Esempio:
```text
@readbin
WAV:DATA?
```

#### `@savebin`
Salva su file il contenuto binario letto con `@readbin`.

```text
@savebin <filename>
```

Comportamento:
- Se non ci sono dati binari disponibili, genera errore.
- Il nome file viene arricchito automaticamente come:
  - `<stem>_<YYYYMMDD_HHMMSS>_<target><suffix>`

Esempio:
```text
@readbin
WAV:DATA?
@savebin wave.bin
```

#### `@startstore`
Abilita il salvataggio automatico in `lastres.csv` di **ogni risposta ASCII di query** da quel punto in poi.

```text
@startstore [label]
```

Dettagli:
- Se `label` è omesso viene usato `AUTO`.
- Il salvataggio viene effettuato dopo ogni query SCPI che produce `last`.

Esempio:
```text
@startstore trend
MEAS:VOLT?
MEAS:CURR?
```

#### `@stopstore`
Disabilita il salvataggio automatico attivato da `@startstore`.

```text
@stopstore
```

#### `@comment`
Salva una riga di commento in `lastres.csv` (utile per annotare eventi, step test, condizioni).

```text
@comment <testo libero>
```

Esempio:
```text
@comment Inizio sweep canale 1
```

### Note utili

- Una query SCPI (comando che termina con `?`) salva la risposta in `last`.
- Una lettura binaria con `@readbin` salva i byte in `last_bin` (e azzera `last`).
- Le risposte ASCII multi-riga vengono salvate correttamente in `lastres.csv` tramite quoting CSV (restano nello stesso record anche se contengono newline).
- Sul transport seriale, il Combined Monitor applica una breve finestra di bufferizzazione dopo la query per ricomporre eventuali righe ASCII addizionali arrivate subito dopo la prima risposta.
- Nel Combined Monitor, se una query seriale va in timeout, viene effettuato automaticamente un retry dopo 1s.
- Se incolli uno script in una sola riga con sequenze letterali `\n` (es. `@conn ...\n@target ...\n*IDN?`), il monitor le converte automaticamente in nuove righe prima dell'esecuzione.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
