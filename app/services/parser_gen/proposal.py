from pydantic import BaseModel, Field


class SelectorProposal(BaseModel):
    """A proposed (or current) set of extraction selectors for a site.

    Returned by both `heuristics.propose_selectors()` and `llm.propose_selectors()`,
    and used to wrap an existing module's selectors when `improvise` loads it.
    """

    content_selectors: list[str] = Field(default_factory=list)
    noise_selectors: list[str] = Field(default_factory=list)
    reasoning: str = ""
