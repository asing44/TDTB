# CONTEXT — TDTB domain glossary

The single-context domain glossary for this repository (`docs/agents/domain.md`).
One line per term plus the code anchor where the concept is defined or enforced.
Use these words as defined here; do not drift to synonyms they exclude.

- **app home** — the machine-local directory (`$TDTB_HOME`, default
  `~/.config/tdtb`) holding the optional config override and the `state/`
  stores (run state, exclusions, deferrals, duration memory, journal). The
  Obsidian vault is retired: nothing reads, writes or requires it. Anchor:
  `app/app_config.py` (`app_home`, `state_dir`).
- **source** — the upstream system that owns a task's identity and properties
  (`todoist`, `capacities`; the wire still carries a retired `vault` literal
  until V5). Anchor: `app/external_sources.py`;
  `frontend/src/model/types.ts` (`Source`).
- **tag** — a Capacities RootTag entity identified by `(space_id, tag_id)`;
  its title is presentation only. Anchor: `app/exclusion_settings.py`
  (`TagExclusion`), `app/tag_exclusions.py`.
- **label** — source-specific text/typed-choice metadata (Todoist labels);
  not automatically a Capacities tag identity.
  Anchor: `app/todoist_client.py` (`labels`).
- **Ignore List** — the config-file exclusion list (`ignore` in
  `config/tdtb.json`, local override on top) matching Todoist ids, paths,
  or folded names. Anchor: `app/main.py`
  (`build_digest`'s `ignore` parameter), `app/config_reader.py`
  (`get_ignore_list`).
- **tag exclusion list** — the app-managed, persistent, any-match tag policy
  applied before selection. Anchor: `app/exclusion_settings.py`,
  `app/tag_exclusions.py` (`apply_tag_exclusions`).
- **RootTag** — Capacities' canonical tag-object structure. Anchor:
  `app/capacities_adapter.py` (`ROOT_TAG_STRUCTURE`, `CapacitiesAdapter.list_tags`).
- **entity property** — a typed Capacities property whose payload references
  other Capacities objects (`{"type": "entity", "entity": [{id, title}]}`).
  Anchor: `app/capacities_adapter.py` (`_capacities_tag_refs`).
- **Capacities property payload** — the only trustworthy object surface: custom
  properties arrive keyed by raw property id, not by name (a Project payload
  returned `f779f78a…` and `f240c060…`, which the content frontmatter revealed
  as Assigned/Status/Priority). Anchor: direct probe of the object payload;
  `app/capacities_adapter.py` (`properties` projection).
- **Capacities relation visibility** — the API surface is narrower than the
  app: a relation the app renders can be absent from an object's payload (a
  `Context` relation was visible in the app while that object's `properties`
  had no `Context` key). Anchor: direct probe of the object payload vs the app.
- **Capacities subtask/parentage** — not exposed: no `parent` on objects, no
  `children` on a parent, no children endpoint. Anchor: direct probe of the
  object payload. The `hierarchy` field in `readObjectBlocks` is heading level
  within a document (`{"key":"H2","val":2}`), not object parentage — a
  dangerous lookalike, never an edge.
- **Capacities `objectType`** — may be a name or a raw structure id, depending
  on whether the type has a name. Anchor: direct probe of the object payload.
- **Capacities payload rule** — trust only what arrives in an object's
  `properties` and `collections`; anything the app shows but the payload omits
  is unavailable to TDTB. Anchor: direct probe of the object payload.
