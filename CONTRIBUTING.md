# Contributing to Hexis

Thank you for wanting to make Hexis better. The full contributor guide lives in
the docs:

- **[Contributing overview](docs/contributing/index.md)** — dev setup, project
  layout, coding style, commit conventions
- **[Testing](docs/contributing/testing.md)** — running and writing tests
  (`pytest tests -q` against the Docker stack)
- **[Repository guidelines](CLAUDE.md)** — architecture principles, schema
  migration workflow, and the Experience Bar every user-facing change is held to

Quick version:

```bash
git clone https://github.com/QuixiAI/Hexis.git && cd Hexis
pip install -e . && cp .env.local .env
hexis up && hexis doctor
pytest tests -q
```

Schema changes go through forward-only migrations (`db/migrations/NNNN_*.sql` +
`hexis migrate`) — never ask users to wipe their agent's memories. Bugs and
ideas: [GitHub Issues](https://github.com/QuixiAI/Hexis/issues) /
[Discussions](https://github.com/QuixiAI/Hexis/discussions).
