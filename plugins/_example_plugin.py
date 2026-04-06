"""Template plugin per SCPI_combined_monitor_V3.
Rinomina il file rimuovendo l'underscore iniziale per abilitarlo.
"""

def register(engine):
    engine.register_plugin_command("plot", do_plot)
    engine.register_dsl_spec("@plot", {
        "command": "@plot",
        "insert": "@plot var_name",
        "signature": "@plot var_name",
        "help": "Esempio plugin: stampa il valore della variabile nel log.",
        "category": "plugin",
    })


def do_plot(engine, args):
    if not args:
        raise ValueError("@plot richiede almeno un argomento")
    var_name = args[0].lower()
    exists, value, _ = engine._get_var(var_name)
    if not exists:
        raise ValueError(f"Variabile '{var_name}' non trovata")
    engine.logger("INFO", f"PLOT[{var_name}] -> {value}")
