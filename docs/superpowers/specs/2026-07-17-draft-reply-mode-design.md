# Draft Reply Mode Design

## Goal

When `AUTO_PUBLISH=false`, generate a reply, insert it into the matching Dzen
comment input, wait five seconds, and never submit it. When `AUTO_PUBLISH=true`,
retain the existing submit behavior.

## Design

`DzenStudioPage.publish_reply` receives an `auto_publish` flag. It always opens
the matching reply form and fills the generated text. It clicks the submit button
only when the flag is true; otherwise it waits five seconds via Playwright.

`OrchestratorLoop` calls the same page method after saving a generated reply,
passing `settings.AUTO_PUBLISH`. A filled but unsubmitted reply keeps
`ReplyStatus.GENERATED`; a submitted reply becomes `ReplyStatus.PUBLISHED`.

## Safety and verification

- `AUTO_PUBLISH=false` must never click the submit selector.
- The five-second pause is made in the browser interaction layer.
- A targeted page test verifies draft fill, no submit, and the 5,000 ms wait.
- An orchestrator test verifies that safe mode invokes the page draft action.
