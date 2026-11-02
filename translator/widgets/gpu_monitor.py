"""GPU monitor panel — polls nvidia-smi for VRAM, utilization, temp, power."""

import logging

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar
from PyQt6.QtCore import QProcess, Qt, QTimer

from . import theme

log = logging.getLogger(__name__)

_QUERY = "name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw"
_CMD = [
    "nvidia-smi",
    f"--query-gpu={_QUERY}",
    "--format=csv,noheader,nounits",
]


class GPUMonitorPanel(QWidget):
    """Compact GPU stats panel that auto-updates via nvidia-smi."""

    def __init__(self, parent=None, poll_ms: int = 2000):
        super().__init__(parent)
        self._available = False
        self._ever_available = False
        self._disabled = False
        self._temp = None
        self._build_ui()
        self._proc = QProcess(self)
        self._proc.finished.connect(self._on_proc_finished)
        self._proc.errorOccurred.connect(self._on_proc_error)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(poll_ms)
        # First poll happens in showEvent once the panel is visible

    def _build_ui(self):
        self.setObjectName("gpuMonitor")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # GPU name
        self._name_label = QLabel("GPU: detecting...")
        self._name_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self._name_label)

        # Slim bars with the reading beside the caption, never on the fill
        self._vram_label, self._vram_value, self._vram_bar =             self._add_meter(layout, "VRAM")
        self._util_label, self._util_value, self._util_bar =             self._add_meter(layout, "GPU")

        # Temp + Power row
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self._temp_label = QLabel("Temp: --")
        stats_row.addWidget(self._temp_label)
        self._power_label = QLabel("Power: --")
        self._power_label.setObjectName("hint")
        stats_row.addWidget(self._power_label)
        stats_row.addStretch()
        layout.addLayout(stats_row)

    @staticmethod
    def _add_meter(layout, caption: str):
        row = QHBoxLayout()
        label = QLabel(caption)
        label.setObjectName("hint")
        value = QLabel("")
        value.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(label)
        row.addStretch()
        row.addWidget(value)
        layout.addLayout(row)
        bar = QProgressBar()
        bar.setObjectName("meter")
        bar.setRange(0, 100)
        bar.setFixedHeight(6)
        bar.setTextVisible(False)
        layout.addWidget(bar)
        return label, value, bar

    def _poll(self):
        """Start an async nvidia-smi query (never blocks the GUI thread)."""
        if not self.isVisible():
            return  # Nothing to show — skip the subprocess entirely
        if self._proc.state() != QProcess.ProcessState.NotRunning:
            return  # Previous query still running
        self._proc.start(_CMD[0], _CMD[1:])

    def _on_proc_error(self, error):
        """nvidia-smi missing or crashed."""
        if error == QProcess.ProcessError.FailedToStart:
            # No nvidia-smi on this machine — hide the panel for good
            self._disable()
        elif error == QProcess.ProcessError.Crashed and self._available:
            self._set_unavailable()

    def _on_proc_finished(self, exit_code, exit_status):
        """Parse nvidia-smi output and update display."""
        if exit_status != QProcess.ExitStatus.NormalExit:
            return  # errorOccurred handles crashes
        output = bytes(self._proc.readAllStandardOutput()).decode(
            "utf-8", errors="replace")
        if exit_code != 0:
            if self._available:
                self._set_unavailable()  # transient failure — keep the panel
            elif not self._ever_available:
                self._disable()  # nvidia-smi present but no usable NVIDIA GPU
            return
        try:
            line = output.strip().split("\n")[0]
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                return

            name = parts[0]
            mem_used = float(parts[1])
            mem_total = float(parts[2])
            gpu_util = int(float(parts[3]))
            temp = int(float(parts[4]))
            power = float(parts[5])
        except ValueError:
            return

        self._available = True
        self._ever_available = True
        self._name_label.setText(f"GPU: {name}")

        # VRAM
        mem_pct = int(mem_used / mem_total * 100) if mem_total > 0 else 0
        self._vram_bar.setValue(mem_pct)
        self._vram_value.setText(
            f"{mem_used / 1024:.1f} / {mem_total / 1024:.1f} GB  ({mem_pct}%)"
        )
        self._color_bar(self._vram_bar, mem_pct)

        # Utilization
        self._util_bar.setValue(gpu_util)
        self._util_value.setText(f"{gpu_util}%")
        self._color_bar(self._util_bar, gpu_util)

        # Temp + Power
        self._temp_label.setText(f"Temp: {temp}\u00b0C")
        self._temp = temp
        self._color_temp()

        self._power_label.setText(f"Power: {power:.0f}W")

    def showEvent(self, event):
        """Poll immediately when the panel becomes visible."""
        super().showEvent(event)
        if self._timer.isActive():
            self._poll()

    def _disable(self):
        """No NVIDIA GPU / nvidia-smi: stop polling and hide the panel."""
        self._disabled = True
        self._timer.stop()
        self._set_unavailable()
        self.hide()

    def setVisible(self, visible: bool):
        # Never come back (e.g. a parent re-showing children) once disabled
        super().setVisible(visible and not self._disabled)

    def _set_unavailable(self):
        """Mark GPU as unavailable."""
        self._available = False
        self._name_label.setText("GPU: not detected")
        self._vram_bar.setValue(0)
        self._vram_value.setText("N/A")
        self._util_bar.setValue(0)
        self._util_value.setText("N/A")
        self._temp_label.setText("Temp: --")
        self._power_label.setText("Power: --")
        self._temp = None
        self._color_temp()

    def _color_temp(self):
        temp = getattr(self, "_temp", None)
        if temp is None:
            self._temp_label.setStyleSheet("")
            return
        token = "error" if temp >= 80 else "caution" if temp >= 65 else "ok"
        self._temp_label.setStyleSheet(
            f"color: {theme.c(token)};")

    def apply_theme(self):
        """Re-color bars/labels after a dark/light switch."""
        self._color_bar(self._vram_bar, self._vram_bar.value())
        self._color_bar(self._util_bar, self._util_bar.value())
        self._color_temp()

    @staticmethod
    def _color_bar(bar: QProgressBar, pct: int):
        """Color the progress bar based on percentage (theme palette)."""
        if pct >= 90:
            token = "error"
        elif pct >= 70:
            token = "caution"
        elif pct >= 50:
            token = "warn"
        else:
            token = "ok"
        bar.setStyleSheet(
            f"QProgressBar::chunk {{ background: {theme.c(token)}; "
            f"border-radius: 3px; }}")

    @property
    def is_available(self) -> bool:
        """Whether an NVIDIA GPU was detected."""
        return self._available
