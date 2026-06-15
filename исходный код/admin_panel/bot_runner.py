import threading
from main import run_bot

_bot_thread = None

def start_bot(token: str):
    global _bot_thread

    if _bot_thread and _bot_thread.is_alive():
        print("Бот уже запущен")
        return

    _bot_thread = threading.Thread(
        target=run_bot,
        args=(token,),
        daemon=True
    )
    _bot_thread.start()