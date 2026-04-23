# Plugin DSL (V3)

- Ogni plugin è un file `.py` dentro `plugins/`.
- Il modulo deve esporre `register(engine)`.
- Dentro `register` puoi chiamare:
  - `engine.register_plugin_command("nome", callback)`
  - `engine.register_dsl_spec("@nome", {...})` per autocomplete/help.

## Callback
La callback riceve `(engine, args)`:

```python
def my_cmd(engine, args):
    ...
```

## Sicurezza nomi
I comandi built-in (`@conn`, `@if`, `@store`, ecc.) non possono essere sovrascritti.

## Template
Usa `_example_plugin.py` come base e rinominalo (es. `my_plugin.py`) per attivarlo.
