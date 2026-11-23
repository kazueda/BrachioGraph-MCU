# coding=utf-8
"""
bg_rp2040_gui.py  ―  Android Tkinter GUI with Auto-Scaling Parser
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import json
import os
import time
import math

# バックエンドのインポート
try:
    import plotter_rp2040
except ImportError:
    plotter_rp2040 = None


class PlotterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("BrachioGraph Pico 制御・モニター")
        self.root.geometry("450x760")

        self.plotter = None
        self.json_data = None
        self.is_plotting = False
        self.is_moving = False

        # キャンバスのサイズとマージン
        self.canvas_w = 400
        self.canvas_h = 300
        self.pad = 20

        # --- 送信速度と描画密度（ウェイト）の調整パラメータ ---
        self.MOVE_DELAY = 0.015   # 分割された1ステップごとの待機時間 (秒)[cite: 1]
        self.PEN_DELAY = 0.4      # ペン上下後の安定待ち時間 (秒)[cite: 1]
        self.STEP_LENGTH = 0.1    # 直線を何刻みで細かく分割するか[cite: 1]

        # 初期位置（Park）の座標 [X, Y][cite: 1]
        self.PARK_POS = [0.0, 4.0]
        self.current_pos = list(self.PARK_POS)

        # ターゲットとなる物理描画範囲 (BrachioGraphの限界値)[cite: 1]
        self.TARGET_BOUNDS = [-6.5, 4.5, 6.5, 12.5]

        self._create_widgets()

    def _create_widgets(self):
        # 1. MCU接続設定セクション
        frame_conn = ttk.LabelFrame(self.root, text="1. MCU接続設定")
        frame_conn.pack(fill="x", padx=10, pady=5)

        self.lbl_status = ttk.Label(frame_conn, text="ステータス: 未接続", foreground="red")
        self.lbl_status.pack(side="left", padx=10, pady=5)

        self.btn_connect = ttk.Button(frame_conn, text="Picoに接続", command=self.connect_mcu)
        self.btn_connect.pack(side="right", padx=10, pady=5)

        # 2. ファイル選択・プレビューセクション
        frame_file = ttk.LabelFrame(self.root, text="2. プロットデータ選択 (.json)")
        frame_file.pack(fill="x", padx=10, pady=5)

        self.lbl_filename = ttk.Label(frame_file, text="ファイルが選択されていません", wraplength=300)
        self.lbl_filename.pack(side="left", padx=10, pady=5)

        self.btn_select_file = ttk.Button(frame_file, text="ファイルを開く", command=self.select_file)
        self.btn_select_file.pack(side="right", padx=10, pady=5)

        # 3. モニターキャンバス
        frame_cam = ttk.LabelFrame(self.root, text="描画エリア プレビュー (X: -8~8, Y: 4~13)")
        frame_cam.pack(fill="both", expand=True, padx=10, pady=5)

        self.canvas = tk.Canvas(frame_cam, width=self.canvas_w, height=self.canvas_h, bg="white", relief="sunken", bd=2)
        self.canvas.pack(padx=5, pady=5, fill="both", expand=True)

        # 4. コントロールセクション
        frame_ctrl = ttk.LabelFrame(self.root, text="3. 操作パネル")
        frame_ctrl.pack(fill="x", padx=10, pady=5)

        self.btn_start = ttk.Button(frame_ctrl, text="プロット開始", state="disabled", command=self.start_plotting_thread)
        self.btn_start.pack(fill="x", padx=10, pady=5)

        # 個別テスト用ボタン群
        frame_test = ttk.Frame(frame_ctrl)
        frame_test.pack(fill="x", padx=5, pady=2)

        self.btn_home = ttk.Button(frame_test, text="初期位置へ移動(Park)", state="disabled", command=self.go_home)
        self.btn_home.pack(side="left", fill="x", expand=True, padx=2)

        self.btn_p_down = ttk.Button(frame_test, text="ペン下げる", state="disabled", command=self.pen_down_test)
        self.btn_p_down.pack(side="left", fill="x", expand=True, padx=2)

        self.btn_p_up = ttk.Button(frame_test, text="ペン上げる", state="disabled", command=self.pen_up_test)
        self.btn_p_up.pack(side="left", fill="x", expand=True, padx=2)

    def _set_controls_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        self.btn_select_file.config(state=state)
        self.btn_home.config(state=state)
        self.btn_p_down.config(state=state)
        self.btn_p_up.config(state=state)
        if busy or (self.json_data and self.plotter):
            self.btn_start.config(state=state)

    def _move_to_tracked(self, x, y):
        """現在位置を記録しながら plotter.move_to を呼ぶ"""
        self.plotter.move_to(x, y)
        self.current_pos = [float(x), float(y)]

    def _move_smoothly(self, target_x, target_y, step_length=None, move_delay=None):
        """現在位置から目標座標まで、画像描画時と同じ刻み幅とウェイトでゆっくり移動する"""
        if not self.plotter:
            return

        step_len = self.STEP_LENGTH if step_length is None else step_length
        delay = self.MOVE_DELAY if move_delay is None else move_delay

        start_x, start_y = self.current_pos[0], self.current_pos[1]
        dx = target_x - start_x
        dy = target_y - start_y
        distance = math.hypot(dx, dy)

        if distance <= 1e-9:
            self._move_to_tracked(target_x, target_y)
            return

        if distance > step_len:
            steps = int(math.ceil(distance / step_len))
            for s in range(1, steps + 1):
                ratio = s / steps
                curr_x = start_x + dx * ratio
                curr_y = start_y + dy * ratio
                self._move_to_tracked(curr_x, curr_y)
                time.sleep(delay)
        else:
            self._move_to_tracked(target_x, target_y)
            time.sleep(delay)

    def connect_mcu(self):
        if plotter_rp2040 is None:
            messagebox.showerror("エラー", "plotter_rp2040.py が見つかりません。")
            return

        try:
            servo_1_pws = [
                [-162, 2470], [-144, 2250], [-126, 2050], [-108, 1860],
                [-90, 1690], [-72, 1530], [-54, 1350], [-36, 1190],
                [-18, 1010], [0, 840], [18, 640]
            ]
            servo_2_pws = [
                [0, 660], [18, 840], [36, 1030], [54, 1180],
                [72, 1340], [90, 1490], [108, 1640], [126, 1830],
                [144, 2000], [162, 2200], [180, 2410]
            ]

            self.plotter = plotter_rp2040.Plotter(
                inner_arm=8,
                outer_arm=8,
                bounds=[-8, 4, 8, 13],
                servo_1_angle_pws=servo_1_pws,
                servo_2_angle_pws=servo_2_pws,
                pw_down=1300,
                pw_up=1700
            )

            if self.plotter.driver and self.plotter.driver.serial_port:
                self.lbl_status.config(text="ステータス: Pico接続完了", foreground="green")
                self.btn_home.config(state="normal")
                self.btn_p_down.config(state="normal")
                self.btn_p_up.config(state="normal")
                if self.json_data:
                    self.btn_start.config(state="normal")
            else:
                raise RuntimeError("シリアルポートのオープンに失敗しました。")
        except Exception as e:
            messagebox.showerror("接続エラー", f"Picoへの接続に失敗しました:\n{e}")

    def select_file(self):
        ftypes = [('JSON files', '*.json'), ('All files', '*')]
        filename = filedialog.askopenfilename(filetypes=ftypes)
        if not filename:
            return

        try:
            with open(filename, 'r') as f:
                raw_data = json.load(f)

            normalized = self._parse_json_structure(raw_data)
            if not normalized or not normalized[0]:
                raise ValueError("有効な座標データ [X, Y] が見つかりませんでした。")

            scaled_data = self._auto_scale_to_bounds(normalized)

            self.json_data = scaled_data
            self.lbl_filename.config(text=os.path.basename(filename))
            self._draw_preview()

            if self.plotter:
                self.btn_start.config(state="normal")
        except Exception as e:
            messagebox.showerror("ファイルエラー", f"ファイルの読み込み、または解析に失敗しました:\n{e}")

    def _parse_json_structure(self, data):
        if isinstance(data, dict):
            for key in ["lines", "strokes", "data", "drawing", "layers"]:
                if key in data and isinstance(data[key], list):
                    return self._parse_json_structure(data[key])
            for val in data.values():
                if isinstance(val, list):
                    res = self._parse_json_structure(val)
                    if res and res[0]:
                        return res
            return None

        if isinstance(data, list):
            if not data:
                return None

            if isinstance(data[0], list) and len(data[0]) > 0 and isinstance(data[0][0], list) and len(data[0][0]) == 2:
                if isinstance(data[0][0][0], (int, float)):
                    return data

            if isinstance(data[0], list) and len(data[0]) == 2 and isinstance(data[0][0], (int, float)):
                return [data]

            if isinstance(data[0], dict):
                stroke = []
                for pt in data:
                    x = pt.get('x', pt.get('X', None))
                    y = pt.get('y', pt.get('Y', None))
                    if x is not None and y is not None:
                        stroke.append([float(x), float(y)])
                if stroke:
                    return [stroke]

            sub_elements = []
            for item in data:
                res = self._parse_json_structure(item)
                if res:
                    for s in res:
                        sub_elements.append(s)
            if sub_elements:
                return sub_elements

        return None

    def _auto_scale_to_bounds(self, strokes):
        all_pts = [pt for stroke in strokes for pt in stroke]
        if not all_pts:
            return strokes

        xs = [pt[0] for pt in all_pts]
        ys = [pt[1] for pt in all_pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        data_w = max_x - min_x
        data_h = max_y - min_y

        if data_w == 0:
            data_w = 1.0
        if data_h == 0:
            data_h = 1.0

        tgt_min_x, tgt_min_y, tgt_max_x, tgt_max_y = self.TARGET_BOUNDS
        tgt_w = tgt_max_x - tgt_min_x
        tgt_h = tgt_max_y - tgt_min_y

        scale = min(tgt_w / data_w, tgt_h / data_h)

        center_x = tgt_min_x + (tgt_w - data_w * scale) / 2
        center_y = tgt_min_y + (tgt_h - data_h * scale) / 2

        scaled_strokes = []
        for stroke in strokes:
            scaled_stroke = []
            for pt in stroke:
                nx = center_x + (pt[0] - min_x) * scale
                ny = center_y + (pt[1] - min_y) * scale
                scaled_stroke.append([nx, ny])
            scaled_strokes.append(scaled_stroke)

        return scaled_strokes

    def _mm_to_canvas(self, x, y):
        min_x, max_x = -8.0, 8.0
        min_y, max_y = 4.0, 13.0

        scale_x = (self.canvas_w - 2 * self.pad) / (max_x - min_x)
        scale_y = (self.canvas_h - 2 * self.pad) / (max_y - min_y)

        cx = self.pad + (x - min_x) * scale_x
        cy = self.canvas_h - (self.pad + (y - min_y) * scale_y)
        return cx, cy

    def _draw_preview(self):
        self.canvas.delete("all")
        if not self.json_data:
            return

        for stroke_idx, line in enumerate(self.json_data):
            if len(line) < 2:
                continue
            for i in range(len(line) - 1):
                pt1 = line[i]
                pt2 = line[i + 1]
                cx1, cy1 = self._mm_to_canvas(pt1[0], pt1[1])
                cx2, cy2 = self._mm_to_canvas(pt2[0], pt2[1])

                tag_name = f"seg_{stroke_idx}_{i}"
                self.canvas.create_line(cx1, cy1, cx2, cy2, fill="gray", width=1, tags=tag_name)

    def start_plotting_thread(self):
        if self.is_plotting or self.is_moving or not self.json_data or not self.plotter:
            return
        self.is_plotting = True
        self.root.after(0, lambda: self._set_controls_busy(True))

        t = threading.Thread(target=self._plot_loop, daemon=True)
        t.start()

    def _plot_loop(self):
        try:
            self.plotter.pen_up()
            time.sleep(self.PEN_DELAY)

            for stroke_idx, line in enumerate(self.json_data):
                if not line or len(line) < 1:
                    continue

                start_pt = line[0]
                self._move_smoothly(start_pt[0], start_pt[1])
                time.sleep(self.MOVE_DELAY * 10)

                self.plotter.pen_down()
                time.sleep(self.PEN_DELAY)

                for i in range(len(line) - 1):
                    p_from = line[i]
                    p_to = line[i + 1]

                    dx = p_to[0] - p_from[0]
                    dy = p_to[1] - p_from[1]
                    distance = math.hypot(dx, dy)

                    if distance > self.STEP_LENGTH:
                        steps = int(math.ceil(distance / self.STEP_LENGTH))
                        for s in range(1, steps + 1):
                            ratio = s / steps
                            curr_x = p_from[0] + dx * ratio
                            curr_y = p_from[1] + dy * ratio
                            self._move_to_tracked(curr_x, curr_y)
                            time.sleep(self.MOVE_DELAY)
                    else:
                        self._move_to_tracked(p_to[0], p_to[1])
                        time.sleep(self.MOVE_DELAY)

                    tag_name = f"seg_{stroke_idx}_{i}"
                    self.root.after(0, lambda t=tag_name: self.canvas.itemconfig(t, fill="black", width=3))

                self.plotter.pen_up()
                time.sleep(self.PEN_DELAY)

            self._move_smoothly(self.PARK_POS[0], self.PARK_POS[1])
            self.root.after(0, lambda: messagebox.showinfo("完了", "すべてのプロットが滑らかに終了しました。"))
        except Exception as e:
            self.root.after(0, lambda err=e: messagebox.showerror("エラー", f"プロット中にエラーが発生しました:\n{err}"))
        finally:
            self.is_plotting = False
            self.root.after(0, lambda: self._set_controls_busy(False))

    def go_home(self):
        """初期位置へ画像描画時と同じ速度でゆっくり移動する"""
        if not self.plotter or self.is_plotting or self.is_moving:
            return
        self.is_moving = True
        self._set_controls_busy(True)

        def _home_worker():
            try:
                self.plotter.pen_up()
                time.sleep(self.PEN_DELAY)
                self._move_smoothly(self.PARK_POS[0], self.PARK_POS[1])
            except Exception as e:
                self.root.after(0, lambda err=e: messagebox.showerror("移動エラー", f"初期位置への移動に失敗しました:\n{err}"))
            finally:
                self.is_moving = False
                self.root.after(0, lambda: self._set_controls_busy(False))

        threading.Thread(target=_home_worker, daemon=True).start()

    def pen_down_test(self):
        if self.plotter:
            self.plotter.pen_down()

    def pen_up_test(self):
        if self.plotter:
            self.plotter.pen_up()


if __name__ == "__main__":
    root = tk.Tk()
    app = PlotterGUI(root)
    root.mainloop()

