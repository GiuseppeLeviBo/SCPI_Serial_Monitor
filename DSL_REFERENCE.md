# SCPI Combined Monitor V2 - DSL Reference

This document contains the full DSL language reference for `SCPI_combined_monitor_V2.py`.

---

## Core model

Each non-empty line is one of:

- a DSL meta-command (`@...`), or
- a SCPI command (`*IDN?`, `MEAS:VOLT?`, ...).

Comments are supported with `#`:

```text
# this line is ignored
```

---

## Value resolution rules (`_resolve_value`)

When a command argument must be resolved, the engine uses this order:

1. Quoted string (`"abc"`, `'abc'`) -> literal string
2. Built-in read-only name
3. Variable lookup (current-script local first, then global)
4. Numeric literal (`10`, `3.14`)
5. Otherwise -> explicit error

If you need plain non-numeric text, quote it.

---

## Built-in read-only names

Available built-ins include:

- `last`
- `last_command`
- `last_line`
- `last_bin`
- `target`
- `script`
- `time`
- `date`
- `datetime`
- `csvname`
- `binname`

Assigning these names via `@var`, `@gvar`, `@inc`, or `@eval` raises an explicit error.

---

## Variables and math

### `@var` (local static)

```text
@var name value
```

Creates/updates a variable local to the current script identity. Local variables are static per script file.

### `@gvar` (global)

```text
@gvar name value
```

Creates/updates a global variable shared across all scripts in the run.

### Lookup precedence

1. Current-script local
2. Global

### `@inc`

```text
@inc name [step]
```

Increments an existing numeric variable. `step` defaults to `1`.

### `@eval`

```text
@eval dest = expression
```

Evaluates math expressions (safe environment, `math` functions enabled, Python builtins disabled).

- `^` is accepted as power alias for `**`.
- Variable names are case-insensitive.
- If `dest` already exists globally and is not shadowed by current local, global is updated.
- Otherwise `dest` is created/updated as current-script local.

Example:

```text
@gvar gain 2
@var x 3
@eval y = sin(x) * gain + 10
```

---

## Logging and diagnostics

### `@print`

```text
@print <arg1> [arg2 ...]
```

Writes a formatted diagnostic line to the runtime log (`PRINT: ...`). Arguments are resolved when possible; unresolved tokens are kept as raw text.

Example:

```text
@print "Starting test step"
@print channel 1 ready
```

---

## Flow control

### `@if`

```text
@if <left> <op> <right> <action>
```

Supported operators: `==`, `!=`, `>`, `<`, `>=`, `<=`.

`<action>` can be another DSL command or a SCPI command.

### `@ifdef` / `@ifndef`

```text
@ifdef name <action>
@ifndef name <action>
```

Conditionally executes action based on variable existence.

### `@loop`

```text
@loop N
  ...
@endloop
```

Counted loop with nesting support.

### `@while`

```text
@while <left> <op> <right>
  ...
@endwhile
```

Condition is checked at block entry.

### `@break`

```text
@break
```

Exits the current loop frame.

### `@halt`

```text
@halt
```

Stops script execution immediately.

---

## Connections and target selection

### `@conn`

```text
@conn <name> serial <port> [baud=9600] [timeout_s=2.0] [terminator=\n]
@conn <name> visa <resource> [timeout_s=2.0] [backend=auto] [terminator=\n]
@conn <name> socket <host:port | host> [port=5025] [timeout_s=2.0] [terminator=\n]
```

Defines a connection target.

### `@target`

```text
@target <name>
```

Selects the active target for following SCPI commands.

### ASCII query behavior

- SCPI command ending with `?` is treated as query.
- Query response is stored in `last`.
- Serial transport adds extra robustness (flush/retry/reassembly behavior).

---

## Binary data flow

### `@readbin` + query + `@savebin`

```text
@readbin
WAV:DATA?
@savebin wave.bin
```

- `@readbin` arms binary read for the next SCPI line.
- Received bytes are stored in `last_bin`.
- `@savebin` writes bytes to disk.

### `@binname`

```text
@binname <name>
```

Sets default binary output base name.

If `binname` is set, `@savebin` uses `<binname><suffix>` (default `.bin` if missing).
Otherwise fallback naming uses timestamp + target.

---

## CSV logging helpers

Default output file is `lastres.csv`.

Header format:

```text
timestamp;target;command;name;value
```

### `@store`

```text
@store <label>
@store <label> <explicit_value>
```

Stores `last` (or explicit value) into CSV.

### `@startstore` / `@stopstore`

```text
@startstore [label]
@stopstore
```

Enables/disables automatic CSV save for each ASCII query response.

### `@comment`

```text
@comment free text
```

Appends annotation row (`command=@comment`, `name=COMMENT`).

### `@csvname`

```text
@csvname <name>
```

Changes active CSV output file immediately (adds `.csv` extension if missing).

---

## Modular scripts

### `@call` / `@script`

```text
@call script_name
@script script_name
```

Equivalent aliases. Scripts are loaded from current workspace; `.scpi` is auto-appended when omitted.

### `@rts`

```text
@rts
```

Returns immediately from current script frame.

---

## Debugger behavior

Modes:

- **Run**: continuous execution
- **Debug**: starts paused (step mode)

Controls:

- Pause/Resume
- Step
- Stop

UI shows built-ins, globals, local statics, and current executing line highlight.

---

## Minimal complete example

```text
@conn dmm serial COM3 115200
@target dmm

@gvar limit 5
@var i 0

@loop 10
MEAS:VOLT?
@if last > limit @comment "threshold exceeded"
@inc i
@if i >= 10 @break
@endloop

@eval avg = (limit + i) / 2
@store average avg
```

---

## Validation checklist (regression-oriented)

Suggested areas to test after DSL changes:

1. Parser/tokenization and quoted arguments
2. Variable scope (`@var`, `@gvar`, `@eval`, `@inc`)
3. Flow control (`@if`, `@loop`, `@while`, `@break`, `@halt`)
4. Script stack (`@call`, `@script`, `@rts`)
5. Query/write behavior and `last`
6. Binary flow (`@readbin`, `@savebin`, `@binname`)
7. CSV flow (`@store`, `@startstore`, `@stopstore`, `@comment`, `@csvname`)
8. Debugger stepping and pause/resume
