import json
import threading
import time
import tkinter as tk
import re
import contextlib
import csv
import shlex
import locale
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox, simpledialog, filedialog
from tkinter.scrolledtext import ScrolledText
from typing import Callable, Dict, List, Optional

try:
    from dsl_commands import (
        DSL_COMMAND_SPECS,
        BUILTIN_SYMBOL_SPECS,
        get_command_matches,
        get_builtin_matches,
    )
except Exception:
    DSL_COMMAND_SPECS = {}
    BUILTIN_SYMBOL_SPECS = {}
    get_command_matches = None
    get_builtin_matches = None
try:
    from dsl_autocomplete import attach_autocomplete
except Exception:
    attach_autocomplete = None
from SCPI_serial_monitor import (
    RawSocketScpiTransport,
    SerialTransport,
    VisaTransport,
    is_query_command,
)

APP_NAME = "SCPI Combined Monitor"
SCRIPT_INDEX_FILE = Path.home() / ".scpi_combined_scripts.json"
SCRIPT_DIR = Path.home() / ".scpi_macros"



class Translator:
    def __init__(self, default_lang="en"):
        sys_lang = None
        try:
            # Metodo moderno e sicuro per Python 3.11+
            # 1. Salva il locale corrente di Python (di default "C")
            saved_locale = locale.setlocale(locale.LC_ALL, None)
            # 2. Carica il locale di sistema (Windows/Linux)
            locale.setlocale(locale.LC_ALL, "")
            # 3. Leggi la lingua del sistema (es. "it_IT")
            sys_lang = locale.getlocale()[0]
            # 4. Ripristina il locale originale per NON rompere i float SCPI!
            locale.setlocale(locale.LC_ALL, saved_locale)
        except Exception:
            pass

        # Fallback ultra-sicuro tramite variabili d'ambiente
        if not sys_lang:
            sys_lang = os.getenv("LANG", default_lang)

        self.lang = sys_lang[:2].lower() if sys_lang else default_lang
        
        self.dict = {}
        try:
            with open("locales.json", "r", encoding="utf-8") as f:
                self.dict = json.load(f)
            # Se la lingua di sistema non è nel JSON, usa il default
            if self.lang not in self.dict:
                self.lang = default_lang
        except Exception:
            pass # Fallback silenzioso ai testi hardcoded nel codice

    def __call__(self, key: str, fallback: str) -> str:
        return self.dict.get(self.lang, {}).get(key, fallback)

# Istanza globale
_tr = Translator(default_lang="en")

@dataclass
class TargetConnection:
    name: str
    transport: object



# Assicurati di avere in cima al file i tuoi import originali:
# from SCPI_serial_monitor import ( RawSocketScpiTransport, SerialTransport, VisaTransport, is_query_command )

from typing import Any, Callable, Dict, List, Optional
import math
import operator
import shlex
import time
import contextlib
import csv
import threading
from datetime import datetime
from pathlib import Path


