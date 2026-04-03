from __future__ import annotations

from typing import Dict, List, TypedDict


class CommandSpec(TypedDict, total=False):
    command: str
    insert: str
    signature: str
    help: str
    category: str
    examples: List[str]
    readonly: bool


DSL_COMMAND_SPECS: Dict[str, CommandSpec] = {
    "@conn": {
        "command": "@conn",
        "insert": "@conn name serial COM4 115200",
        "signature": "@conn name type endpoint [params]",
        "help": "Crea una connessione verso un target. type: serial, visa, socket.",
        "category": "connection",
        "examples": [
            "@conn Arduino serial COM4 115200",
            '@conn PSU visa "USB0::0x1234::0x5678::INSTR" 2.0 auto',
            "@conn Scope socket 192.168.0.10:5025 2.0",
        ],
    },
    "@target": {
        "command": "@target",
        "insert": "@target name",
        "signature": "@target name",
        "help": "Seleziona il target corrente.",
        "category": "connection",
        "examples": [
            "@target Arduino",
        ],
    },
    "@wait": {
        "command": "@wait",
        "insert": "@wait 1",
        "signature": "@wait seconds",
        "help": "Attende il numero di secondi specificato.",
        "category": "flow",
        "examples": [
            "@wait 0.5",
        ],
    },
    "@halt": {
        "command": "@halt",
        "insert": "@halt",
        "signature": "@halt",
        "help": "Interrompe l'esecuzione dello script.",
        "category": "flow",
    },
    "@var": {
        "command": "@var",
        "insert": "@var name 0",
        "signature": "@var name value",
        "help": "Definisce o aggiorna una variabile locale statica dello script corrente.",
        "category": "variables",
        "examples": [
            "@var count 0",
            '@var stato "READY"',
        ],
    },
    "@gvar": {
        "command": "@gvar",
        "insert": "@gvar name 0",
        "signature": "@gvar name value",
        "help": "Definisce o aggiorna una variabile globale condivisa tra gli script.",
        "category": "variables",
        "examples": [
            "@gvar total 0",
            "@gvar threshold 2.5",
        ],
    },
    "@inc": {
        "command": "@inc",
        "insert": "@inc name 1",
        "signature": "@inc name [step]",
        "help": "Incrementa una variabile numerica locale o globale.",
        "category": "variables",
        "examples": [
            "@inc count",
            "@inc total 0.5",
        ],
    },
    "@eval": {
        "command": "@eval",
        "insert": "@eval dest = sin(x)",
        "signature": "@eval dest = expression",
        "help": "Valuta un'espressione matematica e assegna il risultato.",
        "category": "variables",
        "examples": [
            "@eval y = sin(x)",
            "@eval total = total + 1",
        ],
    },
    "@ifdef": {
        "command": "@ifdef",
        "insert": "@ifdef name @print name",
        "signature": "@ifdef name action",
        "help": "Esegue action solo se la variabile esiste.",
        "category": "flow",
        "examples": [
            "@ifdef total @print total",
        ],
    },
    "@ifndef": {
        "command": "@ifndef",
        "insert": "@ifndef name @var name 0",
        "signature": "@ifndef name action",
        "help": "Esegue action solo se la variabile non esiste.",
        "category": "flow",
        "examples": [
            "@ifndef count @var count 0",
        ],
    },
    "@if": {
        "command": "@if",
        "insert": "@if left == right @print left",
        "signature": "@if left op right action",
        "help": "Valuta una condizione ed esegue action se vera.",
        "category": "flow",
        "examples": [
            "@if x > 10 @halt",
            '@if target == "Arduino" @print target',
        ],
    },
    "@loop": {
        "command": "@loop",
        "insert": "@loop 10",
        "signature": "@loop count",
        "help": "Inizia un loop a conteggio fisso.",
        "category": "flow",
        "examples": [
            "@loop 5",
        ],
    },
    "@endloop": {
        "command": "@endloop",
        "insert": "@endloop",
        "signature": "@endloop",
        "help": "Chiude un blocco @loop.",
        "category": "flow",
    },
    "@while": {
        "command": "@while",
        "insert": "@while x < 10",
        "signature": "@while left op right",
        "help": "Inizia un loop condizionale.",
        "category": "flow",
        "examples": [
            "@while x < 10",
        ],
    },
    "@endwhile": {
        "command": "@endwhile",
        "insert": "@endwhile",
        "signature": "@endwhile",
        "help": "Chiude un blocco @while.",
        "category": "flow",
    },
    "@break": {
        "command": "@break",
        "insert": "@break",
        "signature": "@break",
        "help": "Esce dal loop corrente.",
        "category": "flow",
    },
    "@print": {
        "command": "@print",
        "insert": '@print "x=" x',
        "signature": "@print arg1 [arg2 ...]",
        "help": "Stampa valori o testo nel log.",
        "category": "debug",
        "examples": [
            "@print x",
            '@print "target=" target "time=" time',
        ],
    },
    "@prompt": {
        "command": "@prompt",
        "insert": '@prompt "Premi OK per continuare"',
        "signature": "@prompt text",
        "help": "Mostra un popup bloccante all'utente.",
        "category": "ui",
    },
    "@store": {
        "command": "@store",
        "insert": "@store LABEL value",
        "signature": "@store label [value]",
        "help": "Salva su CSV. Se value manca, salva last.",
        "category": "logging",
        "examples": [
            "@store START",
            "@store TOTAL total",
            '@store "Misura ON" last',
        ],
    },
    "@comment": {
        "command": "@comment",
        "insert": '@comment "nota di test"',
        "signature": "@comment text",
        "help": "Salva un commento testuale nel CSV.",
        "category": "logging",
    },
    "@startstore": {
        "command": "@startstore",
        "insert": "@startstore AUTO",
        "signature": "@startstore [label]",
        "help": "Abilita autostore delle query.",
        "category": "logging",
    },
    "@stopstore": {
        "command": "@stopstore",
        "insert": "@stopstore",
        "signature": "@stopstore",
        "help": "Disabilita autostore.",
        "category": "logging",
    },
    "@csvname": {
        "command": "@csvname",
        "insert": '@csvname date time "testA"',
        "signature": "@csvname part1 [part2 ...]",
        "help": "Imposta subito il nome del file CSV corrente.",
        "category": "logging",
        "examples": [
            "@csvname date time",
            '@csvname "mionome" date time',
        ],
    },
    "@binname": {
        "command": "@binname",
        "insert": '@binname datetime target "raw"',
        "signature": "@binname part1 [part2 ...]",
        "help": "Imposta subito il nome base per il prossimo file binario.",
        "category": "logging",
        "examples": [
            "@binname datetime target",
        ],
    },
    "@call": {
        "command": "@call",
        "insert": "@call SUB",
        "signature": "@call script_name",
        "help": "Chiama un altro script del workspace.",
        "category": "scripts",
        "examples": [
            "@call LOOP",
            "@call init.scpi",
        ],
    },
    "@script": {
        "command": "@script",
        "insert": "@script SUB",
        "signature": "@script script_name",
        "help": "Alias di @call.",
        "category": "scripts",
    },
    "@rts": {
        "command": "@rts",
        "insert": "@rts",
        "signature": "@rts",
        "help": "Ritorna dallo script corrente.",
        "category": "scripts",
    },
    "@readbin": {
        "command": "@readbin",
        "insert": "@readbin",
        "signature": "@readbin",
        "help": "Arma la lettura binaria per il prossimo comando.",
        "category": "binary",
    },
    "@savebin": {
        "command": "@savebin",
        "insert": "@savebin dump.bin",
        "signature": "@savebin filename",
        "help": "Salva il buffer binario letto in precedenza.",
        "category": "binary",
        "examples": [
            "@savebin trace.bin",
        ],
    },
}


