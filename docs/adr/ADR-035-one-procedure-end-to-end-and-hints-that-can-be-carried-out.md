# ADR-035: One Procedure End to End, and Hints That Can Be Carried Out

**Status:** Accepted, 2026-08-18 (part 22, the Statusfeststellung refocus)

## Context

The intake surface has demonstrated three procedures at once since part 13. Four
fictional applicants sat side by side on `/demo/antrag`: two Regelaltersrente
forms (one complete and clear-cut, one missing its Rentenbeginn), one
Statusfeststellung letter with no channel hint, and one Altersrente form with a
start date twenty-two years out that the shadow scorer flags. Each was chosen to
show a different mechanism, and between them they covered every tier the
decision table can produce.

The user's direction of 2026-08-18 is to refocus the whole demonstration on ONE
procedure: Feststellung des Erwerbsstatus nach par. 7a SGB IV. The direction came
with the four persona texts and with a replacement for the seven-item hints
panel - five suggestions, written out, three of which instruct the visitor to
DELETE a field and submit.

Three inherited constraints shaped every decision below.

1. **Part 20 made every prefilled field required.** ADR-033 records the rule:
   what the persona arrived with has to be sent, expressed once over the config's
   own declaration rather than as a list of field names. It is exactly the rule
   that makes three of the five new hints impossible to carry out.
2. **The frozen gold corpus is 101 items of Altersrente, Erwerbsminderung,
   Statusfeststellung and unclassifiable mail, and it is verified by a
   byte-identical rebuild.** Nothing about a refocus may touch it, which means
   the eval, the queues and the seeded journal keep every Altersrente case they
   have.
3. **`statusfeststellung_v1.yaml` has `tier1_enabled: false` and no `clear_cut`
   block**, because par. 7a Abs. 2 S. 1 SGB IV orders a Gesamtwuerdigung and a
   checklist cannot produce one. Refocusing on that procedure therefore removes
   tier 1 from the intake surface entirely - a consequence, not a choice.

## Decision

### 1. All four intake personas file a Statusfeststellung, and the file supersedes to v4

`config/demo/personas_v3.yaml` becomes `personas_v4.yaml`. Three of the four
personas changed procedure, which changes their field ids, their payload paths,
their prepared documents and their letters - a shape change of the same weight
as the two that produced v2 (`name` split into `nachname` + `vorname`) and v3
(`attachments` added), so the house rule applies: supersede, and leave only the
current version on disk.

| persona | what it demonstrates | measured arc |
|---|---|---|
| Beate Schliebermann (NEW) | complete, procedure named by a channel hint | statusfeststellung, tier 3, Referat 340, complete, unflagged (score 0.257) |
| Bernd Beispielmann (converted) | one field left empty | tier 2, gap `taetigkeit_beginn: missing`, the procedure config's own Nachforderung sentence |
| Sabine Musterfrau (unchanged) | no channel hint at all | derivation source `content`, tier 3, Referat 340 |
| Theo Musterkind (converted) | complete and permissible, still unusual | tier 3, complete, FLAGGED at 0.960 against the 0.86 threshold, reason `leitdatum_abstand_jahre` |

Every arc was measured through the real pipeline before the text describing it
was written, bare and with every prepared document ticked, and is pinned by
`tests/test_demo_personas.py::FORM_ARCS`. The Regelaltersrente persona that
occupied the first row from part 13 to part 21 is gone; Bernd's conversion IS
the deprecation v3 announced for him.

**The two tiers on this surface read differently from every other page, and the
config header and the arc table both say so.** A COMPLETE par. 7a application
matches no row of the decision table - the tier-1 row fails on
`procedure.tier1_enabled`, the tier-2 row fails on `completeness.verdict` - and
lands on `default_tier` 3. An INCOMPLETE one matches the tier-2 row. So
completing a form does not improve its tier here; it changes what happens next.
Tier 2 is "routable, something is missing, ask for it" and tier 3 is "a human
decides the whole thing". Tier 1 is still demonstrated, in the queues and in the
eval, on the seeded Altersrente cases - which is where it belongs, because it is
a property of THAT procedure's configuration rather than of the machine.

