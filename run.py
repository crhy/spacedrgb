#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / ".vendor"))
log_dir = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "spaced-rgb"
log_dir.mkdir(parents=True, exist_ok=True)
handler = RotatingFileHandler(log_dir / "app.log", maxBytes=2 * 1024 * 1024, backupCount=3)
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[handler])
logging.info("Spaced RGB starting (pid=%s)", os.getpid())
from spaced_rgb.app import main
raise SystemExit(main())
