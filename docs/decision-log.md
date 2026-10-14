# Decision log

Decisions are numbered and never rewritten. A later decision that replaces an earlier one
refers back to it.

## D-001 Recovery strategy after the failed 0.2.8b1 beta (2026-10-14)

**Context.** 0.2.1 kept working links for at least 43 s. From 0.2.2 on, links stopped after
seconds and the lamp often became unreachable. The 0.2.8b1 connection-parameter beta
failed. A series of single-hypothesis releases had not found the cause.

**Considerations.**
- 0.2.1 and 0.2.2 have identical GATT connection code.
- Every uncached ESPHome proxy link in the logs (7 links) stopped within 21 s. One cached
  link was logged. It was closed by Home Assistant after 5.1 s without a link loss, but
  the lamp did not answer the proxy filter.
- 0.2.7 forced the uncached path (`use_services_cache=False`, cache clear after every
  failure). The 0.2.1 sessions were probably cached (inferred, not observed).
- The lamp changed from Network ID to Node Identity advertising when the failures began.
  It showed a provisioning GATT table once. Its current provisioning state is unknown.
- Details: [protocol.md](protocol.md), "Source audit and capture correlation (0.2.9b1)".

**Options.**
- A. Passive network check, the 0.2.1 cache policy, connection instrumentation and one
  test procedure, as prerelease 0.2.9b1 from main. Gives: separates lamp state (H1) from
  connection path (H2) in one test; checks provisioning without a connection. Costs: code
  and a test; does not repair the lamp if H1 is true.
- B. Test the existing v0.2.1 reference prerelease without changes. Gives: no code change,
  exact reference. Costs: no network check, no link lifetimes; H1 and H2 stay mixed.
- C. Only the passive network check. Gives: smallest change. Costs: one more round before
  any change to the connection.

**Recommendation.** A, because it is the only option that can reject or support H2 in one
test, and it checks first whether the lamp still belongs to the network.

**Decision.** A, chosen by Sylvan Loves. Status: settled.

## D-002 When to clear the GATT service cache (2026-10-14)

**Context.** Part of D-001. 0.2.7 cleared the cache after every failed setup.

**Considerations.** A cleared cache makes the next connection uncached. Every logged
uncached link failed. A cached table can be wrong when the lamp changes its GATT layout,
as at 12:15 UTC (provisioning layout without 0x1828).

**Options.**
- Never clear (0.2.1). Gives: always cached after the first connection. Costs: a cached
  table without 0x1828 stays until Home Assistant restarts.
- Clear after every failure (0.2.7). Gives: fresh tables. Costs: forces the failing path.
- Clear only when the table has no Mesh Proxy service. Gives: cached path after link loss,
  and recovery from a wrong table. Costs: a wrong table that still contains 0x1828 is not
  detected.

**Recommendation.** Clear only when the table has no Mesh Proxy service.

**Decision.** Clear only when the table has no Mesh Proxy service, approved with D-001 by
Sylvan Loves. Status: settled.

## D-003 Keep the 0.2.2–0.2.6 application changes (2026-10-14)

**Context.** Part of D-001. A pure 0.2.1 baseline would also undo the latency fix.

**Considerations.** Links also stopped with no mesh traffic at all (capture 12) and with
only four answered status reads (capture 14). The 0.2.2 change removed a proven 30 s
command-lock delay.

**Options.** Keep them (gives the latency fix; costs a small difference from the 0.2.1
baseline), or revert them (gives an exact baseline; brings back the 30 s delay).

**Recommendation.** Keep them. The evidence does not implicate them.

**Decision.** Keep them, approved with D-001 by Sylvan Loves. Status: settled.

## D-004 Diagnostics version from the manifest (2026-10-14)

**Context.** Found during the D-001 audit. `data.version` in diagnostics was a hard-coded
string. Captures 3–5 reported 0.2.1 while 0.2.2 and 0.2.3 ran.

**Considerations.** Wrong version labels mislead every capture comparison.

**Options.** Read the version from the integration manifest (gives one source of truth;
costs one loader call), or keep a manual string (costs repeated mistakes).

**Recommendation.** Read the version from the manifest.

**Decision.** Implemented in 0.2.9b1. Status: open, awaiting review by Sylvan Loves.
