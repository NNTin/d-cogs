# DoNotRespond Cog Roadmap

This roadmap outlines the development of the `donotrespond` cog, an extension for `langcore` designed to act as an intelligent filter for channel auto-replies.

## Vision
To create a lightweight, separate AI agent that acts as a gatekeeper for the main bot. It classifies incoming messages to determine if the heavy-duty AI agent should engage, ignore the chatter, or conclude a conversation. This reduces token usage and allows for natural user-to-user conversations in bot-enabled channels.

## Milestones

### Iteration 1: Architectural Design
**Goal:** Define the interaction model and integration points with `langcore`.
*Focus: Architecture, Integration Strategy, K.I.S.S. Principle.*

- [ ] **Integration Point Analysis**: Determine how to intercept `langcore`'s auto-reply trigger.
    - *Challenge*: Needs to run *before* the main agent starts processing.
- [ ] **Separate Agent Design**: Design the "Activation Classifier" as a distinct entity with its own isolated context.
    - It must not share the heavy context of the main agent to save resources.
- [ ] **State Machine Definition**: Formalize the transitions between `IGNORE`, `RESPOND`, and `END`.
    - Define how `IGNORE`d messages are buffered (in case they become relevant later).
    - Define how `END` signals the `ConversationManager` to reset.
- [ ] **Model Selection Strategy**: Design the configuration schema to allow selecting specific small models (e.g., `gemma3`, `llama3.2:1b`) independent of the main agent's model.

### Iteration 2: Minimum Viable Product (MVP)
**Goal:** A working filter that can distinguish between responding and ignoring.
*Focus: Basic Classification, Hook Implementation.*

- [ ] **Cog Skeleton**: Create the basic cog structure and register it with `langcore` (or `ChainHub` if applicable).
- [ ] **Classifier Implementation**: Implement the loop that sends recent messages to a small LLM with the classification system prompt.
    - *Prompt*: "Respond with exactly one word: RESPOND, IGNORE or END..."
- [ ] **Blocking Mechanism**: Implement the logic to prevent the main `langcore` agent from triggering if the state is `IGNORE`.
- [ ] **Basic Config**: Add commands to enable/disable this behavior per channel.
- [ ] **Default Model**: Hardcode or configure a default lightweight model for the MVP.

### Iteration 3: Feature Complete
**Goal:** Full state management, context handling, and user customization.
*Focus: Context Buffering, END State, Model Overrides.*

- [ ] **Context Buffering**: Implement the logic for the `IGNORE` state where messages are tracked but not acted upon, ensuring the main agent has context if the state switches to `RESPOND`.
- [ ] **Conversation Reset (END)**: Implement the logic to clear the main agent's conversation history when the classifier detects an `END` state.
- [ ] **Model Override Config**: Add user-facing commands to specify which model the classifier uses (e.g., `[p]donotrespond setmodel gemma3`).
- [ ] **Performance Tuning**: Optimize the prompt and context window for the classifier to ensure minimal latency.
- [ ] **Refinement**: Evaluate if the "Keep It Simple" approach holds or if `ConversationManager` needs a deeper refactor (as noted in the initial analysis).
