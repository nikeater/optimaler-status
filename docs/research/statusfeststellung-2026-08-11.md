# Statusfeststellungsverfahren (par. 7a SGB IV): Research and Implementation Proposal

Research report, 2026-08-11, for part 03b. Verified against the current statute text, the live DRV V0027 form (V0027-00), BT-Drs. 21/1059, and Bundestag WD 6-3000-029/25. Sources and uncertainty markers at the end. This document was the primary input to the part 03b build.

## 0. Repo facts this proposal is built against

- Procedure configs: `config/procedures/altersrente_v1.yaml`, `erwerbsminderungsrente_v1.yaml`. Blocks: `flags`, `requirements`, `field_map`, `clear_cut`, `derivation`, `nachforderung`.
- `clear_cut` and `derivation` are OPTIONAL (`engine/config_loader.py`: `ClearCutCriteria | None`, `DerivationSignals | None`). A procedure may honestly omit `clear_cut` entirely.
- Validation vocabulary (`SUPPORTED_VALIDATION_KEYS`): `pattern`, `one_of`, `min_length`, `max_length`, `date` (absolute bounds only), `cross_field` (`birthdate_in_vsnr`, `min_years_after`).
- `kind: document` requirements currently ALWAYS report MISSING (`engine/evidence/completeness.py`: "Dokumentenpruefung ist noch nicht implementiert"); EM defers its Befundbericht with TODO(part 04).
- Decision table `table_v1.yaml`: tier-1 row requires `procedure.tier1_enabled == true` AND `procedure.clear_cut == true`; tier-2 row requires `completeness.verdict == incomplete` plus routing confidence >= 0.9; else `default_tier: 3`.
- Routing `routing_v2.yaml`: priority bands 10 (Zustaendigkeits-Vorrang), 20 (content), 40 (derived procedure), 50 (channel hint); every rule declares fixtures.
- Scenario kinds in use: `complete_clear`, `missing_field`, `invalid_field`, `ambiguous_conflicting`, `hint_missing`, `anomalous_rule_passing`; per item `scenario_id`, `kind`, `description`, `procedure_id`, `procedure_hint`, `facts`, `expected {unit_id, tier, derivation_source, gaps}`, optional `anomaly_expected`/`anomaly_pattern`/`notes`.
- Taxonomy honesty rules per `drv_bund_v1.yaml`: functions citable where publicly established; unit numbers and Zuschnitt marked as derived placeholders.

## 1. Legal analysis

### 1.1 Par. 7a SGB IV as reformed 1 Apr 2022 (status as of Aug 2026)

- Abs. 1: Beteiligte may apply to DRV Bund for a decision whether an Auftragsverhaeltnis is Beschaeftigung or selbststaendige Taetigkeit (Elementenfeststellung of the ERWERBSSTATUS, no longer of the Versicherungspflicht - the 2022 core change). Satz 2: the Einzugsstelle MUST file (obligatorisches Verfahren) when the par. 28a SGB IV Meldung indicates Ehegatte/Lebenspartner/Abkoemmling of the Arbeitgeber or geschaeftsfuehrender GmbH-Gesellschafter.
- Abs. 2 Satz 1: decision "auf Grund einer Gesamtwuerdigung aller Umstaende des Einzelfalles". Satz 2-3 (befristet): Dreieckskonstellationen.
- Abs. 3: DRV Bund names required Angaben and Unterlagen and sets a Frist (the Nachforderung basis).
- Abs. 4: Anhoerung before the decision (concretizes par. 24 SGB X).
- Abs. 4a (befristet): Prognoseentscheidung before Taetigkeitsaufnahme; changes within one month reported unverzueglich; substantial deviation -> Aufhebung per par. 48 SGB X.
- Abs. 4b/4c (befristet): gutachterliche Aeusserung fuer gleiche Auftragsverhaeltnisse (Gruppenfeststellung); reliance protection up to two years.
- Abs. 5: deferred Beginn der Versicherungspflicht if applied within ONE MONTH of Taetigkeitsaufnahme, Zustimmung, and interim Absicherung (pre-2022 this sat in Abs. 6 - old numbering). Abs. 6: Widerspruch und Klage have aufschiebende Wirkung; Abs. 6 S. 2 (befristet): muendliche Anhoerung im Widerspruchsverfahren on request.
- Abs. 7: befristete provisions expire 30 Jun 2027. Erfahrungsbericht was delivered; BMAS announced publication 17 Feb 2026. No Entfristung found in force as of this research: mark as "befristete Instrumente, Entfristung offen". (Uncertainty: later 2026 legislation may exist; Koalitionsvertrag 2025 commits to a reform.)
- Context: Herrenberg/Musikschullehrer-II line (BSG 28.06.2022, B 12 R 3/20 R) tightened the Gesamtabwaegung (tatsaechliche Ausgestaltung controls); par. 127 SGB IV special transitional regime for Lehrtaetigkeiten extended to end 2027 (March 2026 law).

