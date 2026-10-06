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
- Without `LARK_PASSWORD` the app only answers on `localhost`. To put it on a server, set `LARK_PASSWORD`, serve it over HTTPS and set `LARK_SECURE_COOKIE=1`. Five wrong passwords in ten minutes pause logins for five minutes.

## Deploy

On a Linux server with Python 3.11+, Node 22 and nginx. The files in `deploy/` are templates.

```sh
useradd --system --home /var/lib/lark --shell /usr/sbin/nologin lark
git clone https://github.com/omni-gh549/lark /opt/lark
cd /opt/lark/web && npm ci && npm run build
cd ../server && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

install -d -m 700 /etc/lark
printf 'LARK_PASSWORD=%s\nLARK_SECURE_COOKIE=1\nLARK_DATA=/var/lib/lark\n' "$(openssl rand -base64 24 | tr -d '+/=')" > /etc/lark/lark.env
chmod 600 /etc/lark/lark.env

cp /opt/lark/deploy/lark.service /etc/systemd/system/ && systemctl enable --now lark
```

Lark listens on `127.0.0.1:8790`. Put nginx in front (`deploy/nginx.conf`) and get a certificate with `certbot --nginx`. Until then, reach it with `ssh -L 8790:localhost:8790 your-server`.

To update: `git pull`, rebuild `web`, `systemctl restart lark`. Keys and settings are in `/var/lib/lark`; back that up.

## Tests

```sh
python server/tests/test_api.py
```

## License

MIT
