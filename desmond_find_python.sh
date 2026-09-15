#!/bin/bash
# Desmond — shared helper: find a REAL Python 3 on this Mac.
#
# Source this from a launcher (after cd-ing to the Desmond folder):
#     . "./desmond_find_python.sh" || exit 1
# On success it sets $PY to a working interpreter (3.8+ with sqlite3) and
# exports PYTHONUNBUFFERED / PYTHONIOENCODING so progress shows live and
# emoji in output can't crash under a plain C locale.
#
# Why this exists: on a fresh Mac /usr/bin/python3 is only a STUB that pops an
# "install developer tools?" dialog until the Command Line Tools are installed,
# and Finder-launched .command files get a minimal PATH where Homebrew /
# python.org installs are invisible. So we look in the usual places first,
# skip the stub, and probe each candidate before trusting it.

PY=""
for candidate in \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
    "$(command -v python3 2>/dev/null)"
do
    [ -n "$candidate" ] && [ -x "$candidate" ] || continue
    if [ "$candidate" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1; then
        continue   # the stub — running it would only pop a dialog
    fi
    if "$candidate" -c 'import sys, sqlite3; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done

if [ -z "$PY" ]; then
    echo "ERROR: a working Python 3 (3.8 or newer) was not found on this Mac."
    echo ""
    echo "Fix it in one of these ways, then run this again:"
    echo "  1) Open Terminal and run:  xcode-select --install"
    echo "     (installs Apple's Command Line Tools, which include python3)"
    echo "  2) Or install Python from https://www.python.org/downloads/macos/"
    echo ""
    return 1 2>/dev/null || exit 1
fi

export PYTHONUNBUFFERED=1      # live progress even when output is piped to a log
export PYTHONIOENCODING=utf-8  # emoji/accents in output never raise under a C locale
export PY
