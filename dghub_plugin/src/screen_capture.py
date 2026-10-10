"""
screen_capture.py
屏幕截图模块 — DXGI Desktop Duplication 后端 (dxcam)。

V0.4.5 替换 mss (GDI BitBlt) → dxcam (DXGI Desktop Duplication):
- GDI BitBlt + CAPTUREBLT 在 HD2 全屏 flip-model 下会触发内核死锁
- DXGI Desktop Duplication 专为 DX 游戏设计，不经过 GDI/DWM bypass 切换

接口保持不变: grab() → np.ndarray (BGR) | None
调用方 (main.py) 无需任何改动。
"""

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class ScreenCapture:
    """屏幕截图器 — dxcam 后端，封装 DXGI Desktop Duplication API。"""

    def __init__(self, monitor_index: int = 1):
        """
        Args:
            monitor_index: 显示器编号 (1=主屏, 2=副屏, ...) — 与旧 mss 接口兼容
        """
        self.monitor_index = monitor_index
        self._camera = None
        self._monitor_size: tuple[int, int] = (0, 0)
        self._init_dxcam()

    def _init_dxcam(self):
        try:
            import dxcam
            # dxcam output_idx 从 0 开始，monitor_index 从 1 开始（兼容旧接口）
            output_idx = max(0, self.monitor_index - 1)
            self._camera = dxcam.create(output_idx=output_idx, output_color="BGR")
            if self._camera is None:
                logger.error("dxcam.create() returned None — D3D11 device creation failed")
                return

            # DXCamera 已公开当前 output 的实际尺寸；device_info() 只是
            # 面向人类的字符串，不能用于索引显示器元数据。
            self._monitor_size = (int(self._camera.width), int(self._camera.height))

            logger.info(
                "screen_capture ready  backend=dxcam  output_idx=%d  size=%dx%d",
                output_idx, self._monitor_size[0], self._monitor_size[1],
            )
        except Exception as e:
            logger.error("screen_capture init failed: %s", e)
            self._camera = None

    @property
    def is_ready(self) -> bool:
        return self._camera is not None

    @property
    def monitor_size(self) -> tuple[int, int]:
        return self._monitor_size

    def grab(self) -> Optional[np.ndarray]:
        """截取当前屏幕，返回 BGR numpy 数组 (H, W, 3)。

        DXGI Desktop Duplication 专为游戏截屏优化:
        - 不经过 GDI/DWM，无 CAPTUREBLT 死锁风险
        - AcquireNextFrame 有内置超时，不会无限期阻塞
        - 全屏 DX 游戏中帧率可达 60fps+

        Returns:
            BGR 图像数组，截图失败返回 None。
        """
        if not self.is_ready:
            logger.warning("screen_capture not ready, cannot grab")
            return None
        try:
            # dxcam 默认只返回“新”桌面帧。桌面静止时会连续返回 None，
            # 这不是捕获故障，却会让 vision 的帧计数和 watchdog 看起来
            # 像卡死。复用最近一次成功截图可保留正常的检测节拍；游戏或
            # 桌面一有变化，dxcam 仍会自动换成新帧。
            frame = self._camera.grab(new_frame_only=False)
            if frame is None:
                # 首次抓取前还没有可复用帧，或 DXGI 正在恢复输出。
                return None
            # dxcam 返回 BGR uint8 ndarray (H, W, 3)，无需转换
            return frame
        except Exception as e:
            logger.error("screen_capture grab failed: %s", e)
            return None

    def close(self):
        if self._camera:
            try:
                self._camera.release()
            except Exception:
                pass
            self._camera = None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cap = ScreenCapture()
    if cap.is_ready:
        frame = cap.grab()
        if frame is not None:
            print(f"captured: shape={frame.shape}, dtype={frame.dtype}")
        else:
            print("grab returned None (no new frame)")
    else:
        print("screen capture not available")
    cap.close()
