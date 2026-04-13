## ADDED Requirements

### Requirement: Resource management page accessible from Construction Console
The Construction Ops Console SHALL provide a navigation entry to a resource management page at `/construction/resources`.

#### Scenario: Navigate to resources page
- **WHEN** the user clicks a "Resources" button in the Construction Ops Console hero section
- **THEN** the browser SHALL navigate to `/construction/resources`
- **AND** the page SHALL display a tabbed interface with three tabs: Employees, Sites, Vehicles

### Requirement: Employee table view
The Employees tab SHALL display all active employees in a searchable table with key columns: Name, Role, Primary Skill, Certificates, Can Drive, Can Lead, Status.

#### Scenario: Load employees table
- **WHEN** the user navigates to the Employees tab
- **THEN** the system SHALL fetch employees from `GET /api/construction/resources?kind=employees`
- **AND** render a table with columns: Name, Role, Primary Skill, Certificates, Can Drive, Can Lead, Status
- **AND** each row SHALL have Edit and Delete action buttons

#### Scenario: Search employees
- **WHEN** the user types in the search input
- **THEN** the table SHALL filter rows in real-time to show only matching names, roles, or skills

### Requirement: Site table view
The Sites tab SHALL display all sites in a searchable table with key columns: Name, Code, Address, Required Headcount, Risk Level, Urgency, Customer Priority.

#### Scenario: Load sites table
- **WHEN** the user navigates to the Sites tab
- **THEN** the system SHALL fetch sites from `GET /api/construction/resources?kind=sites`
- **AND** render a table with columns: Name, Code, Address, Headcount, Risk Level, Urgency, Priority
- **AND** each row SHALL have Edit and Delete action buttons

### Requirement: Vehicle table view
The Vehicles tab SHALL display all vehicles in a searchable table with key columns: Code, Plate Number, Type, Seat Capacity, Status, Maintenance.

#### Scenario: Load vehicles table
- **WHEN** the user navigates to the Vehicles tab
- **THEN** the system SHALL fetch vehicles from `GET /api/construction/resources?kind=vehicles`
- **AND** render a table with columns: Code, Plate Number, Type, Seats, Status, Maintenance
- **AND** each row SHALL have Edit and Delete action buttons

### Requirement: Inline edit of resource records
Clicking "Edit" on a table row SHALL convert that row into editable input fields. Saving SHALL POST the updated record to `/api/construction/resource`.

#### Scenario: Edit an employee record
- **WHEN** the user clicks "Edit" on an employee row
- **THEN** the row SHALL become editable with input fields pre-filled with current values
- **AND** "Edit" and "Delete" buttons SHALL change to "Save" and "Cancel" buttons

#### Scenario: Save edited record
- **WHEN** the user modifies fields and clicks "Save"
- **THEN** the system SHALL POST `{kind: "employees", record: {...updatedFields}}` to `/api/construction/resource`
- **AND** on success, the row SHALL return to read-only mode with updated values
- **AND** on error, the error message SHALL be displayed and the row SHALL stay in edit mode

#### Scenario: Cancel edit
- **WHEN** the user clicks "Cancel"
- **THEN** the row SHALL return to read-only mode with original values restored

### Requirement: Add new resource record
Each tab SHALL provide an "Add" button that inserts a new editable row at the top of the table.

#### Scenario: Add new employee
- **WHEN** the user clicks the "Add Employee" button
- **THEN** a new editable row SHALL appear at the top of the table with empty fields
- **AND** the row SHALL have "Save" and "Cancel" buttons

#### Scenario: Save new record
- **WHEN** the user fills fields and clicks "Save" on a new row
- **THEN** the system SHALL POST the record to `/api/construction/resource`
- **AND** on success, the new record SHALL appear in the table with a server-assigned ID

### Requirement: Delete resource record with confirmation
Clicking "Delete" SHALL prompt for confirmation, then call a DELETE endpoint that soft-deletes the record.

#### Scenario: Delete with confirmation
- **WHEN** the user clicks "Delete" on a row
- **THEN** a confirmation dialog SHALL appear asking "Delete this {kind} record?"
- **AND** if confirmed, the system SHALL call `DELETE /api/construction/resource?kind={kind}&id={id}`
- **AND** on success, the row SHALL be removed from the table

#### Scenario: Cancel deletion
- **WHEN** the user dismisses the confirmation dialog
- **THEN** no action SHALL be taken and the row SHALL remain unchanged

### Requirement: DELETE resource API endpoint
The server SHALL expose `DELETE /api/construction/resource?kind={kind}&id={id}` that performs a soft-delete by setting the record's status field to an inactive value.

#### Scenario: Delete an employee
- **WHEN** `DELETE /api/construction/resource?kind=employees&id={id}` is called
- **THEN** the employee's `availability_status` SHALL be set to `"inactive"`
- **AND** the API SHALL return `{"ok": true, "id": "{id}"}`

#### Scenario: Delete a vehicle
- **WHEN** `DELETE /api/construction/resource?kind=vehicles&id={id}` is called
- **THEN** the vehicle's `current_status` SHALL be set to `"decommissioned"`
- **AND** the API SHALL return `{"ok": true, "id": "{id}"}`

#### Scenario: Delete a site
- **WHEN** `DELETE /api/construction/resource?kind=sites&id={id}` is called
- **THEN** the site's `risk_level` SHALL be set to `"closed"`
- **AND** the API SHALL return `{"ok": true, "id": "{id}"}`

#### Scenario: Delete non-existent resource
- **WHEN** a DELETE request targets a non-existent ID
- **THEN** the API SHALL return HTTP 404 with `{"error": "not found"}`

#### Scenario: Delete with invalid kind
- **WHEN** a DELETE request uses an unsupported kind value
- **THEN** the API SHALL return HTTP 400 with `{"error": "unsupported kind"}`
