# Tool Registration

LangCore’s ChainHub discovers extension cogs, registers their tool schemas with LangChain, and binds them to the active provider. This lifecycle keeps tool exposure declarative and reversible.

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

Tool callbacks accept an injected `ExtensionContext`, allowing providers and stores to be resolved per guild and tools to add messages back to the conversation safely. Unloading a cog cleanly unregisters its tool schemas, avoiding stale bindings.
