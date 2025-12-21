Vibecoding is nice. But you need to **own** the architecture. Not understanding the architecture, having the AI agent make hacky solutions leads to unmaintainable code. 

We need to enforce the concept: Separation of Concerns strictly.  

Langcore is our connecting piece in the whole ecosystem. It abstracts the LLM provider and Vector DB. Furthermore it creates the hub for registering tools.  
Users using this project may use Ollama or OpenAI/OpenRouter or something different. All those providers need to be supported equally through the Abstraction Layer ChainProvider. It is (will be) possible to have multiple ChainProviders.
Users may have different databases. A local chromaDB can be spinned up fast. A qdrant requires a bit more setup. The Abstraction Layer ChainProvider makes it database agnostic.

Finally we have the hub that makes it easy to register/unregister functions and also inject system prompts.

Cogs that are implemented can bring their own sophisticated configuration.


---

implement common pitfalls for test check:
- importing a python module from another cog (should use cogchain)
- defining interface without doc string
- usage of getattr


fix pipeline, add automatic tests, activate tests in pipeline



Best practice of ruff, uv, ty, pydantic
Note: mirrored cogs, e.g. hotreload, need to be exempt

---

OpenRouter is untested. Created but I don't use API keys for it yet.

---

later: Fallback strategy: OpenRouter (free) -> Ollama (cloud) -> Ollama (local hardware)
-> worth offloading as a cog since different people have different ideas of it

---

create mkdocs


---

improve [nntin-labs](https://github.com/NNTin/nntin-labs) and get my docker-stack web ready:
minimum requirement: 
- traefik, oauth2-proxy and keycloak <- ensure only authenticated users can access endpoint
- currently solved as a VPN, ugly workaround

dozzle and portainer is hosted
start hosting red-discordbot as a docker service -> easier time checking on the bot


---

PyPI langchain-ollama and langchain-openai requested but not used (not using langchain embeddings, since we maintain conversation ourselves)
-> adjust the architectural images, throw out dependency

---

re-evaluate conversation handling in mermaid and spoilarr

in mermaid the mermaid agent uploads image on discord -> adding to conversation so conversation agent is aware
in spoilarr the spoiler agent does **not** interact with discord -> should not be added to the conversation

mermaid talks to discord through MessageHandler. MessageHandler should implement the Conversation handling.  

Conversation:  
Discord User: Create me a diagram that shows a conversation between Bob and Alice
<Conversation Agent deletegates task to Mermaid Agent>
Mermaid Agent: *posts raw Mermaid Syntax into conversation*
<Mermaid Agent uploads a Discord file image showing the Mermaid diagram>
Mermaid Agent: post System image for Conversation Agent: The mermaid syntax has been sent to the Discord User as an image
Conversation Agent: *talks about the diagram without posting the mermaid syntax again - unless specifically asked to do so*

---

sync cog pipeline requires

pip install -e cogchain

---

SubAgents defined in cogchain but not used

---

hotreload is amazing. It reloads the cog on file changes.  
cogchain is not a cog. It is a python module that has to be installed.

Create me another cog, named modreload (short for python module reload)

**Investigate** how hotreload works and create an **extensive** implementation plan for modreload. Put it into modreload/README.md