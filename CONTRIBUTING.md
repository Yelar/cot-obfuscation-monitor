# Contributing

Contributions should preserve experimental provenance and avoid silently changing frozen inputs.

1. Create a focused branch.
2. Run `python -m cot_obfuscation_repro.audit` and `python -m pytest`.
3. Regenerate plots when result-summary or plotting code changes.
4. Record new dataset revisions, hashes, seeds, and transformations.
5. Do not commit credentials, private Tinker URIs, or raw private reasoning traces.
6. Clearly label deviations from the frozen protocol as new experiments instead of reproductions.
