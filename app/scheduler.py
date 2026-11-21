import threading, time
from . import engine, inbox


def start():
    def loop():
        while True:
            try: inbox.poll(); engine.tick()
            except Exception as e: print("scheduler error:", e)
            time.sleep(3)
    threading.Thread(target=loop, daemon=True).start()
