import json
import queue
import re
import socket
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional
import contextlib

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter.scrolledtext import ScrolledText

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

try:
    import pyvisa
except ImportError:
    pyvisa = None


APP_NAME = "SCPI Serial Monitor"
MACRO_FILE = Path.home() / ".scpi_monitor_macros.json"
SETTINGS_FILE = Path.home() / ".scpi_monitor_settings.json"
RECENTS_FILE = Path.home() / ".scpi_monitor_recents.json"
MAX_RECENTS = 12


def is_query_command(command: str) -> bool:
    stripped = command.strip()
    if not stripped:
        return False
    keyword = stripped.split(maxsplit=1)[0]
    return "?" in keyword


@dataclass
class Macro:
    name: str
    commands: List[str]


@dataclass
class RecentConnection:
    label: str
    conn_type: str
    serial_port: str = ""
    baudrate: str = "9600"
    visa_resource: str = ""
    visa_backend: str = "auto"
    socket_host: str = ""
    socket_port: str = "5025"
    timeout_s: str = "2"
    terminator: str = "\n"


class TransportBase:
    def connect(self):
        raise NotImplementedError

    def disconnect(self):
        raise NotImplementedError

    def write(self, data: str):
        raise NotImplementedError

    def query(self, data: str) -> str:
        raise NotImplementedError

    def read_available(self) -> Optional[str]:
        return None

    @property
    def is_connected(self) -> bool:
        raise NotImplementedError


class SerialTransport(TransportBase):
    def __init__(self, port: str, baudrate: int, timeout: float, terminator: str = "\n"):
        if serial is None:
            raise RuntimeError("pyserial non installato")
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.terminator = terminator
        self.ser = None

    def connect(self):
        self.ser = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

    def disconnect(self):
        if self.ser is not None:
            self.ser.close()
            self.ser = None

    def write(self, data: str):
        payload = (data + self.terminator).encode("utf-8", errors="replace")
        self.ser.write(payload)
        self.ser.flush()

    def query(self, data: str) -> str:
        last_reply = b""
        for attempt in range(2):
            self.write(data)
            reply = self.ser.readline()

            if not reply:
                # Alcuni device seriali non inviano '\n': prova a leggere ciò che c'è nel buffer.
                waiting = getattr(self.ser, "in_waiting", 0)
                if waiting:
                    reply = self.ser.read(waiting)

            if reply:
                return reply.decode("utf-8", errors="replace").rstrip("\r\n")

            last_reply = reply or b""
            if attempt == 0:
                # Primo timeout: piccola attesa e retry (utile per device che si inizializzano dopo open).
                time.sleep(min(0.2, max(self.timeout, 0.0)))

        if last_reply:
            return last_reply.decode("utf-8", errors="replace").rstrip("\r\n")
        raise TimeoutError("Nessuna risposta dal dispositivo (Timeout).")

    def read_available(self) -> Optional[str]:
        if self.ser is None or not self.ser.is_open:
            return None
        try:
            waiting = self.ser.in_waiting
            if waiting <= 0:
                return None
            data = self.ser.read(waiting)
            return data.decode("utf-8", errors="replace")
        except Exception:
            # Errore I/O (es. cavo scollegato)
            return None

    @property
    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open


class VisaTransport(TransportBase):
    def __init__(self, resource_name: str, timeout_ms: int = 2000, terminator: str = "\n", backend: str = "auto"):
        if pyvisa is None:
            raise RuntimeError("pyvisa non installato")
        self.resource_name = resource_name
        self.timeout_ms = timeout_ms
        self.terminator = terminator
        self.backend = backend
        self.rm = None
        self.inst = None
        self.backend_used = None

    @staticmethod
    def _try_open_resource_manager(spec: Optional[str]):
        if spec:
            return pyvisa.ResourceManager(spec)
        return pyvisa.ResourceManager()

    def connect(self):
        last_error = None
        if self.backend == "ni":
            backend_order = [("ni", "@ni")]
        elif self.backend == "py":
            backend_order = [("py", "@py")]
        else:
            backend_order = [("ni", "@ni"), ("py", "@py"), ("default", None)]

        for backend_name, spec in backend_order:
            rm = None
            inst = None
            try:
                rm = self._try_open_resource_manager(spec)
                inst = rm.open_resource(self.resource_name)
                inst.timeout = self.timeout_ms
                inst.read_termination = self.terminator
                inst.write_termination = self.terminator
                self.rm = rm
                self.inst = inst
                self.backend_used = backend_name
                return
            except Exception as exc:
                last_error = exc
                with contextlib.suppress(Exception):
                    if inst is not None:
                        inst.close()
                with contextlib.suppress(Exception):
                    if rm is not None:
                        rm.close()

        raise RuntimeError(f"Impossibile aprire VISA resource con backend '{self.backend}': {last_error}")

    def disconnect(self):
        if self.inst is not None:
            try:
                self.inst.close()
            finally:
                self.inst = None
        if self.rm is not None:
            try:
                self.rm.close()
            finally:
                self.rm = None

    def write(self, data: str):
        self.inst.write(data)

    def query(self, data: str) -> str:
        try:
            return self.inst.query(data)
        except pyvisa.errors.VisaIOError as e:
            raise TimeoutError(f"Errore di IO VISA (Timeout?): {e.description}")

    def read_available(self) -> Optional[str]:
        if self.inst is None:
            return None
        original_timeout = self.inst.timeout
        try:
            # Lettura non bloccante best-effort per drenare eventuale output residuo.
            self.inst.timeout = 1
            try:
                data = self.inst.read()
            except pyvisa.errors.VisaIOError:
                return None
            return data if data else None
        finally:
            self.inst.timeout = original_timeout

    @property
    def is_connected(self) -> bool:
        return self.inst is not None


