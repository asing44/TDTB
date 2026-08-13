import yaml

with open('docs/plan/2026-08-12-tdtb-project-extraction/plan.yaml', 'r') as f:
    plan = yaml.safe_load(f)

plan['objective'] = "Extract the TDTB application and project boundary out of the Claudius repository and into the canonical TDTB repository."
plan['tldr'] = "Execute the extraction of the TDTB application from the sibling Claudius repository into this canonical TDTB repository, preserving dirty state and rewiring consumers."

# Save historical provenance
plan['prior_decisions'] = plan.get('prior_decisions', [])
plan['prior_decisions'].append({
    "decision": "Retain the old document-migration objective and criteria as historical provenance only.",
    "context": "The prior migration wrapper incorrectly made the document-migration objective and no-code-change criteria authoritative. The user confirmed the scope is to execute the actual extraction."
})
plan['prior_decisions'].append({
    "historical_objective": plan['baseline']['objective'],
    "historical_acceptance_criteria": plan['baseline']['acceptance_criteria']
})

plan['baseline']['objective'] = plan['objective']
plan['baseline']['acceptance_criteria'] = [
    "The TDTB application and project boundary are successfully extracted into the canonical TDTB repository.",
    "Current dirty work is preserved during extraction.",
    "Claudius consumers are correctly rewired to point to the new repository.",
    "The extraction is independently verified against the dirty-state receipt.",
    "A safe closeout and cutover handoff is produced."
]

plan['plan_lineage']['revision'] = 2
plan['plan_lineage']['replan_count'] = 1
plan['plan_lineage']['parent_revision'] = 1
plan['plan_lineage']['reason'] = "scope_change"

plan['constraints']['hard'] = [c for c in plan['constraints']['hard'] if "No application source code is changed" not in c]
plan['constraints']['hard'].append("The canonical repo is the current working directory. The previous source repo is expected to be a sibling Claudius repository; determine exact paths only through bounded repository inspection.")

plan['replan'] = {
    "reason": "Revise the imported TDTB extraction plan so it is authoritative and executable in the canonical TDTB repository.",
    "changed_tasks": [],
    "added_tasks": [],
    "removed_tasks": [],
    "preserved_acceptance_criteria": [],
    "new_risks": ["Path resolution between the canonical TDTB repository (CWD) and the sibling Claudius repository."],
    "progress_signal": "Plan revised for execution."
}

# Update task context
for task in plan['tasks']:
    task['handoff']['known_context'].append("The canonical repo is the current working directory. The source repo is a sibling Claudius repository.")
    plan['replan']['changed_tasks'].append(task['id'])

with open('docs/plan/2026-08-12-tdtb-project-extraction/plan.yaml', 'w') as f:
    yaml.dump(plan, f, sort_keys=False, default_flow_style=False)
