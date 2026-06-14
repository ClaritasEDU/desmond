#!/usr/bin/env python3
"""
Desmond — run logger.

Writes a PII-safe log of each run (a human-readable .log and a structured .json)
that you can share back to refine how the product behaves. It records counts,
timings, environment, and any errors — but **never** message text or contact
names, and it redacts your home path, Google Drive account, and email addresses.
"""

import json
import os
import platform
import re
import time
import traceback
from datetime import datetime

_HOME = os.path.expanduser("~")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_GDRIVE = re.compile(r"GoogleDrive-[^/\\]+")


def sanitize(value):
    """Strip personal bits from a string so the log is safe to share."""
    if not isinstance(value, str):
        return value
    s = value
    if _HOME and _HOME not in ("/", ""):
        s = s.replace(_HOME, "~")
    s = _GDRIVE.sub("GoogleDrive-<account>", s)
    s = _EMAIL.sub("[email]", s)
    return s


class RunLogger:
    """Lightweight structured logger. Use as a context manager or call close()."""

    def __init__(self, tool, log_dir=None):
        self.tool = tool
        self.started = time.time()
        self.events = []
        self.metrics = {}
        self.errors = []
        log_dir = log_dir or os.path.expanduser("~/Downloads/Desmond_Logs")
        os.makedirs(log_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.join(log_dir, f"{tool}_{stamp}")
        self.txt_path = base + ".log"
        self.json_path = base + ".json"
        self.env = {
            "tool": tool,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "started": datetime.now().isoformat(timespec="seconds"),
        }
        self._txt = open(self.txt_path, "w", encoding="utf-8")
        self.log("info", f"{tool} started",
                 python=self.env["python"], platform=self.env["platform"])

    def log(self, level, msg, **fields):
        ts = datetime.now().isoformat(timespec="seconds")
        clean = {k: sanitize(v) for k, v in fields.items()}
        rec = {"ts": ts, "level": level, "msg": msg, **clean}
        self.events.append(rec)
        line = f"[{ts}] {level.upper():6} {msg}"
        if clean:
            line += " | " + " ".join(f"{k}={v}" for k, v in clean.items())
        try:
            self._txt.write(line + "\n")
            self._txt.flush()
        except Exception:
            pass
        if level == "error":
            self.errors.append(rec)
        return rec

    def metric(self, **kv):
        self.metrics.update({k: sanitize(v) for k, v in kv.items()})
        return self.log("metric", "metrics", **kv)

    def phase(self, name):
        return _Phase(self, name)

    def exception(self, msg="exception"):
        tb = traceback.format_exc()
        rec = self.log("error", msg)        # recorded once (events + errors)
        rec["traceback"] = sanitize(tb)
        try:
            self._txt.write(sanitize(tb) + "\n")
        except Exception:
            pass

    def close(self, status="ok", **summary):
        seconds = round(time.time() - self.started, 2)
        self.log("info", f"{self.tool} finished", status=status, seconds=seconds)
        payload = {
            "environment": self.env,
            "status": status,
            "seconds": seconds,
            "metrics": self.metrics,
            "summary": {k: sanitize(v) for k, v in summary.items()},
            "error_count": len(self.errors),
            "errors": self.errors,
            "events": self.events,
        }
        try:
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            self._txt.write(f"\nStructured log: {sanitize(self.json_path)}\n")
            self._txt.close()
        except Exception:
            pass
        return self.json_path

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.exception("uncaught exception")
            self.close(status="error")
        return False


class _Phase:
    def __init__(self, logger, name):
        self.logger = logger
        self.name = name

    def __enter__(self):
        self.t = time.time()
        self.logger.log("info", f"phase start: {self.name}")
        return self

    def __exit__(self, exc_type, exc, tb):
        seconds = round(time.time() - self.t, 2)
        if exc_type:
            self.logger.exception(f"phase failed: {self.name}")
        else:
            self.logger.log("info", f"phase done: {self.name}", seconds=seconds)
        return False
