# Release provenance

This directory is a cleaned extraction from a private research workspace frozen on 2026-08-08. The private workspace contained exploratory branches, failed runs, API environments, raw private checkpoint paths, and unrelated follow-up investigations. None of those are necessary to reproduce the final experiment.

The release retains:

- final leak-hardened compositional corpora;
- exact public source revisions and content hashes;
- the historical raw-document SDFT trainer;
- a clean evaluator implementing the final seeded/resampling protocol;
- conservative metric and selector implementations;
- deidentified count-preserving results;
- generation/review inputs and scripts;
- one-command experiment and figure-generation workflows.

The release removes:

- `.env` and all credential material;
- virtual environments and caches;
- private Tinker run/state/sampler URIs;
- W&B run state;
- raw hosted-model reasoning traces from private evaluations;
- aborted dataset-forensics and checkpoint-reconstruction studies;
- duplicate, superseded, and failed pipelines.

## Protocol clarification

The actual shared checkpoint used for the reported compositional runs came from the 1,272-row behavioral dataset, batch size 32, four epochs, selected at epoch 4. Stage 2 used five epochs. This release documents that executable provenance explicitly instead of using the simplified “1,000 documents each” description.

The frozen result table uses a conservative recalculation that counts monitor-ineligible and monitor-error concealments as uncaught. Some earlier internal reports excluded those rows from detection and uncaught denominators; those superseded figures are not presented as primary results here.
