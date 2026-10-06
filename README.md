# Lark

An open source, self-hosted personal assistant. Chat with a model of your choice, through OpenRouter or Vercel AI Gateway.

Early days: chat and settings work. Memory, Telegram and the VM are next.

## Run it

```sh
cd web && npm install && npm run build
cd ../server && pip install -r requirements.txt
python -m uvicorn lark.main:app --host 127.0.0.1 --port 8000
```

Open http://localhost:8000, go to Settings, and paste an API key.

For development, run the command above and `npm run dev` in `web/` (it proxies `/api` to port 8000).

## Security

- API keys are encrypted with Fernet and stored in `data/`. They are never sent back to the browser.
- The encryption key lives in `data/secret.key`, or set `LARK_SECRET_KEY` to keep it off the disk.
- Without `LARK_PASSWORD` the app only answers on `localhost`. To put it on a server, set `LARK_PASSWORD`, serve it over HTTPS and set `LARK_SECURE_COOKIE=1`.

## Tests

```sh
python server/tests/test_api.py
```

## License

MIT
