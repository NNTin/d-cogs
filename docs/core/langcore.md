# langcore Cog

`langcore` is the core framework cog for the bot, built on top of the LangChain framework. It provides the foundational abstractions for AI agent orchestration:

- **ChainProvider Abstraction**: Defines a standard interface for LLM providers.
- **ChainStore Abstraction**: Defines a standard interface for vector storage and retrieval.
- **ChainHub**: A registry for functions and tools that AI agents can access.

All other cogs connect to `langcore` either by implementing its abstractions or registering functionality via ChainHub. In future `langcore.get_provider()` may be interesting when some LLM providers are unreliable -> return fallback providers or handle load balancing.

For BYOK users the ChainProvider implementation is easy, a simple endpoint with API key. Self-hosted hardware users may define multiple endpoints or combine both setups.

## Conversation class architecture

```mermaid
sequenceDiagram
    participant CM as ConversationManager
    participant Conv as Conversation
    participant Lock as asyncio.Lock
    participant MM as MermaidManager
    participant MH as MessageHandler

    MM->>CM: get_conversation(member_id, channel_id, guild_id)
    CM-->>MM: Conversation instance
    
    MM->>CM: get_conversation_lock(member_id, channel_id, guild_id)
    CM-->>MM: Lock instance
    
    MM->>Lock: acquire()
    Lock-->>MM: acquired
    
    MM->>Conv: get_messages()
    Conv-->>MM: List[Dict] (copy)
    
    MM->>Conv: add_assistant_message(syntax)
    Conv->>Conv: append to messages
    Conv->>Conv: refresh()
    
    MM->>Lock: release()
    
    MM->>MH: send_file(ctx, discord.File)
    MH-->>MM: discord.Message
```

MessageHandler sends Discord-facing messages. Sub-agents like the Mermaid Manager can also send messages; the Conversation class keeps the main agent aware of the topic even when sub-agents act.

```mermaid
sequenceDiagram
    participant User as Discord User
    participant CM as ConversationManager
    participant LLM as Language Model
    participant Tool as generate_mermaid
    participant MM as MermaidManager
    participant DC as Discord Channel
    participant Conv as Conversation

    User->>CM: "[p]chat create sequence diagram..."
    CM->>Conv: add user message
    CM->>LLM: invoke with tools
    LLM->>Tool: call generate_mermaid(description, diagram_type)
    Note over Tool: Context wrapper injects<br/>guild_id, channel_id, member_id
    Tool->>MM: create_diagram(description, diagram_type, guild_id)
    MM->>MM: LLM generates syntax
    MM->>MM: render to PNG
    alt Syntax Error
        MM->>MM: LLM fixes syntax
        MM->>MM: re-render
    end
    MM-->>Tool: return (syntax, file)
    Tool->>DC: upload PNG file
    DC-->>User: Display diagram
    Tool->>Conv: add_assistant_message(syntax)
    Tool-->>LLM: "✅ Diagram uploaded: [url]"
    LLM->>CM: final response
    CM->>Conv: add AI message
    CM-->>User: "I've created the diagram"
```

## Integration notes
- Tool wrappers inject an `ExtensionContext` (`guild_id`, `channel_id`, `member_id`, `langcore`) instead of loose kwargs. Use `ctx.get_provider()` and `await ctx.add_to_conversation(...)` to avoid manual locking.
- Guilds configure their preferred LLM with `[p]langcore defaultprovider <name>`; `ctx.get_provider()` honors that setting.
- Lazy loading: extension and provider cogs register on `on_langcore_cog_add` and avoid hard `langcore` imports so standalone features continue working when langcore is absent.
- Extension cogs can define sub-agents (e.g., MermaidManager) that run their own tool loop while writing results back to the conversation.
