# EingangsLotse: Stack-Annotated Technical Architecture

Companion to the conceptual diagrams in `eingangslotse-implementation-plan.md` (plan wins on conflict). Contracts live in `eingangslotse/schemas/`; this diagram annotates each component with its concrete stack.

**As-built status (post part 10, 2026-08-12):** every component and wiring rule in this diagram exists and is tested; the stack annotations state the production TARGET where phase 0 runs dev backends (JSONL/in-memory stores instead of PostgreSQL 16, plain CSS instead of Tailwind, no pgvector index, correction-pool consumption still pilot scope). The consolidated as-built record, including those deltas and every measured number, is [technical-spec.md](technical-spec.md).

```mermaid
flowchart TD
    subgraph INGEST_LAYER ["1. Ingestion & Adapters (FastAPI / REST API / Pydantic v2)"]
        FC["FIT-Connect Payload Adapter<br/><code>REST API / OZG JSON</code>"]
        EM["Email Receiver Adapter<br/><code>IMAP / MIME Parser</code>"]
        SC["Scan Output Adapter<br/><code>TR-RESISCAN PDF + OCR Text</code>"]
        NORM["Channel Normalizer<br/><code>Envelope Builder / Pydantic v2</code>"]
    end

    subgraph PRIVACY_BOUNDARY ["2. Privacy & Identity Boundary (Presidio / spaCy / Custom Recognizers)"]
        VALT[("Identity Vault<br/><code>PostgreSQL 16 JSONB, encrypted at rest</code><br/>Sealed PII + placeholder mapping")]
        REDACT["NER & Pattern Redactor<br/><code>spaCy de_core_news_lg + Presidio + Custom Regex</code><br/>Collision-Resistant Randomized Placeholder Registry"]
        CANARY["Post-Redaction Verification Pass<br/><code>Second-Detector Sweep + Canary Assertions</code>"]
    end

    subgraph EVIDENCE_PLANE ["3. Evidence Plane (Probabilistic: produces evidence only)"]
        TEXTLAYER["Normalized Text Layer<br/><code>Unicode NFC / De-Hyphenation / Offset Map</code>"]
        STRUCT["Structured Schema Mapper<br/><code>Pydantic v2 Direct Mapper (no LLM)</code>"]
        UNSTRUCT["LLM Value Extractor<br/><code>Ollama dev / vLLM pilot (Mistral- or OpenGPT-X-class)</code><br/>JSON-Schema Constrained Decoding"]
        SPANVER["Span Verification Engine<br/><code>Exact + Bounded-Fuzzy Offset Matcher (rapidfuzz)</code>"]
        ROUTEEV["Routing Evidence<br/><code>YAML Rules first + e5-multilingual Embeddings / pgvector</code><br/>Calibrated Confidence"]
        COMPEV["Completeness Evaluator<br/><code>Declarative YAML Requirement Lists</code><br/>Gap List with Span References"]
        SHADOW["ML Shadow Risk Gate<br/><code>scikit-learn IsolationForest + Consistency Features</code><br/>Unsupervised (phase 1), Identity-Blind, Log-Only First<br/>Supervised upgrade: pilot phase only, same valve"]
    end

    subgraph DECISION_PLANE ["4. Decision Plane (Deterministic: makes every decision)"]
        DECIDE{"Tier Decision Table<br/><code>Pure Functional Evaluator over Versioned Config</code>"}
        CONFIG["AgencyRiskConfig<br/><code>Pydantic v2 / YAML: thresholds, downgrade conditions,<br/>efficiency budget, per-procedure tier-1 flags</code>"]
    end

    subgraph DISPATCH_LAYER ["5. Drafting, Review & Dispatch (Jinja2 / HTMX / REST)"]
        DRAFT["Conditional Draft Generator<br/><code>Jinja2 Templates + Vault Re-hydration (round-trip checked)</code>"]
        UI["Caseworker Review UI<br/><code>HTMX + Tailwind + BITV 2.0</code><br/>Role-Based Unit Queues, Evidence Spans, Anomaly Reasons"]
        CC["Correction Capture<br/><code>Override Events -> Labeled Training Pool</code><br/>Gold set stays frozen"]
        DISP_AMT["Agency Unit Queue Dispatcher<br/><code>xdomea-shaped XML Export (pilot adapter)</code>"]
        DISP_CIT["Applicant Notification Dispatcher<br/><code>Journal Projection Worker -> FIT-Connect Status / SMTP</code><br/>AUTOMATED, Informational Realakte only, no VA"]
    end

    subgraph JOURNAL_LAYER ["6. Audit & Journal"]
        JRNL[("Append-Only Case Journal<br/><code>PostgreSQL 16 Event Store</code><br/>Version-Stamped: audit, AI Act, Art. 22 proof, training data")]
    end

    %% Pipeline Connections
    FC -->|HTTP POST JSON| NORM
    EM -->|Parsed Text| NORM
    SC -->|OCR Text| NORM
    NORM -->|Sealed identity fields| VALT
    NORM -->|Unredacted stream| REDACT
    NORM -.->|"received" event| JRNL
    REDACT --> CANARY
    CANARY -->|Redacted working copy| TEXTLAYER
    TEXTLAYER --> STRUCT
    TEXTLAYER --> UNSTRUCT
    UNSTRUCT -->|Extracted JSON| SPANVER
    SPANVER -->|Verified spans| ROUTEEV
    SPANVER -->|Verified spans| COMPEV
    SPANVER -->|Identity-blind features| SHADOW
    STRUCT --> ROUTEEV
    STRUCT --> COMPEV
    STRUCT -->|Identity-blind features| SHADOW

    ROUTEEV -->|Routing evidence| DECIDE
    COMPEV -->|Gap list evidence| DECIDE
    SHADOW -->|ONE-WAY VALVE: downgrade only,<br/>never qualifies tier 1| DECIDE
    CONFIG -->|Thresholds & monotonic rules| DECIDE

    DECIDE -->|Tier result + reasons| DRAFT
    VALT -.->|Re-hydration strictly at render, in code| DRAFT
    VALT -.->|Re-hydration at render| DISP_CIT
    DRAFT -->|Rendered drafts| UI

    UI -->|One-click confirm / override| DISP_AMT
    UI --> CC
    CC -.->|Training pool| ROUTEEV
    CC -.->|Pilot phase: supervised upgrade,<br/>same valve| SHADOW

    DECIDE -.->|Immutable event stamping| JRNL
    SHADOW -.->|Anomaly score + reasons log| JRNL
    UI -.->|Caseworker actions| JRNL
    JRNL -->|"received" -> instant receipt<br/>"routed" -> status update| DISP_CIT
```

