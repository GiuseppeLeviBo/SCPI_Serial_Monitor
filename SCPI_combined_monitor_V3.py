import importlib.util
from pathlib import Path
from typing import Callable, Dict

import SCPI_combined_monitor_V2 as v2


class CombinedScriptEngine(v2.CombinedScriptEngine):
    """Estensione del motore V2 con supporto plugin DSL caricati a runtime."""

    BUILTIN_META_COMMANDS = {
        "conn", "target", "wait", "halt", "var", "gvar", "inc", "eval",
        "ifdef", "ifndef", "if", "loop", "while", "endloop", "endwhile",
        "break", "print", "csvname", "binname", "prompt", "store",
        "startstore", "stopstore", "comment", "call", "script", "rts",
        "readbin", "savebin",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.plugin_commands: Dict[str, Callable] = {}

    def register_plugin_command(self, cmd_name: str, callback: Callable):
        cmd = str(cmd_name).strip().lower().lstrip("@")
        if not cmd:
            raise ValueError("Nome comando plugin non valido")
        if cmd in self.BUILTIN_META_COMMANDS:
            raise ValueError(f"Il comando '@{cmd}' è già built-in e non può essere sovrascritto")
        self.plugin_commands[cmd] = callback

    def register_dsl_spec(self, cmd_with_at: str, spec_dict: dict):
        from dsl_commands import DSL_COMMAND_SPECS

        cmd = str(cmd_with_at).strip().lower()
        if not cmd.startswith("@"):
            cmd = f"@{cmd}"

        if cmd in DSL_COMMAND_SPECS and cmd[1:] in self.BUILTIN_META_COMMANDS:
            raise ValueError(f"La spec '{cmd}' è built-in e non può essere sovrascritta")

        payload = dict(spec_dict)
        payload.setdefault("command", cmd)
        payload.setdefault("insert", cmd)
        payload.setdefault("signature", cmd)
        payload.setdefault("help", f"Plugin command {cmd}")
        payload.setdefault("category", "plugin")
        DSL_COMMAND_SPECS[cmd] = payload

    def _run_meta(self, line: str):
        import shlex

        tokens = shlex.split(line)
        if not tokens:
            return

        cmd = tokens[0][1:].lower()
        if cmd in self.BUILTIN_META_COMMANDS:
            return super()._run_meta(line)

        if cmd in self.plugin_commands:
            try:
                self.plugin_commands[cmd](self, tokens[1:])
            except Exception as e:
                raise ValueError(f"Errore nel plugin '@{cmd}': {e}") from e
            return

        return super()._run_meta(line)


# Patch: la GUI V2 istanzierà automaticamente il motore esteso.
v2.CombinedScriptEngine = CombinedScriptEngine


class CombinedMonitorApp(v2.CombinedMonitorApp):
    def __init__(self):
        super().__init__()
        self._load_plugins()

    def _load_plugins(self):
        plugins_dir = Path(__file__).resolve().parent / "plugins"
        if not plugins_dir.exists():
            plugins_dir.mkdir(parents=True, exist_ok=True)
            self._append_log("INFO", f"Cartella plugin creata: {plugins_dir}")
            return

        for file_path in sorted(plugins_dir.glob("*.py")):
            if file_path.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(file_path.stem, file_path)
                if spec is None or spec.loader is None:
                    raise RuntimeError("spec/loader non disponibile")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                if hasattr(module, "register"):
                    module.register(self.engine)
                    self._append_log("INFO", f"Plugin caricato: {file_path.name}")
                else:
                    self._append_log("WARN", f"Plugin ignorato (manca register): {file_path.name}")
            except Exception as e:
                self._append_log("ERR", f"Impossibile caricare il plugin {file_path.name}: {e}")


def main():
    app = CombinedMonitorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