### 1.2 Substantive criteria and why no clear_cut can exist

Par. 7 Abs. 1 SGB IV Anhaltspunkte: Taetigkeit nach Weisungen, Eingliederung in die Arbeitsorganisation des Weisungsgebers. Par. 611a BGB codifies the parallel definition. BSG criteria in the Gesamtabwaegung: Weisungsgebundenheit (Zeit, Dauer, Ort, Art), Eingliederung, unternehmerisches Risiko, eigene Betriebsstaette, Verfuegungsmoeglichkeit ueber die eigene Arbeitskraft, Honorarhoehe as gewichtiges Indiz (BSG 31.03.2017, B 12 R 7/15 R). Since Musikschullehrer II: gelebte Praxis decides, keine abstrakten Zuordnungen nach Berufsbildern.

CONCLUSION (verified): the outcome is by statutory design a Gesamtwuerdigung aller Umstaende des Einzelfalles. "Die Statusbeurteilung sei stets im Einzelfall vorzunehmen, nicht abstrakt" (BT-Drs. 20/12811 via WD 6-029/25). Therefore: `tier1_enabled: false` mandatory; NO `clear_cut` block at all (unlike EM, no documented target state either - here the Gesamtabwaegung IS the statutory decision form). Triage value is completeness + routing + anomaly flags only.

### 1.3 Legal automation map

- receipt_confirmation: automatable (Realakt).
- nachforderung: prepared_release - par. 7a Abs. 3 SGB IV expressly has DRV Bund name Unterlagen and set a Frist; machine-prepared, human-released is well within it. Cooperation duties: par. 28o Abs. 2 SGB IV, par. 196 Abs. 1 SGB VI, par. 98 Abs. 1 SGB X (cited on the V0027 form itself).
- decision: prepared_only is the ceiling; fully_automated: never - par. 31a SGB X blocked by the Beurteilungsspielraum of par. 7a Abs. 2 S. 1; Anhoerung duties presuppose a responsible human. Unlike EM there is no realistic future flip.
- AI Act: Annex III 4(b) (decisions affecting terms of work-related relationships) is addressed to employment contexts, not squarely to a social-insurance determination; 5(a) (benefits eligibility) also not squarely met (Statusfeststellung establishes an obligation, not benefit eligibility) - but benefits-adjacent. Honest posture: any outcome-influencing ML would make high-risk classification strongly arguable; EingangsLotse confines ML to log-only downgrade-only flags and the tier never leaves 2/3 human handling for this procedure. This is analysis, not settled guidance.

### 1.4 The application in practice

Forms (verified live, Aug 2026): V0027 Antrag auf Feststellung des Erwerbsstatus (two checkboxes: Antrag nach Abs. 1 S. 1 vs. Prognoseantrag nach Abs. 4a S. 1); V0028 Erlaeuterungen; C0031 Beschreibung des Auftragsverhaeltnisses; C0032 Anlage GmbH-Gesellschafter/Geschaeftsfuehrer, Kommanditisten; C0033 Anlage mitarbeitende Angehoerige; C0050/C0051 gutachterliche Aeusserung (Gruppenfeststellung).

V0027 field inventory (by Ziffer): 1 Auftragnehmer (Name, Geburtsdatum, Versicherungsnummer, Staatsangehoerigkeit, Anschrift); 2 Auftraggeber (Firmenname, Betriebsnummer, Inhaber, Adresse); 3 Auftragsverhaeltnis (Bezeichnung der Taetigkeit, Beginn, ggf. Ende, Einkommen ueber Geringfuegigkeitsgrenze - form quotes "603 Euro ab 1.1.2026" -, Rahmenvertrag, 3.2 Vertraege in Kopie: ohne diese "kann ueber den Erwerbsstatus NICHT entschieden werden"); 4 Verwandtschaft (-> C0033); 5 GmbH/KG (-> C0032, sonst C0031); 6 Dreiecksverhaeltnis; 7 weitere Verfahren (Sperrwirkung Abs. 1 S. 1 Hs. 2); 8 vorhandene gutachterliche Aeusserung; 9 Antragsteller-Rolle (auftragnehmer/auftraggeber/gemeinsam); 10-12 Krankenkasse, Bestaetigung des Vertragspartners, Betriebspruefung, AUE-Erlaubnis; 14/15 Erklaerungen; 16 Anlagen checklist.