### 2. Required is what the persona arrived with, MINUS what a hint tells you to delete

ADR-033's rule stands and gains one declared exemption list of three:

```python
HINT_DELETED_FIELDS = frozenset({"versicherungsnummer", "auftraggeber_name", "vorname"})
```

A list rather than a cleverer rule, and it lives three lines above the rule it
modifies. The reason a list is right here is that the exemption is not a property
of a field - nothing about a Versicherungsnummer makes it optional - it is a
property of a SENTENCE ON THE SAME PAGE. A page that prints "Leeren Sie das
Textfeld" and then has the browser refuse the submission is worse than either
half alone: it teaches a visitor that the demonstration does not work.

The list is countable, it is asserted against the personas (every field it names
is a field all four carry), and a sixth hint asking for a fourth deletion has to
add its field here. Everything else on the form keeps the part-20 treatment -
`required`, the red `:user-invalid` edge, and the pre-rendered sentence - and the
browser walk confirms both halves: emptying the Nachname blocks the submission
with a 220,0,0 edge and its sentence, emptying the Versicherungsnummer beside it
does not.

The intro paragraph on the form says which three and why, because a form that
behaves differently for three of its controls has to explain itself on the
screen where it does.

### 3. One value in the user's hint text was corrected, and the reason is arithmetic

The third hint reads "Beginn der Tätigkeit weit in die Zukunft legen - Wählen Sie
den 1. Januar 2048. Die Kalendergrenzen der Verfahrenskonfiguration fangen das
bewusst nicht ab."

2048 is the date the ALTERSRENTE version of this hint used, and
`altersrente_v1.yaml` bounds `rentenbeginn` at 2050-12-31, so it was true there.
`statusfeststellung_v1.yaml` bounds `taetigkeit_beginn` at 2035-12-31. Measured
on the real pipeline: 2048-01-01 produces `taetigkeit_beginn: invalid` with the
Nachforderung "Bitte pruefen Sie die Angabe zum Beginn der Taetigkeit (Datum
2048-01-01 liegt nach der Obergrenze 2035-12-31)" and a tier-2 case - so the
sentence "the calendar bounds deliberately do not catch this" would be false on
the very screen it is printed on, and the hint would teach the opposite of the
mechanism it names.

The date becomes 1 January 2035. Every other word of the user's hint is
unchanged, and the promise is now real: complete, tier 3, flagged at 0.960,
reason rendered in plain German. The persona whose card makes the same promise
carries 2035-07-01 for the same reason, +8.9 years from the day it was measured.

This is the one place where the contract text was changed rather than
implemented, and it is recorded here rather than absorbed: the alternative was a
hint that is arithmetically false, and the standing rule of this project is that
a page may not promise a behaviour that does not happen.

### 4. Stage (e) names the tier an armed scorer would have set

The flagged persona's card promises that the page "shows which tier an armed
scorer would have set, and that the decision was reached without it". The page
showed the first half only by implication - mode `log_only`, and a note saying no
tier moved - and a reader had to know that a downgrade's target is fixed at 3 to
finish the sentence.

`PipelineView.would_be_tier` READS that number out of the decision table's
downgrade rows. It is not a derivation and not a second answer to "what tier is
this case": the schema already constrains every downgrade to a fixed `to_tier` of
3 with monotone operators (ADR-004), and the decided tier still comes from the
journal through `review_state`. It matters more after the refocus than before,
because a Statusfeststellung case is already at tier 3 - so the flag moves
nothing in the strongest possible sense, and the page now says that out loud.

### 5. `.help` loses its character measure, and the class of bug becomes a test

