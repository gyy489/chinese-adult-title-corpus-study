# Sampling stage

The study used two explicitly distinguished components: a fixed-seed stratified
probability track and a smaller pre-specified mechanism-enriched track.
[`fixed_seed.py`](fixed_seed.py) provides the order-independent SHA-256 ranking,
Hamilton quota allocation, without-replacement selection, track labels, and
design summaries.

The final corpus contains 36,303 probability-track texts and 1,995
mechanism-enriched texts. Restricted frame rows and source-level cell counts are
not included, so the public repository demonstrates and tests the algorithm but
cannot recreate the confidential sample membership.
