# cogchain

Shared contracts and interfaces for this Red-DiscordBot cog collection.

## Why

Several cogs (e.g., `langcore`, `mermaid`, `qdrant`, `spoilarr`) currently reach into each other with `getattr()` calls. That works but throws away the benefits of defined interfaces, makes refactors risky, and hides missing features until runtime. We need a single place to publish Abstractions/Contracts/Interfaces that cogs can agree on without creating hard references between plugins.

## What this package provides

- Common interface definitions and data structures that cogs can import.
- A neutral dependency target so cogs do not import each other directly.
- A path toward cleaner type-checking and safer refactors across the cog set.

## Project layout (keep it clean)

```
cogchain/
  README.md
  pyproject.toml
  cogchain/
    __init__.py
    interfaces/        # Abstractions / Interfaces / Protocols / ABCs
    models/            # Shared constants, types, and data models
    errors.py          # Exceptions / error contracts
```

The goal is to keep only interfaces, protocols, and shared models here—no implementation details. If a cog needs new cross-cog behavior, add or adjust the interface first, then implement it inside the individual cog.

## Local development

Install in editable mode while building or consuming the interfaces locally:

```bash
pip install -e .
```

Update interfaces here first, then adjust consuming cogs to use the shared definitions instead of `getattr()` glue.

## Publishing

The PyPI package will be published as `cogchain` once the interfaces stabilize. Until then, keep using the editable install for local dev and testing. When ready, bump the version, publish to PyPI, and update cogs to depend on the released package.