Who may apply: Auftragnehmer, Auftraggeber, both jointly; since 2022 Dritte in Dreieckskonstellationen (befristet); the Einzugsstelle in obligatorischen Faellen. Prognose before Aufnahme possible.

## 2. Implementation proposals (a-e)

### a. Requirement list (procedure_id `statusfeststellung`, version `statusfeststellung_requirements_v1`)

Flags: `tier1_enabled: false`, `fully_automated: false`. No `clear_cut` block; header comment states why (par. 7a Abs. 2 S. 1 Gesamtwuerdigung; no klar constellation exists; unlike EM no target state). Placeholder caveat in the header like the sister files.

1. `versicherungsnummer` (field): `pattern: "^[0-9]{8}[A-Z][0-9]{3}$"` + `cross_field birthdate_in_vsnr` vs. `geburtsdatum`. Detail texts as in sister configs.
2. `geburtsdatum` (field): pattern + `date: {min: 1900-01-01, max: 2012-12-31}`.
3. `antragsart` (field): `one_of: [feststellung_nach_aufnahme, prognose_vor_aufnahme]`. A wrong value is a human question (the form "umdeutet" impossible Prognosen), not a rejection.
4. `antragsteller_rolle` (field): `one_of: [auftragnehmer, auftraggeber, gemeinsam]` (V0027 Ziffer 9.1).
5. `taetigkeit_bezeichnung` (field): `min_length: 3, max_length: 200` (Ziffer 3.1).
6. `taetigkeit_beginn` (field): pattern + `date: {min: 1990-01-01, max: 2035-12-31}` (FUTURE dates are legitimate - Prognoseantrag; never validate against "today") + `cross_field min_years_after geburtsdatum 14` (par. 5 JArbSchG floor).
7. `auftraggeber_name` (field): `min_length: 3, max_length: 200`. Without an identified Auftraggeber no beurteilbares Auftragsverhaeltnis.
8. `vertrag_kopie` (kind: document): "Saemtliche Vertraege in Kopie (V0027 Ziffer 3.2)". ENGINE CAVEAT: with completeness.py as-is any document requirement reports MISSING for every item, forcing every scenario to tier 2 and hiding the "complete but judgment call -> tier 3" shape that is this procedure's pedagogical point. RECOMMENDATION: ship as TODO(part 04) header comment like EM's Befundbericht, NOT as an active requirement. Activating it anyway changes all complete_* expectations to tier 2 with gap `vertrag_kopie: missing` - a conscious decision, never an accident.

Nachforderung wording: reuse sister texts for geburtsdatum/versicherungsnummer; new: taetigkeit_bezeichnung missing "Bitte bezeichnen Sie die zu beurteilende Taetigkeit genau (V0027, Ziffer 3.1)."; auftraggeber_name missing "Bitte nennen Sie Firmenname und Anschrift Ihres Auftraggebers."; antragsart invalid "Bitte geben Sie an, ob der Erwerbsstatus fuer eine bereits aufgenommene Taetigkeit oder im Wege der Prognose vor Aufnahme der Taetigkeit festgestellt werden soll ({problem})."; vertrag_kopie missing "Bitte reichen Sie saemtliche Vertraege und Vertragsunterlagen zum Auftragsverhaeltnis in Kopie ein. Ohne diese Unterlagen kann ueber den Erwerbsstatus nicht entschieden werden (par. 7a Abs. 3 SGB IV)."

### b. field_map

```
antragsteller.versicherungsnummer -> versicherungsnummer
antragsteller.geburtsdatum        -> geburtsdatum
antrag.antragsart                 -> antragsart
antrag.antragsteller_rolle        -> antragsteller_rolle
antrag.taetigkeit_bezeichnung     -> taetigkeit_bezeichnung
antrag.taetigkeit_beginn          -> taetigkeit_beginn
auftraggeber.firmenname           -> auftraggeber_name
```

Indizien payload paths deliberately NOT requirements (turning Indizien into Pflichtfelder would fake a checklist where the law prescribes an Abwaegung): `antrag.weisungsgebunden`, `antrag.eingliederung_arbeitsorganisation`, `antrag.arbeitsort` (beim_auftraggeber/eigene_betriebsstaette/wechselnd), `antrag.weitere_auftraggeber`, `antrag.umsatzanteil_hauptauftraggeber` (percent), `antrag.honorar_modell` (fest_monatlich/nach_stunden/nach_ergebnis), `antrag.honorar_monatlich`, `antrag.rahmenvertrag`, `antrag.dreiecksverhaeltnis`, `auftraggeber.betriebsnummer`.

