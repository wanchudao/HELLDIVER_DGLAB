"""
screen_capture.py
屏幕截图模块。支持 mss 截图和离线传入 (Mock)。
"""

import logging
import math
import queue
import threading
import time
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
            # mss 已创建但显示器索引等后续初始化失败时，也必须由当前线程释放。
            if self._mss is not None:
                try:
                    self._mss.close()
                except Exception:
                    logger.exception("screen_capture cleanup after init failure failed")
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
            capture = self._mss
            self._mss = None
            self._monitor = None
            capture.close()


class BufferedScreenCapture:
    """实时入口的有界抓屏线程；同步 ScreenCapture 仍供校准工具使用。

    mss 的创建、抓取和关闭全部在 worker 中执行（其句柄为线程局部）。
    原生 grab 即使卡死，UI 线程的 grab/close 也有时间上限；不能安全强杀
    原生线程，所以卡住的 worker 保持 daemon，返回后自行释放底层资源。
    """

    def __init__(
        self, monitor_index: int = 1, *, startup_timeout: float = 2.0,
        max_frame_age: float = 1.0, target_fps: float = 30.0,
        capture_factory=None, clock=None,
    ):
        if not math.isfinite(startup_timeout) or startup_timeout < 0:
            raise ValueError("startup_timeout must be finite and non-negative")
        if not math.isfinite(max_frame_age) or max_frame_age <= 0:
            raise ValueError("max_frame_age must be finite and positive")
        if not math.isfinite(target_fps) or target_fps < 0:
            raise ValueError("target_fps must be finite and non-negative")
        self.monitor_index = monitor_index
        self._clock = clock or time.monotonic
        self._factory = capture_factory or ScreenCapture
        self._max_frame_age = min(float(max_frame_age), 1.0)
        self._frame_interval = 1.0 / target_fps if target_fps else 0.0
        self._frames = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._monitor_size = (0, 0)
        self._error = None
        self._thread = threading.Thread(
            target=self._run, name="ScreenCaptureWorker", daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(min(float(startup_timeout), 2.0)):
            self._error = TimeoutError("screen capture initialization timed out")
            self._stop.set()
            logger.error("buffered screen_capture init timed out")

    @property
    def is_ready(self) -> bool:
        return (self._ready.is_set() and self._error is None
                and not self._stop.is_set() and self._thread.is_alive())

    @property
    def monitor_size(self) -> tuple[int, int]:
        return self._monitor_size

    def _discard_pending(self):
        try:
            self._frames.get_nowait()
        except queue.Empty:
            pass

    def _run(self):
        capture = None
        try:
            # 不把主线程创建的 mss 实例传入后台线程。
            capture = self._factory(monitor_index=self.monitor_index)
            if not capture.is_ready:
                raise RuntimeError("screen capture backend is not ready")
            self._monitor_size = capture.monitor_size
            self._ready.set()
            while not self._stop.is_set():
                started = self._clock()
                frame = capture.grab()
                if self._stop.is_set():
                    break
                # 采用抓取开始时刻：长阻塞后返回的画面不能伪装成刚捕获。
                age = self._clock() - started
                if frame is not None and 0 <= age <= self._max_frame_age:
                    self._discard_pending()
                    self._frames.put_nowait((started, frame))
                # 不限帧率时也避免连续抓取失败造成忙等。
                retry_floor = 0.005 if frame is None else 0.0
                self._stop.wait(max(retry_floor, self._frame_interval - age, 0.0))
        except Exception as exc:
            self._error = exc
            logger.exception("buffered screen_capture worker failed")
        finally:
            self._ready.set()
            self._discard_pending()
            if capture is not None:
                try:
                    capture.close()
                except Exception:
                    logger.exception("buffered screen_capture worker cleanup failed")

    def grab(self, timeout: float = 0.05) -> Optional[np.ndarray]:
        """最多等待 50ms，消费最新且不超过 1 秒的帧；不会返回缓存旧帧。"""
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be finite and non-negative")
        if not self.is_ready:
            return None
        # 队列等待使用真实 monotonic，测试注入的帧时钟不改变等待上限。
        deadline = time.monotonic() + min(float(timeout), 0.05)
        while not self._stop.is_set():
            try:
                captured_at, frame = self._frames.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                return None
            age = self._clock() - captured_at
            if not self._stop.is_set() and 0 <= age <= self._max_frame_age:
                return frame
            if time.monotonic() >= deadline:
                return None
        return None

    def close(self):
        """立即禁止取帧，至多等待 200ms；绝不跨线程释放 mss。"""
        self._stop.set()
        self._discard_pending()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=0.2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cap = ScreenCapture()
    if cap.is_ready:
        frame = cap.grab()
        print(f"captured: shape={frame.shape}, dtype={frame.dtype}")
    else:
        print("screen capture not available")
    cap.close()
