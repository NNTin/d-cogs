# Shared Interfaces (`cogchain`)

The `cogchain` package holds cross-cog contracts so providers, stores, and extension cogs can evolve without tight coupling. By importing common interfaces instead of each other’s modules, cogs stay independent while sharing stable protocols.

## Interface flow

```mermaid
flowchart LR
    subgraph PyPI Package
        CC[cogchain\n(interfaces + models)]
    end

    subgraph Core Cog
        LC[langcore]
    end

    subgraph Providers
        OR[openrouter]
        OL[ollama]
    end

    subgraph Stores
        QD[qdrant]
    end

    subgraph Extensions
        SP[spoilarr]
        MM[mermaid]
        EM[embed]
    end

    CC --> LC
    CC --> OR
    CC --> OL
    CC --> QD
    CC --> SP
    CC --> MM
    CC --> EM
```

## Decoupling strategy
- Interfaces, models, and error contracts ship in one neutral package.
- Cogs implement `ChainProvider`, `ChainStore`, or register tools via ChainHub without importing each other directly.
- Refactors happen by updating the interface first, then consumers, reducing runtime breakage from `getattr` glue.

See the package deep-dive in [core/cogchain.md](../core/cogchain.md) for project layout and development details.
