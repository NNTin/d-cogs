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