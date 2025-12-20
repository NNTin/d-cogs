from typing import Any, Dict, List, Optional

# Spoiler-sensitive fields that should be censored when spoiler_mode is disabled
SPOILER_FIELDS = [
    "overview",
    "tagline",
    "biography",
    "backdrop_path",
    "poster_path",
    "release_date",
    "first_air_date",
]


def censor_spoilers(data: Dict[str, Any], fields: Optional[List[str]] = None) -> Dict[str, Any]:
    """Return a copy of data with spoiler fields replaced by Discord spoiler markup."""
    if fields is None:
        fields = SPOILER_FIELDS

    def _censor(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: ("||spoiler||" if k in fields else _censor(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [_censor(item) for item in value]
        return value

    return _censor(data)