### c. Routing

Taxonomy node: `unit_id: Referat_340_Clearingstelle`, name "Referat 340 - Clearingstelle Statusfeststellung", parent `GB_Versicherung_Rente` (placeholder attachment), responsibilities: Feststellung des Erwerbsstatus nach par. 7a SGB IV (optionale und obligatorische Verfahren); Prognoseentscheidungen und gutachterliche Aeusserungen. Source line (honest): exclusive nationwide DRV-Bund competence is statutory (BT-Drs. 21/1059 answer 4: other Traeger forward applications); "Clearingstelle" is DRV Bund's own public name; Berlin address printed on V0027 (10704 Berlin); Referatsnummer, Zuschnitt und Einhaengung are derived placeholders - internal org position is not publicly documented.

Rules (all with fixtures): `rule_statusfeststellung_antragsart` prio 20 (`payload.antrag.antragsart in [...]`); `rule_statusfeststellung_auftraggeber` prio 20 (`all: [payload.auftraggeber.firmenname ne null, payload.antrag.taetigkeit_bezeichnung ne null]` - no other procedure has an `auftraggeber.*` namespace); `rule_statusfeststellung_verfahren` prio 40 (`procedure_id eq statusfeststellung`); `rule_statusfeststellung_hint` prio 50. NO priority-10 Auslands rule: par. 7a has no Auslandssonderzustaendigkeit (BT-Drs. 21/1059 answer 23) - add a fixture asserting an `auslandsbezug: ja` case STAYS with the Clearingstelle and does not reroute to Referat_318.

Derivation block `statusfeststellung_content_v1`: `any: [antragsart in [...], auftraggeber+taetigkeit signature]`; house rule restated: if another procedure's signal also fires, no procedure derived, tier 3.

### d. Scenario file `corpus/generator/scenarios/statusfeststellung.yaml` (prefix `sf-`)

Tier analysis for complete items: tier-1 row cannot fire (tier1_enabled false, clear_cut absent); tier-2 row requires incomplete; a complete, routable Statusantrag matches NO row -> `default_tier: 3`. This is the EM precedent (`em-0001`): a formally complete application ends at tier 3 by intent. Label honestly: `kind: complete_clear`, `expected.tier: 3`, `gaps: []`, note "vollstaendig erfasst, zugeordnet, in voller fachlicher Wuerdigung beim Menschen". Do NOT invent a tier 2.5 or force tier 2.

