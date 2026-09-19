# Preferred relaxes a relevance threshold; it never force-supplies

A Preferred Skill lowers the confidence bar for a positive Judgment but is never injected
on that basis alone. It still requires a Judgment that finds it relevant.

_Considered options_: "preferred = always include". Rejected because it makes profile
authoring a hidden grant path, turning preference into authority — authority that
deterministic code is meant to hold exclusively.
