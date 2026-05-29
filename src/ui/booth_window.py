"""Fullscreen booth window: state machine, camera wiring, upload trigger.

Global keys (any state):
  Cmd+Shift+Q  exit
  Cmd+,        toggle settings overlay (placeholder until M7)
  Esc, Cmd+Q   intentionally inert (kid-proofing)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path

import cv2
from PyQt6.QtCore import Qt, QThread, QTimer
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QLabel, QMainWindow, QStackedWidget, QWidget

from ..camera import CameraWorker
from ..chroma import ChromaKeyer
from ..composite_worker import CompositeJob
from ..compositor import composite, jpeg_bytes_to_bgr, make_strip, save_jpeg
from ..config import Config
from ..immich import ImmichClient
from ..notify import send_sms_alert
from ..upload_daemon import UploadDaemon, enqueue_session_files
from .attract_widget import AttractWidget
from .background_picker import BackgroundPicker
from .countdown_widget import CountdownWidget
from .preview_widget import PreviewWidget
from .review_widget import ReviewWidget
from .settings_overlay import SettingsOverlay
from .stub_widgets import (
    CaptureFlashWidget,
    DoneWidget,
    UploadingWidget,
)

LOG = logging.getLogger(__name__)


class BoothState(Enum):
    ATTRACT = auto()
    PICK_BACKGROUND = auto()
    LIVE_PREVIEW = auto()
    COUNTDOWN = auto()
    CAPTURE = auto()
    REVIEW = auto()
    UPLOADING = auto()
    DONE = auto()


class BoothWindow(QMainWindow):
    def __init__(
        self,
        cfg: Config,
        enable_camera: bool = True,
        synthesize_when_no_camera: bool = False,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self._enable_camera = enable_camera
        self._synthesize_when_no_camera = synthesize_when_no_camera
        self._state = BoothState.ATTRACT
        self._capture_index = 0
        self._shutting_down = False
        self.current_background: Path | None = None
        self._session_composites: list[Path] = []
        self._session_started_at: datetime = datetime.now(timezone.utc)
        self._composites_pending = 0
        self._composite_jobs: list[CompositeJob] = []
        self._waiting_for_composites = False
        self._session_strip_path: Path | None = None
        self._keyer = ChromaKeyer(
            hue_low=cfg.chroma.hue_low,
            hue_high=cfg.chroma.hue_high,
            sat_min=cfg.chroma.sat_min,
            val_min=cfg.chroma.val_min,
            feather_px_preview=cfg.chroma.feather_px_preview,
            feather_px_final=cfg.chroma.feather_px_final,
            spill_suppress=cfg.chroma.spill_suppress,
            guided_filter=cfg.chroma.guided_filter,
        )

        self.setWindowTitle("Photobooth")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet("background-color: #111; color: #eee;")

        self._stack = QStackedWidget(self)
        self.setCentralWidget(self._stack)

        self._widgets: dict[BoothState, QWidget] = {
            BoothState.ATTRACT: AttractWidget(cfg, self),
            BoothState.PICK_BACKGROUND: BackgroundPicker(cfg, self),
            BoothState.LIVE_PREVIEW: PreviewWidget(cfg, self),
            BoothState.COUNTDOWN: CountdownWidget(cfg, self),
            BoothState.CAPTURE: CaptureFlashWidget(cfg, self),
            BoothState.REVIEW: ReviewWidget(cfg, self),
            BoothState.UPLOADING: UploadingWidget(cfg, self),
            BoothState.DONE: DoneWidget(cfg, self),
        }
        for w in self._widgets.values():
            self._stack.addWidget(w)

        picker = self._widgets[BoothState.PICK_BACKGROUND]
        picker.background_selected.connect(self._on_background_selected)
        countdown = self._widgets[BoothState.COUNTDOWN]
        countdown.finished.connect(self._on_countdown_done, Qt.ConnectionType.UniqueConnection)
        countdown.pre_fire.connect(self._on_countdown_pre_fire, Qt.ConnectionType.UniqueConnection)

        self._settings_overlay = SettingsOverlay(cfg, self._keyer, self)
        self._settings_overlay.hide()

        # Low-battery banner: a floating child of the window (like the settings
        # overlay) so it paints over whatever state widget is showing. Shown by
        # _on_battery when charge drops to/under the configured threshold.
        self._battery_banner = QLabel("", self)
        self._battery_banner.setStyleSheet(
            "background-color: rgba(176, 32, 32, 235); color: #fff;"
            " font-size: 30px; font-weight: bold; padding: 14px 30px;"
            " border-radius: 12px;"
        )
        self._battery_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._battery_banner.hide()
        self._battery_low_latched = False  # so we text the phone once per dip
        self._last_battery_pct = -1
        self._battery_startup_alert_sent = False  # one info text on first reading

        # Adult exit: three quick Escs (Cmd+Shift+Q conflicts with macOS logout).
        self._esc_press_times: list[float] = []
        self._esc_chord_window_s = 1.5
        self._esc_chord_count = 3

        # Cmd+, opens the chroma-key settings overlay.
        from PyQt6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence("Ctrl+,"), self, activated=self._toggle_settings)

        self._inactivity_timer = QTimer(self)
        self._inactivity_timer.setSingleShot(True)
        self._inactivity_timer.timeout.connect(self._on_inactivity_timeout)

        # Camera + upload daemon.
        self._camera_thread: QThread | None = None
        self._camera_worker: CameraWorker | None = None
        self._camera_connected = False
        if self._enable_camera:
            self._start_camera_worker()

        # Persistent background uploader. Sessions just drop files into
        # output/pending_upload/ and notify the daemon; the daemon does
        # one health-check at startup, then continuously processes the queue.
        self._upload_daemon = UploadDaemon(
            client_factory=self._make_immich_client,
            pending_dir=cfg.pending_upload_dir,
            parent=self,
        )
        self._upload_daemon.queue_changed.connect(
            self._on_upload_queue_changed, Qt.ConnectionType.QueuedConnection,
        )
        self._upload_daemon.status_changed.connect(
            self._on_upload_status_changed, Qt.ConnectionType.QueuedConnection,
        )
        self._upload_daemon.start()

        self.transition_to(BoothState.ATTRACT)

    # -------------------------------------------------------------- camera

    def _start_camera_worker(self) -> None:
        self._camera_thread = QThread(self)
        self._camera_worker = CameraWorker(self.cfg.camera)
        self._camera_worker.moveToThread(self._camera_thread)
        self._camera_thread.started.connect(self._camera_worker.start)
        preview = self._widgets[BoothState.LIVE_PREVIEW]
        self._camera_worker.frame.connect(
            preview.update_frame,
            Qt.ConnectionType.QueuedConnection,
        )
        # Consumer ack for the worker's frame backpressure (see CameraWorker).
        if hasattr(preview, "frame_consumed"):
            preview.frame_consumed.connect(
                self._camera_worker.mark_frame_consumed,
                Qt.ConnectionType.QueuedConnection,
            )
        self._camera_worker.connected.connect(self._on_camera_connected)
        self._camera_worker.disconnected.connect(self._on_camera_disconnected)
        self._camera_worker.captured.connect(
            self._on_captured, Qt.ConnectionType.QueuedConnection,
        )
        self._camera_worker.battery.connect(
            self._on_battery, Qt.ConnectionType.QueuedConnection,
        )
        self._camera_thread.start()

    def _on_camera_connected(self) -> None:
        LOG.info("camera connected")
        self._camera_connected = True
        # Refresh any visible status indicators.
        preview = self._widgets[BoothState.LIVE_PREVIEW]
        if hasattr(preview, "set_camera_status"):
            preview.set_camera_status(True, None)

    def _on_camera_disconnected(self, reason: str) -> None:
        LOG.warning("camera disconnected: %s", reason)
        self._camera_connected = False
        preview = self._widgets[BoothState.LIVE_PREVIEW]
        if hasattr(preview, "set_camera_status"):
            preview.set_camera_status(False, reason)
        # If we were mid-session, bail.
        if self._state in (BoothState.COUNTDOWN, BoothState.CAPTURE):
            self._abort_session(f"Camera disconnected: {reason}")

    def _on_battery(self, percent: int, raw: str) -> None:
        """Camera battery reading from CameraWorker. Shows/hides the on-screen
        banner and texts the phone once when charge dips to the threshold."""
        self._last_battery_pct = percent
        # On the first reading after launch, text the current level (if alerts
        # are configured — send_sms_alert no-ops otherwise) so you get a "booth
        # is up, battery is X" confirmation without watching the screen.
        if not self._battery_startup_alert_sent:
            self._battery_startup_alert_sent = True
            level = f"{percent}%" if percent >= 0 else repr(raw)
            sent = send_sms_alert(
                self.cfg.alerts, f"Photobooth started. Camera battery: {level}."
            )
            LOG.info(
                "startup battery alert: level=%s, sms %s",
                level, "dispatched" if sent else "skipped (alerts not configured)",
            )
        if percent < 0:
            return  # couldn't parse a number — don't alert on an unknown value
        threshold = self.cfg.camera.battery_low_threshold_pct
        if percent <= threshold:
            self._battery_banner.setText(
                f"⚠  Camera battery low: {percent}% — swap the battery soon"
            )
            self._battery_banner.show()
            self._position_battery_banner()
            if not self._battery_low_latched:
                self._battery_low_latched = True
                send_sms_alert(
                    self.cfg.alerts,
                    f"Photobooth: camera battery low ({percent}%). Swap the battery soon.",
                )
        elif percent >= threshold + 10:
            # Hysteresis: only clear once clearly recovered (e.g. after a swap),
            # so a reading hovering at the threshold doesn't flicker the banner.
            self._battery_banner.hide()
            self._battery_low_latched = False

    def _position_battery_banner(self) -> None:
        b = self._battery_banner
        b.adjustSize()
        b.move(max(0, (self.width() - b.width()) // 2), 24)
        b.raise_()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._battery_banner.isVisible():
            self._position_battery_banner()

    def _abort_session(self, message: str) -> None:
        """Mid-capture failure — surface the reason and reset."""
        LOG.warning("aborting session: %s", message)
        # Show a brief banner on the DONE screen with the failure reason.
        done = self._widgets[BoothState.DONE]
        if hasattr(done, "set_message"):
            done.set_message(f"Couldn't take photos:\n{message}\nPress SPACE to try again.")
        self.transition_to(BoothState.DONE)

    # -------------------------------------------------------------- state

    @property
    def state(self) -> BoothState:
        return self._state

    def transition_to(self, new_state: BoothState) -> None:
        old_widget = self._widgets[self._state]
        if old_widget is not None and hasattr(old_widget, "on_exit"):
            try:
                old_widget.on_exit()
            except Exception as e:
                LOG.warning("on_exit on %s raised: %s", self._state, e)
        self._state = new_state
        widget = self._widgets[new_state]
        self._stack.setCurrentWidget(widget)
        if hasattr(widget, "on_enter"):
            widget.on_enter()
        self._reset_inactivity_timer()
        # Note: we deliberately do NOT pause the camera preview between
        # states. The R6 needs live-view active to keep Servo AF tracking;
        # if we pause during the countdown, the lens un-focuses and the
        # capture has a visible "rack-focus" lag. PreviewWidget itself
        # skips chroma-key work when not visible, so unused frames are cheap.

    def advance(self) -> None:
        s = self._state
        if s is BoothState.ATTRACT:
            self.transition_to(BoothState.PICK_BACKGROUND)
        elif s is BoothState.PICK_BACKGROUND:
            picker = self._widgets[BoothState.PICK_BACKGROUND]
            picker.commit_selection()
            self.transition_to(BoothState.LIVE_PREVIEW)
        elif s is BoothState.LIVE_PREVIEW:
            self._begin_capture_session()
        elif s is BoothState.REVIEW:
            self._enqueue_session_for_upload()
        elif s is BoothState.DONE:
            self.transition_to(BoothState.ATTRACT)

    def _begin_capture_session(self) -> None:
        self._capture_index = 0
        self._session_composites = []
        self._session_started_at = datetime.now(timezone.utc)
        self._composites_pending = 0
        self._composite_jobs = []
        self._waiting_for_composites = False
        self._session_strip_path = None
        self.transition_to(BoothState.COUNTDOWN)

    def _on_countdown_pre_fire(self) -> None:
        """Fired `shutter_lead_ms` before countdown ends. Send the capture
        command now so the actual R6 shutter coincides with the SNAP page."""
        if self._shutting_down or self._state is not BoothState.COUNTDOWN:
            LOG.info("pre-fire ignored: shutting_down=%s state=%s",
                     self._shutting_down, self._state)
            return
        if self._camera_connected and self._camera_worker is not None:
            LOG.info("pre-fire: requesting capture from camera worker")
            self._camera_worker.request_capture()

    def _on_countdown_done(self) -> None:
        if self._shutting_down or self._state is not BoothState.COUNTDOWN:
            return
        LOG.info("countdown done; camera_connected=%s", self._camera_connected)
        self.transition_to(BoothState.CAPTURE)
        if self._camera_connected and self._camera_worker is not None:
            # Real capture was already requested in _on_countdown_pre_fire;
            # nothing more to do here.
            pass
        elif self._synthesize_when_no_camera:
            QTimer.singleShot(400, self._synthesize_capture)
        else:
            LOG.warning("capture requested but camera not connected — aborting")
            self._abort_session("Camera not connected")

    def _synthesize_capture(self) -> None:
        if self.current_background is None:
            return
        bg = cv2.imread(str(self.current_background))
        if bg is None:
            return
        # Just use the background as both fg and bg (no real subject) — pipeline test only.
        ok, jpeg = cv2.imencode(".jpg", bg, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if ok:
            self._on_captured(jpeg.tobytes(), f"synthetic_{self._capture_index}.jpg")

    def _on_captured(self, jpeg_bytes: bytes, suggested_name: str) -> None:
        if self._shutting_down or self._state is not BoothState.CAPTURE:
            # A capture that fired during shutdown or after we already left
            # the capture state — discard the bytes so we don't litter the
            # output dir with stray shots of whatever the lens is pointing at.
            LOG.info("dropping stray capture (state=%s shutting_down=%s)",
                     self._state, self._shutting_down)
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        stem = f"{ts}_{self._capture_index + 1}"

        raw_path = self.cfg.raw_dir / f"{stem}.jpg"
        raw_path.write_bytes(jpeg_bytes)

        if self.current_background is not None:
            comp_path = self.cfg.composite_dir / f"{stem}_composite.jpg"
            if self._camera_connected:
                # Real captures are 5472×3648 — composite on a worker so the
                # countdown animation between shots doesn't stutter.
                self._spawn_composite_worker(jpeg_bytes, self.current_background, comp_path)
            else:
                # Synthesize/test path: composite is small, do it synchronously.
                try:
                    bgr = jpeg_bytes_to_bgr(jpeg_bytes)
                    img = composite(bgr, self.current_background, self._keyer)
                    save_jpeg(img, comp_path, quality=92)
                    self._session_composites.append(comp_path)
                except Exception as e:
                    LOG.error("composite failed: %s", e)

        self._capture_index += 1
        # Flash beat — let the user see the flash for at least 600ms.
        QTimer.singleShot(600, self._after_capture)

    def _spawn_composite_worker(
        self, jpeg_bytes: bytes, bg_path: Path, out_path: Path,
    ) -> None:
        job = CompositeJob(jpeg_bytes, bg_path, out_path, self._keyer, parent=self)
        job.finished.connect(self._on_composite_done, Qt.ConnectionType.QueuedConnection)
        self._composite_jobs.append(job)  # keep alive until signal fires
        self._composites_pending += 1
        job.start()

    def _on_composite_done(self, path: object) -> None:
        self._composites_pending -= 1
        if path is not None:
            self._session_composites.append(path)  # type: ignore[arg-type]
        # If we're already in REVIEW and the last composite just finished,
        # rebuild the strip with all the composites.
        if (self._composites_pending == 0
            and self._waiting_for_composites):
            self._waiting_for_composites = False
            self._build_strip_and_review()

    def _after_capture(self) -> None:
        if self._capture_index < self.cfg.ui.capture_count:
            self.transition_to(BoothState.COUNTDOWN)
        else:
            self._finalize_session()

    def _finalize_session(self) -> None:
        # If composites are still running on background threads, wait for them
        # before assembling the strip. (Most of the time they'll already be done.)
        if self._composites_pending > 0:
            LOG.info("waiting on %d composite(s) before strip", self._composites_pending)
            self._waiting_for_composites = True
            # Show a brief "Processing…" state by entering REVIEW with no strip yet.
            self.transition_to(BoothState.REVIEW)
            return
        self._build_strip_and_review()

    def _build_strip_and_review(self) -> None:
        try:
            from PIL import Image as PILImage
            composites = [PILImage.open(p) for p in self._session_composites]
            if composites:
                strip = make_strip(composites, self.cfg.strip.header_text)
                ts = self._session_started_at.strftime("%Y%m%d_%H%M%S")
                strip_path = self.cfg.strips_dir / f"{ts}_strip.jpg"
                save_jpeg(strip, strip_path, quality=92)
                self._session_strip_path = strip_path
            else:
                self._session_strip_path = None
        except Exception as e:
            LOG.error("strip assembly failed: %s", e)
            self._session_strip_path = None

        if self._state is not BoothState.REVIEW:
            self.transition_to(BoothState.REVIEW)
        else:
            review = self._widgets[BoothState.REVIEW]
            if hasattr(review, "on_enter"):
                review.on_enter()

        # Hand the new files off to the upload daemon and head to DONE.
        # No per-session health check, no blocking — the daemon is already
        # running and will pick these up immediately.
        QTimer.singleShot(3000, self._enqueue_session_for_upload)

    # -------------------------------------------------------------- upload

    def _make_immich_client(self) -> ImmichClient:
        return ImmichClient(
            base_url=self.cfg.immich.base_url,
            api_key=self.cfg.immich.api_key,
            album_name=self.cfg.immich.album_name,
            state_dir=self.cfg.state_dir,
        )

    def _enqueue_session_for_upload(self) -> None:
        files = list(self._session_composites)
        if self._session_strip_path:
            files.append(self._session_strip_path)
        if files:
            LOG.info("enqueueing %d file(s) for upload", len(files))
            enqueue_session_files(
                files, self._session_started_at, self.cfg.pending_upload_dir,
            )
            self._upload_daemon.notify()
        self.transition_to(BoothState.DONE)
        QTimer.singleShot(5000, lambda: self.transition_to(BoothState.ATTRACT))

    def _on_upload_queue_changed(self, count: int) -> None:
        attract = self._widgets[BoothState.ATTRACT]
        if hasattr(attract, "set_pending_count"):
            attract.set_pending_count(count)

    def _on_upload_status_changed(self, status: str) -> None:
        LOG.info("upload daemon: %s", status)
        attract = self._widgets[BoothState.ATTRACT]
        if hasattr(attract, "set_upload_status"):
            attract.set_upload_status(status)

    # -------------------------------------------------------------- keys

    def keyPressEvent(self, event: QKeyEvent) -> None:
        self._reset_inactivity_timer()
        key = event.key()
        widget = self._widgets[self._state]

        # Adult exit chord: three Esc presses within 1.5s.
        if key == Qt.Key.Key_Escape:
            now = time.monotonic()
            self._esc_press_times = [
                t for t in self._esc_press_times
                if now - t < self._esc_chord_window_s
            ]
            self._esc_press_times.append(now)
            if len(self._esc_press_times) >= self._esc_chord_count:
                LOG.info("triple-Esc quit chord detected")
                self.close()
            event.accept()
            return

        if self._state is BoothState.PICK_BACKGROUND and key in (
            Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
        ):
            if hasattr(widget, "handle_arrow"):
                widget.handle_arrow(key)
                event.accept()
                return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._state is BoothState.PICK_BACKGROUND:
                self.advance()
                event.accept()
                return

        if key == Qt.Key.Key_Space:
            self.advance()
            event.accept()
            return

        event.ignore()

    def _on_background_selected(self, path: Path) -> None:
        self.current_background = path

    def _toggle_settings(self) -> None:
        if self._settings_overlay.isVisible():
            self._settings_overlay.hide()
        else:
            self._settings_overlay.setGeometry(self.rect())
            self._settings_overlay.open()

    def _reset_inactivity_timer(self) -> None:
        self._inactivity_timer.start(self.cfg.ui.inactivity_timeout_s * 1000)

    def _on_inactivity_timeout(self) -> None:
        if self._state is not BoothState.ATTRACT:
            self.transition_to(BoothState.ATTRACT)

    # -------------------------------------------------------------- shutdown

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._shutting_down = True
        # Stop the countdown's timers explicitly — otherwise an in-flight
        # pre_fire/tick can deliver a stray capture request during teardown.
        for widget in self._widgets.values():
            if hasattr(widget, "on_exit"):
                try:
                    widget.on_exit()
                except Exception:
                    pass
        if self._upload_daemon is not None:
            try:
                self._upload_daemon.stop()
            except Exception:
                pass
        if self._camera_worker is not None:
            try:
                self._camera_worker.stop()  # queued — worker will exit thread
            except Exception:
                pass
        if self._camera_thread is not None:
            # Worker calls thread.quit() from inside _cleanup_in_worker_thread,
            # so we just need to wait it out.
            self._camera_thread.wait(3000)
        # Wait briefly for any in-flight composite threads (plain daemon
        # threads — they'll die with the process either way).
        for job in list(self._composite_jobs):
            t = getattr(job, "_thread", None)
            if t and t.is_alive():
                t.join(2.0)
        self._composite_jobs.clear()
        super().closeEvent(event)

    # Hook for tests.
    def simulate_advance(self) -> None:
        self.advance()
