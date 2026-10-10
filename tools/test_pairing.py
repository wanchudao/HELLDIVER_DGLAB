"""test_pairing.py — 测试 DG-LAB SOCKET v2 连接与配对。

使用方法:
    1. 启动后端
    2. 操作者现场确认后 python tools/test_pairing.py --allow-hardware
    3. 终端弹出 QR 码 -> APP 扫码
    4. 看到 "配对成功" 后自动发送测试波形

本工具仅检查人工短脉冲链路，不提供有效视觉帧续租；脉冲会被帧租约
提前停止（默认1秒），不能用它验收完整死亡时长或持续反馈。
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config
from src.dglab_sender import init_sender, wait_for_pair
from src.dglab_sender import send_action, safe_stop
from src.feedback_mixer import action_profiles, FeedbackMixer

TESTS = [
    ("heal_pulse", "治疗确认"),
    ("injury_pulse", "受伤满额测试"),
    ("death_pulse", "死亡反馈"),
]

def main(argv=None):
    parser = argparse.ArgumentParser(description="手动硬件测试，会发送真实波形")
    parser.add_argument("--allow-hardware", action="store_true",
                        help="明确允许连接设备并发送测试波形")
    args = parser.parse_args(argv)
    if not args.allow_hardware:
        parser.error("默认禁止硬件输出；人工确认后才可加 --allow-hardware")
    config = load_config()
    profiles = action_profiles(config)
    mixer = FeedbackMixer(config)
    try:
        print("初始化 DG-LAB 连接...")
        init_sender(config)
        print("等待 APP 扫码配对...")
        if not wait_for_pair(timeout=120):
            print("配对超时（120s），退出。")
            return 1
        for action, desc in TESTS:
            # 与实际 sender 共用配置校验/量化，不能显示硬编码默认强度。
            profile = profiles[action]
            mixer.reset()
            mixer.add_pulse(action, strength=profile["strength"],
                            duration=profile["duration"], waveform=profile["waveform"], now=1)
            strength = mixer.output(1).strength
            print(f"[{desc}] 配置输出强度={strength}, 无续租最多约{mixer.lease_sec:g}秒；3秒后开始...")
            time.sleep(3)
            result = send_action(action, {"hp": 0.5, "state": "test"})
            print(f"  {action}: {result}")
            time.sleep(min(profile["duration"], mixer.lease_sec) + 0.5)
        print("测试完成。")
        return 0
    finally:
        safe_stop()


if __name__ == "__main__":
    sys.exit(main())
