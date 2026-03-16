#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import time
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / 'shared' / '.env')
load_dotenv()

def send_telegram_message(bot_token, chat_id, message, repeat_count, interval):
    try:
        import requests
        
        for i in range(repeat_count):
            try:
                requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": message,
                        "disable_web_page_preview": False
                    },
                    timeout=5
                )
            except:
                pass
            
            if i < repeat_count - 1:
                time.sleep(interval)
    except:
        pass

if __name__ == "__main__":
    if len(sys.argv) < 6:
        sys.exit(1)
    
    bot_token = sys.argv[1]
    chat_id = sys.argv[2]
    product = sys.argv[3]
    sizes = sys.argv[4]
    url = sys.argv[5]
    
    repeat_count = int(os.getenv("TELEGRAM_REPEAT_COUNT", "5"))
    interval = float(os.getenv("TELEGRAM_INTERVAL", "3.0"))
    
    message = f"🔔 재고 알림!\n\n상품: {product}\n사이즈: {sizes}\n\n{url}"
    
    send_telegram_message(bot_token, chat_id, message, repeat_count, interval)

