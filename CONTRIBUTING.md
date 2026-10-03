# Contributing

Use Python 3.14.2 or newer and a sibling checkout of `Paskooter/phoenix`. Tests start disposable Home Assistant instances with synthetic light/switch platforms, scenes and scripts, and an isolated Phoenix Account/Gateway behind a locally trusted test TLS certificate. They never contact a household or a robot.

```sh
python3.14 -m venv .venv
.venv/bin/pip install homeassistant==2026.9.4 -r requirements-test.txt -r requirements-conversation-2026.9.4.txt
(cd ../phoenix && npm ci --ignore-scripts)
PHOENIX_SERVER_DIR=../phoenix .venv/bin/python -m pytest -q -s
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

For the minimum supported version, install `homeassistant==2026.8.1` with `requirements-conversation-2026.8.1.txt` in a fresh environment. Conversation dependencies match each release's official manifest. Without `PHOENIX_SERVER_DIR`, transport tests are skipped; that does not constitute full release validation. Use an absolute server path if your development layout differs. OpenSSL and Node 20 or newer must be on PATH.

Keep tokens, private operator notes, real household identifiers, robot captures, and Home Assistant storage out of Git. Security tests should use invented identities and disposable stores. Do not log bearer credentials, code values, or utterances from real homes.
