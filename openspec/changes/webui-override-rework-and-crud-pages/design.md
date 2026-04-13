## Context

The Construction Ops Console (`/construction` in `status_web.py`) is a single-page server-rendered HTML app with vanilla JS. It currently renders all sections vertically: Hero → Plan → Override form (always visible) → Replan → Operator Tools (collapsible).

The Override form is a 6-field panel that stays on screen even when not needed, pushing frequently-used sections below the fold. Resource management (employees, sites, vehicles) is buried inside the collapsible Operator Tools as a raw JSON editor — no structured table view or inline editing.

The backend already supports listing (`GET /api/construction/resources?kind=X`) and upserting (`POST /api/construction/resource`) resources. There is no DELETE endpoint.

## Goals / Non-Goals

**Goals:**
- Reduce visual clutter by hiding the Override form until needed
- Provide a user-friendly CRUD interface for employees, sites, and vehicles
- Add DELETE support for resources at the API level
- Maintain the existing server-rendered HTML + vanilla JS architecture (no SPA framework)

**Non-Goals:**
- No authentication/authorization changes for the new endpoints
- No pagination — resource counts are expected to stay under ~200 records per table
- No data export/import functionality
- No bulk edit or batch operations
- No redesign of the plan assignment cards or other existing sections

## Decisions

### 1. Override panel → Modal overlay (instead of collapsible section)

**Decision**: Convert the Override form into a CSS modal that slides in when "Prepare Override" is clicked.

**Alternatives considered**:
- Collapsible `<details>` section (like Operator Tools): still takes up DOM space and adds nesting
- Separate `/construction/override` page: loses context of which assignment was selected

**Rationale**: A modal preserves the assignment card context (highlighted card visible behind), matches the existing "Prepare Override" interaction flow, and removes 200px+ of always-visible form fields.

### 2. Resource CRUD as a sub-page (instead of inline tabs)

**Decision**: Add a new `/construction/resources` route with a tabbed interface (Employees | Sites | Vehicles). Accessible via a "Resources" button in the hero section.

**Alternatives considered**:
- Inline tabs within `/construction`: page is already long, adding tables makes it worse
- Separate `/resources` page outside the construction section: breaks the domain boundary

**Rationale**: A sub-page keeps the construction context while giving tables enough screen space. The URL pattern `/construction/resources` is discoverable and can be linked directly.

### 3. Table rendering via server-generated HTML (not client-side template)

**Decision**: Render table rows server-side in `_render_resources_html()`, same pattern as the plan cards.

**Alternatives considered**:
- Client-side JS template with fetch() + DOM manipulation: more dynamic but introduces a second rendering pattern
- Shared JS component library: overkill for this project size

**Rationale**: Consistency with existing codebase. All current HTML is server-rendered. Adding a client-side rendering pattern would require a paradigm shift.

### 4. DELETE endpoint using soft-delete flag

**Decision**: Add `DELETE /api/construction/resource?kind=X&id=Y` that sets `availability_status = 'inactive'` (employees) or `current_status = 'decommissioned'` (vehicles) or `risk_level = 'closed'` (sites) rather than hard-deleting rows.

**Rationale**: Schedule history and override logs reference these records by ID. Hard deletes would break referential integrity. Soft-delete preserves audit trails while removing records from active views.

## Risks / Trade-offs

- **[Risk] Modal may feel "hidden"** → Mitigation: Add a subtle "Edit Override" link in the Overrides header area that opens the modal with empty fields, for users who want to manually type IDs
- **[Risk] Resource tables may get slow with 100+ records** → Mitigation: Add client-side search/filter (JS `Array.filter` on rendered rows) — no server-side pagination needed at current scale
- **[Trade-off] Server-rendered tables mean full page reload on edit** → Acceptable for this use case; the page is already full-refresh on most actions
