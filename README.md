# AI automation cogs

Inspiration has been drawn from assistant (see `Synced Cogs Notice`), however since I am navigating "unused" code (OpenAI is ignored) too much I'll re-invent the wheel. Most of my changes are towards Ollama. Thus working with the original assistant cog has been quite a challenge.  
Huge thanks goes out towards [vertyco](http://github.com/vertyco/). While working with assistant I have learned quite a few things which makes me comfortable starting my own version.

## Architecture

### LangCore Cog Architecture and ChainHub Integration
```mermaid
graph TD
    subgraph CogManager
        LangCore
        ProviderCog
        StorageCog
        ChainHubCogs
    end

    %% Core framework
    subgraph LangCore[langcore: Cog]
        ChainHubManager[ChainHubManager]
        ConversationManager
        ClassifierManager
        ChainStore[ChainStore Abstraction]
        ChainProvider[ChainProvider Abstraction]
        MessageHandler[MessageHandler Abstraction]
    end

    subgraph ConversationManager[ConversationManager: main agent]
        langchainModule[PyPI: langchain]
        conversation[conversation.py]
        CPCInstance[langcore.get_provider]
    end

    
    subgraph ClassifierManager[ClassifierManager: background agent]
        langchainModule3[PyPI: langchain]
        classifier[classifier.py]
        CPClInstance[langcore.get_provider]
    end

    %% ChainHub cogs
    subgraph ChainHubCogs[ExtensionCogs for tools/functions]
        subgraph SpoilarrCogSub[SpoilarrCog: spoilarr]
            SpoilarrAgent[SpoilarrManager: sub agent]
            Spoilarr[spoilarr]
            CPSpInstance[langcore.get_provider]
        end

        
        subgraph EmbedCogSub[EmbedCog: embed]
            Embed[embed]
            CPEmInstance[langcore.get_provider]
        end

        subgraph MemoryCogSub[MemoryCog: memory]
            Memory[memory]
            CPMInstance[langcore.get_provider]
            CSMInstance[langcore.get_store]
        end

        subgraph MermaidCogSub[MermaidCog: mermaid]
            MermaidAgent[MermaidManager: sub agent]
            Mermaid[mermaid]
            MermaidMessageHandler[MessageHandler: send image as sub agent]
            CPMeInstance[langcore.get_provider]
            CSMeInstance[langcore.get_store]
        end

        subgraph AiDefenderCogSub[AiDefenderCog: aidefender]
            AiDefenderAgent[AiDefenderManager: background agent]
            AIDefender[aidefender]
            CPAIInstance[langcore.get_provider]
            CSAIInstance[langcore.get_store]
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
        ragPipeline[RAG pipeline, formerely inside ragutils cog]
    end

    %% Connections
    LangCore -->|implements abstraction| ChainStore
    LangCore -->|implements abstraction| ChainProvider
    LangCore -->|implements abstraction| MessageHandler
    ChainProvider -->|1:n implemented by| ProviderCog
    ChainStore -->|0:1 implemented by| StorageCog

    subgraph ChainHubManager
        hub.py
        langchainModule2[PyPI: langchain]
    end

    ChainHubManager -->|for each langcore-compatible cog registers functions| ChainHubCogs
    ChainHubManager <-->|langchain linked| ConversationManager
    ConversationManager <-->|When enabled Classifier Agent controls Conversation Agent engagement| ClassifierManager
    
    %% Define styles
    classDef PyPI fill:#FFE08A,stroke:#B45309,stroke-width:2px,color:#1F2937;
    classDef chainStore fill:#B7F7D8,stroke:#047857,stroke-width:2.5px,color:#064E3B;
    classDef chainProvider fill:#C7D2FE,stroke:#3730A3,stroke-width:2.5px,color:#1E1B4B;
    classDef agent fill:#FED7AA,stroke:#C2410C,stroke-width:4px,color:#431407;
    classDef messageHandler fill:#FEE2E2,stroke:#F87171,stroke-width:2px,color:#7F1D1D;

    %% Apply styles
    class langchainModule,langchainModule2,langchainModule3,ollamaModule,qdrantModule PyPI;
    class ChainStore,CSMInstance,CSAIInstance,CSMeInstance chainStore;
    class ChainProvider,CPMeInstance,CPCInstance,CPEmInstance,CPClInstance,CPAIInstance,CPSpInstance chainProvider;
    class ConversationManager,ClassifierManager,MermaidAgent,AiDefenderAgent,SpoilarrAgent agent;
    class MessageHandler,MermaidMessageHandler messageHandler;
```

Note: ConversationManager is growing in complexity. In future worth abstracting it so the ConversationManager can be overwritten by other cogs. Same goes for ClassifierManager.

*Managers are agents and don't share the same context.  

Discord Users are talking with the main agent -> Conversation Manager. Conversations are are not shared between Discord Users and are unique to the Discord User.  
A background agent, the ClassifierManager, decides if the Conversation Manager should engage in the conversation. This agent is disabled by default.  
You can also define your own background agents in order to e.g. spot abusive behavior through AI -> AI Defender Manager.  
Cogs may also define sub agents. They only become active through other agents. For example a Mermaid image should be created but there is a syntax error. The agent will fix the syntax error.

### LangChain PyPI Package Interaction and Extension Model
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

### Tool and Function Registration Lifecycle in LangCore
```mermaid
graph TD
    %% Function / Tool Registration Lifecycle

    subgraph AppRuntime[Application Runtime]
        CHM[ChainHubManager]
        CM[ConversationManager]
    end

    subgraph LangChainCore[PyPI: langchain]
        ToolReg[Tool / Runnable Registry]
        ChainExec[Chain Execution Engine]
        ToolSchema[Tool Schemas & Signatures]
    end

    subgraph OllamaPkg[PyPI: langchain-ollama]
        OllamaLLM[LLM + Tool Calling Support]
    end

    subgraph QdrantPkg[PyPI: langchain-qdrant]
        QdrantVS[VectorStore Tools]
    end

    subgraph ExtensionCogs[LangCore-compatible Cogs]
        RagCog[RagCog]
        MemoryCog[MemoryCog]
        MermaidCog[MermaidCog]
    end

    %% Registration flow
    CHM -->|discovers & loads| ExtensionCogs
    ExtensionCogs -->|expose callable functions| CHM

    CHM -->|registers tools| ToolReg
    ToolReg -->|stores schema| ToolSchema

    %% Provider & model side
    OllamaLLM -->|requests tool schemas| ToolSchema
    CM -->|binds tools to model| OllamaLLM

    %% Runtime execution
    ChainExec -->|LLM selects tool| ToolReg
    ToolReg -->|dispatches call| ExtensionCogs
    ExtensionCogs -->|returns result| ChainExec

    %% Unregistration flow
    CHM -.->|unload / disable cog| ExtensionCogs
    CHM -.->|unregister tools| ToolReg
    ToolReg -.->|remove schemas| ToolSchema

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

In future `langcore.get_provider()` may be interesting when some LLM providers are unreliable -> return fallback providers.  
Or when you want to do load balancing accross your LLM providers.  

For BYOK users the ChainProvider implementation is easy, a simple endpoint with API key.  
For self-host hardware users you may want to define multiple endpoints.  
Or even combine both setup.

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

Add RAG pipeline. Fomerely implemented as a cog ragutils for assistant cog. This will move directly into qdrant to be a part of it.

---

## Synced Cogs Notice

This repository mirrors cogs that were authored elsewhere. **Please install them directly from their source repositories instead of from this mirror**.

- `hotreload` — sourced from [`cswimr/SeaCogs`](https://c.csw.im/cswimr/SeaCogs) (branch `main`). 

These copies are synced for convenience and are not my creations. Please support and credit the original authors when using their cogs.

- `assistant` — sourced from [`vertyco/vrt-cogs`](https://github.com/vertyco/vrt-cogs) (branch `main`). 

Used to be mirroed but is replaced by the cogs `langcore`, `ollama` and `qdrant`.

Huge thanks to [cswimr](https://c.csw.im/cswimr) and [vertyco](https://github.com/vertyco) for allowing me to mirror their work!