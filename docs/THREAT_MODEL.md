# DynosAI 1.0 threat model

This document freezes what DynosAI **enforces** in the 1.0 line and what it
does **not**. It is evidence for RC5, not a claim of production-ready 1.0 and
not a claim that DynosAI is an OS sandbox.

DynosAI reduces agent authority. It does not replace operating-system
isolation, hypervisor isolation, or the human who runs Studio on a machine.

Loopback binding is a local-browser control. **Loopback is not a full security boundary.**

## Enforced

These controls are implemented in Core, Git guard, execution policy, capability
manifests and the App Server. Regression coverage lives in `tests/test_250.py`
plus earlier harness/profile tests.

| Control | What is enforced |
|---|---|
| path roots / escape | `PathPolicyEngine` denies reads/writes that escape the project root. |
| symlink escape | Symlinks that resolve outside the root are denied when the platform supports them. |
| secret / context boundaries | Sensitive paths such as `.env` are denied. Secret brokers refuse model materialization. Vault materialization is runtime-only when configured. |
| certified-client refusal | Host/Studio/setup refuse uncertified adapters and project extension packs. Cursor ACP and Codex app-server remain the supported 1.0 target provider transports. 1.0 live certification is pending MATRIX_1.0. |
| Git / governance authority | Git is source truth. `.dynosai/knowledge.db` is workflow truth. Agent Git is wrapped by `GitCommandPolicy` (write/complex forms blocked). |
| human gates | Spec, plan, code and merge gates stay required, including execution profile Autonomous. |
| host-owned execution profile | Strict / Balanced / Autonomous are selected by the host. MCP agents cannot change them. |
| process timeout | `LocalExecutionRuntime.run` applies a timeout; timed-out processes are reported, not treated as success. |
| loopback Studio binding | The App Server only binds `127.0.0.1`, `localhost` or `::1`. |
| Host header | Requests whose `Host` is not a loopback name receive `403 forbidden_host`. |
| JSON POST | Mutating `/api/` POSTs require `Content-Type: application/json`. |

Studio static responses also send `Content-Security-Policy` and
`X-Content-Type-Options`. Responses do not advertise
`Access-Control-Allow-Origin`.

## Not enforced

| Non-control | Honest status |
|---|---|
| OS network sandbox | Network policy is **decision-only**. OS-level child-process interception is not shipped. |
| Docker / VM isolation | `require_local_runtime("docker")` and VM/remote backends raise a policy error. They are not implemented isolators. |
| arbitrary model obedience | The model is not a security boundary. Policy must not depend on the model choosing to obey. |
| uncertified clients | A local process that can run `dynosai-mcp` may still speak MCP. Generic MCP initialize is not an authentication sandbox. Refusal is the host capability/setup/pack layer, not a guarantee that no other client can connect. |
| remote execution isolation | Remote runtimes are not shipped. |

## Local-browser threat surface

Studio is a loopback HTML/JS client of `DynosAIApplication`.

Assessed controls:

- bind address limited to loopback;
- `Host` allow-list (`127.0.0.1`, `localhost`, `::1`);
- same-origin browser rules (no CORS `Access-Control-Allow-Origin`);
- JSON POST requirement on mutating API routes;
- CSP on packaged Studio assets.

These reduce accidental cross-site use from a *remote* web origin. They do
**not** stop another process or user on the same machine from opening
`http://127.0.0.1:8765/`, sending a forged `Host: 127.0.0.1`, or driving the
API if they can reach loopback.

Do not treat “it is on loopback” as sufficient against local malware, a
malicious browser extension with local-network access, or a confused-deputy
tab that can talk to loopback.

## Residual risk the operator still owns

- OS permissions and who can log into the machine;
- provider credentials and MCP process lifetime;
- validation commands the human approved;
- secrets stored in the project vault or `.env`;
- whether Studio is left running on a shared workstation.

See `SECURITY.md` for reporting and `CONTRIBUTING.md` for how a later client
would become certified.