BUILTIN_SYMBOL_SPECS: Dict[str, CommandSpec] = {
    "last": {
        "command": "last",
        "insert": "last",
        "signature": "last",
        "help": "Ultima risposta ricevuta dal device o ultimo valore built-in.",
        "category": "builtin",
        "readonly": True,
    },
    "target": {
        "command": "target",
        "insert": "target",
        "signature": "target",
        "help": "Nome del target corrente.",
        "category": "builtin",
        "readonly": True,
    },
    "script": {
        "command": "script",
        "insert": "script",
        "signature": "script",
        "help": "Nome dello script corrente.",
        "category": "builtin",
        "readonly": True,
    },
    "date": {
        "command": "date",
        "insert": "date",
        "signature": "date",
        "help": "Data corrente formattata per uso generale/filename.",
        "category": "builtin",
        "readonly": True,
    },
    "time": {
        "command": "time",
        "insert": "time",
        "signature": "time",
        "help": "Ora corrente formattata per uso generale/filename.",
        "category": "builtin",
        "readonly": True,
    },
    "datetime": {
        "command": "datetime",
        "insert": "datetime",
        "signature": "datetime",
        "help": "Data e ora correnti formattate per uso generale/filename.",
        "category": "builtin",
        "readonly": True,
    },
    "csvname": {
        "command": "csvname",
        "insert": "csvname",
        "signature": "csvname",
        "help": "Nome CSV corrente.",
        "category": "builtin",
        "readonly": True,
    },
    "binname": {
        "command": "binname",
        "insert": "binname",
        "signature": "binname",
        "help": "Nome BIN corrente.",
        "category": "builtin",
        "readonly": True,
    },
}

def get_command_matches(prefix: str) -> list[dict]:
    prefix = prefix.lower()
    matches = []
    for key, spec in DSL_COMMAND_SPECS.items():
        if key.lower().startswith(prefix):
            matches.append(spec)
    return matches


def get_builtin_matches(prefix: str) -> list[dict]:
    prefix = prefix.lower()
    matches = []
    for key, spec in BUILTIN_SYMBOL_SPECS.items():
        if key.lower().startswith(prefix):
            matches.append(spec)
    return matches