"""Canon EOS R6 camera worker (python-gphoto2).

Runs in its own QThread. Emits live-preview frames as BGR numpy arrays and
captured JPEG bytes on demand. Handles:

  - macOS ptpcamerad claim (kill + retry on init)
  - Conflicting process preflight (EOS Utility, Image Capture, Photos)
  - Startup config (autopoweroff=0, imageformat=Large Fine JPEG, capturetarget=RAM)
  - Disconnect → emits `disconnected(reason)` and shuts down

The preview loop is driven by `QTimer.singleShot(0, ...)` so the worker's
event loop keeps running between frames — capture requests scheduled via
queued signals get processed naturally between preview grabs.

DO NOT call `--list-all-config` from runtime: it has been known to brick
the R6 viewfinder until USB replug. Use it manually during dev to discover
the exact config-key names if the defaults don't apply to your firmware.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Any

import cv2
import numpy as np
from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

try:
    import gphoto2 as gp
    _GPHOTO_AVAILABLE = True
except ImportError:
    gp = None  # type: ignore
    _GPHOTO_AVAILABLE = False

from .config import CameraConfig

LOG = logging.getLogger(__name__)

# Match against the actual app bundle path so we don't false-positive on
# macOS background services like PhotosAgent or mediaanalysisd-photos.
# (ptpcamerad is the real camera-claimer and we kill it directly — we don't
# also need to police Image Capture / Photos, which only claim a camera when
# the user actively interacts with them.)
CONFLICTING_APP_PATHS = [
    "/EOS Utility.app/Contents/MacOS/",
    "/EOSWebcamUtility.app/Contents/MacOS/",
    "/EOS Webcam Utility.app/Contents/MacOS/",
]


def check_conflicting_processes() -> list[str]:
    """Return any conflicting Canon-tool process names currently running."""
    found = []
    for path_fragment in CONFLICTING_APP_PATHS:
        r = subprocess.run(
            ["pgrep", "-f", path_fragment],
            capture_output=True, text=True, check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            # Extract the human-readable name from the path fragment.
            name = path_fragment.split("/")[1].replace(".app", "")
            found.append(name)
    return found


def kill_ptpcamerad() -> None:
    """Best-effort kill of macOS's ptpcamerad. launchd will respawn it;
    the point is to make it release the USB claim long enough for us to grab.
    No sudo required."""
    subprocess.run(
        ["killall", "-9", "ptpcamerad"],
        capture_output=True, check=False,
    )


def detect_camera() -> str | None:
    """Return the first detected camera's model name, or None.

    Done before `Camera.init()` so we never hand libgphoto2 a USB device
    that doesn't exist — that path has SIGABRT'd in the wild.
    """
    if not _GPHOTO_AVAILABLE:
        return None
    try:
        cameras = list(gp.Camera.autodetect())
    except Exception:
        return None
    if not cameras:
        return None
    name, _port = cameras[0]
    return name


def detect_camera_with_kill_loop(timeout_s: float = 6.0) -> str | None:
    """Hammer ptpcamerad in a tight loop while polling autodetect.

    macOS's ptpcamerad daemon claims the USB device aggressively and respawns
    in under 500ms after `killall`. A single kill + sleep loses the race
    most of the time; a tight loop wins almost always.
    """
    import time as _t
    deadline = _t.monotonic() + timeout_s
    while _t.monotonic() < deadline:
        kill_ptpcamerad()
        name = detect_camera()
        if name:
            return name
        _t.sleep(0.1)
    return None


class CameraWorker(QObject):
    frame = pyqtSignal(np.ndarray)        # BGR preview frame
    captured = pyqtSignal(bytes, str)     # JPEG bytes, suggested filename
    connected = pyqtSignal()
    disconnected = pyqtSignal(str)        # reason

    _capture_requested = pyqtSignal()     # internal — fires capture in this thread
    _stop_signal = pyqtSignal()           # internal — clean up inside worker thread

    def __init__(self, cfg: CameraConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self._camera: Any = None
        self._alive = False           # camera is connected and ready
        self._preview_streaming = False  # preview loop is iterating
        self._busy_capturing = False
        # Backpressure: only one preview frame may be "in flight" to the GUI
        # at a time. We keep grabbing from the camera (so Servo AF stays
        # alive) but drop frames we'd otherwise emit until the consumer acks
        # the previous one via mark_frame_consumed(). Without this, a GUI that
        # can't keep up lets the queued-connection backlog grow without bound
        # and preview latency climbs the longer the booth runs.
        self._consumer_ready = True
        self._capture_requested.connect(self._on_capture_request)
        self._stop_signal.connect(self._cleanup_in_worker_thread)

    @pyqtSlot()
    def start(self) -> None:
        """Entry point — connected to QThread.started in BoothWindow."""
        if not _GPHOTO_AVAILABLE:
            self.disconnected.emit(
                "python-gphoto2 not installed. Run: pip install gphoto2"
            )
            return

        conflicting = check_conflicting_processes()
        if conflicting:
            self.disconnected.emit(
                f"Conflicting app running: {', '.join(conflicting)}. "
                "Quit it and reconnect the camera."
            )
            return

        # Autodetect with a tight ptpcamerad-kill loop. A single kill
        # loses the race against launchd respawn ~50% of the time.
        detected = detect_camera_with_kill_loop(timeout_s=6.0)
        if detected is None:
            self.disconnected.emit(
                "No camera detected. Connect the R6 (USB-C, Wi-Fi off, "
                "PHOTO mode), quit Image Capture/Photos, then try again."
            )
            return
        LOG.info("detected camera: %s", detected)

        try:
            self._init_camera()
        except Exception as e:  # gp.GPhoto2Error subclasses Exception
            self.disconnected.emit(f"Camera init failed: {e}")
            return

        try:
            self._apply_startup_config()
        except Exception as e:
            LOG.warning("startup config partially failed: %s", e)

        self._alive = True
        self._preview_streaming = True
        self.connected.emit()
        QTimer.singleShot(0, self._grab_one_preview)

    def _init_camera(self) -> None:
        """Init with a tight kill loop. The 0.5s sleep version lost the race
        against ptpcamerad respawn; this hammers init while killing."""
        last_err: Exception | None = None
        attempts = max(1, self.cfg.init_retries * 4)  # roughly same total time
        for attempt in range(attempts):
            kill_ptpcamerad()
            try:
                self._camera = gp.Camera()
                self._camera.init()
                LOG.info("camera connected (attempt %d)", attempt + 1)
                return
            except Exception as e:
                last_err = e
                self._camera = None
                if attempt == attempts - 1:
                    LOG.warning("camera init attempt %d failed: %s", attempt + 1, e)
                time.sleep(0.15)
        raise RuntimeError(last_err)

    def _apply_startup_config(self) -> None:
        """Apply settings one at a time. The R6 returns -2 (Bad parameters)
        on bulk set_config; set_single_config per widget works fine."""
        keyvals = [
            (self.cfg.image_format_key, self.cfg.image_format_value),
            (self.cfg.capture_target_key, self.cfg.capture_target_value),
            (self.cfg.auto_poweroff_key, self.cfg.auto_poweroff_value),
        ]
        for key, value in keyvals:
            try:
                config = self._camera.get_config()
                widget = config.get_child_by_name(key)
                widget.set_value(value)
                self._camera.set_single_config(key, widget)
                LOG.info("camera config %s=%s applied", key, value)
            except Exception as e:
                LOG.warning("camera config %s=%s not applied: %s", key, value, e)

    @pyqtSlot()
    def _grab_one_preview(self) -> None:
        if not self._alive or self._camera is None:
            return  # only stops on shutdown — see _cleanup_in_worker_thread
        if (not self._preview_streaming) or self._busy_capturing:
            # Paused or busy: idle without touching the camera, poll back.
            QTimer.singleShot(80, self._grab_one_preview)
            return
        try:
            cam_file = self._camera.capture_preview()
        except Exception as e:
            self._handle_disconnect(str(e))
            return
        # capture_preview() above already serviced the camera / Servo AF for
        # this tick. Only pay for the JPEG copy + decode + emit when the GUI
        # is ready for another frame; otherwise drop it and grab again.
        if self._consumer_ready:
            try:
                data = bytes(memoryview(cam_file.get_data_and_size()))
            except Exception as e:
                self._handle_disconnect(str(e))
                return
            arr = np.frombuffer(data, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is not None:
                self._consumer_ready = False
                self.frame.emit(frame)
        QTimer.singleShot(0, self._grab_one_preview)

    @pyqtSlot()
    def mark_frame_consumed(self) -> None:
        """Consumer ack — the GUI has finished with the last preview frame.

        Connected from PreviewWidget via a queued connection, so it runs in
        this worker thread and is safe to flip the flag from. Re-arms the
        next emit in _grab_one_preview.
        """
        self._consumer_ready = True

    def request_capture(self) -> None:
        """Thread-safe — schedules a full-res capture in the worker thread."""
        LOG.info("request_capture: emitting capture signal")
        self._capture_requested.emit()

    @pyqtSlot()
    def _on_capture_request(self) -> None:
        LOG.info("_on_capture_request: alive=%s camera=%s",
                 self._alive, self._camera is not None)
        if not self._alive or self._camera is None:
            LOG.warning("capture skipped: camera not alive")
            return
        self._busy_capturing = True
        try:
            # No explicit autofocus drive — set body AF mode to Servo + use
            # live view, and capture lands instantly with whatever focus the
            # camera currently has. Driving AF here added ~350ms per shot.
            LOG.info("calling camera.capture(GP_CAPTURE_IMAGE)…")
            cam_path = self._camera.capture(gp.GP_CAPTURE_IMAGE)
            LOG.info("capture returned path: %s/%s", cam_path.folder, cam_path.name)
            cam_file = self._camera.file_get(
                cam_path.folder, cam_path.name, gp.GP_FILE_TYPE_NORMAL,
            )
            data = bytes(memoryview(cam_file.get_data_and_size()))
            LOG.info("captured %d bytes; emitting captured signal", len(data))
            self.captured.emit(data, cam_path.name)
        except Exception as e:
            LOG.error("capture failed: %s", e)
            self._handle_disconnect(f"capture failed: {e}")
            return
        finally:
            self._busy_capturing = False
        # Don't auto-resume preview here — BoothWindow drives that explicitly
        # via resume_preview() when entering LIVE_PREVIEW.

    def pause_preview(self) -> None:
        """Toggle flag only (thread-safe). The loop continues to run in the
        worker thread but idles without touching the camera."""
        if self._preview_streaming:
            LOG.info("pausing preview")
        self._preview_streaming = False

    def resume_preview(self) -> None:
        """Toggle flag only. The loop in the worker thread picks it up on
        its next poll tick (≤80ms)."""
        if not self._alive or self._camera is None:
            return
        if not self._preview_streaming:
            LOG.info("resuming preview")
        self._preview_streaming = True

    def _handle_disconnect(self, reason: str) -> None:
        self._alive = False
        self._preview_streaming = False
        if self._camera is not None:
            try:
                self._camera.exit()
            except Exception:
                pass
            self._camera = None
        self.disconnected.emit(reason)

    def stop(self) -> None:
        """Thread-safe — only fires a signal. Actual camera cleanup happens
        in the worker thread; calling camera.exit() from the GUI thread
        while the worker is mid-capture_preview crashes libgphoto2."""
        self._stop_signal.emit()

    @pyqtSlot()
    def _cleanup_in_worker_thread(self) -> None:
        """Runs in the worker thread via queued signal. Safe to touch
        self._camera here because we own it."""
        LOG.info("cleanup_in_worker_thread")
        self._alive = False
        self._preview_streaming = False
        if self._camera is not None:
            try:
                self._camera.exit()
            except Exception as e:
                LOG.debug("camera.exit() raised: %s", e)
            self._camera = None
        t = self.thread()
        if t is not None:
            t.quit()
