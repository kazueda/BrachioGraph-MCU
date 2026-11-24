import subprocess
import sys

PACKAGES = [
    "numpy",
    "usbserial4a",
    "usb4a",
]

def main():
    print("=== Pydroid 3 ライブラリ自動インストール ===")
    for pkg in PACKAGES:
        print(f"\n[インストール中] {pkg} ...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
            print(f"[完了] {pkg}")
        except subprocess.CalledProcessError as e:
            print(f"[エラー] {pkg} のインストールに失敗しました (終了コード: {e.returncode})")
            return

    print("\nすべてのライブラリのインストールが完了しました！")

if __name__ == "__main__":
    main()
