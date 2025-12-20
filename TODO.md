in @langcore/conversation.py:

we should consistently use `convert_to_messages`, we do not need `convert_to_openai_messages`.
when an extension cog is loaded a SystemMessage should be added.  
We need to expose a way for cog to write a SystemMessage when they are registered in the ChainHub and remove the SystemMessage when they are unregistered. Give me suggestions there.

Maintain the modularity. ExtensionCogs like mermaid and spoilarr are dynamic. There should be no hardcoded references of it in langcore.

---

Best practice of ruff, uv, ty, pydantic
Note: mirrored cogs, e.g. hotreload, need to be exempt

---

fix pipeline, add automatic tests, activate tests in pipeline

---

support OpenRouter ChainProvider
Fallback strategy: OpenRouter (free) -> Ollama (cloud) -> Ollama (local hardware)

---

create mkdocs