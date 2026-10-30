# -*- coding: utf-8 -*-
"""VHS Studio — оцифровка VHS через Rombica Pro Studio."""

__version__ = "1.3.0"

from .capture import AudioMonitor, Preview, Recorder          # noqa: E402,F401
from .commands import (build_filters, codec_args, dshow_args,  # noqa: E402,F401
                       output_ext, preview_size)
from .config import DEFAULTS, CODECS, AUDIO_CODECS             # noqa: E402,F401
from .estimates import (estimate_gb_per_hour, estimate_mbps,    # noqa: E402,F401
                       fmt_gb, human_size, hms)
from .device import FFMPEG, FFPLAY, list_dshow_devices          # noqa: E402,F401
from .ui import App                                            # noqa: E402,F401
