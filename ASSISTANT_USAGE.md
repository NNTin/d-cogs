# ASSISTANT_USAGE.md - In-Depth Usage Guide

Comprehensive guide to using the Assistant and RAGUtils cogs for Discord. Organized by API provider and user role.

## Table of Contents
- [Overview](#overview)
- [Setup by API Provider](#setup-by-api-provider)
  - [OpenAI Setup](#openai-setup)
  - [Ollama Setup](#ollama-setup)
- [Bot Owner Guide](#bot-owner-guide)
- [Guild Admin Guide](#guild-admin-guide)
- [Normal Member Guide](#normal-member-guide)
- [Advanced Features](#advanced-features)
- [Troubleshooting](#troubleshooting)
- [Quick Reference](#quick-reference)
- [Comparison: OpenAI vs Ollama](#comparison-openai-vs-ollama)
- [Additional Resources](#additional-resources)

## Overview

### What is Assistant?
AI-powered chatbot for Discord using OpenAI's ChatGPT models or compatible endpoints like Ollama. Supports:
- Per-user, per-channel conversations with memory
- Custom embeddings for knowledge retrieval (RAG)
- Function calling for extended capabilities
- Image generation with DALL-E (OpenAI only)
- Conversation summarization (TLDR)
- Customizable prompts and behavior
- Reaction-based memory creation: react with 🧠 to save messages as embeddings

### What is RAGUtils?
Companion cog that enhances RAG with:
- Sentence-aware chunking
- Cross-encoder reranking
- MMR (Maximal Marginal Relevance) for diverse results
- ChromaDB (default) or Qdrant backend
- Keyword and numeric boosting

## Setup by API Provider

### OpenAI Setup
OpenAI provides the most feature-complete experience (GPT-4/5, DALL-E, vision, full functions).

**Prerequisites**
- OpenAI API key from `platform.openai.com`
- Account with credit balance

**Initial configuration (per server)**
- `[p]assistant openaikey` (follow DM prompts)

**Choose models**
- `[p]assistant model gpt-4o-mini`
- `[p]assistant embedmodel text-embedding-3-small`
- Recommended chat: `gpt-4o-mini` (cost-effective), `gpt-4o` (advanced), `gpt-5.1` (latest)
- Recommended embeddings: `text-embedding-3-small` (768 dims), `text-embedding-3-large` (3072 dims)

**Enable features**
- `[p]assistant toggle`
- `[p]assistant functioncalls`
- `[p]assistant toggledraw`

**OpenAI-specific features**
- Image generation: `/draw` with DALL-E 3 or `gpt-image-1`
- Vision: GPT-4 Vision can analyze images
- Advanced models: `o1` (reasoning), `gpt-5` (verbosity control)

### Ollama Setup
Ollama is free, self-hosted, and private (no API costs; limited function support).

**Prerequisites**
- Install Ollama: `ollama.ai`
- Start service: `ollama serve`
- Pull models:
  - `ollama pull gemma3` (chat)
  - `ollama pull qwen3-embedding` (embedding)
  - `ollama pull nomic-embed-text` (alt embedding, 768 dims)

**Initial configuration (bot owner)**
- `[p]assistant endpointoverride http://localhost:11434/v1`

**Choose models (per server)**
- `[p]assistant model gemma3`
- `[p]assistant embedmodel qwen3-embedding`
- `[p]assistant refreshembeds`

**Recommended Ollama models**
- Chat: `gemma3`, `llama3.1`, `mistral`, `qwen2.5`
- Embeddings: `nomic-embed-text` (768), `all-minilm` (384), `qwen3-embedding`

**Enable features**
- `[p]assistant toggle`
- `[p]assistant functioncalls`

**Supported vs unsupported**
- ✅ Chat completions, embeddings, memory functions, web search (Brave), core function calling, convo management, RAG/embeddings
- ❌ Image generation, image editing, vision models, some advanced model params

**Important notes**
- Ollama endpoints expose only core tools; custom functions may not work on all models
- Performance depends on hardware; no API cost but uses local resources

**Ollama model management (bot owner)**
- `[p]assistant ollama list`
- `[p]assistant ollama pull <model>`
- `[p]assistant ollama delete <model>`
- `[p]assistant ollama setdefault <chat_model> <embed_model>`
- `[p]assistant ollama toolscope <global|guild>`

**Embedding model compatibility**

| Model              | Dimensions | Notes                         |
|--------------------|------------|-------------------------------|
| nomic-embed-text   | 768        | High quality general use      |
| all-minilm         | 384        | Faster, lower memory          |
| qwen3-embedding    | 1024       | Good balance                  |
| embeddinggemma     | 3072       | High capacity                 |

After changing embedding models: `[p]assistant refreshembeds` to regenerate embeddings to the new dimensions.

## Bot Owner Guide

Bot owners control global config across all servers.

### Global Configuration
- Endpoint management: `[p]assistant endpointoverride [url]` (OpenAI default empty, Ollama `http://localhost:11434/v1`)
- API keys: `[p]assistant braveapikey` (web search; works with OpenAI and Ollama)
- Bot behavior: `[p]assistant listentobots`, `[p]assistant persist`

### Data Management
- `[p]assistant wipecog <confirm>` wipe all cog data/settings
- `[p]assistant backupcog` / `[p]assistant restorecog`
- `[p]assistant resetglobalconversations <yes_or_no>`
- `[p]assistant resetglobalembeddings <yes_or_no>`

### Ollama Management (owner only)
- Models: `[p]assistant ollama list|pull|delete`
- Defaults: `[p]assistant ollama setdefault <chat> <embed>`
- Function scope: `[p]assistant ollama toolscope <global|guild>`

### Best Practices for Owners
- Security: keep keys private; use DM-only for sensitive commands; review `[p]customfunctions`
- Resource management: monitor `[p]assistant usage`; set sane `maxtokens`/`maxretention`; monitor Ollama system resources
- Backups: run before major changes; test restores in dev
- Public bots: disable `listentobots`; set conservative limits; review custom functions; consider Ollama to reduce cost

## Guild Admin Guide

Admins configure the Assistant per server and manage embeddings.

### Basic Setup
1) Enable: `[p]assistant toggle`  
2) API key (OpenAI only, if no global endpoint): `[p]assistant openaikey` (DM flow)  
3) Core settings:
```
[p]assistant model gpt-4o-mini      # or gemma3 for Ollama
[p]assistant temperature 0.7        # 0=focused, 2=creative
[p]assistant maxtokens 4000
[p]assistant maxretention 20
```

### Channel Configuration
- Auto-response: `[p]assistant channel #ai-chat`, `[p]assistant listen`, `[p]assistant mentionrespond`
- Channel prompts: `[p]assistant channelprompt #support You are a helpful support assistant. Be concise and professional.`
- View prompt: `[p]assistant channelpromptshow #support`

### Conversation Settings
- Memory: `[p]assistant maxretention <number>` (0=unlimited), `[p]assistant maxtime <seconds>` (0=never), `[p]assistant collab`
- Behavior: `[p]assistant questionmark`, `[p]assistant minlength <chars>`, `[p]assistant mention`, `[p]assistant sysoverride`

### Prompt Engineering
- System prompt: `[p]assistant system You are a friendly Discord bot assistant. You help users with their questions and provide accurate information.`
- Placeholders: `{botname}`, `{timestamp}`, `{day}`, `{date}`, `{time}`, `{timetz}`, `{username}`, `{displayname}`, `{roles}`, `{rolementions}`, `{server}`, `{members}`, `{owner}`, `{channelname}`, `{channelmention}`, `{topic}`, `{balance}`, `{currency}`, `{bank}`

### Embeddings (Knowledge Base)
- Manage: `[p]embeddings` (add/edit/delete/search)
- Import/Export: `[p]assistant importcsv <overwrite>`, `[p]assistant importjson <overwrite>`, `[p]assistant exportcsv`, `[p]assistant exportexcel`, `[p]assistant exportjson`
- Configure: `[p]assistant topn <number>` (3–5), `[p]assistant relatedness <0.0-1.0>` (0.25–0.35), `[p]assistant embedmethod` (dynamic/static/hybrid/user), `[p]assistant questionmode`, `[p]assistant refreshembeds`
- Test: `[p]query How do I reset my password?` (shows retrieved embeddings)

### Reaction-Based Memory Creation
- Tutors: `[p]assistant tutor @role` / `[p]assistant tutor @user`
- Flow: tutor reacts with 🧠 → bot summarizes (includes reply chain) → creates embedding → reacts ✅/❌
- Notes: requires function calling enabled; message must have content; works on OpenAI and Ollama; auto-cleans irrelevant details (e.g., speaker names)
- Uses: capture support answers, decisions, announcements, procedures

### Model Configuration
- Select: `[p]assistant model [model]`, `[p]assistant embedmodel [model]`
- Params: `[p]assistant temperature <0.0-2.0>`, `[p]assistant frequency <-2.0 to 2.0>`, `[p]assistant presence <-2.0 to 2.0>`, `[p]assistant seed [number]`, `[p]assistant maxtokens <number>`, `[p]assistant maxresponsetokens <number>`
- Advanced (OpenAI only): `[p]assistant resolution`, `[p]assistant reasoning`, `[p]assistant verbosity`

### Function Calling
- Enable/limits: `[p]assistant functioncalls`, `[p]assistant maxrecursion <number>`
- Manage functions: `[p]customfunctions [function_name]` (see `assistant/example-funcs/README.md`)
- Note: Ollama endpoints support only core tools; some custom functions may not work

### Access Control
- Blacklist: `[p]assistant blacklist @user|@role|#channel`
- Tutors: tutors can create/edit embeddings via function calls and 🧠 reactions; have access to `create_memory`/`edit_memory`
- Role overrides: `[p]assistant override model gpt-4o @premium`, `maxtokens`, `maxretention`, `maxresponsetokens`, `maxtime`

### Image Generation (OpenAI only)
- Toggle: `[p]assistant toggledraw`
- Usage: `/draw prompt:A beautiful sunset over mountains size:1024x1024 quality:high style:vivid`

### Auto-Answer
- `[p]assistant autoanswer`
- `[p]assistant autoanswerthreshold <0.0-1.0>`
- `[p]assistant autoanswermodel <model>`
- `[p]assistant autoanswerignore #channel`

### Content Filtering
- `[p]assistant regexblacklist <regex_pattern>`
- `[p]assistant regexfailblock`

### Data Management (server)
- `[p]assistant resetconversations <yes_or_no>`
- `[p]assistant resetembeddings <yes_or_no>`
- `[p]assistant resetusage`

### View Configuration
- `[p]assistant view` (shows current settings)

### RAGUtils Configuration (Admin)
- Status: `[p]ragutils status` (features, backend, thresholds, deps, migration)
- Enable/Disable: `[p]ragutils enable reranking|mmr|chunking`, `[p]ragutils disable reranking|mmr|chunking`
- Parameters: `[p]ragutils threshold <0.0-1.0>` (default 0.33), `[p]ragutils mmrlambda <0.0-1.0>` (default 0.5), `[p]ragutils chunksize <min> <max>` (default 12-120)
- Backend: `[p]ragutils backend chromadb|qdrant`, `[p]ragutils qdranturl http://localhost:6333`
- Migration: `[p]ragutils migrate`, `[p]ragutils migrate True` (force reset collection)
- Health/Test: `[p]ragutils health`, `[p]ragutils test "How do I configure the bot?"`

## Normal Member Guide

### Basic Chat Commands
- Start: `[p]chat Hello! How are you?`, `[p]ask What's the weather like?`
- Mentions: `@BotName Can you help me with something?`

### Chat Arguments
- Output to file: `[p]chat write a python script --outputfile script.py`
- Extract code blocks: `[p]chat write a python function --extract [--outputfile code.py]`
- Resend last: `[p]chat --last [--outputfile response.txt]`

### File Comprehension
- Attach files with your question (supported: `.py .js .java .cpp .c .cs .php .rb .go .rs .swift .kt .ts .html .css .json .xml .yaml .yml .md .txt .log .sh .bat .ps1 .sql`)

### Conversation Management
- Stats: `[p]convostats` / `[p]convostats @user`
- Clear: `[p]convoclear`
- Pop last: `[p]convopop`
- Copy: `[p]convocopy #other-channel`
- Prompt (if enabled): `[p]convoprompt You are a helpful coding assistant specializing in Python.`
- Transcript (owners): `[p]convoshow @user #channel`
- Import (owners): `[p]importconvo` with `conversation.json`

### Image Generation (OpenAI only)
- `/draw prompt:A futuristic city at night`
- `/draw prompt:A cat wearing a hat size:1024x1024 quality:high style:vivid model:dall-e-3`
- Parameters: size `1024x1024 | 1792x1024 | 1024x1792 | 1024x1536 | 1536x1024`; quality `low|medium|high|standard|hd`; style `natural|vivid`; model `dall-e-3|gpt-image-1`

### TLDR (Summarization)
- `/tldr timeframe:1h`
- `/tldr timeframe:30m question:What did we decide about the event? channel:#planning`
- `/tldr timeframe:2h member:@user private:True`

### Reaction-Based Memory (Tutors Only)
- React with 🧠 on a message; bot summarizes (includes replies) and saves as embedding; ✅/❌ indicates success
- Tips: works great for capturing decisions, support answers, and announcements; auto-summarizes and removes irrelevant details

### Tips for Better Conversations
- Be specific and include context; reply to prior messages
- Attach files instead of pasting long code
- Clear/reset if the conversation drifts
- Use `--extract` and `--outputfile` for codegen
- Watch `[p]convostats` to track tokens
- Tutors: use 🧠 reactions to grow the knowledge base

### Getting Help
- `[p]chathelp` for the full help message

## Advanced Features

- Collaborative conversations: shared per-channel convo when enabled; only moderators can clear/manage
- Embedding methods: Dynamic (token-saving), Static (better retention), Hybrid (first static), User (inject as user messages)
- Function calling: balances, web search (Brave), translations (Fluent), DB queries, create/edit memories (tutors), and more via `[p]customfunctions` (Ollama limited to core tools)
- RAG pipeline (with RAGUtils): chunking → boosting → reranking → MMR
- Qdrant backend: scales better than ChromaDB, supports distributed setups; migrate via `[p]ragutils migrate`

## Troubleshooting

- **"The API key is not set up!" (OpenAI)**: admins run `[p]assistant openaikey` or owner sets `[p]assistant endpointoverride`
- **"No embeddings configured"**: add via `[p]embeddings`, import, or have tutors use 🧠 reactions
- **"Top N is set to 0"**: set `[p]assistant topn 3`
- **Bot does not respond**: check `[p]assistant toggle`; channel config; blacklist; min length; `[p]assistant view`
- **Embeddings not working**: `[p]query <question>`; verify `topn/relatedness`; check embedding model; `[p]assistant refreshembeds`
- **RAGUtils features not working**: `[p]ragutils status`; install `sentence-transformers`, `nltk`, `qdrant-client`; run `python -m nltk.downloader punkt`; test with `[p]ragutils test "your query"`
- **Qdrant issues**: ensure server running; check URL `[p]ragutils qdranturl`; `[p]ragutils migrate`; firewall/network checks
- **Token limit exceeded**: reduce `[p]assistant maxretention`; `[p]convoclear`; shorten prompts; increase `[p]assistant maxtokens` if model allows
- **Conversation expired**: conversations expire per `maxtime`; start new or increase limit
- **Ollama: model not found**: `ollama pull <model-name>`
- **Ollama: slow responses**: check CPU/RAM/GPU; try smaller model (e.g., `gemma3`); reduce `maxretention`
- **Ollama: function calling not working**: only core tools supported; some custom functions won't work; check `[p]assistant ollama toolscope`
- **Embedding dimension mismatch**: `[p]assistant refreshembeds`
- **Reaction-based memory not working**: ensure tutor role via `[p]assistant tutor @you`; function calling enabled; message has content; check logs

### Getting Support
- Check `[p]assistant view`, `[p]convostats`, `[p]query`, `[p]ragutils status`; then contact admins or bot owner

## Quick Reference

**Bot Owner**
- `[p]assistant endpointoverride <url>`
- `[p]assistant braveapikey`
- `[p]assistant backupcog` / `[p]assistant restorecog`
- `[p]assistant ollama list|pull|delete`

**Guild Admin**
- `[p]assistant toggle`
- `[p]assistant openaikey` (OpenAI)
- `[p]assistant model <model>`
- `[p]assistant channel #channel`
- `[p]assistant system <prompt>`
- `[p]assistant tutor @role`
- `[p]embeddings`
- `[p]ragutils enable|disable <feature>`
- `[p]ragutils status`

**Normal Member**
- `[p]chat <question>`
- `[p]convostats`
- `[p]convoclear`
- `[p]chathelp`
- `/draw prompt:<text>` (OpenAI only)
- `/tldr` (if moderator)
- React with 🧠 (if tutor)

## Comparison: OpenAI vs Ollama

| Feature             | OpenAI                     | Ollama                        |
|---------------------|----------------------------|-------------------------------|
| Cost                | Pay per token              | Free (self-hosted)            |
| Setup               | API key                    | Local install + models        |
| Models              | GPT-4/5, o1, etc.          | Llama, Gemma, Mistral, etc.   |
| Image Generation    | ✅ DALL-E 3                | ❌ Not available              |
| Vision              | ✅ GPT-4 Vision            | ❌ Not available              |
| Function Calling    | ✅ Full support            | ⚠️ Core tools only            |
| Embeddings          | ✅ All models              | ✅ All models                 |
| RAG                 | ✅ Full support            | ✅ Full support               |
| Reaction Memory     | ✅ Supported               | ✅ Supported                  |
| Performance         | Cloud (fast)               | Hardware-dependent            |
| Privacy             | Data sent to OpenAI        | Data stays local              |
| Reliability         | High (SLA)                 | Depends on your setup         |

## Additional Resources
- Function Calling Guide: `assistant/example-funcs/README.md`
- Setup Guide: `ASSISTANT_RAGUTILS_SETUP.md`
- OpenAI Prompt Engineering: https://platform.openai.com/docs/guides/prompt-engineering
- JSON Schema Reference: https://json-schema.org/understanding-json-schema/
- OpenAI Function Calling: https://platform.openai.com/docs/guides/gpt/function-calling
- Ollama Documentation: https://ollama.ai/docs
