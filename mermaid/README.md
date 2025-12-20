test different mermaid syntax creation, see which prompts have the best result
should mermaid syntax create syntax error, however these errors are often times always the same mistakes
-> collect the mistakes and the fixes
Learnings:
- apply a non-LLM cleanup
- improve the prompt by consolidating the mistake and fixes. LLM will tell us a better prompt

-> save on token usage
-> when it still fails, then finally utilize LLM

For now there is a bug that the LLM only sees "fix the syntax error", however the syntax error is not concretely explained, making fixes tiresome. (Might be overkill)


improve CSS styling:
custom CSS styles can be added (see /gitroot/README.md)
by default: transparent background, image is readable for light and dark mode

Users can configure light, dark or both. LLM will optimize against that. By default hybrid is selected

---

latest git commit has the error:
Bot response:

Here's your Mermaid diagram:
<Image says Syntax Error!>
Here’s the diagram you requested:

✅ Diagram uploaded: Mermaid PNG

The Mermaid source used is already added to this conversation for your reference.