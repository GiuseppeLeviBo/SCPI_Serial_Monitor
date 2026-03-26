import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText
from typing import Callable, Dict, Optional

from SCPI_serial_monitor import (
    RawSocketScpiTransport,
    SerialTransport,
    VisaTransport,
    is_query_command,
)

APP_NAME = "SCPI Combined Monitor"


@dataclass
class TargetConnection:
    name: str
    transport: object


class CombinedScriptEngine:
    """Motore script ispirato a core_engine ma integrato con il monitor."""

    def __init__(self, logger: Callable[[str, str], None]):
        self.logger = logger
        self.targets: Dict[str, TargetConnection] = {}
        self.current_target: Optional[str] = None
        self.last: Optional[str] = None
        self.last_command: Optional[str] = None
        self.stop_requested = False

    def reset_runtime(self):
        self.current_target = None
        self.last = None
        self.last_command = None
        self.stop_requested = False

    def close_all(self):
        for tc in self.targets.values():
            try:
                tc.transport.disconnect()
            except Exception:
                pass
        self.targets.clear()

    def _parse_terminator(self, value: str) -> str:
        return value.encode("utf-8").decode("unicode_escape")

    def cmd_conn(self, args):
        if len(args) < 3:
            raise ValueError("@conn richiede: nome tipo endpoint [parametri]")

        name = args[0]
        conn_type = args[1].lower()
        endpoint = args[2]
        params = args[3:]

        if conn_type == "serial":
            baud = int(params[0]) if params else 9600
            timeout_s = float(params[1]) if len(params) > 1 else 2.0
            terminator = self._parse_terminator(params[2]) if len(params) > 2 else "\n"
            transport = SerialTransport(endpoint, baud, timeout_s, terminator)

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

        else:
            raise ValueError(f"Transport sconosciuto: {conn_type}")

        transport.connect()
        self.targets[name] = TargetConnection(name=name, transport=transport)
        self.logger("INFO", f"CONN: {name} -> {conn_type} {endpoint}")

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

    def run_lines(self, lines):
        self.reset_runtime()
        for idx, raw in enumerate(lines, start=1):
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
                self.logger("ERR", f"L{idx}: {exc}")
                raise

    def _run_meta(self, line: str):
        tokens = line.split()
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
        else:
            self.logger("WARN", f"Meta comando non supportato: {line}")

    def _run_command(self, cmd: str):
        if not self.current_target:
            raise RuntimeError("Nessun target selezionato (@target)")

        transport = self.targets[self.current_target].transport
        self.last_command = cmd
        self.logger("TX", f"[{self.current_target}] {cmd}")

        if is_query_command(cmd):
            reply = transport.query(cmd)
            self.last = reply
            self.logger("RX", f"[{self.current_target}] {reply}")
        else:
            transport.write(cmd)
            self.last = None


class CombinedMonitorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1150x760")
        self.minsize(900, 600)

        self.engine = CombinedScriptEngine(self._append_log)
        self.run_thread: Optional[threading.Thread] = None
        self.running = False

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
        ttk.Button(actions, text="Chiudi Connessioni", command=self.close_connections).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Pulisci Log", command=self.clear_log).pack(side="right")

        bottom = ttk.LabelFrame(root, text="Monitor Log", padding=10)
        bottom.pack(fill="both", expand=True, pady=(10, 0))
        self.log = ScrolledText(bottom, wrap="word", font=("Consolas", 10), state="disabled")
        self.log.pack(fill="both", expand=True)

    def _append_log(self, level: str, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", f"[{level}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

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

        lines = self.script_text.get("1.0", "end").splitlines()
        self._set_running(True)

        def worker():
            try:
                self.engine.run_lines(lines)
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
