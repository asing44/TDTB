# CONTEXT — TDTB domain glossary

The single-context domain glossary for this repository (`docs/agents/domain.md`).
One line per term plus the code anchor where the concept is defined or enforced.
Use these words as defined here; do not drift to synonyms they exclude.

- **vault** — the filesystem root for legacy planning inputs and local app
  caches, not Capacities authority. Anchor: `app/runstate.py` (`vault_root` is
  always a caller-supplied parameter), `app/main.py` (`resolve_vault_root`).
- **source** — the upstream system that owns a task's identity and properties
  (`vault`, `todoist`, `capacities`). Anchor: `app/external_sources.py`;
  `frontend/src/model/types.ts` (`Source`).
- **tag** — a Capacities RootTag entity identified by `(space_id, tag_id)`;
  its title is presentation only. Anchor: `app/exclusion_settings.py`
  (`TagExclusion`), `app/tag_exclusions.py`.
- **label** — source-specific text/typed-choice metadata (Todoist labels,
  vault frontmatter tags); not automatically a Capacities tag identity.
  Anchor: `app/todoist_client.py` (`labels`),
  `app/gather/tdtb_gather.py` (`get_tags`).
- **Ignore List** — the existing vault-configured exclusion list matching
  Todoist ids, vault paths, or folded names. Anchor: `app/main.py`
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