## Key wiring rules (verify against any future edit)

1. Notifications flow **from the journal** (JRNL -> DISP_CIT), never from the UI.
2. The structured path feeds the shadow scorer too (STRUCT -> SHADOW), not only the LLM path.
3. The scorer's only edge into the decision plane is the downgrade-only valve (SHADOW -> DECIDE).
4. Re-hydration edges from the vault exist only at render time: VALT -> DRAFT and VALT -> DISP_CIT, dashed, in code.
5. The correction pool feeds the classifier now (CC -> ROUTEEV) and the supervised scorer only in the pilot phase (CC -> SHADOW, dashed).

## Contract-to-component map

| Contract (schemas/) | Produced by | Consumed by |
|---|---|---|
| Envelope | NORM (after REDACT/CANARY) | TEXTLAYER, STRUCT |
| TextLayer | TEXTLAYER | UNSTRUCT, SPANVER |
| ExtractionSet | SPANVER / STRUCT | ROUTEEV, COMPEV, SHADOW |
| EvidenceRecord | ROUTEEV + COMPEV | DECIDE |
| AnomalyEvidence | SHADOW | DECIDE (downgrade conditions only), JRNL |
| DecisionRecord | DECIDE | DRAFT, JRNL |
| Event | every stage | JRNL, DISP_CIT (projections) |
| DecisionTable / AgencyRiskConfig | agency-editable config | DECIDE |
