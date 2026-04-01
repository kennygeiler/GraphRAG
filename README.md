# Narrative MRI 🎬

> "Stop guessing if your script works. Measure its physics."

**Narrative MRI** is a GraphRAG-powered diagnostic tool for screenwriters and producers. It transforms flat scripts into a living, queryable knowledge graph to identify structural **Dead Air**, protagonist **passivity**, and un-fired **Chekhov’s Guns**.

For **roadmap, metric definitions, dashboard map, and AI working rules**, see [`strategy.md`](strategy.md) in the repo root (update it when milestones change).

## The Philosophy

Most script coverage is subjective. **Narrative MRI** is objective. By treating a screenplay as a series of mathematical relationships between Characters, Locations, and Props, we can visualize the **metabolic rate** of a story.

- **Friction vs. exposition:** We calculate the **heat** of every scene. If characters aren’t in meaningful conflict (relative to who’s in frame), the scene is flagged as dead air.
- **The agency gauge:** We track the **passivity index** of your cast. If your protagonist isn’t driving the plot, the graph will show it.
- **Prop utility:** We filter out set dressing to track only the items that earn narrative load (USES / CONFLICTS touches).

## The Stack

- **Intelligence:** Claude 3.5 Sonnet / Haiku via [`instructor`](https://github.com/jxnl/instructor)
- **Storage:** [Neo4j](https://neo4j.com/) (graph database)
- **Engine:** Python 3.12 + [`uv`](https://github.com/astral-sh/uv)
- **Interface:** [Streamlit](https://streamlit.io/) + [Plotly](https://plotly.com/python/)

## Current Capabilities

- [x] **Validated parsing:** Verbatim `source_quote` on narrative edges (Pydantic + Instructor).
- [x] **Agency analytics:** Character passivity from Neo4j (`metrics.py` / dashboard).
- [x] **Structural heartbeat:** Scene-by-scene friction mapping (heat vs. boredom threshold).
- [x] **Neo4j loader & dashboard:** Ingest `validated_graph.json`, explore in Streamlit.
- [ ] **Production complexity:** (Planned) Cost-per-scene estimation from graph density.

## Prerequisites

- [uv](https://github.com/astral-sh/uv) installed (`uv sync` installs from `pyproject.toml` / `uv.lock`; `requirements.txt` is only a short pointer)
- A running **Neo4j** instance (local or Aura)
- An **Anthropic** API key

Create a `.env` in the project root with your real values. **Do not commit** `.env` (it contains secrets).

```env
ANTHROPIC_API_KEY=sk-ant-...
NEO4J_URI=neo4j://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password
```

## Usage

```bash
# Install dependencies
uv sync

# 1) Parse Final Draft → raw_scenes.json (default output path: ./raw_scenes.json)
uv run python parser.py screenplay.fdx

# 2) Generate the master lexicon (characters & locations) from raw scenes JSON
uv run python lexicon.py raw_scenes.json

# 3) Extract per-scene graphs with lexicon constraints → validated_graph.json
#    Saves after each successful scene by default. If ingest stops early, run again (or
#    `ingest.py --resume`) to continue from disk without redoing finished scene numbers.
#    `ingest.py --fresh` deletes the file first for a full re-extract.
uv run python ingest.py

# 4) Wipe & load Neo4j from validated_graph.json (default path)
uv run python neo4j_loader.py

# 5) Launch the MRI dashboard
uv run streamlit run app.py
```

**Optional CLI tools**

```bash
# Narrative metrics in the terminal (passivity, heat, Chekhov props)
uv run python metrics.py --heat --props --character alan

# Fuzzy duplicate characters + ghost-node audit + interactive merge
uv run python reconcile.py --dry-run
```

## Project layout (high level)

| Module | Role |
|--------|------|
| `parser.py` | `.fdx` → `raw_scenes.json` |
| `lexicon.py` | Master cast/location list → `master_lexicon.json` |
| `ingest.py` | Scene graphs → `validated_graph.json` |
| `neo4j_loader.py` | JSON → Neo4j (`Character`, `Location`, `Prop`, `Event`, `IN_SCENE`, narrative rels) |
| `pipeline_state.json` | Written by `ingest.py` / loader — progress metadata (see Engine Room status) |
| `metrics.py` | Passivity, heat, load-bearing props |
| `app.py` | Streamlit + Plotly producer dashboard |
| `reconcile.py` | Entity reconciliation helpers |
| `agent.py` | LangChain Cypher QA (“Ask the graph”) |
| `schema.py` | Pydantic graph shapes |

## License

Add your license here (e.g. MIT) when you publish the repo.
