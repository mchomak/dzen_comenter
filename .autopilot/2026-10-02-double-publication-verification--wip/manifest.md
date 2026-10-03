# Requirements

| ID | Requirement | State |
|---|---|---|
| R01 | Use the current DOMEO account identity for reply author matching. | done |
| R02 | After submitting, wait at least two seconds and inspect the Studio thread without reloading it. | done |
| R03 | Open the corresponding public article in Playwright and scroll to comments. | done |
| R04 | Reveal additional comments and hidden replies until the source comment can be inspected. | done |
| R05 | Confirm exact reply text and bot author under the matching source comment on the public article. | done |
| R06 | Complete the durable publication only after both Studio and public article checks succeed; report failures clearly in JSON logs. | done |
| R07 | Use the supplied HTML fragments and full article HTML as implementation evidence. | done |
| R08 | Run visible local Playwright with the saved authenticated session and report observed behavior. | done |

Previous Autopilot run remains unfinished and separate. Avoid duplicate public replies during verification.
