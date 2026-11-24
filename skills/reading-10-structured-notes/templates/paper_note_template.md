---
paper_id: "{{paper_id}}"
title: "{{title}}"
authors: {{authors}}
status: "{{status}}"
tags: {{tags}}
---

# {{title}}

## 1. Unified Paper Card

| Field | Extracted Value | Verification Tier | Provenance Anchors |
|---|---|---|---|
| Research Question | {{research_question}} | `needs_human_review` | {{rq_anchors}} |
| Data & Materials | {{data_and_materials}} | `needs_human_review` | {{data_anchors}} |
| Methods & Setup | {{methods_and_setup}} | `needs_human_review` | {{method_anchors}} |
| Conditions & Preconditions | {{operating_conditions}} | `needs_human_review` | {{condition_anchors}} |
| Key Findings & Metrics | {{key_findings}} | `machine_verified` | {{finding_anchors}} |
| Limitations & Validity | {{limitations}} | `needs_human_review` | {{limitation_anchors}} |
| Unreported / Missing | {{unreported_items}} | `needs_human_review` | — |

## 2. Section-Aligned Notes (Original Paper Skeleton)

{{section_notes_markdown}}

## 3. Searchable Knowledge Entries & Terminology Glossary

{{knowledge_entries_markdown}}

{{terminology_glossary_markdown}}

## 4. Intra-Paper Discrepancies & Uncertainties

{{conflicts_and_uncertainties_markdown}}

## 5. Verification Ledger (Machine-Verified vs Human-Review)

{{verification_ledger_markdown}}
