Vibecoding is nice. But you need to **own** the architecture. Not understanding the architecture, having the AI agent make hacky solutions leads to unmaintainable code. 

We need to enforce the concept: Separation of Concerns strictly.  

Langcore is our connecting piece in the whole ecosystem. It abstracts the LLM provider and Vector DB. Furthermore it creates the hub for registering tools.  
Users using this project may use Ollama or OpenAI/OpenRouter or something different. All those providers need to be supported equally through the Abstraction Layer ChainProvider. It is (will be) possible to have multiple ChainProviders.
Users may have different databases. A local chromaDB can be spinned up fast. A qdrant requires a bit more setup. The Abstraction Layer ChainProvider makes it database agnostic.

Finally we have the hub that makes it easy to register/unregister functions and also inject system prompts.

Cogs that are implemented can bring their own sophisticated configuration.



---

Analyze the plugin langcore. The langcore cog offers modularity by having the abstraction ChainProvider and ChainStore as well has the hub.py and conversation.py for registering/unregistering functions and sharing the conversation.  

langcore is not aware of the plugins that will be installed. There should be no hard references to other plugins.  

Review langcore and check if their means of exposing functions follow best practice.  
Finally check the extension cogs mermaid and spoilarr if they integrate the functions using the best practice.  

The code is working. We need architectural improvements so we have standardized means. ChainProvider and ChainStore have this fully abstracted. 

---

we need to define interfaces langcore offers
- ChainProvider - done in abc.py
- ChainStore    - done in abc.py
- conversation.py
- hub.py

---


fix ollama/modelspy: from langcore.models import BaseModel

---

implement common pitfalls for test check:
- importing a python module from another cog
- defining interface without doc string

---

Best practice of ruff, uv, ty, pydantic
Note: mirrored cogs, e.g. hotreload, need to be exempt

---

fix pipeline, add automatic tests, activate tests in pipeline

---

support OpenRouter as an additional ChainProvider, see @ollama implementation
later: Fallback strategy: OpenRouter (free) -> Ollama (cloud) -> Ollama (local hardware)

---

create mkdocs

---

refactor mermaid similar to spoilarr
for each AI cog: put prompts into <cogname>/prompts.py similar to spoilarr

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