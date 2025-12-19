Model defined in Ollama are default models for the ExtensionCogs.  
ExtensionCogs can override the model.

Possible to define multiple Ollama providers
- either as a fallback
- or as load balancer

--> langcore.get_provider() will have to handle this