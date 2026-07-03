#!/usr/bin/env bash
# Build PcController.app on macOS with PyInstaller.
#
#   bash build_mac.sh
#
# Requires a Python 3 that has tkinter (the python.org installer does; a bare
# Homebrew python needs:  brew install python-tk).
set -euo pipefail

echo "==> installing build deps"
python3 -m pip install --upgrade pip
python3 -m pip install pyinstaller mss pynput Pillow pyperclip

echo "==> building PcController.app"
python3 -m PyInstaller --clean --noconfirm --windowed --name PcController \
  --hidden-import pynput.keyboard._darwin \
  --hidden-import pynput.mouse._darwin \
  --hidden-import remote_control.homeapp \
  --hidden-import remote_control.p2p \
  --exclude-module remote_control.winhook \
  pccontroller.py

echo
echo "================================================================"
echo " 完成:dist/PcController.app"
echo
echo " 首次运行:"
echo "   1) 右键点 PcController.app -> 打开(绕过 Gatekeeper 未签名拦截)"
echo "   2) 系统设置 -> 隐私与安全性,给 PcController 打开:"
echo "        - 屏幕录制   (否则控制端只看到黑屏 / 本程序窗口)"
echo "        - 辅助功能   (否则无法被控制鼠标键盘)"
echo "      开启屏幕录制后,需完全退出 App 再重开才生效。"
echo "================================================================"
