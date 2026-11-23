# coding=utf-8
"""
plotter_rp2040.py  ―  USB-Serial4a Backend with Hardware-level Easing
"""

from time import sleep
import time
import math
import os
import json


def find_mcu_port():
    # 互換用ダミー（GUI側から呼ばれても問題ないようにしておく）
    return "USB_OTG_RP2040"


class AndroidUSBSerialDriver:
    """
    RP2040-Zero (CircuitPython code.py) と対話するための USB シリアルドライバ。
    プロトコルは以下に対応：
      - PING → PONG / READY
      - QUIET → OK
      - M pw1 pw2
      - S1 pw
      - S2 pw
      - S3 pw
    """

    def __init__(self, vid=None, pid=None):
        self.device = None
        self.serial_port = None
        self.target_vid = vid
        self.target_pid = pid

        try:
            from usbserial4a import serial4a
            from usb4a import usb

            usb_device_list = usb.get_usb_device_list()
            print(f"[USB Debug] Found {len(usb_device_list)} USB devices.")

            if not usb_device_list:
                raise RuntimeError("USBデバイスが検出されません。")

            target_dev = None
            for dev in usb_device_list:
                d_vid = dev.getVendorId()
                d_pid = dev.getProductId()
                print(f"[USB Debug] Device VID=0x{d_vid:04X}, PID=0x{d_pid:04X}")

                if self.target_vid is not None and self.target_pid is not None:
                    if d_vid == self.target_vid and d_pid == self.target_pid:
                        target_dev = dev
                        break
                else:
                    target_dev = dev
                    break

            if target_dev is None:
                raise RuntimeError("対象となるUSBシリアルデバイスが見つかりません。")

            self.device = target_dev
            self.serial_port = serial4a.get_serial_port(
                self.device.getDeviceName(),
                baudrate=115200,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=1,
            )

            if not self.serial_port.is_open:
                self.serial_port.open()

            print("[USB Debug] Serial port successfully opened.")
            self._wait_ready()

        except Exception as e:
            print(f"[USB Error] Connection failed: {e}")
            self.serial_port = None

    def _wait_ready(self):
        if not self.serial_port:
            raise RuntimeError("シリアルポートが初期化されていません。")

        for _ in range(20):
            try:
                self.serial_port.write(b"PING\n")
            except Exception as e:
                print(f"[USB Debug] PING send failed: {e}")
                break

            sleep(0.1)
            try:
                if self.serial_port.in_waiting:
                    msg = self.serial_port.readline().decode(errors="ignore").strip()
                    print(f"[USB Debug] MCU responded: {msg}")
                    if msg in ("PONG", "READY"):
                        print("[USB Debug] MCU handshake OK.")
                        return
            except Exception as e:
                print(f"[USB Debug] read failed: {e}")
                break

        raise RuntimeError("MCU が READY/PONG を返しません。")

    def send_nowait(self, cmd_str: str) -> bool:
        if self.serial_port and self.serial_port.is_open:
            try:
                full_cmd = cmd_str.strip() + "\n"
                self.serial_port.write(full_cmd.encode("utf-8"))
                return True
            except Exception as e:
                print(f"[USB Send Error] {e}")
        return False


