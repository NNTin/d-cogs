# Spoilarr Cog Roadmap

This roadmap guides the implementation of the `spoilarr` cog, an AI assistant extension that fetches rich movie and TV data from TMDb and delivers Discord-friendly summaries while respecting spoiler sensitivity.

## Vision
Provide conversational access to TMDb data with configurable spoiler management and extensible tooling so other cogs or LangCore agents can reuse the same movie/TV intelligence.

## Milestones

### Iteration 1: Foundations & API Integration
**Goal:** Establish a reliable bridge between the cog and TMDb.
- [ ] **Configuration Schema**: Add settings for the TMDb API key, default language/region, and per-guild overrides.
- [ ] **HTTP Client Wrapper**: Implement a thin client (with retry + rate-limit awareness) for `/search/movie`, `/search/tv`, `/movie/{id}`, `/tv/{id}` endpoints.
- [ ] **Basic Commands**: Provide simple lookup commands (e.g., `[p]spoilarr movie "Inception"`).
- [ ] **Error Handling**: Surface API errors, missing results, and quota issues with actionable guidance.

### Iteration 2: Tooling & Endpoint Coverage
**Goal:** Expose reusable tools and expand the dataset depth. 
- [ ] **ChainHub Tools**: Register movie/TV lookup helpers so LangCore agents can call them (
  e.g., `search_movie`, `get_movie_details`, `get_cast_list`).
- [ ] **Credits Endpoints**: Add `/movie/{id}/credits` and `/tv/{id}/credits` for cast/crew retrieval.
- [ ] **Media Assets**: Support poster/backdrop URLs and key metadata (runtime, genres, release windows).
- [ ] **Response Templates**: Create structured summaries that downstream cogs can parse (JSON blocks describing synopsis, cast highlights, ratings).

### Iteration 3: Spoiler-Aware Presentation
**Goal:** Respect spoiler preferences without losing important context.
- [ ] **Spoiler Configuration**: Per-channel toggles that wrap sensitive sections in Discord spoiler tags (`||text||`).
- [ ] **Spoiler Detection Rules**: Define what qualifies as a spoiler (plot twists, endings, surprise cameos) and tag those fields in responses/tools.
- [ ] **Fallback Messaging**: When spoilers are censored, add a note explaining how to opt-in for full details.

### Iteration 4: Advanced UX & Customization
**Goal:** Deliver polished interactions and allow power users to steer the tone.
- [ ] **System Override Prompt**: Allow admins to set custom guidance for how the cog presents information (e.g., "write as a movie critic," "keep it concise").
- [ ] **Persona Hooks**: Inject the override prompt into tool outputs and LangCore tool schemas.
- [ ] **Command Enhancements**: Add `[p]spoilarr random genre=<genre>` or filters for streaming availability (if TMDb data permits).
- [ ] **Testing & Docs**: Include example configs, instructions for obtaining the TMDb key, and integration tests for the major endpoints.

## Dependencies & Notes
- Requires a valid TMDb API key stored securely (env var or Red config).
- Responses should remain lightweight for Discord, with longer data (cast lists, gallery links) delivered via files or collapsible sections when necessary.
- Build with extensibility in mind so future cogs (e.g., recommendation engines) can reuse the same TMDb client and tool registry.
