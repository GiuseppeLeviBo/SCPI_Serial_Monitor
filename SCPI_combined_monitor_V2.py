import json
import threading
import time
import tkinter as tk
import re
import contextlib
import csv
import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox, simpledialog, filedialog
from tkinter.scrolledtext import ScrolledText
from typing import Callable, Dict, List, Optional

from SCPI_serial_monitor import (
    RawSocketScpiTransport,
    SerialTransport,
    VisaTransport,
    is_query_command,
)

APP_NAME = "SCPI Combined Monitor"
SCRIPT_INDEX_FILE = Path.home() / ".scpi_combined_scripts.json"
SCRIPT_DIR = Path.home() / ".scpi_macros"


@dataclass
class TargetConnection:
    name: str
    transport: object


import math
import operator
import shlex
import time
import contextlib
import csv
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

# Assicurati di avere in cima al file i tuoi import originali:
# from SCPI_serial_monitor import ( RawSocketScpiTransport, SerialTransport, VisaTransport, is_query_command )

class CombinedScriptEngine:
    """Motore script con supporto Variabili globali, Loop, Math e predisposizione Debugger."""

    def __init__(
        self,
        logger: Callable[[str, str], None],
        history_logger: Optional[Callable[[str], None]] = None,
        script_loader: Optional[Callable[[str], List[str]]] = None,
    ):
        self.logger = logger
        self.history_logger = history_logger
        self.script_loader = script_loader
        
        # --- Stato Connessioni e Strumenti ---
        self.targets: Dict[str, TargetConnection] = {}
        self.current_target: Optional[str] = None
        
        self.last: Optional[str] = None
        self.last_bin: Optional[bytes] = None
        self.last_command: Optional[str] = None
        self.readbin_armed = False
        
        # --- Stato del DSL (Variabili e Loop) ---
        self.variables: Dict[str, any] = {}
        self.loop_stack: List[Dict[str, any]] = []
        self.call_stack: List[Dict[str, object]] =[]
        
        # --- Predisposizione Debugger e UI ---
        self.stop_requested = False
        self.step_event = threading.Event()
        self.step_event.set()  # Di default corre libero
        self.breakpoints: set[int] = set() 
        self.on_state_change: Optional[Callable[[], None]] = None
        self.prompt_callback: Optional[Callable[[str], None]] = None
        
        # --- Impostazioni di Logging e Timeout ---
        self.auto_store_enabled = False
        self.auto_store_label = "AUTO"
        self.serial_query_retry_delay_s = 1.0
        self.serial_pre_query_flush = True
        self.serial_multiline_idle_s = 0.12

        # Ambiente sicuro per @eval (solo funzioni matematiche standard)
        self._math_env = {k: v for k, v in math.__dict__.items() if not k.startswith("__")}

    def reset_runtime(self):
        self.current_target = None
        self.last = None
        self.last_bin = None
        self.last_command = None
        self.stop_requested = False
        self.readbin_armed = False
        self.call_stack =[]
        self.variables.clear()
        self.loop_stack.clear()
        self.step_event.set()
        self.auto_store_enabled = False
        self.auto_store_label = "AUTO"

    def close_all(self):
        for tc in self.targets.values():
            try:
                tc.transport.disconnect()
            except Exception:
                pass
        self.targets.clear()

    # ==========================================
    # CORE DSL: Variabili ed Espressioni
    # ==========================================
    def _resolve_value(self, val_str: str) -> any:
        """Converte una stringa in numero, variabile o mantiene la stringa."""
        val_str = val_str.strip(" '\"")
        v_lower = val_str.lower()
        
        if v_lower == "last":
            val_str = self.last if self.last is not None else "0"
            v_lower = str(val_str).lower()
            
        if v_lower in self.variables:
            return self.variables[v_lower]
            
        try:
            return float(val_str)
        except ValueError:
            return val_str

    def _evaluate_condition(self, left_raw: str, op: str, right_raw: str) -> bool:
        """Valuta espressioni logiche per @if e @while."""
        left = self._resolve_value(left_raw)
        right = self._resolve_value(right_raw)

        try:
            left = float(left)
            right = float(right)
        except (ValueError, TypeError):
            left = str(left)
            right = str(right)

        ops = {
            "==": operator.eq, "!=": operator.ne,
            ">": operator.gt, "<": operator.lt,
            ">=": operator.ge, "<=": operator.le
        }
        
        if op not in ops:
            raise ValueError(f"Operatore non supportato: {op}")
            
        return ops[op](left, right)

    def _skip_to_endloop(self):
        """Avanza il Program Counter ignorando tutto fino al prossimo @endloop/endwhile."""
        frame = self.call_stack[-1]
        lines = frame["lines"]
        pc = frame["pc"]
        nesting = 1
        
        while pc < len(lines):
            line = lines[pc].strip().lower()
            if not line or line.startswith("#"):
                pc += 1
                continue
                
            if line.startswith("@loop") or line.startswith("@while"):
                nesting += 1
            elif line.startswith("@endloop") or line.startswith("@endwhile"):
                nesting -= 1
                if nesting == 0:
                    frame["pc"] = pc + 1
                    return
            pc += 1
        raise ValueError("Raggiunta fine file senza trovare @endloop o @endwhile")

    # ==========================================
    # CONNESSIONI E TRASPORTI
    # ==========================================
    def _parse_terminator(self, value: str) -> str:
        return value.encode("utf-8").decode("unicode_escape")

    @staticmethod
    def _escape_terminator(value: str) -> str:
        return value.encode("unicode_escape").decode("ascii")

    def _build_conn_history_command(
        self, name: str, conn_type: str, endpoint: str, timeout_s: float, terminator: str,
        baud: Optional[int] = None, backend: Optional[str] = None, socket_port: Optional[int] = None
    ) -> str:
        if conn_type == "serial":
            return f"@conn {name} serial {endpoint} {baud} {timeout_s:g} {self._escape_terminator(terminator)}"
        if conn_type == "visa":
            return f"@conn {name} visa {endpoint} {timeout_s:g} {backend} {self._escape_terminator(terminator)}"
        if conn_type == "socket":
            endpoint_value = f"{endpoint}:{socket_port}" if socket_port is not None else endpoint
            return f"@conn {name} socket {endpoint_value} {timeout_s:g} {self._escape_terminator(terminator)}"
        return f"@conn {name} {conn_type} {endpoint}"

    def cmd_conn(self, args):
        if len(args) < 3:
            raise ValueError("@conn richiede: nome tipo endpoint [parametri]")

        name, conn_type, endpoint = args[0], args[1].lower(), args[2]
        params = args[3:]
        history_cmd = None

        if conn_type == "serial":
            baud = int(params[0]) if params else 9600
            timeout_s = float(params[1]) if len(params) > 1 else 2.0
            terminator = self._parse_terminator(params[2]) if len(params) > 2 else "\n"
            transport = SerialTransport(endpoint, baud, timeout_s, terminator)
            history_cmd = self._build_conn_history_command(name, conn_type, endpoint, timeout_s, terminator, baud=baud)

        elif conn_type == "visa":
            timeout_s = float(params[0]) if params else 2.0
            backend = params[1] if len(params) > 1 else "auto"
            terminator = self._parse_terminator(params[2]) if len(params) > 2 else "\n"
            transport = VisaTransport(endpoint, int(timeout_s * 1000), terminator, backend)
            history_cmd = self._build_conn_history_command(name, conn_type, endpoint, timeout_s, terminator, backend=backend)

        elif conn_type == "socket":
            if ":" in endpoint:
                host, port = endpoint.rsplit(":", 1)
                port = int(port)
            else:
                host, port = endpoint, int(params[0]) if params else 5025
                params = params[1:]
            timeout_s = float(params[0]) if params else 2.0
            terminator = self._parse_terminator(params[1]) if len(params) > 1 else "\n"
            transport = RawSocketScpiTransport(host, port, timeout_s, terminator)
            history_cmd = self._build_conn_history_command(name, conn_type, host, timeout_s, terminator, socket_port=port)

        else:
            raise ValueError(f"Transport sconosciuto: {conn_type}")

        transport.connect()
        self.targets[name] = TargetConnection(name=name, transport=transport)
        self.logger("INFO", f"CONN: {name} -> {conn_type} {endpoint}")
        if history_cmd and self.history_logger:
            self.history_logger(history_cmd)

    def cmd_target(self, args):
        if not args: raise ValueError("@target richiede il nome del target")
        name = args[0]
        if name not in self.targets: raise ValueError(f"Target '{name}' non connesso")
        self.current_target = name
        self.last = None
        self.logger("INFO", f"TARGET -> {name}")

    # ==========================================
    # ESECUZIONE SCRIPT E LOOP PRINCIPALE
    # ==========================================
    def _load_script_lines(self, script_name: str) -> List[str]:
        if not self.script_loader: raise ValueError("Script loader non configurato")
        return list(self.script_loader(script_name))

    def _push_script(self, script_name: str, lines: List[str]):
        self.call_stack.append({
            "name": script_name,
            "lines": lines,
            "pc": 0,
        })
        self.logger("INFO", f"CALL -> {script_name}")

    def run_lines(self, lines, entry_script_name: str = "__main__"):
        self.reset_runtime()
        self._push_script(entry_script_name, list(lines))

        while self.call_stack and not self.stop_requested:
            # BLOCCO DEBUGGER: aspetta qui finché la UI non dice "vai"
            self.step_event.wait()
            
            frame = self.call_stack[-1]
            script_name = str(frame["name"])
            script_lines = frame["lines"]
            pc = int(frame["pc"])

            if pc >= len(script_lines):
                self.call_stack.pop()
                continue

            raw = script_lines[pc]
            frame["pc"] = pc + 1
            
            # AGGIORNAMENTO UI: Lampeggio riga corrente (futuro)
            if self.on_state_change:
                self.on_state_change()

            if self.stop_requested:
                self.logger("WARN", "Esecuzione interrotta")
                break

            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            try:
                if line.startswith("@"):
                    self._run_meta(line)
                else:
                    self._run_command(line)
            except Exception as exc:
                self.logger("ERR", f"{script_name}:L{pc + 1}: {exc}")
                self.stop_requested = True

    # ==========================================
    # META COMANDI (@)
    # ==========================================
    def _run_meta(self, line: str):
        tokens = shlex.split(line)
        if not tokens: return
        cmd = tokens[0][1:].lower()
        args = tokens[1:]

        # Connessioni
        if cmd == "conn": self.cmd_conn(args)
        elif cmd == "target": self.cmd_target(args)
        
        # Flusso e Tempo
        elif cmd == "wait":
            delay = float(self._resolve_value(args[0])) if args else 0.0
            self.logger("INFO", f"WAIT: {delay}s")
            time.sleep(delay)
        elif cmd == "halt":
            self.stop_requested = True
            self.logger("INFO", "HALT")
            
        # Variabili
        elif cmd == "var":
            if len(args) < 2: raise ValueError("@var richiede: nome valore")
            var_name = args[0].lower()
            val = self._resolve_value(" ".join(args[1:]))
            self.variables[var_name] = val
            self.logger("INFO", f"VAR: {var_name} = {val}")

        elif cmd == "inc":
            if not args: raise ValueError("@inc richiede il nome della variabile")
            var_name = args[0].lower()
            step = float(self._resolve_value(args[1])) if len(args) > 1 else 1.0
            if var_name in self.variables:
                self.variables[var_name] += step
                self.logger("INFO", f"VAR INC: {var_name} = {self.variables[var_name]}")
            else:
                raise ValueError(f"Variabile '{var_name}' non definita per @inc")

        elif cmd == "eval":
            if len(args) < 3 or args[1] != "=":
                raise ValueError("@eval formato: @eval dest = espressione")
            var_dest = args[0].lower()
            expr = " ".join(args[2:]).replace("^", "**") # Converte potenza
            
            # Creiamo un ambiente per eval() sicuro e "Case-Insensitive"
            eval_env = {"__builtins__": {}}
            eval_env.update(self._math_env)
            # Aggiungiamo le funzioni math anche in maiuscolo (es: SIN e sin)
            eval_env.update({k.upper(): v for k, v in self._math_env.items()})
            
            # Passiamo a eval() le variabili in minuscolo e maiuscolo
            for k, v in self.variables.items():
                eval_env[k.lower()] = v
                eval_env[k.upper()] = v
                
            try:
                res = eval(expr, eval_env)
                self.variables[var_dest] = res
                self.logger("INFO", f"EVAL: {var_dest} = {res}")
            except NameError as e:
                # Ora se sbagliamo nome restituisce un errore chiaro
                raise ValueError(f"Variabile non trovata in '{expr}': {e}")
            except Exception as e:
                raise ValueError(f"Errore espressione '{expr}': {e}")

        # Condizioni e Loop
        elif cmd == "if":
            if len(args) < 4: raise ValueError("@if formato: @if <left> <op> <right> <azione>")
            if self._evaluate_condition(args[0], args[1], args[2]):
                action_line = " ".join(args[3:])
                self.logger("INFO", f"IF TRUE: '{action_line}'")
                if action_line.startswith("@"): self._run_meta(action_line)
                else: self._run_command(action_line)

        elif cmd == "loop":
            if not args: raise ValueError("@loop richiede numero iterazioni")
            count = int(self._resolve_value(args[0]))
            self.loop_stack.append({
                "type": "loop", "start_pc": self.call_stack[-1]["pc"], "remaining": count
            })
            self.logger("INFO", f"LOOP START: {count} iterazioni")

        elif cmd == "while":
            if len(args) < 3: raise ValueError("@while richiede: <left> <op> <right>")
            self.loop_stack.append({
                "type": "while", "start_pc": self.call_stack[-1]["pc"], "args": args
            })
            if not self._evaluate_condition(args[0], args[1], args[2]):
                self._skip_to_endloop()

        elif cmd in ("endloop", "endwhile"):
            if not self.loop_stack: raise ValueError(f"@{cmd} senza blocco di apertura")
            curr = self.loop_stack[-1]
            
            if curr["type"] == "loop":
                curr["remaining"] -= 1
                if curr["remaining"] > 0:
                    self.call_stack[-1]["pc"] = curr["start_pc"]
                else:
                    self.loop_stack.pop()
                    self.logger("INFO", "LOOP END")
                    
            elif curr["type"] == "while":
                args = curr["args"]
                if self._evaluate_condition(args[0], args[1], args[2]):
                    self.call_stack[-1]["pc"] = curr["start_pc"]
                else:
                    self.loop_stack.pop()
                    self.logger("INFO", "WHILE END")

        elif cmd == "break":
            if not self.loop_stack: raise ValueError("@break fuori da un loop")
            self.loop_stack.pop()
            self._skip_to_endloop()
            self.logger("INFO", "BREAK: Uscita forzata dal loop")

        # UI e Salvataggio dati
        elif cmd == "prompt":
            msg = " ".join(args).strip("\"'")
            self.logger("WARN", f"PROMPT: {msg}")
            if self.prompt_callback:
                self.prompt_callback(msg)

        elif cmd == "store":
            if len(args) == 0:
                self._store_value("LAST")
            elif len(args) == 1:
                # Comportamento originale: @store ETICHETTA (salva 'last')
                self._store_value(args[0])
            else:
                # NUOVO: @store ETICHETTA VARIABILE (es: @store Risultato_Math RISULTATO)
                label = args[0]
                val = self._resolve_value(" ".join(args[1:]))
                self._store_value(label, str(val))
        elif cmd == "startstore":
            self.auto_store_enabled = True
            if args: self.auto_store_label = " ".join(args).strip()
            self.logger("INFO", f"STARTSTORE: attivo (label={self.auto_store_label})")
        elif cmd == "stopstore":
            self.auto_store_enabled = False
            self.logger("INFO", "STOPSTORE: disattivato")
        elif cmd == "comment":
            text = " ".join(args).strip()
            if not text: raise ValueError("@comment richiede un testo")
            self._store_comment(text)
            
        # Funzioni e binario
        elif cmd in ("call", "script"):
            script_name = " ".join(args).strip().strip("()")
            lines = self._load_script_lines(script_name)
            self._push_script(script_name, lines)
        elif cmd == "rts":
            if self.call_stack:
                ended = self.call_stack.pop()
                self.logger("INFO", f"RTS <- {ended['name']}")
            if not self.call_stack:
                self.stop_requested = True
        elif cmd == "readbin":
            self.readbin_armed = True
            self.logger("INFO", "READBIN armato")
        elif cmd == "savebin":
            if not args: raise ValueError("@savebin richiede un filename")
            self._save_binary(args[0])
        else:
            self.logger("WARN", f"Meta comando non supportato: {line}")

    # ==========================================
    # GESTIONE RISPOSTE E CSV
    # ==========================================
    def _read_binary_response(self, transport) -> bytes:
        if isinstance(transport, SerialTransport):
            deadline = time.time() + max(transport.timeout, 0.2)
            chunks: List[bytes] =[]
            while time.time() < deadline:
                waiting = getattr(transport.ser, "in_waiting", 0)
                if waiting:
                    chunks.append(transport.ser.read(waiting))
                else:
                    time.sleep(0.01)
            return b"".join(chunks)

        inst = getattr(transport, "inst", None)
        if inst is not None and hasattr(inst, "read_raw"):
            data = inst.read_raw()
            return data if isinstance(data, (bytes, bytearray)) else bytes(data)

        sock = getattr(transport, "sock", None)
        if sock is not None:
            chunks: List[bytes] =[]
            deadline = time.time() + getattr(transport, "timeout", 2.0)
            while time.time() < deadline:
                try: chunk = sock.recv(4096)
                except Exception: break
                if not chunk: break
                chunks.append(chunk)
            return b"".join(chunks)

        raise RuntimeError("Transport non supporta lettura binaria")

    def _query_with_buffered_multiline(self, transport, cmd: str) -> str:
        first = transport.query(cmd)
        if not isinstance(transport, SerialTransport): return first

        lines: List[str] =[first] if first is not None else[]
        pending = ""
        idle_deadline = time.time() + self.serial_multiline_idle_s
        while time.time() < idle_deadline:
            extra = transport.read_available()
            if not extra:
                time.sleep(0.01)
                continue
            idle_deadline = time.time() + self.serial_multiline_idle_s
            pending += extra.replace("\r\n", "\n").replace("\r", "\n")
            chunks = pending.split("\n")
            pending = chunks.pop() if chunks else ""
            for raw_line in chunks:
                clean = raw_line.strip()
                if clean: lines.append(clean)

        tail = pending.strip()
        if tail: lines.append(tail)
        return "\n".join(lines).strip()

    @staticmethod
    def _csv_sanitize(value: Optional[str]) -> str:
        if value is None: return "NOVAL"
        return str(value).replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")

    def _append_lastres_row(self, target: str, command: str, name: str, value: Optional[str]):
        ts = datetime.now().strftime("%d%m%Y %H:%M")
        with open("lastres.csv", "a", newline="", encoding="utf-8") as fp:
            writer = csv.writer(fp, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            writer.writerow([ts, target, command, name, self._csv_sanitize(value)])

    def _append_lastres_block(self, target: str, command: str, name: str, value: Optional[str]):
        ts = datetime.now().strftime("%d%m%Y %H:%M")
        normalized = self._csv_sanitize(value)
        with open("lastres.csv", "a", newline="", encoding="utf-8") as fp:
            fp.write(f"{ts};{target};{command};{name}\n")
            if not normalized:
                fp.write("NOVAL\n")
                return
            for raw_line in normalized.splitlines():
                fp.write(f"{raw_line}\n")

    def _store_value(self, name: str, value: Optional[str] = None):
        target = self.current_target or ""
        command = self.last_command or ""
        stored_value = self.last if value is None else value
        if self.auto_store_enabled and value is not None:
            self._append_lastres_block(target, command, name, stored_value)
        else:
            self._append_lastres_row(target, command, name, stored_value)
        self.logger("INFO", f"STORE: {name} [{target}]")

    def _store_comment(self, text: str):
        target = self.current_target or ""
        clean_text = text.lstrip("\r\n")
        self._append_lastres_row(target, "@comment", "COMMENT", clean_text)
        self.logger("INFO", f"COMMENT salvato: {clean_text}")

    def _save_binary(self, filename: str):
        if self.last_bin is None:
            raise RuntimeError("Nessun dato binario (usa prima @readbin + comando)")
        p = Path(filename)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self.current_target or "notarget"
        out = p.with_name(f"{p.stem}_{ts}_{target}{p.suffix}")
        out.write_bytes(self.last_bin)
        self.logger("INFO", f"SAVEBIN: {out} ({len(self.last_bin)} bytes)")

    def _run_command(self, cmd: str):
        if not self.current_target: raise RuntimeError("Nessun target selezionato (@target)")

        transport = self.targets[self.current_target].transport
        self.last_command = cmd
        self.logger("TX", f"[{self.current_target}] {cmd}")

        if self.readbin_armed:
            transport.write(cmd)
            data = self._read_binary_response(transport)
            self.last_bin = data
            self.last = None
            self.readbin_armed = False
            self.logger("RXBIN", f"[{self.current_target}] {len(data)} bytes")
        elif is_query_command(cmd):
            if self.serial_pre_query_flush:
                if isinstance(transport, SerialTransport):
                    with contextlib.suppress(Exception): transport.ser.reset_input_buffer()
                else:
                    with contextlib.suppress(Exception): transport.read_available()
            try:
                reply = self._query_with_buffered_multiline(transport, cmd)
            except TimeoutError:
                if not isinstance(transport, SerialTransport): raise
                self.logger("WARN", f"[{self.current_target}] Timeout query: retry tra {self.serial_query_retry_delay_s:g}s")
                time.sleep(self.serial_query_retry_delay_s)
                reply = self._query_with_buffered_multiline(transport, cmd)
            self.last = reply
            self.last_bin = None
            self.logger("RX", f"[{self.current_target}] {reply}")
            if self.auto_store_enabled:
                self._store_value(self.auto_store_label, value=reply)
        else:
            transport.write(cmd)
            self.last = None
            self.last_bin = None
class CombinedMonitorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1200x800")
        self.minsize(900, 600)

        # Stato della GUI (Workspace e Tab)
        self.current_workspace: Optional[Path] = None
        self.open_tabs: Dict[str, dict] = {}  # Mappa tab_id -> {"path": Path, "text_widget": ScrolledText}
        self.connection_history: List[str] =[]

        # Inizializza l'Engine
        self.engine = CombinedScriptEngine(
            self._append_log,
            self._add_conn_history_entry,
            script_loader=self._load_script_lines_for_engine,
        )
        self.engine.prompt_callback = self._handle_prompt
        self.run_thread: Optional[threading.Thread] = None
        self.running = False

        self._build_ui()

    def _build_ui(self):
        # Finestra divisa in due: Sidebar a sinistra, Area principale a destra
        main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_paned.pack(fill="both", expand=True, padx=5, pady=5)

        # ==================== SIDEBAR (Progetto) ====================
        sidebar = ttk.Frame(main_paned)
        main_paned.add(sidebar, weight=1)

        btn_ws = ttk.Button(sidebar, text="Apri Cartella Progetto", command=self.open_workspace)
        btn_ws.pack(fill="x", pady=(0, 5))

        # Lista dei file
        self.file_list = tk.Listbox(sidebar, font=("Consolas", 10))
        self.file_list.pack(fill="both", expand=True)
        self.file_list.bind("<Double-1>", self._on_file_double_click)

        # ==================== AREA PRINCIPALE ====================
        main_area = ttk.Frame(main_paned)
        main_paned.add(main_area, weight=4)

        # Toolbar
        toolbar = ttk.Frame(main_area)
        toolbar.pack(fill="x", pady=(0, 5))

        self.btn_run = ttk.Button(toolbar, text="Esegui Tab", command=self.run_script)
        self.btn_run.pack(side="left", padx=(0, 4))
        self.btn_stop = ttk.Button(toolbar, text="Stop", command=self.request_stop)
        self.btn_stop.pack(side="left", padx=(0, 15))
        self.btn_stop.state(["disabled"])

        ttk.Button(toolbar, text="Nuovo", command=self.new_tab).pack(side="left", padx=(0, 4))
        ttk.Button(toolbar, text="Salva", command=self.save_current_tab).pack(side="left", padx=(0, 4))
        ttk.Button(toolbar, text="Salva Come...", command=self.save_tab_as).pack(side="left", padx=(0, 4))
        ttk.Button(toolbar, text="Chiudi Tab", command=self.close_current_tab).pack(side="left", padx=(0, 15))

        ttk.Button(toolbar, text="Chiudi Connessioni", command=self.close_connections).pack(side="right", padx=(4, 0))
        ttk.Button(toolbar, text="Pulisci Log", command=self.clear_log).pack(side="right")

        # Splitter verticale per i Tab e il Log
        right_paned = ttk.PanedWindow(main_area, orient=tk.VERTICAL)
        right_paned.pack(fill="both", expand=True)

        # Notebook (Area Tab)
        self.notebook = ttk.Notebook(right_paned)
        right_paned.add(self.notebook, weight=3)

        # ==================== BOTTOM AREA (Log & History) ====================
        bottom_frame = ttk.Frame(right_paned)
        right_paned.add(bottom_frame, weight=1)

        log_frame = ttk.LabelFrame(bottom_frame, text="Monitor Log", padding=5)
        log_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))
        self.log = ScrolledText(log_frame, wrap="word", font=("Consolas", 10), state="disabled")
        self.log.pack(fill="both", expand=True)

        hist_frame = ttk.LabelFrame(bottom_frame, text="History (@conn)", padding=5)
        hist_frame.pack(side="right", fill="both", expand=False)
        self.conn_history_list = tk.Listbox(hist_frame, width=45, font=("Consolas", 9))
        self.conn_history_list.pack(fill="both", expand=True)
        self.conn_history_list.bind("<Double-1>", self._insert_selected_conn_history)

        # Crea un tab iniziale di default
        self.new_tab(title="Senza Nome", content="# Inserisci comandi SCPI qui\n")

    # ------------------ GESTIONE WORKSPACE ------------------
    def open_workspace(self):
        folder = filedialog.askdirectory(parent=self, title="Seleziona Cartella Progetto")
        if not folder:
            return
        self.current_workspace = Path(folder)
        self.title(f"{APP_NAME} - {self.current_workspace.name}")
        self._append_log("INFO", f"Progetto aperto: {self.current_workspace}")
        self.refresh_file_list()

    def refresh_file_list(self):
        self.file_list.delete(0, tk.END)
        if not self.current_workspace:
            return
        # Carica tutti i file .scpi nella cartella
        for file in sorted(self.current_workspace.glob("*.scpi")):
            self.file_list.insert(tk.END, file.name)

    def _on_file_double_click(self, event):
        selection = self.file_list.curselection()
        if not selection:
            return
        filename = self.file_list.get(selection[0])
        filepath = self.current_workspace / filename
        self.open_file_in_tab(filepath)

    # ------------------ GESTIONE TAB ------------------
    def new_tab(self, title="Senza Nome", content="", filepath: Optional[Path] = None):
        frame = ttk.Frame(self.notebook)
        text_widget = ScrolledText(frame, font=("Consolas", 10), undo=True)
        text_widget.pack(fill="both", expand=True)
        text_widget.insert("1.0", content)

        self.notebook.add(frame, text=title)
        self.notebook.select(frame)  # Porta in primo piano il nuovo tab

        tab_id = self.notebook.select()
        self.open_tabs[tab_id] = {
            "path": filepath,
            "text_widget": text_widget
        }

    def open_file_in_tab(self, filepath: Path):
        # Evita di riaprire un file se è già in un tab esistente
        for tab_id, data in self.open_tabs.items():
            if data["path"] == filepath:
                self.notebook.select(tab_id)
                return
        
        try:
            content = filepath.read_text(encoding="utf-8")
            self.new_tab(title=filepath.name, content=content, filepath=filepath)
            self._append_log("INFO", f"Script caricato: {filepath.name}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Impossibile aprire il file:\n{exc}")

    def get_current_tab_data(self) -> Optional[dict]:
        tab_id = self.notebook.select()
        if not tab_id:
            return None
        return self.open_tabs.get(tab_id)

    def close_current_tab(self):
        tab_id = self.notebook.select()
        if not tab_id:
            return
        self.notebook.forget(tab_id)
        self.open_tabs.pop(tab_id, None)

    # ------------------ SALVATAGGIO ------------------
    def save_current_tab(self):
        tab_data = self.get_current_tab_data()
        if not tab_data: return

        if tab_data["path"] is None:
            self.save_tab_as()
            return

        content = tab_data["text_widget"].get("1.0", "end-1c")
        try:
            tab_data["path"].write_text(content, encoding="utf-8")
            self._append_log("INFO", f"Salvato: {tab_data['path'].name}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Impossibile salvare:\n{exc}")

    def save_tab_as(self):
        tab_data = self.get_current_tab_data()
        if not tab_data: return

        initial_dir = self.current_workspace if self.current_workspace else str(Path.home())
        filepath = filedialog.asksaveasfilename(
            parent=self,
            title="Salva Script Come",
            defaultextension=".scpi",
            filetypes=[("Script SCPI", "*.scpi"), ("Tutti i file", "*.*")],
            initialdir=initial_dir
        )
        if not filepath: return

        path = Path(filepath)
        content = tab_data["text_widget"].get("1.0", "end-1c")
        try:
            path.write_text(content, encoding="utf-8")
            tab_data["path"] = path
            
            # Aggiorna il nome del Tab visibile
            tab_id = self.notebook.select()
            self.notebook.tab(tab_id, text=path.name)
            self._append_log("INFO", f"Salvato come: {path.name}")
            
            # Aggiorna la vista file se l'abbiamo salvato nel workspace corrente
            if self.current_workspace and str(path).startswith(str(self.current_workspace)):
                self.refresh_file_list()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Impossibile salvare:\n{exc}")

    # ------------------ INTEGRAZIONE ENGINE E RUN ------------------
    def _load_script_lines_for_engine(self, script_name: str) -> List[str]:
        """Usato dall'engine quando incontra @call o @script."""
        if not self.current_workspace:
            raise ValueError(f"Nessun progetto aperto. Impossibile caricare '@call {script_name}'")
        
        name = script_name if script_name.lower().endswith(".scpi") else f"{script_name}.scpi"
        path = self.current_workspace / name
        if not path.exists():
            raise ValueError(f"Script non trovato nel progetto corrente: {path}")
            
        return path.read_text(encoding="utf-8").splitlines()

    def run_script(self):
        if self.running:
            return

        tab_data = self.get_current_tab_data()
        if not tab_data:
            return

        raw_script = tab_data["text_widget"].get("1.0", "end")
        normalized_script = re.sub(r"\\n(?=\s*[@*A-Za-z])", "\n", raw_script)
        lines = normalized_script.splitlines()

        if normalized_script != raw_script:
            self._append_log("INFO", "Rilevate sequenze \\n nel testo: convertite in nuove righe")

        self._set_running(True)
        script_name = tab_data["path"].name if tab_data["path"] else "Tab senza nome"

        def worker():
            try:
                self.engine.run_lines(lines, entry_script_name=script_name)
                self._append_log("INFO", "Script completato")
            except Exception as exc:
                self._append_log("ERR", f"Script terminato con errore: {exc}")
            finally:
                self.after(0, lambda: self._set_running(False))

        self.run_thread = threading.Thread(target=worker, daemon=True)
        self.run_thread.start()

    def request_stop(self):
        self.engine.stop_requested = True
        self._append_log("INFO", "Stop richiesto")

    def _set_running(self, value: bool):
        self.running = value
        if value:
            self.btn_run.state(["disabled"])
            self.btn_stop.state(["!disabled"])
        else:
            self.btn_run.state(["!disabled"])
            self.btn_stop.state(["disabled"])

    # ------------------ LOG E HISTORY ------------------
    def close_connections(self):
        self.engine.close_all()
        self._append_log("INFO", "Connessioni chiuse")

    def _append_log(self, level: str, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", f"[{level}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _add_conn_history_entry(self, command: str):
        self.connection_history.append(command)
        if hasattr(self, "conn_history_list"):
            self.conn_history_list.insert("end", command)
            self.conn_history_list.see("end")

    def _insert_selected_conn_history(self, _event=None):
        """Incolla il comando selezionato dalla history nel Tab corrente, alla posizione del cursore."""
        selection = self.conn_history_list.curselection()
        if not selection:
            return
        command = self.conn_history_list.get(selection[0])
        
        tab_data = self.get_current_tab_data()
        if tab_data:
            text_widget = tab_data["text_widget"]
            text_widget.insert(tk.INSERT, f"{command}\n")
            text_widget.see(tk.INSERT)

    def destroy(self):
        self.engine.close_all()
        super().destroy()
        
    def _handle_prompt(self, msg: str):
        """Blocca l'engine finché l'utente non risponde al popup."""
        event = threading.Event()
        self.after(0, lambda: self._show_prompt_dialog(msg, event))
        event.wait() 

    def _show_prompt_dialog(self, msg: str, event: threading.Event):
        messagebox.showinfo("Azione Richiesta", msg, parent=self)
        event.set()
        
if __name__ == "__main__":
    app = CombinedMonitorApp()
    app.mainloop()
