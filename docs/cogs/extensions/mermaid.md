# mermaid Cog

Tests and improves mermaid syntax generation with iterative fixes and custom styling for Discord-friendly images. Uses a sub-agent pattern to generate, validate, and repair diagrams before sending them back to the conversation.

## Architecture

```mermaid
sequenceDiagram
    participant CM as ConversationManager
    participant MM as MermaidManager
    participant LLM as LLM Provider
    participant Render as render_mermaid_syntax()
    
    CM->>MM: create_diagram(description, type, guild_id)
    MM->>MM: generate_syntax()
    MM->>LLM: Generate mermaid syntax
    LLM-->>MM: Return syntax
    
    loop Retry up to 3 times
        MM->>Render: render_mermaid_syntax(syntax)
        alt Success
            Render-->>MM: discord.File (PNG)
            MM-->>CM: (syntax, file)
        else Error
            Render-->>MM: Exception with error
            MM->>MM: fix_syntax_error()
            MM->>LLM: Fix the syntax error
            LLM-->>MM: Fixed syntax
        end
    end
    
    alt Max retries exceeded
        MM-->>CM: RuntimeError
    end
```

| Aspect                  | Decision                               | Rationale                                                                 |
|-------------------------|----------------------------------------|---------------------------------------------------------------------------|
| Context Independence    | MermaidManager has its own LLM calls   | Sub-agents don't share context with ConversationManager                   |
| No ChainHub Registration| Manager is NOT a tool                  | Delegation happens later, not via tool calling                        |
| Error Handling          | Retry loop with LLM-based fixing       | LLM can analyze and fix syntax errors intelligently                        |
| Return Type             | Tuple of (syntax, file)                | Syntax for conversation, file for Discord upload                          |
| Prompt Storage          | Class-level constants                  | Easy to modify and maintain prompts                                       |
| Max Retries             | 3 attempts                             | Balance between success rate and performance                               |

## Behavior notes
- Captures common syntax errors and applies non-LLM cleanup before retrying with the LLM.
- Default styling targets both light and dark Discord themes; users can configure light, dark, or hybrid.
- If langcore is absent, the cog still works, but when registered it can add generated syntax back into the conversation history for the main agent.
