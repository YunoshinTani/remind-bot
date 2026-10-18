#!/bin/sh
set -eu
umask 077
TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
TASK_PYTHON="$TASK_ROOT/python/bin/python3"
if [ ! -x "$TASK_PYTHON" ]; then
    echo "Bundled Python is not executable: $TASK_PYTHON" >&2
    exit 1
fi
unset PYTHONHOME
export PYTHONPATH="$TASK_ROOT/vendor:$TASK_ROOT"
export PYTHONNOUSERSITE=1
export PYTHONUTF8=1
export PYTHONUNBUFFERED=1
export SSL_CERT_FILE="$TASK_ROOT/vendor/certifi/cacert.pem"
cd "$TASK_ROOT"
if [ "$#" -eq 0 ]; then
    echo "Usage: ./bot.sh doctor|start|stop|status|run|check|preview|members|test|unit-tests|version"
    exit 2
fi
case "$1" in
    start|stop|status|run)
        exec "$TASK_PYTHON" -s "$TASK_ROOT/deployment/control.py" "$@"
        ;;
    doctor)
        shift
        exec "$TASK_PYTHON" -s "$TASK_ROOT/deployment/doctor.py" "$@"
        ;;
    version)
        exec "$TASK_PYTHON" --version
        ;;
    unit-tests)
        exec "$TASK_PYTHON" -s -m unittest discover -s tests -v
        ;;
    *)
        exec "$TASK_PYTHON" -s -m remind_bot "$@"
        ;;
esac
