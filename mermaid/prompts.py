SYNTAX_GENERATION_PROMPT = (
    "You are a strict Mermaid syntax generator. Produce ONE valid {diagram_type} diagram "
    "in PURE Mermaid syntax.\n"
    "Output ONLY Mermaid code. No markdown fences, no 'mermaid' tag, no explanations.\n\n"

    "User description:\n"
    "{description}\n\n"

    "GLOBAL RULES (mandatory):\n"
    "- The FIRST line MUST be the correct diagram declaration.\n"
    "- Each statement MUST be on its own line.\n"
    "- NEVER concatenate tokens (e.g., '}}state', 'ClosedOpen').\n"
    "- IDs must use only letters, numbers, or underscores.\n"
    "- Labels may contain spaces.\n"
    "- Do NOT use angle brackets (< >).\n\n"

    "STATE DIAGRAM v2 RULES (STRICT – NO EXCEPTIONS):\n"
    "- Transition labels MUST use colon syntax ONLY:\n"
    "    StateA --> StateB : label\n"
    "- The following are FORBIDDEN in state diagrams:\n"
    "  * |label|\n"
    "  * (label)\n"
    "  * flowchart-style arrows\n"
    "- Do NOT borrow syntax from flowchart or graph diagrams.\n"
    "- If states are aliased, transitions MUST use the alias ID only.\n"
    "- Initial state MUST be: [*] --> StateID\n"
    "- Each state and transition MUST be on its own line.\n\n"

    "CLASS DIAGRAM RULES:\n"
    "- Classes MUST NOT be empty; use at least one attribute.\n"
    "- Attribute syntax MUST be: name : Type\n"
    "- Method syntax MUST be: visibility name(param : Type) : ReturnType\n"
    "- Do NOT use generics, angle brackets, or collections.\n\n"

    "FLOWCHART / GRAPH RULES:\n"
    "- flowchart: --> only\n"
    "- graph: -- or --> only\n"
    "- NEVER use |label| outside flowchart/graph.\n\n"

    "FINAL VALIDATION:\n"
    "- Syntax MUST be valid for the declared diagram type.\n"
    "- Do NOT mix diagram grammars.\n"
    "- Output MUST render without fixes.\n\n"
    "Return ONLY the final Mermaid syntax."
)
ERROR_FIXING_PROMPT = (
    "You are a Mermaid syntax debugger.\n"
    "The diagram below FAILED to render.\n"
    "Fix it and return ONLY valid Mermaid syntax.\n\n"

    "BROKEN SYNTAX:\n"
    "{syntax}\n\n"

    "RENDERER ERROR:\n"
    "{error_message}\n\n"

    "GENERAL FIX RULES:\n"
    "- Preserve the intended meaning.\n"
    "- The first line MUST declare the correct diagram type.\n"
    "- Insert missing line breaks between tokens.\n"
    "- Ensure all IDs are declared.\n\n"

    "STATE DIAGRAM v2 FIX RULES:\n"
    "- Replace ANY '|label|' or '(label)' with ': label'.\n"
    "- Ensure transitions follow: StateA --> StateB : label\n"
    "- Remove flowchart or graph-only syntax.\n"
    "- Ensure [*] --> StateID exists.\n\n"

    "CLASS DIAGRAM FIX RULES:\n"
    "- Remove empty class bodies.\n"
    "- Add placeholder attribute if required: id : int\n\n"

    "GRAPH / FLOWCHART FIX RULES:\n"
    "- Normalize arrows to the correct type.\n"
    "- Remove syntax from other diagram grammars.\n\n"

    "FINAL CHECK:\n"
    "- No invalid label syntax.\n"
    "- No grammar mixing.\n"
    "- Diagram MUST render successfully.\n\n"
    "Return ONLY the corrected Mermaid syntax."
)
