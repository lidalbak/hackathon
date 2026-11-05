import argparse

import uvicorn

parser = argparse.ArgumentParser(description='Airbnb-style inbox with a read/write API')
parser.add_argument('--host', default='127.0.0.1', help='use 0.0.0.0 only behind a token (CHAT_API_TOKEN)')
parser.add_argument('--port', type=int, default=8766)
args = parser.parse_args()
# Open event streams would otherwise keep Ctrl+C waiting.
uvicorn.run('airbnb_chat.server:app', host=args.host, port=args.port, timeout_graceful_shutdown=1)
