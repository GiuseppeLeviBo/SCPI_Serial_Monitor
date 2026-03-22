import time
import datetime
import serial
from pathlib import Path

class SerialTransport:
    def __init__(self, port, baudrate):
        self.ser = serial.Serial(port, baudrate, timeout=1)

    def write(self, cmd):
        self.ser.write((cmd + "\n").encode())

    def query(self, cmd):
        self.write(cmd)
        return self.ser.readline().decode().strip()

    def read_raw(self):
        return self.ser.read_all()

class ExecutionState:
    def __init__(self):
        self.current_target = None
        self.last = None
        self.lastbin = None
        self.lastcommand = None
        self.stack = []
        self.stop_requested = False
        self.readbin_armed = False


class DummyTransport:
    def write(self, cmd):
        print(f"[TX] {cmd}")

    def query(self, cmd):
        print(f"[QUERY] {cmd}")
        return "42"

    def read_raw(self):
        return b"\x00\x01\x02\x03"


class Engine:
    def __init__(self, targets, scripts):
        self.targets = targets
        self.scripts = scripts
        self.state = ExecutionState()

    def log(self, msg):
        print(msg)
    def cmd_conn(self, args):
        name = args[0]
        type_ = args[1]
        device = args[2]
        params = args[3:]

        # --- SERIAL ---
        if type_ == "serial":
            baud = int(params[0]) if params else 9600
            transport = SerialTransport(device, baud)

        # --- VISA ---
        elif type_ == "visa":
            transport = DummyTransport()

        # --- SOCKET ---
        elif type_ == "socket":
            host, port = device.split(":")
            transport = DummyTransport()

        else:
            self.log(f"ERROR: unknown transport {type_}")
            self.state.stop_requested = True
            return

        # registra target
        self.targets[name] = transport

        self.log(f"CONN: {name} -> {type_} {device}")
    def run(self, script_name):
        self.state.stack = [(script_name, 0)]

        while self.state.stack and not self.state.stop_requested:
            script, pc = self.state.stack[-1]
            lines = self.scripts[script]

            if pc >= len(lines):
                self.state.stack.pop()
                continue

            line = lines[pc].strip()
            self.state.stack[-1] = (script, pc + 1)

            if not line or line.startswith("#"):
                continue

            if line.startswith("@"):
                self.handle_meta(line)
            else:
                self.handle_command(line)

    def handle_command(self, line):
        target = self.targets[self.state.current_target]
        self.state.lastcommand = line

        if self.state.readbin_armed:
            target.write(line)
            raw = target.read_raw()
            self.state.lastbin = raw
            self.state.last = None
            self.state.readbin_armed = False
            self.log(f"RXBIN: {len(raw)} bytes")
        else:
            if line.endswith("?"):
                resp = target.query(line)
                self.state.last = resp
                self.state.lastbin = None
                self.log(f"RX: {resp}")
            else:
                target.write(line)
                self.state.last = None
                self.state.lastbin = None

    def handle_meta(self, line):
        tokens = line.split()
        cmd = tokens[0][1:]
        args = tokens[1:]

        if cmd == "target":
            self.state.current_target = args[0]
            self.state.last = None
            self.state.lastbin = None
            self.log(f"TARGET -> {args[0]}")
        elif cmd == "conn":
            self.cmd_conn(args)
        elif cmd == "wait":
            time.sleep(float(args[0]))

        elif cmd == "store":
            name = args[0] if args else ""
            self.store(name)

        elif cmd == "call":
            self.state.stack.append((args[0], 0))

        elif cmd == "rts":
            self.state.stack.pop()

        elif cmd == "halt":
            self.state.stop_requested = True

        elif cmd == "if":
            self.handle_if(args)

        elif cmd == "readbin":
            self.state.readbin_armed = True

        elif cmd == "savebin":
            self.savebin(args[0])

    def handle_if(self, args):
        # formato: operand op value action...
        operand, op, value = args[0], args[1], args[2]
        action = args[3:]

        left = self.state.last
        try:
            left = float(left)
            value = float(value)
        except:
            pass

        cond = False
        if op == "==": cond = left == value
        if op == "!=": cond = left != value
        if op == ">": cond = left > value
        if op == "<": cond = left < value
        if op == ">=": cond = left >= value
        if op == "<=": cond = left <= value

        if cond:
            if action[0] == "@call":
                self.state.stack.append((action[1], 0))
            elif action[0] == "@rts":
                self.state.stack.pop()
            elif action[0] == "@halt":
                self.state.stop_requested = True

    def store(self, name):
        ts = datetime.datetime.now().strftime("%d%m%Y %H:%M")
        target = self.state.current_target
        cmd = self.state.lastcommand or ""

        if self.state.last is None:
            value = "NOVAL"
        else:
            value = self.state.last

        line = f"{ts}; {target}; {cmd}; {name}; {value}\n"

        with open("lastres.csv", "a") as f:
            f.write(line)

        self.log(f"STORE: {line.strip()}")

    def savebin(self, filename):
        if self.state.lastbin is None:
            self.log("ERROR: no binary data")
            return

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self.state.current_target

        p = Path(filename)
        out = f"{p.stem}_{ts}_{target}{p.suffix}"

        with open(out, "wb") as f:
            f.write(self.state.lastbin)

        self.log(f"Saved {out}")


# --- DEMO ---
if __name__ == "__main__":
    targets = {
        "gen": DummyTransport(),
        "scope": DummyTransport()
    }

    scripts = {
        "main": [
            "@conn gen serial COM3 115200",
            "@target gen",
            "@wait 2",
            "*IDN?",
            "SYST:ACK OFF",
            "DIG:OUT D13,1",
            "@wait 1",
            "MEAS:VOLT?",
            "@store volt",
            "DIG:OUT D13,0",
            "@wait 1",
            "MEAS:VOLT?",
            "@store volt",
            "DIG:OUT D13,1",
            "@wait 1",
            "MEAS:VOLT?",
            "@store volt",
            "DIG:OUT D13,0",
            "MEAS:VOLT?",
            "@store freq",
            "@target scope",
            "MEAS:VPP?",
            "@store vpp",
            "@readbin",
            "WAV?",
            "@savebin wave.bin",
            "@call stop_test",
        ],
        "stop_test": [
            "# emergency",
            "@halt"
        ]
    }

    eng = Engine(targets, scripts)
    eng.run("main")
