import threading, time
from . import engine, inbox, master, autopilot


def start():
    def loop():
        while True:
            try: master.check_reload(); inbox.poll(); engine.tick(); autopilot.tick()
            except Exception as e: print("scheduler error:", e)
            time.sleep(3)
    threading.Thread(target=loop, daemon=True).start()
