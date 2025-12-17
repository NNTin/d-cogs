# Embed Cog Roadmap

This roadmap outlines the development of the `embed` cog, a utility extension for `langcore` that allows AI-driven creation and editing of Discord Embeds.

## Vision
To provide a seamless interface for users to generate and modify Discord Embeds using natural language prompts. The system leverages AI not just for generation, but also for self-correcting invalid JSON structures, ensuring robustness beyond standard library validation.

## Milestones

### Iteration 1: Feature Complete
**Goal:** Implement the full lifecycle of embed generation, validation, execution, and context storage.

- [ ] **Cog Structure & Commands**:
    - Implement `[p]embed create #channel <content-prompt>`: Generates a new embed in the specified channel.
    - Implement `[p]embed edit <messagelink> <desired-content-prompt>`: Fetches an existing message and updates its embed based on the prompt.

- [ ] **AI Generation & Validation Loop**:
    - **Prompt Engineering**: Design prompts that instruct the LLM to output raw JSON compatible with Discord's Embed structure.
    - **Self-Correction**: Implement a retry loop where validation errors (from `discord.py` or JSON parsing) are fed back to the LLM to fix the JSON.
    - **Bypass Strict Objects**: Treat the embed data as raw JSON during the generation phase to allow the AI to fix structure before converting to a `discord.Embed` object.

- [ ] **Context Management**:
    - **Metadata Storage**: Develop a strategy to store the generated JSON in the conversation history.
    - **Format**: Ensure the stored JSON is wrapped or formatted in a way that the LLM understands it represents the *current state* of the message, without confusing it with normal conversation flow (e.g., using a specific system block or tool output format).

- [ ] **Execution**:
    - Implement the logic to send the final valid embed to the target channel.
    - Implement the logic to edit the target message with the new embed.
