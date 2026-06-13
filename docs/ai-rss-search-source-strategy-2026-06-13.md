# AI / RSS / Search Source Strategy (2026-06-13)

## Context

This note records the source and retrieval investigation for the polymer materials intelligence platform. The immediate product direction is:

- Keep the current RSS approach for the AI-related lane.
- Strengthen search because it remains the most important discovery channel.
- Treat source quality, query design, and evidence packaging as architecture concerns, not just prompt wording.

## Current Observations

- The existing source table already contains useful candidates, but RSS, listing pages, direct sources, search-only sources, and auxiliary sources are mixed together.
- Recent runs show that source quality gates are not strong enough: some reports were filled mostly or entirely by C-tier industry report pages such as `cir.cn`.
- A report can be marked publishable while markdown generation is weak or empty. Publishing gates should include content-shape checks, not only article counts.
- Search results currently lose too much intent metadata. Template searches know their intended section and language, but merged result rows do not consistently retain query family, section intent, source tier, or why the URL was discovered.

## Source Governance Model

Use five source classes:

| Class | Examples | Main Use | Publish Role |
| --- | --- | --- | --- |
| S/A primary | government, standards, journals, official university/lab, company newsroom | primary evidence | can lead a report item |
| B professional | associations, trade media, exhibition official media, engineering magazines | industry context | can lead with quota |
| C auxiliary | market report sites, aggregators, finance reposts, public accounts | background only | limited, rarely lead |
| D blocked | PR wires, B2B marketplaces, low-quality SEO, pure marketing | none | blocked |
| Watch-only | price snapshots, catalog pages, forums, job pages | monitoring | never lead |

Suggested publish gates:

- A/B sources should be at least 60% of report items.
- C sources should be at most 25% of report items.
- A single domain should normally contribute no more than 1-2 items per daily report.
- If `sections_content` is empty or markdown is too short, do not mark `complete_auto_publish`.
- If all selected items are C-tier, force `hold_for_missing_quality`.

## RSS Findings

### Keep / Add Immediately

Nature subject feeds tested as usable:

- `https://www.nature.com/subjects/materials-science.rss`
- `https://www.nature.com/subjects/polymer-chemistry.rss`
- `https://www.nature.com/subjects/electrochemistry.rss`

ACS feeds tested as usable:

- ACS Macro Letters
- Macromolecules
- Biomacromolecules
- ACS Applied Polymer Materials
- ACS Polymers Au
- ACS Sustainable Chemistry & Engineering

ScienceDirect feeds tested as usable:

- Polymer
- Progress in Polymer Science
- Polymer Degradation and Stability
- Reactive and Functional Polymers
- Additive Manufacturing
- Journal of Membrane Science
- Carbohydrate Polymers
- Polymer Testing
- Composites Science and Technology
- Composites Part B
- Journal of Power Sources
- Energy Storage Materials
- Sustainable Materials and Technologies

Chinese source tested as usable:

- `https://www.gaofenzi.org/feed`

### Do Not Rely On

- RSC guessed RSS URLs returned 404 in testing.
- arXiv category RSS returned empty in a simple test. Use arXiv API keyword queries instead of broad category RSS.
- Many trade media pages are crawlable but do not expose stable RSS; model them as listing/search sources.

## AI Lane Scope

Do not ingest generic AI product news into the polymer platform. AI content should be limited to:

1. AI for materials discovery: GNoME, MatterGen, Materials Project, autonomous labs.
2. AI for polymer science: property prediction, formulation optimization, polymer informatics, literature graphing.
3. AI for manufacturing: injection molding optimization, extrusion control, digital twins, defect detection, predictive maintenance.

Recommended retrieval channels:

- arXiv API keyword queries for materials informatics and AI polymer work.
- OpenAlex/Crossref for recent peer-reviewed papers and DOI metadata.
- Official research blogs from DeepMind, Microsoft Research, Berkeley Lab, Materials Project, universities, and major journals.

Example AI queries:

- `polymer machine learning property prediction`
- `materials informatics polymer`
- `injection molding process optimization machine learning`
- `extrusion digital twin polymer`
- `generative AI materials discovery polymer`
- `autonomous laboratory polymer synthesis`

## Search Strategy

Search should become structured exploration, not a flat list of broad templates.

### Query Families

For each section and category, maintain query families:

- freshness queries: recent news and press releases
- primary-source queries: official domains, universities, companies, agencies
- evidence queries: standards, policy pages, DOI pages, patents
- context queries: trade media and professional media

### Query Metadata

Every search result should carry:

- `query`
- `query_family`
- `intended_section`
- `intended_category`
- `language`
- `provider`
- `source_tier`
- `source_kind`
- `page_kind`
- `discovery_reason`

This metadata is needed for candidate scoring, debugging, and user-facing "why selected" explanations.

### Search Quotas

Daily ingester quotas should be explicit:

- industry: broad media + primary company sources
- policy: government/standards first, trade media second
- academic: journal/API feeds first, news second
- AI lane: AI-for-materials only, not general AI

## Implementation Direction

P0:

- Preserve template query metadata when writing `ArticlePool`.
- Add source-tier and domain diversity gates before publishing.
- Prevent `complete_auto_publish` when markdown content is fallback/too short.

P1:

- Add arXiv API ingester for keyword searches.
- Add listing adapters for company newsroom and high-value Chinese industry pages.
- Add source quota scoring before Agent evaluation.

P2:

- Add `EvidencePack` records with source excerpts and extracted claims.
- Move ResearchAgent local retrieval from SQL LIKE to semantic retrieval.
- Build user-specific watch topics and weekly feedback.