Part 17 removed the `ch` caps from every flowing text element after the user read
the result as a broken layout, and let `.help` keep one on the reasoning that a
help sentence "belongs to a field and reads with it". Part 20 gave the class two
jobs that reasoning never covered: the intro paragraph of the Anlagen fieldset
and the description under each document checkbox, neither of them beside a
control. Measured in a browser at 1920: the descriptions rendered at 550px inside
a fieldset with 1086px of usable width. The user reported it from a screenshot,
which is how the part-17 caps were found too.

Three occurrences is a class of bug, and a class of bug that nothing checks comes
back. The cap is gone (measured after: 1084px for the intro, 1060px for each
description, the difference being the checkbox column they are indented into),
and `tests/test_review_accessibility.py::test_no_flowing_text_carries_a_character_measure_cap`
now parses every `ch` cap out of both stylesheets and requires each one to be a
DECLARED exception with a reason. Two are: the landing page's display headline
and the lead directly under it. A fourth has to be argued in that dict.

## Consequences

- **The intake surface no longer reaches tier 1.** A visitor who wants to see a
  clear-cut case goes to the queues or the eval, where 101 seeded items still
  show every tier. The persona file says this in its header rather than leaving
  it to be discovered.
- **Two hints of the panel are gone** ("Unterlagen beilegen", "Platzhalter-Syntax
  nachahmen"). The Anlagen fieldset stays and still says what ticking a document
  does; the auto-seal path of ADR-017 is no longer suggested to a visitor,
  because the field it needed (a non-identity text box) is a select on this form
  - it stays pinned by test through a direct POST.
- **Three routing and completeness paths left the demo surface with the
  Altersrente personas**: the Auslandsbezug priority-10 conflict, the unknown
  Rentenart, and the forged placeholder in a free text box. All three are still
  configured, still exercised by the eval over the frozen corpus, and still under
  unit test; none of them is reachable from `/demo/antrag` any more.
- **Three fields can be submitted empty from the browser.** That is the intended
  consequence and every one of them is a demonstration: a missing requirement, a
  missing requirement the derivation also reads, and the split-name join.
- **A persona name carries no Muster/Beispiel marker for the first time.** "Beate
  Schliebermann" is the user's own naming choice; the surname is invented and
  collides with nothing in either frozen set. The naming rule is unchanged and
  the exception is a named list of one in the test, so a second unmarked name
  fails rather than joining her.
- **Bookmarks to the three renamed personas fall back to the picker's default**,
  the same "never half-select something" rule the unit picker and the language
  switch follow. The ids were renamed rather than kept because
  `beispielmann_ohne_rentenbeginn` names a field his form no longer has.

## Alternatives considered

**Keep one Altersrente persona for the tier-1 arc.** Rejected: it is exactly the
"three procedures at once" the direction removes, and the tier-1 demonstration
survives in the queues without it. If tier 1 on the intake surface is wanted
back, it comes back as a fifth persona and a second procedure, deliberately.

**Keep "1. Januar 2048" in the third hint and let the visitor see an invalid
field.** Rejected: the hint's own sentence says the bounds do not catch it. Two
sentences on one screen contradicting each other is the failure mode this
project's prime directive exists to prevent.

**Raise `taetigkeit_beginn`'s upper bound to 2050 so 2048 works.** Rejected
harder: `config/procedures/` is a decision-plane configuration frozen into the
gold manifest's version stamps. Moving a calendar bound to make a demo hint true
is the tail wagging the dog, and it would invalidate a frozen corpus.

**Drop the `required` attribute from every field instead of three.** Rejected:
part 20's rule is a demonstration in its own right and the user asked for it one
part ago. Three exemptions with a reason attached keep both.

**Scope `.help`'s width fix to the Anlagen fieldset only.** Rejected: it would
leave the same cap on the same class one selector away, which is precisely how
this bug came back the second time. The part-17 rule applies without an
exception - the measure is the container.
