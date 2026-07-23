"""test_pairing.py — 测试 DG-LAB SOCKET v2 连接与配对。

使用方法:
    1. 启动后端
    2. python tools/test_pairing.py
    3. 终端弹出 QR 码 -> APP 扫码
    4. 看到 "配对成功" 后自动发送测试波形
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config
from src.dglab_sender import init_sender, wait_for_pair
from src.dglab_sender import send_action, safe_stop, ACTION_PROFILES

config = load_config(str(Path(__file__).resolve().parent.parent / "config.ini"))

print("初始化 DG-LAB 连接...")
init_sender(config)

print("等待 APP 扫码配对...")
if not wait_for_pair(timeout=120):
    print("配对超时（120s），退出。")
    safe_stop()
    sys.exit(1)

print("\n[OK] 配对成功!\n")

TESTS = [
    ("heal_pulse",   15, 2.5, "最温和"),
    ("injury_pulse", 20, 2.5, "中等"),
    ("death_pulse",  40, 5.5, "最强，注意!"),
]

for action, _, wait, desc in TESTS:
    strength = ACTION_PROFILES[action]["strength"]
    print(f"[{desc}] 强度={strength}, 3秒后开始...")
    time.sleep(3)
    result = send_action(action, {"hp": 0.5, "state": "test"})
    print(f"  {action}: {result}")
    time.sleep(wait)

safe_stop()
print("\n测试完成。")
