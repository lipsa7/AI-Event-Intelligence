# Instructions for AI assistants working on this project

Read this first, every session. These rules exist because the same bugs survived for weeks while
assistants reported the code as "done" and "as per the objective".

## The objective
The users are news readers and strategists who want to uncover hidden insights and the likely next
moves of the companies, governments and regulators in a topic's news.

Every deep run must release several (target 2-4) strategic insights about the news on the topic the owner
enters. The app serves ANY topic (AI governance is only one example); never hard-code a topic. Each must:
- rest on a concrete, checkable fact (a date gap, an amount, a named document or party);
- say what that fact changes for the organisation in the profile;
- be backed by the sources: facts supported; inferences and forecasts must follow from the facts;
- not stitch unrelated news together, restate the headline, or hide behind a theory label.

The reference insight the owner rated good: "Governments learned of the AI-agent breach about three
months after it happened; the developer, not the government, detected it and reported it on its own
timetable."

## Definition of done (no exceptions)
A change is done only when BOTH pass, and you show their output, not a description:
1. `python -m pytest tests/test_v13_quality_regression.py -v` (on the saved news, no API key needed).
2. After a real deep run: `python -m tools.run_quality` (exit code 0).

Also run the full suite (`python -m pytest -q --continue-on-collection-errors tests`) and confirm no
NEW failures against the known failing list.

Never answer "is the code as per the objective?" or "is it fixed?" from reading code or docs. Answer
only from those outputs, and say plainly what has not been run.

## Rules that past bugs broke
- **Trace a run from input to output before fixing anything.** Unit tests passed while runs produced
  nothing, because each stage worked alone and they cancelled each other out. Check what actually
  reaches the generator, how many candidates each stage drops (the funnel in `run_quality`), and why.
- **Prompts and code must agree.** The code stopped filtering lab ideas by self-score, but the prompt
  still told the model to drop ideas below 7/10, so the lab returned nothing. When you change a rule,
  search the prompts for it too.
- **Field names must match between stages.** The quality check read `evidence_ids` while the generator
  wrote `supporting_evidence_ids`, so insights citing 10 records counted as unsupported. When stages
  pass data to each other, test with the real producer's output, not a hand-made sample.
- **No silent caps.** One insight per deep run ("MVP" setting) and 2 records per story made the
  objective impossible. Any cap on insights, stories, records or candidates needs a test that pins
  the owner's intent (`tests/test_v13_quality_regression.py`).
- **Pick stories by insight potential, not coverage.** The story with the most articles is usually a
  syndicated funding round (`services/insight_potential.py`). The signals must stay topic-neutral; the
  funding and launch penalties switch off when the topic is about funding or launches.
- **Every owner decision becomes an outcome test** in `tests/test_v13_quality_regression.py`, on the
  saved corpus, before it is called done.
- **Research must change the data path, not only prompts.** A better prompt cannot fix a generator
  that receives two records per story.
- **Do not trust session summaries for what is fixed.** Read `docs/CHANGES_v10_reader_standard.md`
  and run the tests.

## Delivery to the owner
- Deliver complete files laid out like the project (a folder or zip to extract and overwrite), with no
  data files.
- Explain in plain language; explain every term.
- Prefer automated fixes over manual steps.
- Validate every deliverable before sending.
