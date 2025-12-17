# Mermaid Cog Roadmap

This roadmap defines the evolution of the `mermaid` cog, focusing on extending the existing prompt-based command into a richer file-aware diagram generator that stores metadata alongside images.

## Vision
Allow users (and LangCore agents) to submit either inline prompts or uploaded text/markdown files, automatically extract relevant diagram definitions, generate Mermaid diagrams as images, and persist structured metadata in conversation history for future reference or editing.

## Milestones

### Iteration 1: File Ingestion & Parsing
**Goal:** Accept file uploads and produce clean Mermaid input text.
- [ ] **Command Enhancements**: Update `[p]mermaid <content>` to accept optional attachments; default to inline text when no file is provided.
- [ ] **File Validation**: Restrict accepted file extensions (`.md`, `.txt`, `.log`, etc.) and enforce size limits to prevent abuse.
- [ ] **Content Extraction Pipeline**: Implement logic that reads attachments, splits out fenced code blocks, removes irrelevant prose, and surfaces candidate Mermaid snippets (flowchart, sequence, class diagrams, etc.).
- [ ] **Fallback UX**: If no Mermaid code is detected, return guidance to the user and optionally echo the parsed text for manual adjustments.

### Iteration 2: Diagram Generation & Metadata Storage
**Goal:** Produce images and persist their provenance for conversational context.
- [ ] **Rendering Workflow**: Ensure the existing renderer (local CLI, web service, or hosted script) can handle both inline and file-derived Mermaid code without manual intervention.
- [ ] **Metadata Envelope**: Define a JSON schema that captures source channel, user, original prompt/file name, sanitized Mermaid code, render timestamp, and image URL/message ID.
- [ ] **Conversation Attachments**: Store the metadata blob alongside the bot response so future turns (or other cogs) can reference the diagram without re-uploading.
- [ ] **Audit/Debug Output**: Optionally retain a lightweight log (e.g., hidden embed field) showing the transformations applied to the uploaded text for transparency.

### Iteration 3: UX Polishing & Extensibility
**Goal:** Make the cog robust for daily use and ready for integration with other tools.
- [ ] **Multi-Diagram Support**: When a file includes several Mermaid blocks, generate multiple images or allow users to choose which block to render.
- [ ] **Editing Flow**: Provide a follow-up command (e.g., `[p]mermaid edit <message-link> <new-prompt-or-file>`) that reuses stored metadata to update diagrams quickly.
- [ ] **Error Recovery**: Detect common Mermaid syntax issues, highlight offending lines, and guide the user to fix them without losing context.
- [ ] **ChainHub Hooks**: Expose helper functions ("render_mermaid", "list_mermaid_assets") so other cogs/agents can request diagrams programmatically using the same metadata conventions.
