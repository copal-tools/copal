# macOS workflow templates

These are property-list XML files captured from Automator-saved Quick Actions.
They cannot be hand-rolled — see [copalpm gotcha #12](../../CLAUDE.md): the
runtime aborts with `Workflow's metaData should be service metaData!` if
`workflowMetaData` isn't the exact shape Automator emits.

## Files

| File | Captured from | Used by |
|------|---------------|---------|
| `Info.plist.template` | Automator → Quick Action → *Service receives selected* **folders** *in Finder* | All folder-targeted verbs (start, stop, new-project) |
| `document.wflow.template` | (same) | All folder-targeted verbs |
| `Info.plist.file.template` | Automator → Quick Action → *Service receives selected* **files or folders** *in Finder* | The `mark-deliverable` verb (file-targeted) |
| `document.wflow.file.template` | (same) | The `mark-deliverable` verb |

If `*.file.template` is missing, the macOS installer skips the file verb
with a one-line stderr warning. Folder verbs continue to install normally.

## Recapturing on a Mac

1. Open **Automator** → **New Document** → **Quick Action**.
2. Set "Workflow receives current" to **files or folders** in **Finder**.
3. Drag in **Run Shell Script** action. Body: `echo "$1"` (placeholder).
4. Save as e.g. `Copal_FileCapture`.
5. Open the bundle at `~/Library/Services/Copal_FileCapture.workflow/Contents/`.
6. Copy `Info.plist` here, then add the substitutions documented below.
7. Copy `document.wflow` here, then add the substitutions below.

## Substitutions the installer applies

In `Info.plist.*.template`:

| Placeholder | Replaced with |
|-------------|---------------|
| `__MENU_TITLE__` | the verb's `title` field (e.g. `Copal: Mark as Deliverable`) |

In `document.wflow.*.template`:

| Placeholder | Replaced with |
|-------------|---------------|
| `__COPALPM_COMMAND__` | `"/abs/path/to/copalpm" shell-trigger <verb> --file "$1"` (or `--folder`) |

When you commit the captured file, find the menu-title string and the
`COMMAND_STRING` value and replace them with the placeholders above.
Nothing else should be edited.
