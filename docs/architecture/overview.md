# Architecture Overview

The d-cogs collection layers shared interfaces, a core orchestration cog, and pluggable providers/stores/extensions. Cross-cog contracts live in `cogchain`, while LangCore manages conversations, tool registration, and coordination with provider/store cogs.

## Shared interfaces via `cogchain`

Cross-cog contracts live in the `cogchain` package so features can evolve without tight coupling. Langcore, providers, stores, and extension cogs all import the same interfaces instead of reaching into each other.

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

## LangCore Cog architecture and ChainHub integration

LangCore provides the agent orchestration, conversation management, and tool registration pipeline. Conversation and classifier managers are separate agents; extension cogs register tools through ChainHub.

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

ConversationManager is the main agent, while the ClassifierManager is a background agent that can gate engagement. Sub-agents (e.g., MermaidManager) handle specialized tool loops without sharing context.

## LangChain PyPI package interaction and extension model

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

## Tool and function registration lifecycle in LangCore

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

Each diagram reflects how cogs communicate through interfaces, how ChainHub manages tool registration, and how LangChain packages supply the underlying LLM and vector store abstractions.
