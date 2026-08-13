# ADR-002: Sealed Identity Vault with Randomized Placeholders

**Status:** Accepted, 2026-08-10

## Context
Personal identifiers must never reach model calls or logs (DSGVO, DSFA posture, fairness: the scorer cannot condition on what it never sees). Redaction must be verifiable, and outbound drafts still need real identity data.

## Options
1. Inline masking with guessable tokens like [NAME].
2. Sealed vault at ingest + collision-resistant randomized placeholders from a reserved alphabet; post-redaction verification pass; re-hydration only at template render, in code.

## Decision
Option 2. PII splits into an encrypted PostgreSQL vault at ingest; the working copy carries randomized placeholder ids that document text cannot collide with and a model cannot plausibly invent. A second detector sweep over the redacted text must find nothing. Re-hydration happens strictly at outbound template rendering, round-trip checked: an unknown placeholder is a hard error that blocks output.

## Consequences
- All model calls and logs process pseudonymized content only; canary tests enforce zero leakage per commit.
- The scorer is identity-blind by construction (fairness guard for free).
- Cost: vault storage plus placeholder registry, and every outbound path must go through the checked re-hydrator.
