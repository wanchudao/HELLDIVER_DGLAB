"""
screen_capture.py
屏幕截图模块。支持 mss 截图和离线传入 (Mock)。
"""

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class ScreenCapture:
    """屏幕截图器，封装 mss 并提供统一的 numpy 数组输出。"""

    def __init__(self, monitor_index: int = 1):
        """
        Args:
            monitor_index: 显示器编号 (1=主屏, 2=副屏, ...)
        """
        self.monitor_index = monitor_index
        self._mss = None
        self._monitor = None
        self._init_mss()

    def _init_mss(self):
        try:
            import mss
            self._mss = mss.mss()
            self._monitor = self._mss.monitors[self.monitor_index]
            logger.info(
                "screen_capture ready  monitor=%s  size=%dx%d",
                self.monitor_index,
                self._monitor["width"],
                self._monitor["height"],
            )
        except Exception as e:
            logger.error("screen_capture init failed: %s", e)
            self._mss = None
            self._monitor = None

    @property
    def is_ready(self) -> bool:
        return self._mss is not None

    @property
    def monitor_size(self) -> tuple[int, int]:
        if self._monitor:
            return self._monitor["width"], self._monitor["height"]
        return 0, 0

    def grab(self) -> Optional[np.ndarray]:
        """截取当前屏幕，返回 BGR numpy 数组 (H, W, 3)。

        Returns:
            BGR 图像数组，截图失败返回 None。
        """
        if not self.is_ready:
            logger.warning("screen_capture not ready, cannot grab")
            return None
        try:
            import mss
            raw = self._mss.grab(self._monitor)
            # mss 返回 BGRA (alpha 通道在最后)
            img = np.array(raw, dtype=np.uint8)
            # 只取前 3 通道 (BGR)，丢弃 alpha
            img = img[:, :, :3]
            return img
        except Exception as e:
            logger.error("screen_capture grab failed: %s", e)
            return None

    def close(self):
        if self._mss:
            self._mss.close()
            self._mss = None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cap = ScreenCapture()
    if cap.is_ready:
        frame = cap.grab()
        print(f"captured: shape={frame.shape}, dtype={frame.dtype}")
    else:
        print("screen capture not available")
    cap.close()
