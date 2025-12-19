## Conversation Class Architecture

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

MessageHandler -> Discord End User facing messages.  
Sub Agents like the Mermaid Manager can also send messages to the Discord End User. In order for the Main Agent / Conversation Manager to keep track of the topic he has access to the Conservation Class.  
So far the Conversation Class has been exclusively between the Discord End User and the Conversation Manager.

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