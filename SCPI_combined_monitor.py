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


class CombinedScriptEngine:
    """Motore script ispirato a core_engine ma integrato con il monitor."""

    def __init__(
        self,
        logger: Callable[[str, str], None],
        history_logger: Optional[Callable[[str], None]] = None,
        script_loader: Optional[Callable[[str], List[str]]] = None,
    ):
        self.logger = logger
        self.history_logger = history_logger
        self.script_loader = script_loader
        self.targets: Dict[str, TargetConnection] = {}
        self.current_target: Optional[str] = None
        self.last: Optional[str] = None
        self.last_bin: Optional[bytes] = None
        self.last_command: Optional[str] = None
        self.stop_requested = False
        self.readbin_armed = False
        self.call_stack: List[Dict[str, object]] = []
        self.auto_store_enabled = False
        self.auto_store_label = "AUTO"
        self.serial_query_retry_delay_s = 1.0
        self.serial_pre_query_flush = True
        self.serial_multiline_idle_s = 0.12

    def reset_runtime(self):
        self.current_target = None
        self.last = None
        self.last_bin = None
        self.last_command = None
        self.stop_requested = False
        self.readbin_armed = False
        self.call_stack = []
        self.auto_store_enabled = False
        self.auto_store_label = "AUTO"

    def close_all(self):
        for tc in self.targets.values():
            try:
                tc.transport.disconnect()
            except Exception:
                pass
        self.targets.clear()

    def _parse_terminator(self, value: str) -> str:
        return value.encode("utf-8").decode("unicode_escape")

    @staticmethod
    def _escape_terminator(value: str) -> str:
        return value.encode("unicode_escape").decode("ascii")

    def _build_conn_history_command(
        self,
        name: str,
        conn_type: str,
        endpoint: str,
        timeout_s: float,
        terminator: str,
        baud: Optional[int] = None,
        backend: Optional[str] = None,
        socket_port: Optional[int] = None,
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

        name = args[0]
        conn_type = args[1].lower()
        endpoint = args[2]
        params = args[3:]
        history_cmd = None

        if conn_type == "serial":
            baud = int(params[0]) if params else 9600
            timeout_s = float(params[1]) if len(params) > 1 else 2.0
            terminator = self._parse_terminator(params[2]) if len(params) > 2 else "\n"
            transport = SerialTransport(endpoint, baud, timeout_s, terminator)
            history_cmd = self._build_conn_history_command(
                name=name,
                conn_type=conn_type,
                endpoint=endpoint,
                baud=baud,
                timeout_s=timeout_s,
                terminator=terminator,
            )

        elif conn_type == "visa":
            timeout_s = float(params[0]) if params else 2.0
            backend = params[1] if len(params) > 1 else "auto"
            terminator = self._parse_terminator(params[2]) if len(params) > 2 else "\n"
            transport = VisaTransport(
                resource_name=endpoint,
                timeout_ms=int(timeout_s * 1000),
                terminator=terminator,
                backend=backend,
            )
            history_cmd = self._build_conn_history_command(
                name=name,
                conn_type=conn_type,
                endpoint=endpoint,
                timeout_s=timeout_s,
                backend=backend,
                terminator=terminator,
            )

        elif conn_type == "socket":
            if ":" in endpoint:
                host, port = endpoint.rsplit(":", 1)
                port = int(port)
            else:
                host = endpoint
                port = int(params[0]) if params else 5025
                params = params[1:]
            timeout_s = float(params[0]) if params else 2.0
            terminator = self._parse_terminator(params[1]) if len(params) > 1 else "\n"
            transport = RawSocketScpiTransport(host, port, timeout_s, terminator)
            history_cmd = self._build_conn_history_command(
                name=name,
                conn_type=conn_type,
                endpoint=host,
                socket_port=port,
                timeout_s=timeout_s,
                terminator=terminator,
            )

        else:
            raise ValueError(f"Transport sconosciuto: {conn_type}")

        transport.connect()
        self.targets[name] = TargetConnection(name=name, transport=transport)
        self.logger("INFO", f"CONN: {name} -> {conn_type} {endpoint}")
        if history_cmd and self.history_logger:
            self.history_logger(history_cmd)

    def cmd_target(self, args):
        if not args:
            raise ValueError("@target richiede il nome del target")
        name = args[0]
        if name not in self.targets:
            raise ValueError(f"Target '{name}' non connesso")
        self.current_target = name
        self.last = None
        self.logger("INFO", f"TARGET -> {name}")

    def _exec_if(self, args):
        if len(args) < 4:
            raise ValueError("@if formato: @if <left> <op> <right> <azione>")
        left_raw, op, right_raw = args[0], args[1], args[2]
        action = args[3:]

        left = self.last if left_raw.lower() == "last" else left_raw
        right = right_raw

        try:
            left = float(left)
            right = float(right)
        except Exception:
            pass

        checks = {
            "==": left == right,
            "!=": left != right,
            ">": left > right,
            "<": left < right,
            ">=": left >= right,
            "<=": left <= right,
        }
        if not checks.get(op, False):
            return

        action_cmd = action[0].lower()
        if action_cmd == "@halt":
            self.stop_requested = True
            self.logger("INFO", "HALT richiesto da @if")
        elif action_cmd == "@wait" and len(action) > 1:
            delay = float(action[1])
            self.logger("INFO", f"WAIT(if): {delay}s")
            time.sleep(delay)
        else:
            self.logger("WARN", f"Azione @if non supportata: {' '.join(action)}")

    def _load_script_lines(self, script_name: str) -> List[str]:
        if not self.script_loader:
            raise ValueError("Script loader non configurato")
        loaded = self.script_loader(script_name)
        return list(loaded)

    def _push_script(self, script_name: str, lines: List[str]):
        self.call_stack.append(
            {
                "name": script_name,
                "lines": lines,
                "pc": 0,
            }
        )
        self.logger("INFO", f"CALL -> {script_name}")

    def run_lines(self, lines, entry_script_name: str = "__main__"):
        self.reset_runtime()
        self._push_script(entry_script_name, list(lines))

        while self.call_stack and not self.stop_requested:
            frame = self.call_stack[-1]
            script_name = str(frame["name"])
            script_lines = frame["lines"]
            pc = int(frame["pc"])

            if pc >= len(script_lines):
                self.call_stack.pop()
                continue

            raw = script_lines[pc]
            frame["pc"] = pc + 1
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
                raise

    def _run_meta(self, line: str):
        tokens = shlex.split(line)
        if not tokens:
            return
        cmd = tokens[0][1:].lower()
        args = tokens[1:]

        if cmd == "conn":
            self.cmd_conn(args)
        elif cmd == "target":
            self.cmd_target(args)
        elif cmd == "wait":
            delay = float(args[0]) if args else 0.0
            self.logger("INFO", f"WAIT: {delay}s")
            time.sleep(delay)
        elif cmd == "halt":
            self.stop_requested = True
            self.logger("INFO", "HALT")
        elif cmd == "if":
            self._exec_if(args)
        elif cmd == "store":
            name = " ".join(args).strip() if args else ""
            self._store_value(name)
        elif cmd == "startstore":
            self.auto_store_enabled = True
            if args:
                self.auto_store_label = " ".join(args).strip()
            self.logger("INFO", f"STARTSTORE: attivo (label={self.auto_store_label})")
        elif cmd == "stopstore":
            self.auto_store_enabled = False
            self.logger("INFO", "STOPSTORE: disattivato")
        elif cmd == "comment":
            text = " ".join(args).strip()
            if not text:
                raise ValueError("@comment richiede un testo")
            self._store_comment(text)
        elif cmd in ("call", "script"):
            if not args:
                raise ValueError("@call richiede il nome script")
            script_name = " ".join(args).strip().strip("()")
            script_lines = self._load_script_lines(script_name)
            self._push_script(script_name, script_lines)
        elif cmd == "rts":
            if self.call_stack:
                ended = self.call_stack.pop()
                self.logger("INFO", f"RTS <- {ended['name']}")
            if not self.call_stack:
                self.stop_requested = True
        elif cmd == "readbin":
            self.readbin_armed = True
            self.logger("INFO", "READBIN armato: prossimo comando leggerà dati binari")
        elif cmd == "savebin":
            if not args:
                raise ValueError("@savebin richiede un filename")
            self._save_binary(args[0])
        else:
            self.logger("WARN", f"Meta comando non supportato: {line}")

    def _read_binary_response(self, transport) -> bytes:
        # Seriale: usa direttamente il buffer bytes del driver.
        if isinstance(transport, SerialTransport):
            deadline = time.time() + max(transport.timeout, 0.2)
            chunks: List[bytes] = []
            while time.time() < deadline:
                waiting = getattr(transport.ser, "in_waiting", 0)
                if waiting:
                    chunks.append(transport.ser.read(waiting))
                else:
                    time.sleep(0.01)
            return b"".join(chunks)

        # VISA: read_raw se disponibile.
        inst = getattr(transport, "inst", None)
        if inst is not None and hasattr(inst, "read_raw"):
            data = inst.read_raw()
            return data if isinstance(data, (bytes, bytearray)) else bytes(data)

        # Socket: lettura binaria best-effort fino a timeout.
        sock = getattr(transport, "sock", None)
        if sock is not None:
            chunks: List[bytes] = []
            deadline = time.time() + getattr(transport, "timeout", 2.0)
            while time.time() < deadline:
                try:
                    chunk = sock.recv(4096)
                except Exception:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)

        raise RuntimeError("Transport non supporta lettura binaria")

    def _query_with_buffered_multiline(self, transport, cmd: str) -> str:
        """
        Esegue una query e, per i transport seriali, prova a ricomporre eventuali
        righe aggiuntive arrivate subito dopo la prima risposta.
        """
        first = transport.query(cmd)
        if not isinstance(transport, SerialTransport):
            return first

        lines: List[str] = [first] if first is not None else []
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
                if clean:
                    lines.append(clean)

        tail = pending.strip()
        if tail:
            lines.append(tail)
        return "\n".join(lines).strip()

    @staticmethod
    def _csv_sanitize(value: Optional[str]) -> str:
        if value is None:
            return "NOVAL"
        # Alcuni strumenti restituiscono multilinee come sequenze letterali "\n".
        # Convertiamole in newline reali per mantenere la struttura del dato in CSV.
        text = str(value)
        return text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")

    def _append_lastres_row(self, target: str, command: str, name: str, value: Optional[str]):
        ts = datetime.now().strftime("%d%m%Y %H:%M")
        with open("lastres.csv", "a", newline="", encoding="utf-8") as fp:
            writer = csv.writer(fp, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            writer.writerow([ts, target, command, name, self._csv_sanitize(value)])

    def _store_value(self, name: str, value: Optional[str] = None):
        target = self.current_target or ""
        command = self.last_command or ""
        stored_value = self.last if value is None else value
        self._append_lastres_row(target, command, name, stored_value)
        self.logger("INFO", f"STORE: {name} [{target}]")

    def _store_comment(self, text: str):
        target = self.current_target or ""
        clean_text = text.lstrip("\r\n")
        self._append_lastres_row(target, "@comment", "COMMENT", clean_text)
        self.logger("INFO", f"COMMENT salvato: {clean_text}")

    def _save_binary(self, filename: str):
        if self.last_bin is None:
            raise RuntimeError("Nessun dato binario disponibile (usa prima @readbin + comando)")
        p = Path(filename)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self.current_target or "notarget"
        out = p.with_name(f"{p.stem}_{ts}_{target}{p.suffix}")
        out.write_bytes(self.last_bin)
        self.logger("INFO", f"SAVEBIN: {out} ({len(self.last_bin)} bytes)")

    def _run_command(self, cmd: str):
        if not self.current_target:
            raise RuntimeError("Nessun target selezionato (@target)")

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
                # Evita che eventuali reply residue (es. "OK" da un comando non-query precedente)
                # vengano lette come risposta della query corrente.
                if isinstance(transport, SerialTransport):
                    with contextlib.suppress(Exception):
                        transport.ser.reset_input_buffer()
                else:
                    with contextlib.suppress(Exception):
                        _ = transport.read_available()
            try:
                reply = self._query_with_buffered_multiline(transport, cmd)
            except TimeoutError:
                if not isinstance(transport, SerialTransport):
                    raise
                self.logger(
                    "WARN",
                    f"[{self.current_target}] Timeout query seriale: retry tra {self.serial_query_retry_delay_s:g}s",
                )
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
        self.geometry("1150x760")
        self.minsize(900, 600)

        self.connection_history: List[str] = []
        self.engine = CombinedScriptEngine(
            self._append_log,
            self._add_conn_history_entry,
            script_loader=self._load_script_lines_for_engine,
        )
        self.run_thread: Optional[threading.Thread] = None
        self.running = False
        self.script_name: Optional[str] = None
        self.script_index: Dict[str, str] = {}

        self._load_script_index()
        self._build_ui()

    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        top = ttk.LabelFrame(root, text="Script SCPI (core_engine + monitor)", padding=10)
        top.pack(fill="both", expand=True)

        hint = (
            "Sintassi base: @conn nome serial COM3 115200 | @target nome | *IDN? | @wait 1 | @if last > 1 @halt"
        )
        ttk.Label(top, text=hint).pack(anchor="w", pady=(0, 6))

        self.script_text = ScrolledText(top, height=18, font=("Consolas", 10))
        self.script_text.pack(fill="both", expand=True)
        self.script_text.insert(
            "1.0",
            "# Esempio\n"
            "@conn gen serial COM3 115200\n"
            "@target gen\n"
            "*IDN?\n"
            "MEAS:VOLT?\n"
            "@if last < 1 @halt\n",
        )

        actions = ttk.Frame(top)
        actions.pack(fill="x", pady=(8, 0))
        self.btn_run = ttk.Button(actions, text="Esegui Script", command=self.run_script)
        self.btn_run.pack(side="left", padx=(0, 4))
        self.btn_stop = ttk.Button(actions, text="Stop", command=self.request_stop)
        self.btn_stop.pack(side="left")
        ttk.Button(actions, text="Carica Script", command=self.load_script).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Salva Script", command=self.save_script).pack(side="left", padx=(4, 0))
        ttk.Button(actions, text="Salva Come…", command=self.save_script_as).pack(side="left", padx=(4, 0))
        ttk.Button(actions, text="Chiudi Connessioni", command=self.close_connections).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Pulisci Log", command=self.clear_log).pack(side="right")

        bottom = ttk.LabelFrame(root, text="Monitor Log", padding=10)
        bottom.pack(fill="both", expand=True, pady=(10, 0))
        self.log = ScrolledText(bottom, wrap="word", font=("Consolas", 10), state="disabled")
        self.log.pack(fill="both", expand=True)

        conn_hist = ttk.LabelFrame(root, text="History connessioni (@conn)", padding=10)
        conn_hist.pack(fill="both", pady=(10, 0))
        self.conn_history_list = tk.Listbox(conn_hist, height=6)
        self.conn_history_list.pack(fill="both", expand=True)
        self.conn_history_list.bind("<Double-1>", self._insert_selected_conn_history)

    @staticmethod
    def _normalize_script_name(name: str) -> str:
        clean = name.replace("\\", "/")
        parts = []
        for raw_part in clean.split("/"):
            normalized = re.sub(r"\s+", " ", raw_part).strip()
            if normalized:
                parts.append(normalized)
        return "/".join(parts)

    @staticmethod
    def _safe_path_part(name: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("_")
        if not safe:
            safe = "script"
        return safe

    @classmethod
    def _script_relpath_from_name(cls, name: str) -> str:
        parts = [p for p in cls._normalize_script_name(name).split("/") if p]
        if not parts:
            return "script.scpi"
        safe_parts = [cls._safe_path_part(part) for part in parts]
        safe_parts[-1] = f"{safe_parts[-1]}.scpi"
        return str(Path(*safe_parts))

    def _load_script_index(self):
        SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        self.script_index = {}
        if not SCRIPT_INDEX_FILE.exists():
            return
        try:
            raw = json.loads(SCRIPT_INDEX_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                scripts = raw.get("scripts", raw)
                if isinstance(scripts, dict):
                    for name, rel_path in scripts.items():
                        if isinstance(name, str) and isinstance(rel_path, str):
                            self.script_index[self._normalize_script_name(name)] = rel_path
        except Exception as exc:
            # In caso di indice corrotto non blocchiamo l'app.
            print(f"[WARN] Impossibile caricare indice script: {exc}")

    def _save_script_index(self):
        payload = {
            "scripts": dict(sorted(self.script_index.items(), key=lambda kv: kv[0].lower())),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "storage_dir": str(SCRIPT_DIR),
        }
        SCRIPT_INDEX_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _script_path_from_name(self, name: str) -> Path:
        rel_path = self.script_index.get(name)
        if rel_path:
            return SCRIPT_DIR / rel_path
        return SCRIPT_DIR / self._script_relpath_from_name(name)

    def load_script(self):
        selected_name = self._choose_script_from_index_dialog()
        if selected_name == "__open_other__":
            self._open_script_with_file_dialog()
            return
        if not selected_name:
            return

        self._load_script_by_name(selected_name)

    def _choose_script_from_index_dialog(self) -> Optional[str]:
        names = sorted(self.script_index.keys(), key=str.lower)
        dialog = tk.Toplevel(self)
        dialog.title("Carica Script")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(True, True)
        dialog.geometry("560x420")

        result = {"value": None}

        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Script presenti nell'indice JSON:").pack(anchor="w", pady=(0, 6))

        listbox = tk.Listbox(frame, height=14)
        listbox.pack(fill="both", expand=True)
        for name in names:
            listbox.insert("end", name)

        if names:
            listbox.selection_set(0)

        btns = ttk.Frame(frame)
        btns.pack(fill="x", pady=(8, 0))

        def choose_selected(_event=None):
            selection = listbox.curselection()
            if not selection:
                return
            result["value"] = listbox.get(selection[0])
            dialog.destroy()

        def choose_open_other():
            result["value"] = "__open_other__"
            dialog.destroy()

        def remove_selected():
            selection = listbox.curselection()
            if not selection:
                return
            selected = listbox.get(selection[0])
            rel_path = self.script_index.get(selected)
            if not rel_path:
                return
            delete_file = messagebox.askyesnocancel(
                APP_NAME,
                (
                    f"Rimuovere '{selected}' dall'indice?\n\n"
                    "Sì = rimuovi anche il file dal disco\n"
                    "No = rimuovi solo dall'indice"
                ),
                parent=dialog,
            )
            if delete_file is None:
                return
            self.script_index.pop(selected, None)
            self._save_script_index()
            if delete_file:
                path = SCRIPT_DIR / rel_path
                with contextlib.suppress(Exception):
                    path.unlink()
            listbox.delete(selection[0])
            if selected == self.script_name:
                self.script_name = None
            self._append_log("INFO", f"Script rimosso dall'indice: {selected}")

        ttk.Button(btns, text="Apri selezionato", command=choose_selected).pack(side="left")
        ttk.Button(btns, text="Rimuovi selezionato", command=remove_selected).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Apri altro...", command=choose_open_other).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Annulla", command=dialog.destroy).pack(side="right")

        listbox.bind("<Double-1>", choose_selected)
        dialog.bind("<Return>", choose_selected)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

        self.wait_window(dialog)
        return result["value"]

    def _open_script_with_file_dialog(self):
        selected_file = filedialog.askopenfilename(
            parent=self,
            title="Apri file script",
            filetypes=[
                ("Script SCPI", "*.scpi"),
                ("JSON", "*.json"),
                ("Testo", "*.txt"),
                ("Tutti i file", "*.*"),
            ],
            initialdir=str(SCRIPT_DIR),
        )
        if not selected_file:
            return

        path = Path(selected_file)
        self._load_script_from_path(path)

        if path.is_relative_to(SCRIPT_DIR):
            relative_path = str(path.relative_to(SCRIPT_DIR))
            rel_parts = list(path.relative_to(SCRIPT_DIR).parts)
            if rel_parts:
                rel_parts[-1] = Path(rel_parts[-1]).stem
            inferred_name = self._normalize_script_name("/".join(rel_parts))
            if inferred_name:
                self.script_index[inferred_name] = relative_path
                self.script_name = inferred_name
                self._save_script_index()

    def _load_script_by_name(self, name: str):
        normalized_name = self._normalize_script_name(name)
        if normalized_name not in self.script_index:
            messagebox.showerror(APP_NAME, f"Script '{normalized_name}' non presente nell'indice.")
            return

        path = self._script_path_from_name(normalized_name)
        self._load_script_from_path(path, normalized_name)

    def _load_script_lines_for_engine(self, script_name: str) -> List[str]:
        normalized_name = self._normalize_script_name(script_name)
        if normalized_name in self.script_index:
            path = self._script_path_from_name(normalized_name)
        else:
            path = SCRIPT_DIR / self._script_relpath_from_name(normalized_name)
        if not path.exists():
            raise ValueError(f"Script '{normalized_name}' non trovato in {path}")
        content = path.read_text(encoding="utf-8")
        return content.splitlines()

    def _load_script_from_path(self, path: Path, script_name: Optional[str] = None):
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Impossibile caricare lo script:\n{exc}")
            return

        self.script_text.delete("1.0", "end")
        self.script_text.insert("1.0", content)
        self.script_name = script_name or self._normalize_script_name(path.stem)
        self._append_log("INFO", f"Script caricato: {self.script_name} ({path})")

    def save_script(self):
        if not self.script_name:
            self.save_script_as()
            return

        self._save_script_by_name(self.script_name)

    def save_script_as(self):
        current_activity = ""
        proposed_name = self.script_name or ""
        if "/" in proposed_name:
            current_activity, proposed_name = proposed_name.rsplit("/", 1)

        activity = simpledialog.askstring(
            APP_NAME,
            "Attività (cartella opzionale):",
            initialvalue=current_activity,
            parent=self,
        )
        if activity is None:
            return

        name = simpledialog.askstring(APP_NAME, "Nome script:", initialvalue=proposed_name, parent=self)
        if not name:
            return
        full_name = f"{activity}/{name}" if activity else name
        normalized_name = self._normalize_script_name(full_name)
        if not normalized_name:
            messagebox.showwarning(APP_NAME, "Il nome script non può essere vuoto.")
            return
        self.script_name = normalized_name
        self._save_script_by_name(normalized_name)

    def _save_script_by_name(self, name: str):
        rel_path = self._script_relpath_from_name(name)
        self.script_index[name] = rel_path
        path = self._script_path_from_name(name)
        try:
            content = self.script_text.get("1.0", "end-1c")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            self._save_script_index()
            self._append_log("INFO", f"Script salvato: {name} ({path})")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Impossibile salvare lo script:\n{exc}")

    def _append_log(self, level: str, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", f"[{level}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _add_conn_history_entry(self, command: str):
        self.connection_history.append(command)
        if hasattr(self, "conn_history_list"):
            self.conn_history_list.insert("end", command)
            self.conn_history_list.see("end")

    def _insert_selected_conn_history(self, _event=None):
        selection = self.conn_history_list.curselection()
        if not selection:
            return
        command = self.conn_history_list.get(selection[0])
        self.script_text.insert("end", f"{command}\n")
        self.script_text.see("end")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _set_running(self, value: bool):
        self.running = value
        if value:
            self.btn_run.state(["disabled"])
            self.btn_stop.state(["!disabled"])
        else:
            self.btn_run.state(["!disabled"])
            self.btn_stop.state(["disabled"])

    def request_stop(self):
        self.engine.stop_requested = True
        self._append_log("INFO", "Stop richiesto")

    def close_connections(self):
        self.engine.close_all()
        self._append_log("INFO", "Connessioni chiuse")

    def run_script(self):
        if self.running:
            return

        raw_script = self.script_text.get("1.0", "end")
        normalized_script = re.sub(r"\\n(?=\s*[@*A-Za-z])", "\n", raw_script)
        lines = normalized_script.splitlines()

        if normalized_script != raw_script:
            self._append_log("INFO", "Rilevate sequenze \\n nel testo: convertite in nuove righe")

        self._set_running(True)

        def worker():
            try:
                entry_name = self.script_name or "__editor__"
                self.engine.run_lines(lines, entry_script_name=entry_name)
                self._append_log("INFO", "Script completato")
            except Exception as exc:
                self._append_log("ERR", f"Script terminato con errore: {exc}")
            finally:
                self.after(0, lambda: self._set_running(False))

        self.run_thread = threading.Thread(target=worker, daemon=True)
        self.run_thread.start()

    def destroy(self):
        self.engine.close_all()
        super().destroy()


if __name__ == "__main__":
    app = CombinedMonitorApp()
    app.mainloop()
