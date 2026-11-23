# CIRCUITPY/code.py
import sys
import supervisor
import pwmio
import board

SERVO_PINS = {
    1: board.GP10,
    2: board.GP9,
    3: board.GP8,
}

PW_MIN = 500
PW_MAX = 2500
PERIOD_US = 20000

def pw_to_duty(pw_us):
    return int(pw_us * 65535 / PERIOD_US)

class ServoController:
    def __init__(self):
        self.pwms = {}
        for sid, pin in SERVO_PINS.items():
            pwm = pwmio.PWMOut(pin, frequency=50, duty_cycle=0)
            self.pwms[sid] = pwm

    def set_pw(self, sid, pw):
        if sid not in self.pwms: return
        if not (PW_MIN <= pw <= PW_MAX): return
        self.pwms[sid].duty_cycle = pw_to_duty(pw)

    def quiet(self):
        for pwm in self.pwms.values():
            pwm.duty_cycle = 0

def reply(msg):
    print(msg)

def main():
    reply("READY")
    ctrl = ServoController()
    buf = ""

    while True:
        n = supervisor.runtime.serial_bytes_available
        if n > 0:
            buf += sys.stdin.read(n)

        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            line = line.strip()
            if not line: continue

            parts = line.split()
            cmd = parts[0].upper()

            try:
                if cmd == "PING":
                    reply("PONG")
                elif cmd == "QUIET":
                    ctrl.quiet()
                    reply("OK")
                elif cmd in ("S1", "S2", "S3"):
                    sid = int(cmd[1])
                    pw = int(parts[1])
                    ctrl.set_pw(sid, pw)
                elif cmd == "M":
                    pw1 = int(parts[1])
                    pw2 = int(parts[2])
                    ctrl.set_pw(1, pw1)
                    ctrl.set_pw(2, pw2)
                else:
                    reply("ERR unknown {}".format(cmd))
            except Exception as e:
                reply("ERR {}".format(e))

if __name__ == "__main__":
    main()

