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

The code is working. We need architectural improvements so we have standardized means. ChainProvider and ChainStore have this fully abstracted. To the ExtensionCogs we need clear interfaces, not hacky solutions that require complicated code.  
Note: It is not possible to import between the cogs!

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
- importing a python module from another cog (should use cogchain)
- defining interface without doc string
- usage of getattr

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

In the ExtensionCogs there are some hard references to langcore.  
Because we cannot control the order in which cogs are loaded, we need a better way to allow lazy loading and only do the registration (ChainHub, ChainProvider and ChainStore) when the langcore cog is loaded. The langcore cog does fire an event for this.  


---

We have several references of `getattr()` in langcore, mermaid, qdrant and spoilarr. This kind of implementation is not nice and does not fully utilize the advantages of defined interfaces. 

I am developing plugins (or cogs) for the Red-DiscordBot. I have the problem that I requires Abstractions, Contracts and Interfaces between the cogs. However there cannot be any hard references between the plugins. They are not directly aware of each other's existance. In order to share the Contracts between them we will create a PyPI package.

The PyPI package will be called cogchain.

In order to be able to locally develop with it, we will locally install the cog with `pip install -e .`  
When the package is ready we will publish it.