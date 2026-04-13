## Why

The Construction Ops Console (`/construction`) has two UX problems:

1. **Override form takes up excessive screen space** — the Overrides panel is always visible with 6+ input fields, even though overrides are an infrequent action. Users must scroll past it to reach the Replan section and Operator Tools, which are more commonly used.

2. **No user-friendly CRUD for employees, sites, and vehicles** — the only way to manage these core resources is through the collapsible "Operator Tools" raw JSON editor, which requires knowing the exact JSON schema. There is no dedicated management page with table views, search, or inline editing.

## What Changes

- **Collapse the Overrides panel** into a hidden state by default. It only appears as a modal/overlay when the user clicks "Prepare Override" on an assignment card. The modal pre-fills with the selected assignment's data (existing behavior preserved).
- **Add a new "Resources" section** (or sub-page) to the Construction Ops Console with three tabs: Employees, Sites, Vehicles. Each tab shows a searchable/filterable table of records with inline editing and add/delete actions.
- **Add DELETE API endpoint** for resources (`DELETE /api/construction/resource?kind=...&id=...`). Currently only upsert exists; deletion is not supported.
- **Add a navigation entry** in the Construction page header to access the Resources management view.

## Capabilities

### New Capabilities
- `override-modal`: Hide the Overrides form panel by default; show it as a modal overlay triggered by "Prepare Override" button click
- `resource-crud-ui`: Table-based CRUD interface for employees, sites, and vehicles with search, inline edit, add, and delete

### Modified Capabilities
<!-- No existing spec-level behavior changes — the override logic and API semantics remain the same -->

## Impact

- **`status_web.py`**: Major HTML/JS changes to `/construction` page — remove inline Override panel, add modal markup, add Resources section with tabbed tables
- **`status_web.py` API**: New `DELETE` endpoint for resource deletion
- **`construction_agent/service.py`**: Add `delete_resource()` method to support the new DELETE endpoint
- **No new dependencies**: All changes are within existing server-rendered HTML + vanilla JS pattern
