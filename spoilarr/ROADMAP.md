Use TMDb API

provides different tools for the langcore cog:  
* searching for movies/series  
* looking up a movie/series
* credits for cast/crew retrieval

Flow:
Currently the only implemented ExtensionCog is mermaid. spoilarr will be our second ExtensionCog. There should be no hard-references of spoilarr in langcore or ollama. We need to maintain our modularity.
This is a more simple implementation since no LLM or sub agent is required. It merely polls an API endpoint (with retry + rate limit awareness), surfaces API errors and forwards the result to the Conversation Manager / Agent. 

For example:  
Prequisite: spoilarr cog is loaded and registered in the ChainHub as a tool.
[p]chat Who is playing in the movie Inception?
-> Conversation Agent from langcore cog evaluates the question, looks into registered tools, sees he can call a tool to get the information
-> Conversation Agent calls the tool.  
-> Spoilarr Cog uses add_tool_message (see langcore/models.py)
-> Conversation Agent sees the tools result and responds to the Discord user: "In the movie Inception the actors ... are playing."

Spoilarr cog supports a configuration for spoiler mode:  
If spoiler mode is disabled the sensitive information are censored via ||spoiler markup||. By default spoiler mode is disabled.  
As well as setting the API key for TMDb.

When spoiler mode is disabled add_assistant_message() from langcore/models.py is called and instructs the ConversationManager to use ||spoiler markup||.

Use toon (https://github.com/toon-format/toon) to convert from json to toon.  
When the Spoilarr Cog uses add_tool_message the payload is sent in toon format.

---

Next step:  
We'll delegate it to a sub agent. 

---

TODOs:
- toon formatting does not work
- we have to refine the system prompt, also inject system prompts when spoilarr and mermaid is loaded, Conversation Agent should only say verifiable things