- complete_clear (tier 3, 4 items): sf-0001 IT-Beraterin, feststellung_nach_aufnahme, rolle auftragnehmer, weitere_auftraggeber ja, eigene_betriebsstaette, nach_stunden (hint, derivation hint); sf-0002 Prognoseantrag with taetigkeit_beginn ~3 months in the FUTURE (documents future Beginn as valid, Abs. 4a); sf-0003 gemeinsame Antragstellung, Rahmenvertrag ja; sf-0004 Auftraggeber-initiated (rolle auftraggeber).
- missing_field (tier 2, 4 items): sf-0010 ohne versicherungsnummer; sf-0011 ohne taetigkeit_bezeichnung; sf-0012 ohne auftraggeber_name (auftraggeber content rule cannot fire; hint carries routing); sf-0013 ohne antragsart und taetigkeit_beginn (two gaps).
- invalid_field (tier 2, 4 items): sf-0020 VSNR 11 Stellen; sf-0021 taetigkeit_beginn Punktformat; sf-0022 antragsart unbekannt (`gruppenfeststellung` - a C0050 matter mislabeled into V0027); sf-0023 taetigkeit_beginn before 14th birthday (cross_field).
- ambiguous_conflicting (tier 3, 3 items): sf-0030 widerspruechliche Weisungsangaben (weisungsgebunden nein + Eingliederung ja + feste Arbeitszeiten beim Auftraggeber; complete -> tier 3; the Gesamtabwaegung a human must do); sf-0031 hint statusfeststellung but payload carries rentenart regelaltersrente + rentenbeginn (two procedures' signals fire, derivation none, tier 3; mirror of ar-0033); sf-0032 Prognose with Beginn in the PAST (both values individually valid; no clock-relative cross_field exists by design; V0027 "umdeutet" such an Antrag - a legal recharacterization only a human may make; alternative clock-free cross-field kind would be a contract request).
- anomalous_rule_passing (tier 3, 3 items; NOTE: unlike altersrente's anomalous items these already sit at tier 3, so the downgrade is a tier no-op - the value is the FLAG in the journal; say so in notes): sf-0040 klassische Scheinselbststaendigkeit (umsatzanteil_hauptauftraggeber 100, arbeitsort beim_auftraggeber, honorar_modell fest_monatlich, weitere_auftraggeber nein, weisungsgebunden nein claimed; pattern `scheinselbststaendigkeit_indizienbuendel`; the 5/6-Umsatz practice heuristic is Verwaltungswissen - do not hard-code); sf-0041 Honorar far below Vergleichslohn (pattern `honorar_niedrig_vs_taetigkeit`, BSG B 12 R 7/15 R); sf-0042 taetigkeit_beginn 15+ years in the past (pattern `taetigkeit_beginn_abstand_tage`, analogous ar-0041; Beitragsnachforderung horizon par. 25 SGB IV).
- hint_missing (2 items): sf-0050 no hint, antragsart present -> derived content, tier 3; sf-0051 no hint, no antragsart, auftraggeber+taetigkeit signature -> derived content, one gap (antragsart) -> tier 2.

### e. Legal automation map entry (per-procedure data)

`receipt_confirmation: automatable` (Realakt); `nachforderung: prepared_release` (par. 7a Abs. 3); `decision: prepared_only`, `fully_automated: never` (par. 31a SGB X vs. Gesamtwuerdigung; Anhoerung par. 24 SGB X / par. 7a Abs. 4; muendliche Anhoerung Abs. 6 S. 2 befristet); `ai_act_note`: triage-only posture, outcome-influencing ML would raise Annex III 4(b)/5(a) questions (analysis, not settled).

## 3. Realism figures (BT-Drs. 21/1059, official)

- Optionale Verfahren: 21,000-23,000 Feststellungen/year 2015-2024 (2023: 22,649; 2024: 23,052; H1/2025: 13,212).
- Outcomes 2024: 9,397 abhaengig beschaeftigt vs. 13,057 selbststaendig (~40/60 across years).
- Bearbeitungszeit 2024: optional avg 82 days, obligatorisch avg 33 days (prefer these over "3-6 months" folklore).
- Widersprueche 2024: 3,002 (669 ganz/teilweise erfolgreich); Klagen 2024: 1,665 (455 erfolgreich).
- No Berufsgruppen breakdown exists officially - do not invent one. clearingstelle.de's ">15,000/year" is a private undercount; prefer BT-Drs.

## 4. Sources

- par. 7a SGB IV: https://www.gesetze-im-internet.de/sgb_4/__7a.html ; par. 7 SGB IV: https://www.gesetze-im-internet.de/sgb_4/__7.html ; par. 611a BGB: https://www.gesetze-im-internet.de/bgb/__611a.html ; par. 31a SGB X: https://www.gesetze-im-internet.de/sgb_10/__31a.html
- BMAS Feststellung des Erwerbsstatus: https://www.bmas.de/DE/Soziales/Sozialversicherung/Sozialversicherungspflichtige-Beschaeftigung/Feststellung-des-Erwerbsstatus/feststellung-des-erwerbsstatus-art.html
- BMAS Meldung 17.02.2026 (DRV-Bericht): https://www.bmas.de/DE/Service/Presse/Meldungen/2026/drv-veroeffentlicht-bericht-ueber-reformiertes-verfahren.html
- BT-Drs. 21/1059: https://dserver.bundestag.de/btd/21/010/2101059.pdf
- Bundestag WD 6-3000-029/25: https://www.bundestag.de/resource/blob/1103572/WD-6-029-25.pdf
- DRV Formularpaket Statusfeststellung: https://www.deutsche-rentenversicherung.de/SharedDocs/Formulare/DE/Formularpakete/01_versicherte/01_vor_der_rente/_DRV_Paket_Versicherung_Statusfeststellung.html
- V0027 PDF: https://www.deutsche-rentenversicherung.de/SharedDocs/Formulare/DE/_pdf/V0027.pdf
- DRV Clearingstelle: https://www.deutsche-rentenversicherung.de/DRV/DE/Rente/Arbeitnehmer-und-Selbststaendige/03_Selbststaendige/clearingstelle-drv-bund.html
- BSG PM 14/2017 (B 12 R 7/15 R): https://www.bsg.bund.de/SharedDocs/Pressemitteilungen/DE/2017/Pressemitteilung_2017_14.html
- AI Act Annex III: https://artificialintelligenceact.eu/annex/3/

Uncertainty markers: (1) possible late-2026 legislation on the 7a sunset not surfaced; (2) internal org position of the Clearingstelle not publicly documented - taxonomy entry worded as placeholder; (3) AI Act applicability is legal analysis, not settled guidance; (4) form version checked is V0027-00 Version 31027 (live Aug 2026); re-verify form ids before pilot.
