"""Linux用の起動・停止。PIDの再利用を検出し、他プロセスの停止を防ぐ。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data"
PID_FILE = STATE / "server-process.json"
LOCK_FILE = STATE / "server-process.lock"
LOG_FILE = STATE / "bot.log"


def process_info(pid: int, proc_root: Path = Path("/proc")) -> dict | None:
    if pid <= 1:
        return None
    try:
        directory = proc_root / str(pid)
        # commに空白や括弧を含むプロセス名にも対応する。
        tail = (directory / "stat").read_text().rsplit(")", 1)[1].split()
        argv = [part.decode("utf-8", "surrogateescape") for part in (directory / "cmdline").read_bytes().split(b"\0") if part]
        boot = (proc_root / "sys/kernel/random/boot_id").read_text().strip()
        return {"pid": pid, "start_ticks": tail[19], "boot_id": boot, "argv": argv}
    except (OSError, IndexError, UnicodeError):
        return None


def same_process(record: dict, actual: dict | None) -> bool:
    if actual is None:
        return False
    try:
        argv = actual["argv"]
        return (
            record["pid"] == actual["pid"]
            and record["start_ticks"] == actual["start_ticks"]
            and record["boot_id"] == actual["boot_id"]
            and Path(argv[0]).resolve() == Path(record["python"]).resolve()
            and argv[1:5] == ["-s", "-m", "remind_bot", "run"]
        )
    except (KeyError, TypeError, IndexError):
        return False


def take_lock(descriptor: int) -> bool:
    if sys.platform == "win32":
        raise RuntimeError("起動・停止の制御は学校サーバーのLinuxで実行してください。")
    import fcntl
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def record_process(pid: int):
    actual = process_info(pid)
    if actual is None:
        raise RuntimeError("起動したプロセスを確認できません。data/bot.logを確認してください。")
    record = {key: actual[key] for key in ("pid", "start_ticks", "boot_id")}
    record["python"] = str(Path(sys.executable).resolve())
    temporary = PID_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(PID_FILE)


def load_record() -> dict:
    try:
        record = json.loads(PID_FILE.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or type(record.get("pid")) is not int:
            return {}
        return record
    except (OSError, ValueError):
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("start", "stop", "status", "run"))
    args = parser.parse_args()
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        available = take_lock(descriptor)
        if args.command in {"start", "run"}:
            if not available:
                print("Botは既に起動中です。./bot.sh status で確認してください。")
                return 1
            command = [sys.executable, "-s", "-m", "remind_bot", "run"]
            if args.command == "run":
                os.set_inheritable(descriptor, True)
                record_process(os.getpid())
                os.execv(sys.executable, command)
            with LOG_FILE.open("ab", buffering=0) as output:
                process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True, pass_fds=(descriptor,))
            time.sleep(1)
            if process.poll() is not None:
                print("起動に失敗しました。data/bot.logを確認してください。", file=sys.stderr)
                return 1
            record_process(process.pid)
            print(f"Botをバックグラウンドで開始しました（PID {process.pid}）。")
            print("接続結果は data/bot.log の「Discord に接続しました」で確認してください。")
            return 0
        if available:
            print("Botは停止中です。")
            return 1 if args.command == "status" else 0
        record = load_record()
        if not same_process(record, process_info(record.get("pid", 0))):
            print("起動ロックは使用中ですが、BotのPIDを確認できません。自動停止は行いません。", file=sys.stderr)
            return 2
        if args.command == "status":
            print(f"Botは稼働中です（PID {record['pid']}）。接続状態はdata/bot.logで確認してください。")
            return 0
        os.kill(record["pid"], signal.SIGINT)
        for _ in range(100):
            time.sleep(0.1)
            if take_lock(descriptor):
                PID_FILE.unlink(missing_ok=True)
                print("Botを停止しました。")
                return 0
        print("停止処理が続いています。data/bot.logとstatusを確認してください。", file=sys.stderr)
        return 1
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        raise SystemExit(1)