class CombinedScriptEngine:
    """Motore script vNext: Globali, Locali Statiche, Loop per-frame, Math e Debugger."""
    BUILTIN_READONLY_NAMES = {"last",  "last_command", "last_line", "last_bin", "target", "script", "time", "date", "datetime", "csvname", "binname"}
    def _get_builtin_value(self, name: str) -> tuple[bool, Any]:
        name = name.lower()

        if name == "last":
            val = self.last if self.last is not None else "0"
            try:
                return True, float(val)
            except (ValueError, TypeError):
                return True, str(val)
        if name == "last_command":
            return True, self.last_command or ""
        if name == "last_line":
            return True, self.last_line or ""
        if name == "last_bin":
            if self.last_bin is None:
                return True, "EMPTY"
            return True, f"BINARY[{len(self.last_bin)} bytes]"
        if name == "csvname":
            return True, self.csvname

        if name == "binname":
            return True, self.binname
        if name == "target":
            return True, self.current_target or ""

        if name == "script":
            if self.call_stack:
                return True, self.call_stack[-1]["name"]
            return True, ""

        if name == "time":
            return True, datetime.now().strftime("%H:%M:%S")
        if name == "date":
            return True, datetime.now().strftime("%d/%m/%Y")
        if name == "datetime":
            return True, datetime.now().strftime("%d/%m/%Y_%H:%M:%S")            
        return False, None

    def _check_writable_name(self, name: str):
        if name.lower() in self.BUILTIN_READONLY_NAMES:
            raise ValueError(_tr("err_builtin_readonly", "'{name}' è un nome built-in di sola lettura e non può essere assegnato").format(name=name))

    def _sanitize_filename(self, text: str) -> str:
        text = str(text).strip()
        text = text.replace(":", "-").replace("/", "_").replace("\\", "_")
        text = text.replace("*", "_").replace("?", "_").replace('"', "_")
        text = text.replace("<", "_").replace(">", "_").replace("|", "_")
        text = re.sub(r"\s+", "_", text)
        text = re.sub(r"_+", "_", text)
        return text.strip("._")

    def _build_name_from_args(self, args: List[str]) -> str:
        if not args:
            raise ValueError(_tr("err_req_arg", "Richiesto almeno un argomento"))

        parts =[]
        for arg in args:
            token = arg.strip()
            token_lower = token.lower()

            exists, val, _ = self._get_var(token_lower)
            if exists:
                text = str(val)
            else:
                try:
                    num = float(token)
                    text = str(int(num)) if num.is_integer() else str(num)
                except ValueError:
                    text = token

            text = self._sanitize_filename(text)
            if text:
                parts.append(text)

        name = "_".join(parts)
        if not name:
            raise ValueError(_tr("err_invalid_filename", "Nome file vuoto o non valido"))
        return name

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
        self.last_line: Optional[str] = None
        self.readbin_armed = False

        self.global_vars: Dict[str, Any] = {}
        self.local_vars: Dict[str, Dict[str, Any]] = {}
        self.call_stack: List[Dict[str, Any]] =[]

        self.stop_requested = False
        self.step_mode = False 
        self.step_event = threading.Event()
        self.step_event.set() 
        self.breakpoints: set[int] = set()
        self.on_state_change: Optional[Callable[[], None]] = None
        self.prompt_callback: Optional[Callable[[str], None]] = None

        self.auto_store_enabled = False
        self.auto_store_label = "AUTO"
        self.serial_query_retry_delay_s = 1.0
        self.serial_pre_query_flush = True
        self.serial_multiline_idle_s = 0.12

        self.csvname = "lastres.csv"
        self.binname = ""
        self.lastres_path = Path.cwd() / self.csvname

        self._math_env = {k: v for k, v in math.__dict__.items() if not k.startswith("__")}

    def reset_runtime(self):
        self.current_target = None
        self.last = None
        self.last_bin = None
        self.last_command = None
        self.last_line = None
        self.stop_requested = False
        self.readbin_armed = False
        self.call_stack =[]
        
        self.global_vars.clear()
        self.local_vars.clear()
        
        self.auto_store_enabled = False
        self.auto_store_label = "AUTO"
        
        if getattr(self, "step_mode", False):
            self.step_event.clear()
        else:
            self.step_event.set()

    def close_all(self):
        for tc in self.targets.values():
            try:
                tc.transport.disconnect()
            except Exception:
                pass
        self.targets.clear()

    def _get_var(self, name: str) -> tuple[bool, Any, str]:
        name = name.lower()
        exists, val = self._get_builtin_value(name)
        if exists: return True, val, "builtin"
        if not self.call_stack: return False, None, ""
        curr_script = self.call_stack[-1]["name"]
        if curr_script in self.local_vars and name in self.local_vars[curr_script]:
            return True, self.local_vars[curr_script][name], "local"
        if name in self.global_vars:
            return True, self.global_vars[name], "global"
        return False, None, ""

    def _resolve_value(self, val_str: str) -> Any:
        val_str = val_str.strip()
        if (val_str.startswith('"') and val_str.endswith('"')) or \
           (val_str.startswith("'") and val_str.endswith("'")):
            return val_str[1:-1]

        v_lower = val_str.lower()
        if v_lower == "last":
            val = self.last if self.last is not None else "0"
            try: return float(val)
            except (ValueError, TypeError): return str(val)

        exists, val, _ = self._get_var(v_lower)
        if exists:
            return val

        try:
            return float(val_str)
        except ValueError:
            raise ValueError(_tr("err_var_undef", "Variabile '{val_str}' non definita. (Se intendevi una stringa testuale, usa le virgolette: \"{val_str}\")").format(val_str=val_str))

    def _evaluate_condition(self, left_raw: str, op: str, right_raw: str) -> bool:
        left = self._resolve_value(left_raw)
        right = self._resolve_value(right_raw)
        try:
            left, right = float(left), float(right)
        except (ValueError, TypeError):
            left, right = str(left), str(right)

        ops = {"==": operator.eq, "!=": operator.ne, ">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le}
        if op not in ops:
            raise ValueError(_tr("err_unsupported_op", "Operatore non supportato: {op}").format(op=op))
        return ops[op](left, right)

    def _skip_to_endloop(self):
        frame = self.call_stack[-1]
        lines, pc, nesting = frame["lines"], frame["pc"], 1
        while pc < len(lines):
            line = lines[pc].strip().lower()
            if not line or line.startswith("#"):
                pc += 1; continue
            if line.startswith("@loop") or line.startswith("@while"): nesting += 1
            elif line.startswith("@endloop") or line.startswith("@endwhile"):
                nesting -= 1
                if nesting == 0:
                    frame["pc"] = pc + 1
                    return
            pc += 1
        raise ValueError(_tr("err_eof_no_endloop", "Raggiunta fine file senza trovare @endloop o @endwhile"))

    def _parse_terminator(self, value: str) -> str: return value.encode("utf-8").decode("unicode_escape")
    @staticmethod
    def _escape_terminator(value: str) -> str: return value.encode("unicode_escape").decode("ascii")

    def _build_conn_history_command(self, name: str, conn_type: str, endpoint: str, timeout_s: float, terminator: str, baud: Optional[int] = None, backend: Optional[str] = None, socket_port: Optional[int] = None) -> str:
        if conn_type == "serial": return f"@conn {name} serial {endpoint} {baud} {timeout_s:g} {self._escape_terminator(terminator)}"
        if conn_type == "visa": return f"@conn {name} visa {endpoint} {timeout_s:g} {backend} {self._escape_terminator(terminator)}"
        if conn_type == "socket": return f"@conn {name} socket {endpoint}:{socket_port} {timeout_s:g} {self._escape_terminator(terminator)}"
        return f"@conn {name} {conn_type} {endpoint}"

    def cmd_conn(self, args):
        if len(args) < 3:
            raise ValueError(_tr("err_conn_args", "@conn richiede: nome tipo endpoint [parametri]"))

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
            host, port = (endpoint.rsplit(":", 1)[0], int(endpoint.rsplit(":", 1)[1])) if ":" in endpoint else (endpoint, int(params[0]) if params else 5025)
            timeout_s = float(params[0] if ":" in endpoint and params else (params[1] if len(params)>1 else 2.0))
            terminator = self._parse_terminator(params[1] if ":" in endpoint and len(params)>1 else (params[2] if len(params)>2 else "\n"))
            transport = RawSocketScpiTransport(host, port, timeout_s, terminator)
            history_cmd = self._build_conn_history_command(name, conn_type, host, timeout_s, terminator, socket_port=port)
        else:
            raise ValueError(_tr("err_unknown_transport", "Transport sconosciuto: {conn_type}").format(conn_type=conn_type))

        transport.connect()
        self.targets[name] = TargetConnection(name=name, transport=transport)
        self.logger("INFO", f"CONN: {name} -> {conn_type} {endpoint}")
        if history_cmd and self.history_logger:
            self.history_logger(history_cmd)

    def cmd_target(self, args):
        if not args:
            raise ValueError(_tr("err_target_args", "@target richiede il nome del target"))
        name = args[0]
        if name not in self.targets:
            raise ValueError(_tr("err_target_not_conn", "Target '{name}' non connesso").format(name=name))
        self.current_target = name
        self.last = None
        self.logger("INFO", f"TARGET -> {name}")

    def _load_script_lines(self, script_name: str) -> List[str]:
        if not self.script_loader:
            raise ValueError(_tr("err_no_script_loader", "Script loader non configurato"))
        return list(self.script_loader(script_name))

    def _push_script(self, script_name: str, lines: List[str]):
        self.call_stack.append({"name": script_name, "lines": lines, "pc": 0, "loop_stack":[]})
        self.logger("INFO", f"CALL -> {script_name}")

    def _format_args_for_display(self, args: List[str]) -> str:
        parts =[]
        for arg in args:
            try: val = self._resolve_value(arg)
            except ValueError: val = arg
            parts.append(str(val))
        return " ".join(parts)
        
    def run_lines(self, lines, entry_script_name: str = "__main__"):
        self.reset_runtime()
        self._push_script(entry_script_name, list(lines))

        while self.call_stack and not self.stop_requested:
            if self.on_state_change: self.on_state_change()
            self.step_event.wait()
            if getattr(self, "step_mode", False): self.step_event.clear()

            frame = self.call_stack[-1]
            if frame["pc"] >= len(frame["lines"]):
                self.call_stack.pop(); continue

            raw = frame["lines"][frame["pc"]]
            frame["pc"] += 1

            if self.stop_requested:
                self.logger("WARN", "Esecuzione interrotta")
                break

            line = raw.strip()
            if not line or line.startswith("#"): continue
            self.last_line = line
            try:
                if line.startswith("@"): self._run_meta(line)
                else: self._run_command(line)
            except Exception as exc:
                self.logger("ERR", f"{frame['name']}:L{frame['pc']}: {exc}")
                self.stop_requested = True

    def _run_meta(self, line: str):
        tokens = shlex.split(line)
        if not tokens: return
        cmd, args, curr_script = tokens[0][1:].lower(), tokens[1:], self.call_stack[-1]["name"]

        if cmd == "conn": self.cmd_conn(args)
        elif cmd == "target": self.cmd_target(args)
        elif cmd == "wait":
            self.logger("INFO", f"WAIT: {float(self._resolve_value(args[0])) if args else 0.0}s")
            time.sleep(float(self._resolve_value(args[0])) if args else 0.0)
        elif cmd == "halt":
            self.stop_requested = True
            self.logger("INFO", "HALT")

        elif cmd == "var":
            if len(args) < 2: raise ValueError(_tr("err_var_args", "@var richiede: nome valore"))
            var_name, val = args[0].lower(), self._resolve_value(" ".join(args[1:]))
            self._check_writable_name(var_name)
            self.local_vars.setdefault(curr_script, {})[var_name] = val
            self.logger("INFO", f"VAR (local): {var_name} = {val}")

        elif cmd == "gvar":
            if len(args) < 2: raise ValueError(_tr("err_gvar_args", "@gvar richiede: nome valore"))
            var_name, val = args[0].lower(), self._resolve_value(" ".join(args[1:]))
            self._check_writable_name(var_name)
            self.global_vars[var_name] = val
            self.logger("INFO", f"GVAR (global): {var_name} = {val}")

        elif cmd == "inc":
            if not args: raise ValueError(_tr("err_inc_args", "@inc richiede il nome della variabile"))
            var_name, step = args[0].lower(), float(self._resolve_value(args[1])) if len(args) > 1 else 1.0
            self._check_writable_name(var_name)
            
            exists, val, scope = self._get_var(var_name)
            if not exists: raise ValueError(_tr("err_inc_undef", "Variabile '{var_name}' non definita per @inc").format(var_name=var_name))
            try: val_num = float(val)
            except (ValueError, TypeError): raise ValueError(_tr("err_inc_not_num", "Impossibile incrementare '{var_name}': il valore attuale '{val}' non è numerico.").format(var_name=var_name, val=val))
                
            new_val = val_num + step
            if scope == "local": self.local_vars[curr_script][var_name] = new_val
            else: self.global_vars[var_name] = new_val
            self.logger("INFO", f"INC ({scope}): {var_name} = {new_val}")

        elif cmd == "eval":
            if len(args) < 3 or args[1] != "=": raise ValueError(_tr("err_eval_args", "@eval formato: dest = expr"))
            var_dest, expr = args[0].lower(), " ".join(args[2:]).replace("^", "**")
            self._check_writable_name(var_dest)

            eval_env = {"__builtins__": {}}
            eval_env.update(self._math_env); eval_env.update({k.upper(): v for k, v in self._math_env.items()})
            for k, v in self.global_vars.items(): eval_env[k.lower()] = v; eval_env[k.upper()] = v
            for k, v in self.local_vars.get(curr_script, {}).items(): eval_env[k.lower()] = v; eval_env[k.upper()] = v

            try: res = eval(expr, eval_env)
            except NameError as e: raise ValueError(_tr("err_eval_var_not_found", "Variabile non trovata in '{expr}': {e}").format(expr=expr, e=e))
            except Exception as e: raise ValueError(_tr("err_eval_expr", "Errore espressione '{expr}': {e}").format(expr=expr, e=e))

            exists, _, scope = self._get_var(var_dest)
            if exists and scope == "global" and var_dest not in self.local_vars.get(curr_script, {}):
                self.global_vars[var_dest] = res
                self.logger("INFO", f"EVAL (global): {var_dest} = {res}")
            else:
                self.local_vars.setdefault(curr_script, {})[var_dest] = res
                self.logger("INFO", f"EVAL (local): {var_dest} = {res}")

        elif cmd in ("ifdef", "ifndef"):
            if len(args) < 2: raise ValueError(_tr("err_ifdef_args", "@{cmd} formato: nome azione").format(cmd=cmd))
            exists, _, _ = self._get_var(args[0].lower())
            if (cmd == "ifdef" and exists) or (cmd == "ifndef" and not exists):
                action = " ".join(args[1:])
                self.logger("INFO", f"{cmd.upper()} TRUE: '{action}'")
                if action.startswith("@"): self._run_meta(action)
                else: self._run_command(action)

        elif cmd == "if":
            if len(args) < 4: raise ValueError(_tr("err_if_args", "@if formato: @if <left> <op> <right> <azione>"))
            if self._evaluate_condition(args[0], args[1], args[2]):
                action_line = " ".join(args[3:])
                self.logger("INFO", f"IF TRUE: '{action_line}'")
                if action_line.startswith("@"): self._run_meta(action_line)
                else: self._run_command(action_line)

        elif cmd == "loop":
            if not args: raise ValueError(_tr("err_loop_args", "@loop richiede numero iterazioni"))
            count = int(self._resolve_value(args[0]))
            self.call_stack[-1]["loop_stack"].append({"type": "loop", "start_pc": self.call_stack[-1]["pc"], "remaining": count})
            self.logger("INFO", f"LOOP START: {count} iterazioni")

        elif cmd == "while":
            if len(args) < 3: raise ValueError(_tr("err_while_args", "@while richiede: <left> <op> <right>"))
            self.call_stack[-1]["loop_stack"].append({"type": "while", "start_pc": self.call_stack[-1]["pc"], "args": args})
            if not self._evaluate_condition(args[0], args[1], args[2]): self._skip_to_endloop()

        elif cmd in ("endloop", "endwhile"):
            loop_stack = self.call_stack[-1]["loop_stack"]
            if not loop_stack: raise ValueError(_tr("err_end_orphan", "@{cmd} senza blocco di apertura").format(cmd=cmd))
            curr = loop_stack[-1]
            if curr["type"] == "loop":
                curr["remaining"] -= 1
                if curr["remaining"] > 0: self.call_stack[-1]["pc"] = curr["start_pc"]
                else: loop_stack.pop(); self.logger("INFO", "LOOP END")
            else:
                if self._evaluate_condition(curr["args"][0], curr["args"][1], curr["args"][2]): self.call_stack[-1]["pc"] = curr["start_pc"]
                else: loop_stack.pop(); self.logger("INFO", "WHILE END")

        elif cmd == "break":
            if not self.call_stack[-1]["loop_stack"]: raise ValueError(_tr("err_break_orphan", "@break fuori da un loop"))
            self.call_stack[-1]["loop_stack"].pop(); self._skip_to_endloop()
            self.logger("INFO", "BREAK: Uscita forzata dal loop")

        elif cmd == "print":
            if not args: raise ValueError(_tr("err_print_args", "@print richiede almeno un argomento"))
            self.logger("INFO", f"PRINT: {self._format_args_for_display(args)}")

        elif cmd == "csvname":
            name = self._build_name_from_args(args)
            self.csvname = name if name.lower().endswith(".csv") else name + ".csv"
            self.lastres_path = Path.cwd() / self.csvname
            self.logger("INFO", f"CSVNAME: {self.csvname}")

        elif cmd == "binname":
            self.binname = self._build_name_from_args(args)
            self.logger("INFO", f"BINNAME: {self.binname}")

        elif cmd == "prompt":
            msg = " ".join(args).strip("\"'")
            self.logger("WARN", f"PROMPT: {msg}")
            if self.prompt_callback: self.prompt_callback(msg)

        elif cmd == "store":
            if len(args) == 0: self._store_value("LAST")
            elif len(args) == 1: self._store_value(args[0])
            elif len(args) == 2: self._store_value(args[0], str(self._resolve_value(args[1])))
            else: raise ValueError(_tr("err_store_args", "@store accetta massimo 2 argomenti (@store <etichetta> [valore]). Se l'etichetta contiene spazi, usa le virgolette. Se volevi inserire testo libero nel file CSV, usa il comando @comment."))
                
        elif cmd == "startstore":
            self.auto_store_enabled = True
            self.auto_store_label = " ".join(args).strip() if args else "AUTO"
            self.logger("INFO", f"STARTSTORE: attivo (label={self.auto_store_label})")

        elif cmd == "stopstore":
            self.auto_store_enabled = False; self.logger("INFO", "STOPSTORE: disattivato")

        elif cmd == "comment":
            text = " ".join(args).strip()
            if not text: raise ValueError(_tr("err_comment_args", "@comment richiede un testo"))
            self._store_comment(text)

        elif cmd in ("call", "script"):
            script_name = " ".join(args).strip().strip("()")
            self._push_script(script_name, self._load_script_lines(script_name))

        elif cmd == "rts":
            if self.call_stack: self.logger("INFO", f"RTS <- {self.call_stack.pop()['name']}")
            if not self.call_stack: self.stop_requested = True

        elif cmd == "readbin":
            self.readbin_armed = True; self.logger("INFO", "READBIN armato")

        elif cmd == "savebin":
            if not args: raise ValueError(_tr("err_savebin_args", "@savebin richiede un filename"))
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
                if waiting: chunks.append(transport.ser.read(waiting))
                else: time.sleep(0.01)
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

        raise RuntimeError(_tr("err_no_bin_read", "Transport non supporta lettura binaria"))

    def _query_with_buffered_multiline(self, transport, cmd: str) -> str:
        first = transport.query(cmd)
        if not isinstance(transport, SerialTransport): return first

        lines: List[str] =[first] if first is not None else[]
        pending, idle_deadline = "", time.time() + self.serial_multiline_idle_s
        while time.time() < idle_deadline:
            extra = transport.read_available()
            if not extra: time.sleep(0.01); continue
            idle_deadline = time.time() + self.serial_multiline_idle_s
            pending += extra.replace("\r\n", "\n").replace("\r", "\n")
            chunks = pending.split("\n")
            pending = chunks.pop() if chunks else ""
            for raw_line in chunks:
                clean = raw_line.strip()
                if clean: lines.append(clean)
        if pending.strip(): lines.append(pending.strip())
        return "\n".join(lines).strip()

    @staticmethod
    def _csv_sanitize(value: Optional[str]) -> str:
        if value is None: return "NOVAL"
        return str(value).replace("\r\n", "\n").replace("\r", "\n")

    def _ensure_lastres_header(self):
        if self.lastres_path.exists() and self.lastres_path.stat().st_size > 0: return
        with open(self.lastres_path, "w", newline="", encoding="utf-8") as fp:
            csv.writer(fp, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\n").writerow(["timestamp", "target", "command", "name", "value"])

    def _append_lastres_row(self, target: str, command: str, name: str, value: Optional[str]):
        self._ensure_lastres_header()
        ts = datetime.now().strftime("%d%m%Y %H:%M:%S")
        with open(self.lastres_path, "a", newline="", encoding="utf-8") as fp:
            csv.writer(fp, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\n").writerow([ts, target, command, name, self._csv_sanitize(value)])

    def _store_value(self, name: str, value: Optional[str] = None):
        target = self.current_target or ""
        command = self.last_command or ""
        stored_value = self.last if value is None else value
        self._append_lastres_row(target, command, name, stored_value)
        self.logger("INFO", f"STORE: {name} [{target}]")

    def _store_comment(self, text: str):
        self._append_lastres_row(self.current_target or "", "@comment", "COMMENT", text.lstrip("\r\n"))
        self.logger("INFO", f"COMMENT salvato: {text.lstrip('\r\n')}")

    def _save_binary(self, filename: str):
        if self.last_bin is None: raise RuntimeError(_tr("err_no_bin_data", "Nessun dato binario (usa prima @readbin + comando)"))
        p = Path(filename)
        out = p.with_name(f"{self.binname}{p.suffix if p.suffix else '.bin'}") if self.binname else p.with_name(f"{p.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{self.current_target or 'notarget'}{p.suffix}")
        out.write_bytes(self.last_bin)
        self.logger("INFO", f"SAVEBIN: {out} ({len(self.last_bin)} bytes)")

    def _run_command(self, cmd: str):
        if not self.current_target: raise RuntimeError(_tr("err_no_target_selected", "Nessun target selezionato (@target)"))
        transport = self.targets[self.current_target].transport
        self.last_command = cmd
        self.logger("TX", f"[{self.current_target}] {cmd}")

        if self.readbin_armed:
            transport.write(cmd)
            self.last_bin = self._read_binary_response(transport)
            self.last, self.readbin_armed = None, False
            self.logger("RXBIN", f"[{self.current_target}] {len(self.last_bin)} bytes")

        elif is_query_command(cmd):
            if self.serial_pre_query_flush:
                with contextlib.suppress(Exception):
                    transport.ser.reset_input_buffer() if isinstance(transport, SerialTransport) else transport.read_available()
            try: reply = self._query_with_buffered_multiline(transport, cmd)
            except TimeoutError:
                if not isinstance(transport, SerialTransport): raise
                self.logger("WARN", f"[{self.current_target}] Timeout query: retry tra {self.serial_query_retry_delay_s:g}s")
                time.sleep(self.serial_query_retry_delay_s)
                reply = self._query_with_buffered_multiline(transport, cmd)
            self.last, self.last_bin = reply, None
            self.logger("RX", f"[{self.current_target}] {reply}")
            if self.auto_store_enabled: self._store_value(self.auto_store_label, value=reply)
        else:
            transport.write(cmd)
            self.last = self.last_bin = None
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
        # Colleghiamo il motore per l'aggiornamento UI ad ogni riga per il Debugger!
        self.engine.on_state_change = self._handle_state_change
        
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

        btn_ws = ttk.Button(sidebar, text=_tr("btn_open_ws", "Apri Cartella Progetto"), command=self.open_workspace)
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

        self.btn_run = ttk.Button(toolbar, text=_tr("btn_run", "Esegui Tab"), command=self.run_script)
        self.btn_run.pack(side="left", padx=(0, 4))
        self.btn_debug = ttk.Button(toolbar, text=_tr("btn_debug", "Debug"), command=self.debug_script)
        self.btn_debug.pack(side="left", padx=(0, 4))
        self.btn_stop = ttk.Button(toolbar, text=_tr("btn_stop", "Stop"), command=self.request_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(0, 15))
        
        # --- PULSANTI DEBUG ---
        self.btn_pause = ttk.Button(toolbar, text=_tr("btn_pause", "Pausa"), command=self.toggle_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=(0, 4))
        self.btn_step = ttk.Button(toolbar, text=_tr("btn_step", "Step"), command=self.step_script, state="disabled")
        self.btn_step.pack(side="left", padx=(0, 15))

        ttk.Button(toolbar, text=_tr("btn_new", "Nuovo"), command=self.new_tab).pack(side="left", padx=(0, 4))
        ttk.Button(toolbar, text=_tr("btn_save", "Salva"), command=self.save_current_tab).pack(side="left", padx=(0, 4))
        ttk.Button(toolbar, text=_tr("btn_close_tab", "Chiudi Tab"), command=self.close_current_tab).pack(side="left", padx=(0, 15))

        ttk.Button(toolbar, text=_tr("btn_close_conn", "Chiudi Connessioni"), command=self.close_connections).pack(side="right", padx=(4, 0))
        ttk.Button(toolbar, text=_tr("btn_clear_log", "Pulisci Log"), command=self.clear_log).pack(side="right")
        # Splitter verticale per i Tab e il Pannello Inferiore
        right_paned = ttk.PanedWindow(main_area, orient=tk.VERTICAL)
        right_paned.pack(fill="both", expand=True)

        # Notebook (Area Tab)
        self.notebook = ttk.Notebook(right_paned)
        right_paned.add(self.notebook, weight=3)

        # ==================== PANNELLO INFERIORE ====================
        bottom_frame = ttk.Frame(right_paned)
        right_paned.add(bottom_frame, weight=1)

        log_frame = ttk.LabelFrame(bottom_frame, text=_tr("tab_log", "Monitor Log"), padding=5)
        log_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))
        self.log = ScrolledText(log_frame, wrap="word", font=("Consolas", 10), state="disabled")
        self.log.pack(fill="both", expand=True)

        # Tabs per Variabili e History
        self.bottom_tabs = ttk.Notebook(bottom_frame)
        self.bottom_tabs.pack(side="right", fill="both", expand=False)

        # Tab Variabili (Debugger)
        
        var_frame = ttk.Frame(self.bottom_tabs)
        self.bottom_tabs.add(var_frame, text=_tr("tab_vars", "Variabili"))

        var_container = ttk.Frame(var_frame)
        var_container.pack(fill="both", expand=True)

        self.var_tree = ttk.Treeview(
            var_container,
            columns=("nome", "valore", "scope"),
            show="headings",
            height=8
        )
        self.var_tree.heading("nome", text=_tr("col_name", "Nome"))
        self.var_tree.heading("valore", text=_tr("col_val", "Valore"))
        self.var_tree.heading("scope", text=_tr("col_scope", "Scope"))

        self.var_tree.column("nome", width=120)
        self.var_tree.column("valore", width=120)
        self.var_tree.column("scope", width=120)

        var_scrollbar = ttk.Scrollbar(var_container, orient="vertical", command=self.var_tree.yview)
        self.var_tree.configure(yscrollcommand=var_scrollbar.set)

        self.var_tree.pack(side="left", fill="both", expand=True)
        var_scrollbar.pack(side="right", fill="y")
        
        

        # Tab History
        hist_frame = ttk.Frame(self.bottom_tabs)
        self.bottom_tabs.add(hist_frame, text="History (@conn)")
        self.conn_history_list = tk.Listbox(hist_frame, width=45, font=("Consolas", 9))
        self.conn_history_list.pack(fill="both", expand=True)
        self.conn_history_list.bind("<Double-1>", self._insert_selected_conn_history)

        # Crea un tab iniziale di default
        self.new_tab(title=_tr("default_tab_title", "Senza Nome"), content=_tr("default_script_content", "# Inserisci comandi SCPI qui\n"))

    # ------------------ GESTIONE WORKSPACE E TAB ------------------
    def open_workspace(self):
        folder = filedialog.askdirectory(parent=self, title=_tr("dialog_sel_ws", "Seleziona Cartella Progetto"))
        if not folder:
            return
        self.current_workspace = Path(folder)
        self.title(f"{APP_NAME} - {self.current_workspace.name}")
        self._append_log("INFO", f"{_tr('msg_ws_opened', 'Progetto aperto:')} {self.current_workspace}")
        self.refresh_file_list()

    def refresh_file_list(self):
        self.file_list.delete(0, tk.END)
        if not self.current_workspace:
            return
        for file in sorted(self.current_workspace.glob("*.scpi")):
            self.file_list.insert(tk.END, file.name)

    def _on_file_double_click(self, event):
        selection = self.file_list.curselection()
        if not selection:
            return
        filename = self.file_list.get(selection[0])
        filepath = self.current_workspace / filename
        self.open_file_in_tab(filepath)

    def new_tab(self, title="Senza Nome", content="", filepath: Optional[Path] = None):
        frame = ttk.Frame(self.notebook)
        text_widget = ScrolledText(frame, font=("Consolas", 10), undo=True)
        text_widget.pack(fill="both", expand=True)
        text_widget.insert("1.0", content)
        if attach_autocomplete is not None:
            attach_autocomplete(text_widget)
        self.notebook.add(frame, text=title)
        self.notebook.select(frame)

        tab_id = self.notebook.select()
        self.open_tabs[tab_id] = {
            "path": filepath,
            "text_widget": text_widget
        }

    def open_file_in_tab(self, filepath: Path):
        for tab_id, data in self.open_tabs.items():
            if data["path"] == filepath:
                self.notebook.select(tab_id)
                return
        try:
            content = filepath.read_text(encoding="utf-8")
            self.new_tab(title=filepath.name, content=content, filepath=filepath)
            self._append_log("INFO", f"{_tr('msg_script_loaded', 'Script caricato:')} {filepath.name}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"{_tr('msg_err_open', 'Impossibile aprire il file:\\n')}{exc}")

    def get_current_tab_data(self) -> Optional[dict]:
        tab_id = self.notebook.select()
        if not tab_id: return None
        return self.open_tabs.get(tab_id)

    def close_current_tab(self):
        tab_id = self.notebook.select()
        if not tab_id: return
        self.notebook.forget(tab_id)
        self.open_tabs.pop(tab_id, None)

    def save_current_tab(self):
        tab_data = self.get_current_tab_data()
        if not tab_data: return
        if tab_data["path"] is None:
            self.save_tab_as()
            return
        content = tab_data["text_widget"].get("1.0", "end-1c")
        try:
            tab_data["path"].write_text(content, encoding="utf-8")
            self._append_log("INFO", f"{_tr('msg_saved', 'Salvato:')} {tab_data['path'].name}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"{_tr('msg_err_save', 'Impossibile salvare:\\n')}{exc}")

    def save_tab_as(self):
        tab_data = self.get_current_tab_data()
        if not tab_data: return

        initial_dir = self.current_workspace if self.current_workspace else str(Path.home())
        filepath = filedialog.asksaveasfilename(
            parent=self, 
            title=_tr("dialog_save_as", "Salva Script Come"),
            defaultextension=".scpi",
            filetypes=[(_tr("filter_scpi", "Script SCPI"), "*.scpi"), (_tr("filter_all", "Tutti i file"), "*.*")],
            initialdir=initial_dir
        )
        if not filepath: return

        path = Path(filepath)
        content = tab_data["text_widget"].get("1.0", "end-1c")
        try:
            path.write_text(content, encoding="utf-8")
            tab_data["path"] = path
            tab_id = self.notebook.select()
            self.notebook.tab(tab_id, text=path.name)
            self._append_log("INFO", f"{_tr('msg_saved_as', 'Salvato come:')} {path.name}")
            if self.current_workspace and str(path).startswith(str(self.current_workspace)):
                self.refresh_file_list()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Impossibile salvare:\n{exc}")

    def _load_script_lines_for_engine(self, script_name: str) -> List[str]:
        if not self.current_workspace:
            raise ValueError(_tr("err_no_ws_call", "Nessun progetto aperto. Impossibile caricare '@call {script_name}'").format(script_name=script_name))
        name = script_name if script_name.lower().endswith(".scpi") else f"{script_name}.scpi"
        path = self.current_workspace / name
        if not path.exists():
            raise ValueError(_tr("err_script_not_found", "Script non trovato nel progetto corrente: {path}").format(path=path))
        return path.read_text(encoding="utf-8").splitlines()
    # ------------------ DEBUGGER E AGGIORNAMENTO UI ------------------

    def _handle_state_change(self):
        """Richiamato dall'Engine in background prima di eseguire una riga."""
        if not self.engine.call_stack: return
        
        # Catturiamo lo stato in modo thread-safe
        frame = self.engine.call_stack[-1]
        pc = frame["pc"]
        script_name = frame["name"]
        
        # Copia sicura dei dizionari: prendiamo TUTTE le variabili locali di tutti gli script!
        g_vars = dict(self.engine.global_vars)
        all_l_vars = {s_name: dict(v_dict) for s_name, v_dict in self.engine.local_vars.items()}
        builtins = {}
        for name in sorted(self.engine.BUILTIN_READONLY_NAMES):
            exists, val = self.engine._get_builtin_value(name)
            if exists:
                builtins[name] = val
        # Scheduliamo l'aggiornamento UI sul Main Thread
        self.after(0, lambda: self._update_debug_ui(script_name, pc, builtins, g_vars, all_l_vars))
    def _update_debug_ui(self, script_name: str, pc: int, builtins: dict, g_vars: dict, all_l_vars: dict):
        """Aggiorna tabella variabili e colora di giallo la riga corrente."""
        # 1. Aggiorna Variabili
        for item in self.var_tree.get_children():
            self.var_tree.delete(item)
            
        for k, v in sorted(builtins.items()):
            self.var_tree.insert("", "end", values=(k, str(v), "Built-in"))
        
        # Inserisci le Globali
        for k, v in sorted(g_vars.items()):
            self.var_tree.insert("", "end", values=(k, str(v), "Global"))
            
        # Inserisci TUTTE le Locali Statiche, indicando a quale script appartengono
        for s_name, vars_dict in sorted(all_l_vars.items()):
            for k, v in sorted(vars_dict.items()):
                # Mostra chiaramente in che script ci troviamo
                scope_label = f"Local ({s_name})"
                # Se è dello script corrente, mettiamo un asterisco per farla risaltare
                if s_name == script_name:
                    scope_label = f"➤ {scope_label}"
                    
                self.var_tree.insert("", "end", values=(k, str(v), scope_label))
            
        # 2. Evidenzia la riga corrente nel tab giusto
        target_tab_id = None
        for tab_id, data in self.open_tabs.items():
            name = data["path"].name if data["path"] else _tr("default_tab_title", "Senza Nome")
            if name == script_name:
                target_tab_id = tab_id
                break

        # Se il tab è aperto, rimuovi l'highlight vecchio e metti il nuovo
        if target_tab_id:
            tw = self.open_tabs[target_tab_id]["text_widget"]
            tw.tag_remove("current_line", "1.0", "end")
            line_idx = pc + 1
            tw.tag_add("current_line", f"{line_idx}.0", f"{line_idx}.end")
            tw.tag_config("current_line", background="yellow", foreground="black")
            tw.see(f"{line_idx}.0")
    def toggle_pause(self):
        if self.engine.step_mode:
            # Riprendi (Play)
            self.engine.step_mode = False
            self.engine.step_event.set()
            self.btn_pause.config(text="Pausa")
            self.btn_step.state(["disabled"])
            self._append_log("INFO", _tr("msg_resumed", "Esecuzione ripresa (RUN)..."))
        else:
            # Metti in Pausa
            self.engine.step_mode = True
            self.engine.step_event.clear()
            self.btn_pause.config(text=_tr("btn_resume", "Riprendi"))
            self.btn_step.state(["!disabled"])
            self._append_log("INFO", _tr("msg_paused", "In pausa. Usa Step per avanzare."))

    def step_script(self):
        if self.engine.step_mode:
            # Sblocca l'engine per UN SOLO ciclo
            self.engine.step_event.set()

    # ------------------ INTEGRAZIONE ENGINE E RUN ------------------
    def debug_script(self):
        """Fa partire lo script già in modalità Pausa (Step-by-Step)."""
        if self.running: return
        self.engine.step_mode = True
        self.btn_pause.config(text=_tr("btn_resume", "Riprendi"))
        self.run_script() # Avvia il thread, ma si fermerà alla riga 1!
    def run_script(self):
        if self.running: return
        tab_data = self.get_current_tab_data()
        if not tab_data: return

        raw_script = tab_data["text_widget"].get("1.0", "end")
        lines = raw_script.splitlines()

        self._set_running(True)
        script_name = tab_data["path"].name if tab_data["path"] else _tr("default_tab_title", "Senza Nome")

        def worker():
            try:
                self.engine.run_lines(lines, entry_script_name=script_name)
                self._append_log("INFO", _tr("msg_completed", "Script completato"))
            except Exception as exc:
                self._append_log("ERR", f"{_tr('msg_err_term', 'Script terminato con errore:')} {exc}")
            finally:
                self.after(0, lambda: self._set_running(False))

        self.run_thread = threading.Thread(target=worker, daemon=True)
        self.run_thread.start()

    def request_stop(self):
        self.engine.stop_requested = True
        self.engine.step_event.set()  # Sblocca se era in pausa, sennò non si ferma!
        self._append_log("INFO", _tr("msg_stop_req", "Stop richiesto"))

    def _set_running(self, value: bool):
        self.running = value
        if value:
            self.btn_run.state(["disabled"])
            if hasattr(self, 'btn_debug'): self.btn_debug.state(["disabled"])
            self.btn_stop.state(["!disabled"])
            self.btn_pause.state(["!disabled"])
            
            # Se siamo partiti col tasto Debug, accendi subito lo Step
            if getattr(self.engine, "step_mode", False):
                self.btn_step.state(["!disabled"])
        else:
            self.btn_run.state(["!disabled"])
            if hasattr(self, 'btn_debug'): self.btn_debug.state(["!disabled"])
            self.btn_stop.state(["disabled"])
            self.btn_pause.state(["disabled"])
            self.btn_step.state(["disabled"])
            self.btn_pause.config(text=_tr("btn_pause", "Pausa"))
            
            self.engine.step_mode = False # Resetta la modalità
            
            for data in self.open_tabs.values():
                data["text_widget"].tag_remove("current_line", "1.0", "end")

    # ------------------ LOG E HISTORY ------------------
    def close_connections(self):
        self.engine.close_all()
        self._append_log("INFO", _tr("msg_conn_closed", "Connessioni chiuse"))

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
        selection = self.conn_history_list.curselection()
        if not selection: return
        tab_data = self.get_current_tab_data()
        if tab_data:
            tab_data["text_widget"].insert(tk.INSERT, f"{self.conn_history_list.get(selection[0])}\n")
            tab_data["text_widget"].see(tk.INSERT)

    def destroy(self):
        self.engine.close_all()
        super().destroy()

    def _handle_prompt(self, msg: str):
        event = threading.Event()
        self.after(0, lambda: self._show_prompt_dialog(msg, event))
        event.wait() 

    def _show_prompt_dialog(self, msg: str, event: threading.Event):
        messagebox.showinfo(_tr("dialog_action_req", "Azione Richiesta"), msg, parent=self)
        event.set()

if __name__ == "__main__":
    app = CombinedMonitorApp()
    app.mainloop()     

