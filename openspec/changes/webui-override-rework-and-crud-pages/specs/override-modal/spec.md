## ADDED Requirements

### Requirement: Override form hidden by default
The Overrides panel SHALL NOT be rendered as a visible section on the Construction Ops Console page. The form fields (Plan ID, Assignment ID, Employees, Vehicle, Reason Type, Reason Text, Should Learn) SHALL only appear inside a modal overlay.

#### Scenario: Page loads without override form visible
- **WHEN** the user navigates to `/construction`
- **THEN** the page SHALL render Hero, Plan, and Replan sections without any visible Override form
- **AND** no Override input fields SHALL be present in the normal page flow

### Requirement: Override modal opens on Prepare Override click
When the user clicks "Prepare Override" on an assignment card, a modal overlay SHALL appear containing all Override form fields, pre-filled with the selected assignment's data.

#### Scenario: Prepare Override opens modal with pre-filled data
- **WHEN** the user clicks the "Prepare Override" button on an assignment card for site "Baustelle Alpha"
- **THEN** a modal overlay SHALL appear over the page
- **AND** the modal SHALL contain Plan ID, Assignment ID, Employees, Vehicle, Reason Type, Reason Text, and Should Learn fields
- **AND** the fields SHALL be pre-filled with the selected assignment's current values
- **AND** Reason Text SHALL default to "调整 {siteName} 今日排班" if empty

#### Scenario: Modal highlights the selected card
- **WHEN** the override modal is open
- **THEN** the selected assignment card SHALL be visually highlighted (teal border/shadow) behind the modal backdrop

### Requirement: Override modal can be closed
The modal SHALL provide a close mechanism that dismisses the overlay and clears the selected card highlight.

#### Scenario: Close via X button
- **WHEN** the user clicks the close button (X) in the modal header
- **THEN** the modal SHALL close
- **AND** the selected card highlight SHALL be removed

#### Scenario: Close via backdrop click
- **WHEN** the user clicks outside the modal content area (on the backdrop)
- **THEN** the modal SHALL close

#### Scenario: Close via Escape key
- **WHEN** the user presses the Escape key while the modal is open
- **THEN** the modal SHALL close

### Requirement: Apply Override submits from modal
The "Apply Override" button inside the modal SHALL function identically to the current implementation — POST to `/api/construction/override` and display the result.

#### Scenario: Successful override from modal
- **WHEN** the user fills override fields in the modal and clicks "Apply Override"
- **THEN** the system SHALL POST the override data to `/api/construction/override`
- **AND** display the result (Override ID, Site, Crew, Vehicle) inside the modal
- **AND** keep the modal open to show the result

#### Scenario: Override fails with error
- **WHEN** the override API returns an error
- **THEN** the error message SHALL be displayed inside the modal
- **AND** the modal SHALL stay open for the user to retry
