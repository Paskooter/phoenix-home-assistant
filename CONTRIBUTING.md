# Contributing

Use Python 3.14.2 or newer. Tests run disposable Home Assistant instances with invented lights, switches, scenes, scripts, areas, and identities. An independent TLS robot fixture exercises physical pairing, certificate pinning, protocol requests, telemetry, and lifecycle handling. Tests never contact a household or a physical robot.

```sh
python3.14 -m venv .venv
.venv/bin/pip install homeassistant==2026.9.4 -r requirements-test.txt -r requirements-controls.txt -r requirements-conversation-2026.9.4.txt
.venv/bin/python -B -m pytest -q -s
.venv/bin/ruff check custom_components tests
.venv/bin/ruff format --check custom_components tests
```

For the minimum supported version, install `homeassistant==2026.8.1` with `requirements-conversation-2026.8.1.txt` in a fresh environment. Conversation dependencies match each release's official manifest. OpenSSL is required for disposable test certificates. Node 22 can check `tests/direct_node_backend.cjs` syntax.

The optional Jev tests use the public [Jev fork](https://github.com/Paskooter/ha-conversation-jev), release `v0.3.0b1`. Install its `requirements-dev.txt` and set `JEV_PROJECT_DIR` to that checkout when running pytest. The actual SDK runs with intercepted provider HTTP; these tests make no paid inference calls. CI runs this coverage on both Home Assistant versions.

The local release gate additionally tests the actual private robot endpoint on the official Node 6.5.0 runtime. Set `PHOENIX_DIRECT_MODULE` to the reviewed `phoenix-local-home-server.js`, `PHOENIX_DIRECT_NODE_BINARY` to that runtime, and `PHOENIX_DIRECT_MODULE_SHA256` to its reviewed hash. Run the whole suite with those variables and `JEV_PROJECT_DIR`. Without both private paths, two endpoint tests are skipped: public CI needs no private firmware, but that skip is insufficient for a robot release. Do not retrieve or copy BE source into this repository.

Keep tokens, private operator notes, household identifiers, robot captures, and Home Assistant storage out of Git. Use invented identities and disposable stores. Do not log pairing credentials, displayed codes, or utterances from real homes. Hardware acceptance and firmware archive integrity are separate release gates; passing isolated tests does not establish either.