class Plotter:
    """
    BrachioGraph 互換の Plotter クラス。
    """

    def __init__(
        self,
        port=None,
        bounds: tuple = [-8, 4, 8, 13],
        inner_arm: float = 8,
        outer_arm: float = 8,
        servo_1_angle_pws: tuple = (),
        servo_2_angle_pws: tuple = (),
        pw_down: int = 1300,
        pw_up: int = 1700,
        vid=None,
        pid=None,
    ):
        self.bounds = bounds
        self.inner_arm = inner_arm
        self.outer_arm = outer_arm

        self.servo_1_angle_pws = servo_1_angle_pws
        self.servo_2_angle_pws = servo_2_angle_pws

        self.pw_down = pw_down
        self.pw_up = pw_up

        # 現在のパルス幅状態（イージングの起点用）[cite: 3]
        self.current_pw_1 = 1690
        self.current_pw_2 = 1490
        self.current_pw_3 = pw_up

        self.servo_1_parked_angle = -90
        self.servo_2_parked_angle = 90

        self.driver = AndroidUSBSerialDriver(vid=vid, pid=pid)

        import numpy

        if self.servo_1_angle_pws:
            srv1_arr = numpy.array(self.servo_1_angle_pws)
            self.angles_to_pw_1 = numpy.poly1d(
                numpy.polyfit(srv1_arr[:, 0], srv1_arr[:, 1], 3)
            )
        else:
            self.angles_to_pw_1 = lambda a: 1500 + (a * -10)

        if self.servo_2_angle_pws:
            srv2_arr = numpy.array(self.servo_2_angle_pws)
            self.angles_to_pw_2 = numpy.poly1d(
                numpy.polyfit(srv2_arr[:, 0], srv2_arr[:, 1], 3)
            )
        else:
            self.angles_to_pw_2 = lambda a: 1500 + (a * 10)

    def _angle_to_pw(self, angle, poly_func):
        pw = poly_func(angle)
        return int(max(500, min(2500, pw)))

    def xy_to_angles(self, x, y):
        hypotenuse = math.sqrt(x**2 + y**2)
        if hypotenuse > (self.inner_arm + self.outer_arm):
            return self.servo_1_parked_angle, self.servo_2_parked_angle

        angle_to_joint = math.atan2(y, x)

        cos_alpha = (
            self.inner_arm**2
            + hypotenuse**2
            - self.outer_arm**2
        ) / (2 * self.inner_arm * hypotenuse)
        alpha = angle_to_joint - math.acos(max(-1.0, min(1.0, cos_alpha)))

        cos_beta = (
            self.inner_arm**2
            + self.outer_arm**2
            - hypotenuse**2
        ) / (2 * self.inner_arm * self.outer_arm)
        beta = math.pi - math.acos(max(-1.0, min(1.0, cos_beta)))

        angle_1 = math.degrees(alpha) - 90
        angle_2 = math.degrees(beta)

        return angle_1, angle_2

    def move_to(self, x, y):
        """
        画像描画用の滑らかな直線分割移動。
        """
        ang1, ang2 = self.xy_to_angles(x, y)
        target_pw1 = self._angle_to_pw(ang1, self.angles_to_pw_1)
        target_pw2 = self._angle_to_pw(ang2, self.angles_to_pw_2)

        diff1 = target_pw1 - self.current_pw_1
        diff2 = target_pw2 - self.current_pw_2
        steps = max(abs(diff1), abs(diff2))

        if self.driver is None:
            return

        if steps > 5:
            step_count = min(50, steps)
            for i in range(1, step_count + 1):
                ratio = i / step_count
                pw1 = int(self.current_pw_1 + diff1 * ratio)
                pw2 = int(self.current_pw_2 + diff2 * ratio)
                self.driver.send_nowait(f"M {pw1} {pw2}")
                sleep(0.007)
        else:
            self.driver.send_nowait(f"M {target_pw1} {target_pw2}")
            sleep(0.01)

        self.current_pw_1 = target_pw1
        self.current_pw_2 = target_pw2

    def move_to_pws(self, target_pw1, target_pw2, max_steps=40, step_delay=0.007):
        """
        パルス幅イージング移動。max_steps と step_delay で速度を調整可能。
        """
        diff1 = target_pw1 - self.current_pw_1
        diff2 = target_pw2 - self.current_pw_2
        steps = max(abs(diff1), abs(diff2))

        if self.driver is None:
            return

        if steps > 5:
            step_count = min(max_steps, steps)
            for i in range(1, step_count + 1):
                ratio = i / step_count
                pw1 = int(self.current_pw_1 + diff1 * ratio)
                pw2 = int(self.current_pw_2 + diff2 * ratio)
                self.driver.send_nowait(f"M {pw1} {pw2}")
                sleep(step_delay)
        else:
            self.driver.send_nowait(f"M {target_pw1} {target_pw2}")
            sleep(step_delay)

        self.current_pw_1 = target_pw1
        self.current_pw_2 = target_pw2

    def pen_down(self):
        if self.driver and self.current_pw_3 != self.pw_down:
            print("[Plotter] Ease Pen Down...")
            start = self.current_pw_3
            end = self.pw_down
            diff = end - start
            steps = abs(diff)

            step_count = min(30, steps)
            for i in range(1, step_count + 1):
                pw3 = int(start + (diff * (i / step_count)))
                self.driver.send_nowait(f"S3 {pw3}")
                sleep(0.005)
            self.current_pw_3 = end

    def pen_up(self):
        if self.driver and self.current_pw_3 != self.pw_up:
            print("[Plotter] Ease Pen Up...")
            start = self.current_pw_3
            end = self.pw_up
            diff = end - start
            steps = abs(diff)

            step_count = min(30, steps)
            for i in range(1, step_count + 1):
                pw3 = int(start + (diff * (i / step_count)))
                self.driver.send_nowait(f"S3 {pw3}")
                sleep(0.005)
            self.current_pw_3 = end

    def park(self, **kwargs):
        """
        ペンを上げてから、初期位置（パーク位置）へ画像描画時と同等?ややゆっくりした速度で戻る。
        """
        print("[Plotter] Parking...")
        self.pen_up()
        sleep(0.3)

        target_pw1 = self._angle_to_pw(
            self.servo_1_parked_angle, self.angles_to_pw_1
        )
        target_pw2 = self._angle_to_pw(
            self.servo_2_parked_angle, self.angles_to_pw_2
        )

        self.move_to_pws(target_pw1, target_pw2, max_steps=120, step_delay=0.012)
        sleep(0.2)

        if self.driver:
            self.driver.send_nowait("QUIET")
        print("[Plotter] Parked successfully.")

