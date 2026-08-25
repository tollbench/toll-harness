# Contributing

Use Python 3.10 or newer. Install the development environment with:

```bash
python -m venv .venv
.venv/bin/pip install -e '.[aws,browser,dev]'
```

Run `pytest` and `ruff check .` before proposing a change. New provider adapters should
implement the existing contracts rather than add provider-specific behavior to the runtime.

