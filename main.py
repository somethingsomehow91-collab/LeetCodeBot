import os
import subprocess
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI

bot_process = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_process
    
    bot_process = subprocess.Popen([sys.executable, "telegram_bot.py"])
    print("Started telegram_bot.py in background...")
    
    yield
    
    if bot_process:
        bot_process.terminate()
        print("Stopped telegram_bot.py.")

app = FastAPI(lifespan=lifespan)

@app.get("/")
def read_root():
    return {"status": "FastAPI app is running"}

@app.head("/health")
def health_check():
    return {"status": "ok"}
