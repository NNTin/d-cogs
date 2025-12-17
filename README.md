# AI automation cogs

Inspiration has been drawn from assistant (see `Synced Cogs Notice`), however since I am navigating "unused" code (OpenAI is ignored) too much I'll re-invent the wheel. Most of my changes are towards Ollama. Thus working with the original assistant cog has been quite a challenge.  
Huge thanks goes out towards [vertyco](http://github.com/vertyco/). While working with assistant I have learned quite a few things which makes me comfortable starting my own version.

## Architecture

```mermaid
graph TD
    subgraph CogManager
        LangCore
        ProviderCog
        StorageCog
        ChainHubCogs
    end

    %% Core framework
    subgraph LangCore[langcore: Cog uses LangChain Framework]
        ChainHubManager[ChainHubManager]
        ConversationManager
        ChainStore[ChainStore Abstraction]
        ChainProvider[ChainProvider Abstraction]
    end

    subgraph ConversationManager[ConversationManager]
        langchainModule[PyPI: langchain]
        conversation[conversation.py]
        CPCInstance[langcore.get_provider]
    end

    %% ChainHub cogs
    subgraph ChainHubCogs[ExtensionCogs for tools/functions]
        subgraph RagCogSub[RagCog: ragutils]
            Rag[ragutils]
            CPRInstance[langcore.get_provider]
            CSRInstance[langcore.get_store]
        end

        subgraph MemoryCogSub[MemoryCog: memory]
            Memory[memory]
            CPMInstance[langcore.get_provider]
            CSMInstance[langcore.get_store]
        end

        subgraph MermaidCogSub[MermaidCog: mermaid]
            Mermaid[mermaid]
            CPMeInstance[langcore.get_provider]
        end
    end

    %% Provider cog
    subgraph ProviderCog[ollama: Cog]
        Ollama[ollama]
        backendOllama[localhost:11434]
        ollamaModule[PyPI: langchain-ollama]
    end

    %% Storage cog
    subgraph StorageCog[qdrant: Cog]
        QDrant[qdrant]
        backendQdrant[localhost:6333]
        qdrantModule[PyPI: langchain-qdrant]
    end

    %% Connections
    LangCore -->|implements abstraction| ChainStore
    LangCore -->|implements abstraction| ChainProvider
    ChainProvider -->|implemented by| ProviderCog
    ChainStore -->|implemented by| StorageCog

    subgraph ChainHubManager
        hub.py
        langchainModule2[PyPI: langchain]
    end

    ChainHubManager -->|for each langcore-compatible cog registers functions| ChainHubCogs

    
    %% Define styles
    classDef PyPI fill:#ffedb3,stroke:#c88a12,stroke-width:2px,color:#000;
    classDef chainStore fill:#e3f5ee,stroke:#2f8f6b,stroke-width:2px,color:#000;
    classDef chainProvider fill:#eef2ff,stroke:#4c63d2,stroke-width:2px,color:#000;



    %% Apply styles
    class langchainModule,langchainModule2,ollamaModule,qdrantModule PyPI;
    class ChainStore,CSRInstance,CSMInstance chainStore;
    class ChainProvider,CPMInstance,CPMeInstance,CPCInstance chainProvider;
```

```mermaid
graph TD
    %% PyPI package interaction graph

    subgraph LangChainCore[PyPI: langchain]
        LCChains[Chains]
        LCLLM[LLM Abstractions / ChainProvider]
        LCEmbeddings[Embeddings]
        LCVectorStores[VectorStore Interface / ChainStore]
    end

    subgraph OllamaPkg[PyPI: langchain-ollama]
        OllamaLLM[Ollama LLM Wrapper]
        OllamaEmb[Ollama Embeddings]
    end

    subgraph QdrantPkg[PyPI: langchain-qdrant]
        QdrantVS[Qdrant VectorStore]
        QdrantClient[qdrant-client]
    end

    %% Relationships
    OllamaPkg -->|extends| LangChainCore
    QdrantPkg -->|extends| LangChainCore

    OllamaLLM -->|implements| LCLLM
    OllamaEmb -->|implements| LCEmbeddings

    QdrantVS -->|implements| LCVectorStores
    QdrantVS -->|uses| QdrantClient

    %% Typical runtime flow
    LCChains -->|calls| LCLLM
    LCChains -->|stores/retrieves| LCVectorStores

    %% Styling
    classDef pypi fill:#ffedb3,stroke:#c88a12,stroke-width:2px,color:#000;
    class LangChainCore,OllamaPkg,QdrantPkg pypi;
```

## Red-DiscordBot Cogs Overview

### 1. langcore (Cog)
`langcore` is the **core framework cog** for the bot, built on top of the LangChain framework. It provides the foundational abstractions for AI agent orchestration:  

- **ChainProvider Abstraction**: Defines a standard interface for LLM providers.  
- **ChainStore Abstraction**: Defines a standard interface for vector storage and retrieval.  
- **ChainHub**: A registry for functions and tools that AI agents can access.  

All other cogs connect to `langcore` either by implementing its abstractions or registering functionality via ChainHub.

---

### 2. ollama (Cog)

`ollama` is the **ChainProvider implementation cog**.  

- Acts as the LLM backend for AI agents.  
- Implements the `ChainProvider` abstraction from `langcore`, enabling agents to query large language models.  
- Connects to an LLM service, e.g., `localhost:11434`.  

This cog allows agents to generate natural language responses and perform model-based reasoning.

---

### 3. qdrant (Cog)
`qdrant` is the **vector storage cog** and implements the `ChainStore` abstraction from `langcore`.  

- Provides persistent vector storage for embeddings.  
- Connects to a Qdrant service, e.g., `localhost:6333`.  

This cog serves as the AI agents’ long-term memory backend.

---

### 4. ragutils (Cog)
`rag` is a **retrieval-augmented generation (RAG) cog**:  

- Registers functions via `ChainHub` like `memory`.  
- Uses `ChainProvider` and `ChainStore` instances to enable agents to perform **retrieval-augmented reasoning**.  
- Combines vector-based memory search with LLM responses, enhancing answer accuracy and relevance by grounding generation in stored knowledge.

---

## Synced Cogs Notice

This repository mirrors two cogs that were authored elsewhere. **Please install them directly from their source repositories instead of from this mirror**.

- `assistant` — sourced from [`vertyco/vrt-cogs`](https://github.com/vertyco/vrt-cogs) (branch `main`). 
- `hotreload` — sourced from [`cswimr/SeaCogs`](https://c.csw.im/cswimr/SeaCogs) (branch `main`). 

These copies are synced for convenience and are not my creations. Please support and credit the original authors when using their cogs.

Huge thanks to [cswimr](https://c.csw.im/cswimr) and [vertyco](https://github.com/vertyco) for allowing me to mirror their work!