class RawSocketScpiTransport(TransportBase):
    def __init__(self, host: str, port: int = 5025, timeout: float = 2.0, terminator: str = "\n"):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.terminator = terminator
        self.sock = None

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)

    def disconnect(self):
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
                self.sock.close()
            except Exception:
                pass
            finally:
                self.sock = None

    def write(self, data: str):
        payload = (data + self.terminator).encode("utf-8", errors="replace")
        self.sock.sendall(payload)

    def query(self, data: str) -> str:
        self.write(data)
        chunks = []
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                break
            if not chunk: # Peer disconnesso
                break
            chunks.append(chunk)
            if chunk.endswith(self.terminator.encode()):
                break
                
        if not chunks:
            raise TimeoutError("Nessuna risposta ricevuta dal socket (Timeout).")
            
        return b"".join(chunks).decode("utf-8", errors="replace").rstrip("\r\n")

    def read_available(self) -> Optional[str]:
        if self.sock is None:
            return None
        chunks = []
        original_timeout = self.sock.gettimeout()
        try:
            self.sock.setblocking(False)
            while True:
                try:
                    chunk = self.sock.recv(4096)
                except BlockingIOError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            self.sock.settimeout(original_timeout)
        if not chunks:
            return None
        return b"".join(chunks).decode("utf-8", errors="replace")

    @property
    def is_connected(self) -> bool:
        if self.sock is None:
            return False
        try:
            # Controllo reale sullo stato del socket (fallisce se disconnesso remotamente)
            self.sock.getpeername()
            return True
        except OSError:
            return False


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1100x760")
        self.minsize(920, 640)

        self.transport: Optional[TransportBase] = None
        self.reader_thread = None
        self.reader_stop = threading.Event()
        self.rx_queue: queue.Queue = queue.Queue()
        
        self.io_lock = threading.Lock()
        self.command_busy = False
        self.post_write_delay = 0.1 # Tempo di ritardo standard dopo una scrittura (no query)

        self.history: List[str] = []
        self.history_index: Optional[int] = None
        self.macros: List[Macro] = []
        self.recents: List[RecentConnection] = []

        self._build_ui()
        self._load_macros()
        self._load_recents()
        self._load_settings()
        self._refresh_ports()
        
        self._update_connection_fields()
        self._update_ui_state()
        
        self._pump_rx_queue()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        # --- TOP FRAME: Impostazioni Connessione ---
        top_container = ttk.Frame(self, padding=10)
        top_container.pack(fill="x")

        conn_frame = ttk.LabelFrame(top_container, text="Impostazioni Connessione", padding=10)
        conn_frame.pack(fill="x")

        self.conn_type = tk.StringVar(value="serial")
        ttk.Label(conn_frame, text="Tipo:").grid(row=0, column=0, sticky="w")
        self.conn_box = ttk.Combobox(conn_frame, textvariable=self.conn_type, values=["serial", "visa", "socket"], state="readonly", width=10)
        self.conn_box.grid(row=0, column=1, padx=(4, 15))
        self.conn_box.bind("<<ComboboxSelected>>", lambda e: self._update_connection_fields())

        ttk.Label(conn_frame, text="Timeout (s):").grid(row=0, column=2, sticky="w")
        self.timeout_s = tk.StringVar(value="2")
        self.timeout_entry = ttk.Entry(conn_frame, textvariable=self.timeout_s, width=6)
        self.timeout_entry.grid(row=0, column=3, padx=(4, 15))

        ttk.Label(conn_frame, text="Terminatore:").grid(row=0, column=4, sticky="w")
        self.terminator = tk.StringVar(value="\\n")
        self.terminator_entry = ttk.Entry(conn_frame, textvariable=self.terminator, width=6)
        self.terminator_entry.grid(row=0, column=5, padx=(4, 15))

        self.btn_connect = ttk.Button(conn_frame, text="Connetti", command=self.connect)
        self.btn_connect.grid(row=0, column=6, padx=4)
        
        self.btn_disconnect = ttk.Button(conn_frame, text="Disconnetti", command=self.disconnect)
        self.btn_disconnect.grid(row=0, column=7, padx=4)

        param_frame = ttk.Frame(conn_frame)
        param_frame.grid(row=1, column=0, columnspan=8, sticky="w", pady=(10, 0))

        ttk.Label(param_frame, text="Seriale:").pack(side="left")
        self.serial_port = tk.StringVar()
        self.serial_combo = ttk.Combobox(param_frame, textvariable=self.serial_port, width=22, state="normal")
        self.serial_combo.pack(side="left", padx=(4, 2))
        self.btn_refresh = ttk.Button(param_frame, text="↻", command=self._refresh_ports, width=3)
        self.btn_refresh.pack(side="left", padx=(0, 15))

        ttk.Label(param_frame, text="Baud:").pack(side="left")
        self.baudrate = tk.StringVar(value="9600")
        self.baud_entry = ttk.Entry(param_frame, textvariable=self.baudrate, width=8)
        self.baud_entry.pack(side="left", padx=(4, 25))

        ttk.Label(param_frame, text="VISA:").pack(side="left")
        self.visa_resource = tk.StringVar(value="TCPIP0::192.168.0.10::inst0::INSTR")
        self.visa_entry = ttk.Entry(param_frame, textvariable=self.visa_resource, width=30)
        self.visa_entry.pack(side="left", padx=(4, 15))

        ttk.Label(param_frame, text="Backend:").pack(side="left")
        self.visa_backend = tk.StringVar(value="auto")
        self.visa_backend_combo = ttk.Combobox(param_frame, textvariable=self.visa_backend, values=["auto", "ni", "py"], state="readonly", width=8)
        self.visa_backend_combo.pack(side="left", padx=(4, 25))

        ttk.Label(param_frame, text="Host:").pack(side="left")
        self.socket_host = tk.StringVar(value="192.168.0.10")
        self.socket_host_entry = ttk.Entry(param_frame, textvariable=self.socket_host, width=15)
        self.socket_host_entry.pack(side="left", padx=(4, 15))

        ttk.Label(param_frame, text="Port:").pack(side="left")
        self.socket_port = tk.StringVar(value="5025")
        self.socket_port_entry = ttk.Entry(param_frame, textvariable=self.socket_port, width=6)
        self.socket_port_entry.pack(side="left", padx=(4, 0))

        # --- MIDDLE FRAME: Log, Comandi, Macro ---
        mid = ttk.Panedwindow(self, orient="horizontal")
        mid.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        left = ttk.Frame(mid, padding=4)
        right = ttk.Frame(mid, padding=4)
        mid.add(left, weight=4)
        mid.add(right, weight=1)

        self.log = ScrolledText(left, wrap="word", font=("Consolas", 10), state="disabled")
        self.log.pack(fill="both", expand=True)

        bottom = ttk.Frame(left)
        bottom.pack(fill="x", pady=(8, 0))

        ttk.Label(bottom, text="Comando:").pack(side="left")
        self.command_var = tk.StringVar()
        self.command_entry = ttk.Entry(bottom, textvariable=self.command_var)
        self.command_entry.pack(fill="x", side="left", expand=True, padx=(4, 8))
        self.command_entry.bind("<Return>", lambda e: self.send_command())
        self.command_entry.bind("<Up>", self._history_up)
        self.command_entry.bind("<Down>", self._history_down)

        self.btn_send = ttk.Button(bottom, text="Invia", command=self.send_command)
        self.btn_send.pack(side="left", padx=2)
        
        self.btn_query = ttk.Button(bottom, text="Query", command=lambda: self.send_command(force_query=True))
        self.btn_query.pack(side="left", padx=2)
        
        ttk.Button(bottom, text="Pulisci Log", command=self.clear_log).pack(side="left", padx=2)

        # --- RIGHT FRAME ---
        macro_bar = ttk.Frame(right)
        macro_bar.pack(fill="x")
        
        ttk.Label(macro_bar, text="Connessioni recenti:").pack(anchor="w")
        self.recent_combo = ttk.Combobox(right, state="readonly")
        self.recent_combo.pack(fill="x", pady=(4, 4))
        self.recent_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_selected_recent())

        recent_buttons = ttk.Frame(right)
        recent_buttons.pack(fill="x", pady=(0, 15))
        ttk.Button(recent_buttons, text="Carica", command=self.apply_selected_recent).pack(side="left", fill="x", expand=True, padx=(0,2))
        ttk.Button(recent_buttons, text="Rimuovi", command=self.delete_selected_recent).pack(side="right", fill="x", expand=True, padx=(2,0))

        ttk.Label(right, text="Macro salvate:").pack(anchor="w")
        self.macro_list = tk.Listbox(right, height=10)
        self.macro_list.pack(fill="both", expand=False, pady=(4, 4))
        self.macro_list.bind("<Double-1>", lambda e: self.open_macro_editor_from_selection())

        macro_buttons_1 = ttk.Frame(right)
        macro_buttons_1.pack(fill="x", pady=(0, 2))
        ttk.Button(macro_buttons_1, text="Esegui", command=self.run_selected_macro).pack(side="left", fill="x", expand=True, padx=(0,2))
        ttk.Button(macro_buttons_1, text="Nuova", command=self.open_macro_editor).pack(side="right", fill="x", expand=True, padx=(2,0))
        
        macro_buttons_2 = ttk.Frame(right)
        macro_buttons_2.pack(fill="x", pady=(0, 2))
        ttk.Button(macro_buttons_2, text="Modifica", command=self.open_macro_editor_from_selection).pack(side="left", fill="x", expand=True, padx=(0,2))
        ttk.Button(macro_buttons_2, text="Da History", command=self.create_macro_from_history).pack(side="right", fill="x", expand=True, padx=(2,0))

        macro_buttons_3 = ttk.Frame(right)
        macro_buttons_3.pack(fill="x", pady=(10, 2))
        ttk.Button(macro_buttons_3, text="Importa", command=self.import_macros).pack(side="left", fill="x", expand=True, padx=(0,2))
        ttk.Button(macro_buttons_3, text="Esporta", command=self.export_macros).pack(side="right", fill="x", expand=True, padx=(2,0))
        ttk.Button(right, text="Elimina Macro", command=self.delete_selected_macro).pack(fill="x", pady=(2, 0))

        hist_frame = ttk.LabelFrame(right, text="History Corrente")
        hist_frame.pack(fill="both", expand=True, pady=(15, 0))
        self.history_list = tk.Listbox(hist_frame)
        self.history_list.pack(fill="both", expand=True, padx=4, pady=4)
        self.history_list.bind("<Double-1>", self._history_list_to_entry)

        # --- STATUS BAR ---
        status_frame = ttk.Frame(self, relief="sunken", padding=(2, 2))
        status_frame.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar(value="Pronto - Disconnesso")
        ttk.Label(status_frame, textvariable=self.status_var, font=("Segoe UI", 9)).pack(side="left", padx=4)

    def _update_ui_state(self):
        is_conn = self.transport is not None and self.transport.is_connected
        
        if is_conn:
            self.btn_connect.state(["disabled"])
            self.btn_disconnect.state(["!disabled"])
            
            self.conn_box.state(["disabled"])
            self.timeout_entry.state(["disabled"])
            self.terminator_entry.state(["disabled"])
            self.serial_combo.configure(state="disabled")
            self.baud_entry.state(["disabled"])
            self.visa_entry.state(["disabled"])
            self.visa_backend_combo.state(["disabled"])
            self.socket_host_entry.state(["disabled"])
            self.socket_port_entry.state(["disabled"])
            self.btn_refresh.state(["disabled"])
            
            cmd_state = ["disabled"] if self.command_busy else ["!disabled"]
            self.btn_send.state(cmd_state)
            self.btn_query.state(cmd_state)
            self.command_entry.state(cmd_state)
        else:
            self.btn_connect.state(["!disabled"])
            self.btn_disconnect.state(["disabled"])
            
            self.conn_box.state(["readonly"])
            self.timeout_entry.state(["!disabled"])
            self.terminator_entry.state(["!disabled"])
            self.btn_refresh.state(["!disabled"])
            
            self.btn_send.state(["disabled"])
            self.btn_query.state(["disabled"])
            self.command_entry.state(["disabled"])
            
            self._update_connection_fields()

    def _update_connection_fields(self):
        if self.transport is not None and self.transport.is_connected:
            return

        mode = self.conn_type.get()
        self.serial_combo.configure(state="disabled")
        self.baud_entry.state(["disabled"])
        self.visa_entry.state(["disabled"])
        self.visa_backend_combo.state(["disabled"])
        self.socket_host_entry.state(["disabled"])
        self.socket_port_entry.state(["disabled"])

        if mode == "serial":
            # Campo editabile: permette inserimento manuale su SO dove la rilevazione non funziona.
            # La tendina continua a mostrare tutte le porte rilevate.
            self.serial_combo.configure(state="normal")
            self.baud_entry.state(["!disabled"])
        elif mode == "visa":
            self.visa_entry.state(["!disabled"])
            self.visa_backend_combo.state(["readonly"])
        elif mode == "socket":
            self.socket_host_entry.state(["!disabled"])
            self.socket_port_entry.state(["!disabled"])

    def _set_command_busy(self, busy: bool):
        self.command_busy = busy
        self._update_ui_state()

    def _finish_command_busy(self):
        self.command_busy = False
        self._update_ui_state()
        if self.transport and self.transport.is_connected:
            self.command_entry.focus_set()

    def _translate_terminator(self) -> str:
        value = self.terminator.get()
        return value.encode("utf-8").decode("unicode_escape")

    def _refresh_ports(self):
        ports = []
        if list_ports is not None:
            ports = [p.device for p in list_ports.comports()]
        self.serial_combo["values"] = ports

        current = self.serial_port.get().strip()
        if not current and ports:
            self.serial_port.set(ports[0])

        self._update_connection_fields()

    def _append_log(self, text: str, kind: str = "INFO"):
        timestamp = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{timestamp}] {kind}: {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _set_status(self, text: str):
        self.status_var.set(text)

    # --- MACRO EDITOR E LOGICA ---
    def _load_macros(self):
        self.macros = []
        if MACRO_FILE.exists():
            try:
                data = json.loads(MACRO_FILE.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    raise ValueError("Il file macros non contiene una lista.")
                for item in data:
                    if "name" in item and "commands" in item:
                        self.macros.append(Macro(name=item["name"], commands=item["commands"]))
            except Exception as exc:
                self._append_log(f"Errore caricamento macro: {exc}", "ERR")
        self._refresh_macro_list()

    def _save_macros(self):
        payload = [asdict(m) for m in self.macros]
        MACRO_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _refresh_macro_list(self):
        self.macro_list.delete(0, "end")
        for macro in self.macros:
            self.macro_list.insert("end", macro.name)

    def open_macro_editor_from_selection(self, event=None):
        sel = self.macro_list.curselection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Seleziona una macro da modificare.")
            return
        idx = sel[0]
        self.open_macro_editor(existing_macro=self.macros[idx], index=idx)

    def create_macro_from_history(self):
        if not self.history:
            messagebox.showinfo(APP_NAME, "La history è vuota.")
            return
        self.open_macro_editor(prefill_commands=list(self.history))

    def open_macro_editor(self, existing_macro: Macro = None, index: int = None, prefill_commands: List[str] = None):
        editor = tk.Toplevel(self)
        editor.title("Editor Macro" if not existing_macro else f"Modifica Macro: {existing_macro.name}")
        editor.geometry("400x500")
        editor.transient(self)
        editor.grab_set()

        ttk.Label(editor, text="Nome Macro:").pack(anchor="w", padx=10, pady=(10, 2))
        name_var = tk.StringVar(value=existing_macro.name if existing_macro else "")
        name_entry = ttk.Entry(editor, textvariable=name_var)
        name_entry.pack(fill="x", padx=10)
        name_entry.focus_set()

        ttk.Label(editor, text="Comandi (uno per riga):").pack(anchor="w", padx=10, pady=(10, 2))
        text_area = ScrolledText(editor, wrap="none", font=("Consolas", 10))
        text_area.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        if existing_macro:
            text_area.insert("1.0", "\n".join(existing_macro.commands))
        elif prefill_commands:
            text_area.insert("1.0", "\n".join(prefill_commands))

        def save_macro():
            m_name = name_var.get().strip()
            m_cmds = [line.strip() for line in text_area.get("1.0", "end-1c").split("\n") if line.strip()]
            
            if not m_name:
                messagebox.showwarning("Attenzione", "Il nome della macro è obbligatorio.", parent=editor)
                return
            if not m_cmds:
                messagebox.showwarning("Attenzione", "Inserisci almeno un comando.", parent=editor)
                return
            
            new_macro = Macro(name=m_name, commands=m_cmds)
            if index is not None:
                self.macros[index] = new_macro
                self._append_log(f"Macro '{m_name}' aggiornata.", "INFO")
            else:
                self.macros.append(new_macro)
                self._append_log(f"Macro '{m_name}' creata.", "INFO")
                
            self._save_macros()
            self._refresh_macro_list()
            editor.destroy()

        btn_frame = ttk.Frame(editor)
        btn_frame.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(btn_frame, text="Salva", command=save_macro).pack(side="right", padx=(4, 0))
        ttk.Button(btn_frame, text="Annulla", command=editor.destroy).pack(side="right")

    def run_selected_macro(self):
        sel = self.macro_list.curselection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Seleziona una macro da eseguire.")
            return
        if self.transport is None or not self.transport.is_connected:
            messagebox.showwarning(APP_NAME, "Nessuna connessione attiva.")
            return
        if self.command_busy:
            self._append_log("Attendere il completamento del comando corrente", "INFO")
            return
            
        macro = self.macros[sel[0]]
        self._set_command_busy(True)

        def worker():
            self.rx_queue.put(("INFO", f"Esecuzione macro '{macro.name}'"))
            try:
                # Lock globale sulle IO durante la MACRO
                with self.io_lock:
                    for cmd in macro.commands:
                        self.rx_queue.put(("TX", cmd))
                        if is_query_command(cmd):
                            reply = self.transport.query(cmd)
                            self.rx_queue.put(("RX", reply))
                        else:
                            self.transport.write(cmd)
                            time.sleep(self.post_write_delay)
                self.rx_queue.put(("INFO", f"Macro '{macro.name}' completata"))
            except TimeoutError as te:
                self.rx_queue.put(("ERR", f"Timeout nella macro: {te}"))
            except Exception as exc:
                self.rx_queue.put(("ERR", f"Macro interrotta: {exc}"))
            finally:
                self.after(0, self._finish_command_busy)

        threading.Thread(target=worker, daemon=True).start()

    def export_macros(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        payload = [asdict(m) for m in self.macros]
        Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        self._append_log(f"Macro esportate in {path}", "INFO")

    def import_macros(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("Il file deve contenere una lista JSON valida.")
            
            imported = 0
            for item in data:
                if "name" in item and "commands" in item:
                    self.macros.append(Macro(name=item["name"], commands=item["commands"]))
                    imported += 1
            
            self._save_macros()
            self._refresh_macro_list()
            self._append_log(f"Importate {imported} macro da {path}", "INFO")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Errore nell'importazione:\n{exc}")
            self._append_log(f"Import macro fallito: {exc}", "ERR")

    def delete_selected_macro(self):
        sel = self.macro_list.curselection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Seleziona la macro da eliminare.")
            return
        macro = self.macros.pop(sel[0])
        self._save_macros()
        self._refresh_macro_list()
        self._append_log(f"Macro '{macro.name}' eliminata", "INFO")

    # --- RECENTI E SETTINGS ---
    def _load_recents(self):
        self.recents = []
        if RECENTS_FILE.exists():
            try:
                data = json.loads(RECENTS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self.recents = [RecentConnection(**item) for item in data if isinstance(item, dict)]
            except Exception as exc:
                self._append_log(f"Errore caricamento recenti: {exc}", "ERR")
        self._refresh_recent_list()

    def _save_recents(self):
        payload = [asdict(item) for item in self.recents[:MAX_RECENTS]]
        RECENTS_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _refresh_recent_list(self):
        labels = [item.label for item in self.recents]
        self.recent_combo["values"] = labels
        if labels and not self.recent_combo.get():
            self.recent_combo.set(labels[0])

    def _capture_current_connection(self) -> RecentConnection:
        mode = self.conn_type.get()
        if mode == "serial":
            label = f"SERIAL {self.serial_port.get()} @ {self.baudrate.get()}"
        elif mode == "visa":
            label = f"VISA {self.visa_resource.get()} [{self.visa_backend.get()}]"
        else:
            label = f"SOCKET {self.socket_host.get()}:{self.socket_port.get()}"

        return RecentConnection(
            label=label,
            conn_type=mode,
            serial_port=self.serial_port.get(),
            baudrate=self.baudrate.get(),
            visa_resource=self.visa_resource.get(),
            visa_backend=self.visa_backend.get(),
            socket_host=self.socket_host.get(),
            socket_port=self.socket_port.get(),
            timeout_s=self.timeout_s.get(),
            terminator=self.terminator.get(),
        )

    def _store_recent_connection(self):
        item = self._capture_current_connection()
        self.recents = [r for r in self.recents if r.label != item.label]
        self.recents.insert(0, item)
        self.recents = self.recents[:MAX_RECENTS]
        self._save_recents()
        self._refresh_recent_list()
        self.recent_combo.set(item.label)

    def apply_selected_recent(self):
        label = self.recent_combo.get()
        if not label:
            return
        match = next((item for item in self.recents if item.label == label), None)
        if match is None:
            return
        self.conn_type.set(match.conn_type)
        self.serial_port.set(match.serial_port)
        self.baudrate.set(match.baudrate)
        self.visa_resource.set(match.visa_resource)
        self.visa_backend.set(match.visa_backend)
        self.socket_host.set(match.socket_host)
        self.socket_port.set(match.socket_port)
        self.timeout_s.set(match.timeout_s)
        self.terminator.set(match.terminator)
        self._update_connection_fields()
        self._append_log(f"Configurazione recente caricata: {label}", "INFO")

    def delete_selected_recent(self):
        label = self.recent_combo.get()
        if not label:
            return
        self.recents = [item for item in self.recents if item.label != label]
        self._save_recents()
        self._refresh_recent_list()
        self.recent_combo.set("")
        self._append_log(f"Configurazione recente rimossa: {label}", "INFO")

    def _load_settings(self):
        if not SETTINGS_FILE.exists():
            return
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            self.conn_type.set(data.get("conn_type", "serial"))
            self.serial_port.set(data.get("serial_port", ""))
            self.baudrate.set(str(data.get("baudrate", "9600")))
            self.visa_resource.set(data.get("visa_resource", self.visa_resource.get()))
            self.visa_backend.set(data.get("visa_backend", self.visa_backend.get()))
            self.socket_host.set(data.get("socket_host", self.socket_host.get()))
            self.socket_port.set(str(data.get("socket_port", self.socket_port.get())))
            self.timeout_s.set(str(data.get("timeout_s", self.timeout_s.get())))
            self.terminator.set(data.get("terminator", "\\n"))
        except Exception as exc:
            self._append_log(f"Errore caricamento settings: {exc}", "ERR")

    def _save_settings(self):
        data = {
            "conn_type": self.conn_type.get(),
            "serial_port": self.serial_port.get(),
            "baudrate": self.baudrate.get(),
            "visa_resource": self.visa_resource.get(),
            "visa_backend": self.visa_backend.get(),
            "socket_host": self.socket_host.get(),
            "socket_port": self.socket_port.get(),
            "timeout_s": self.timeout_s.get(),
            "terminator": self.terminator.get(),
        }
        SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _escape_for_script(value: str) -> str:
        return value.encode("unicode_escape").decode("ascii")

    @staticmethod
    def _suggest_target_name(mode: str, endpoint: str) -> str:
        base = re.sub(r"[^a-zA-Z0-9_]+", "_", endpoint.strip()).strip("_")
        if not base:
            base = f"{mode}_dev"
        if base[0].isdigit():
            base = f"dev_{base}"
        return base.lower()

    def _build_conn_history_command(self, mode: str, timeout: float, terminator: str) -> str:
        escaped_term = self._escape_for_script(terminator)
        if mode == "serial":
            endpoint = self.serial_port.get().strip()
            target = self._suggest_target_name(mode, endpoint)
            return f"@conn {target} serial {endpoint} {self.baudrate.get().strip()} {timeout:g} {escaped_term}"
        if mode == "visa":
            endpoint = self.visa_resource.get().strip()
            target = self._suggest_target_name(mode, endpoint)
            return f"@conn {target} visa {endpoint} {timeout:g} {self.visa_backend.get().strip()} {escaped_term}"

        host = self.socket_host.get().strip()
        port = self.socket_port.get().strip()
        target = self._suggest_target_name(mode, host)
        return f"@conn {target} socket {host}:{port} {timeout:g} {escaped_term}"

    # --- HISTORY E INPUT ---
    def _push_history(self, cmd: str):
        if not cmd:
            return
        if not self.history or self.history[-1] != cmd:
            self.history.append(cmd)
            self.history_list.insert("end", cmd)
        self.history_index = None

    def _history_up(self, event=None):
        if not self.history:
            return "break"
        if self.history_index is None:
            self.history_index = len(self.history) - 1
        else:
            self.history_index = max(0, self.history_index - 1)
        self.command_var.set(self.history[self.history_index])
        self.command_entry.icursor("end")
        return "break"

    def _history_down(self, event=None):
        if not self.history:
            return "break"
        if self.history_index is None:
            return "break"
        self.history_index += 1
        if self.history_index >= len(self.history):
            self.history_index = None
            self.command_var.set("")
        else:
            self.command_var.set(self.history[self.history_index])
            self.command_entry.icursor("end")
        return "break"

    def _history_list_to_entry(self, event=None):
        sel = self.history_list.curselection()
        if not sel:
            return
        self.command_var.set(self.history_list.get(sel[0]))
        self.command_entry.focus_set()
        self.command_entry.icursor("end")

    # --- COMUNICAZIONE ---
    def _reader_loop(self):
            """Thread background che legge dati non sollecitati."""
            rx_buffer = ""  # <--- NUOVO: Buffer per ricostruire le righe spezzate
            
            while not self.reader_stop.is_set():
                try:
                    if self.transport and self.transport.is_connected:
                        with self.io_lock:
                            msg = self.transport.read_available()
                        
                        if msg:
                            rx_buffer += msg
                            # Finché c'è un 'a capo' nel buffer, estrai la riga completa
                            while '\n' in rx_buffer:
                                line, rx_buffer = rx_buffer.split('\n', 1)
                                clean_line = line.strip()
                                # Se la riga non è vuota, mandala alla GUI
                                if clean_line: 
                                    self.rx_queue.put(("RX", clean_line))
                                    
                except Exception as exc:
                    self.rx_queue.put(("ERR", f"Reader fermato: {exc}"))
                    break
                time.sleep(0.05)

    def _pump_rx_queue(self):
        try:
            while True:
                kind, text = self.rx_queue.get_nowait()
                self._append_log(text, kind)
        except queue.Empty:
            pass
        self.after(100, self._pump_rx_queue)

    def connect(self):
        # Chiude preventivamente senza spammare log fasulli
        was_connected_before = self.transport is not None and self.transport.is_connected
        self.reader_stop.set()
        if was_connected_before:
            try:
                self.transport.disconnect()
            except Exception:
                pass
        self.transport = None
        
        term = self._translate_terminator()
        try:
            timeout = float(self.timeout_s.get())
        except ValueError:
            messagebox.showerror(APP_NAME, "Timeout non valido.")
            return

        mode = self.conn_type.get()
        try:
            if mode == "serial":
                self._refresh_ports()
                selected_port = self.serial_port.get().strip()
                if not selected_port:
                    raise ValueError("Nessuna porta seriale selezionata.")
                self.transport = SerialTransport(
                    port=selected_port,
                    baudrate=int(self.baudrate.get()),
                    timeout=timeout,
                    terminator=term,
                )
            elif mode == "visa":
                self.transport = VisaTransport(
                    resource_name=self.visa_resource.get(),
                    timeout_ms=int(timeout * 1000),
                    terminator=term,
                    backend=self.visa_backend.get(),
                )
            else:
                self.transport = RawSocketScpiTransport(
                    host=self.socket_host.get(),
                    port=int(self.socket_port.get()),
                    timeout=timeout,
                    terminator=term,
                )

            self.transport.connect()
            self._set_status(f"Connesso via {mode.upper()}")
            self._append_log(f"Connessione aperta ({mode.upper()})", "INFO")
            self._push_history(self._build_conn_history_command(mode, timeout, term))
            self._save_settings()
            self._store_recent_connection()

            if isinstance(self.transport, VisaTransport) and self.transport.backend_used:
                self._append_log(f"Backend VISA utilizzato: {self.transport.backend_used}", "INFO")

            if mode == "serial":
                self.reader_stop.clear()
                self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
                self.reader_thread.start()
                
            self._update_ui_state()

        except Exception as exc:
            self.transport = None
            self._set_status("Errore di connessione")
            self._append_log(str(exc), "ERR")
            self._update_ui_state()
            messagebox.showerror(APP_NAME, f"Connessione fallita:\n{exc}")

    def disconnect(self):
        self.reader_stop.set()
        if self.transport is not None:
            was_connected = self.transport.is_connected
            try:
                self.transport.disconnect()
                if was_connected:
                    self._append_log("Connessione chiusa", "INFO")
            except Exception as exc:
                self._append_log(f"Errore in disconnect: {exc}", "ERR")
        self.transport = None
        self._set_status("Pronto - Disconnesso")
        self._update_ui_state()

    def send_command(self, force_query: bool = False):
        cmd = self.command_var.get().strip()
        if not cmd:
            return
        if self.transport is None or not self.transport.is_connected:
            messagebox.showwarning(APP_NAME, "Nessuna connessione attiva")
            return
        if self.command_busy:
            self._append_log("Attendere il completamento del comando corrente", "INFO")
            return

        self._push_history(cmd)
        self._append_log(cmd, "TX")
        self.command_var.set("")
        self._set_command_busy(True)

        def worker():
            try:
                with self.io_lock:
                    expect_reply = force_query or is_query_command(cmd)
                    if expect_reply:
                        reply = self.transport.query(cmd)
                        self.rx_queue.put(("RX", reply))
                    else:
                        self.transport.write(cmd)
                        time.sleep(self.post_write_delay)
            except TimeoutError as te:
                self.rx_queue.put(("ERR", str(te)))
            except Exception as exc:
                self.rx_queue.put(("ERR", f"Errore I/O: {exc}"))
            finally:
                self.after(0, self._finish_command_busy)

        threading.Thread(target=worker, daemon=True).start()

    def on_close(self):
        self._save_settings()
        self.disconnect()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
