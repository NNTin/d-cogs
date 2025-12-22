# LangCore Integration

LangCore orchestrates AI agents, tool registration, and provider/store abstraction. It exposes `ChainProvider`, `ChainStore`, and `MessageHandler` interfaces that other cogs implement or consume.

## ChainHub integration

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

ConversationManager is growing in complexity; future work will abstract it so alternative managers can be swapped in. The same applies to ClassifierManager. Managers are agents and do not share context. Discord users interact with the ConversationManager; the ClassifierManager can gate responses. Background agents like AI Defender and sub-agents like Mermaid Manager operate independently, only engaging when their capabilities are needed.

## Agent patterns
- **ConversationManager**: main agent coordinating tool calls and responses.
- **ClassifierManager**: optional background agent that decides if the conversation agent should engage.
- **Sub-agents**: specialized managers (e.g., MermaidManager) that run isolated tool loops but can add context back to conversations.

LangCore links these agents through ChainHub so extension cogs can register tools without tight coupling. For extension guidance, see [core/extension-guide.md](../core/extension-guide.md).
