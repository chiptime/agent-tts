# Crew federation core (S7)

Self-contained federation for herdr-brain: one lead, peers, a
mutually-pinned TLS link and a shared crew secret. This package is
framework-free — the FastAPI wiring is a thin adapter described at
the bottom. With zero peers nothing here is imported by the server
path, so a solo brain keeps its exact single-host behavior (doc 10
acceptance 1).

## Security model in one breath

A crew request is authenticated only when BOTH independent factors
hold: the TLS peer certificate's DER hashes (SHA-256) to the pin of
an ENROLLED member, AND `Authorization: Bearer <crew secret>` matches
the roster's current hash or the previous hash inside the rotation
grace window (`hmac.compare_digest` everywhere). Any failure — either
factor missing or wrong, or the roster unreadable — answers with the
SAME `(401, {"detail": "unauthorized"})`. There is no oracle about
which factor failed, not even in control flow (both factors are
evaluated before the verdict).

The crew secret is shown once (at `init`, or minted by `rotate-secret`)
and stored only as SHA-256. A secret typed as a CLI argument is
REFUSED (`-` stdin or `@file` only). Rotation keeps the previous
secret valid 600 s so the operator can walk the new one across every
member in either order.

## Files under `<state_dir>/`

| Path | Mode | Contents |
|---|---|---|
| `crew.json` | 0600 (atomic write) | role (`solo\|lead\|peer`), self name, lead name, peers `{name: {url, cert_sha256_pin, added_at}}`, `secret_hash`, `previous_secret_hash` + deadline |
| `crew-join-token.json` | 0600 | the live join token's hash, TTL, attempt budget (deleted on use) |
| `crew-certs/<member>.pem` | 0600 | enrolled members' certificate PEMs (public material, private dir) |
| `crew-tls/crew.crt`, `crew.key` | 0600 | this host's own certificate (openssl-generated, ~10 years) |

A corrupt `crew.json` fails CLOSED: `CrewRoster.load` raises
`CrewRosterCorrupt`, the service answers the uniform 401, and the CLI
errors — never a silent reset to solo (a reset would un-pin every
peer).

## Enrolment (operator-mediated, no chicken-and-egg)

1. Lead: `crew init` (mints + prints the secret once, generates the
   host cert) and `crew join-token` (one use, 10 min TTL, 5 wrong
   guesses burn it — the pairing.py code discipline).
2. Operator carries to the candidate host: the lead's certificate PEM,
   the crew secret, the join token.
3. Candidate: `crew join ...` (see verbs below) — it POSTs
   `/crew/v1/enrol` to the lead with the token as bearer plus its own
   name/url/certificate PEM. The lead pins the certificate, records
   the peer, consumes the token; a replay enrols nobody.
4. The candidate then records the lead (explicit name + pinned cert)
   and its own cert locally.

`/crew/v1/enrol` is the ONLY route without the two factors — the join
token IS its authentication. Its token-side failures also answer the
uniform 401; malformed payloads answer 400 without burning the token.

## Peer-side routes (`CrewService.handle`)

The dispatcher takes `(method, path, query, body, headers_get,
peer_cert_der)` and returns `(status, payload)`. Route table:

```
POST /crew/v1/enrol                        join-token bearer
GET  /crew/v1/herd
GET  /crew/v1/view?pane_id=
GET  /crew/v1/history?pane_id=&…           query passes through
GET  /crew/v1/pane/{pane_id}/mirror?…
POST /crew/v1/pane/{pane_id}/reply         {"text": …}
POST /crew/v1/pane/{pane_id}/keys          {"keys": […]}
POST /crew/v1/pane/{pane_id}/dialog/answer {…dialog payload…}
GET  /crew/v1/approval/current?pane_id=
POST /crew/v1/approval/{gate_id}/action    {"decision": …}
POST /crew/v1/msg/who                    {…message payload…}
POST /crew/v1/msg/send                   {…message payload…}
POST /crew/v1/msg/status                 {…message payload…}
POST /crew/v1/msg/cancel                 {…message payload…}
```

Handlers are injected: `herd()`, `view(pane_id)`, `history(pane_id, **q)`,
`mirror(pane_id, **q)`, `reply(pane_id, text)`, `keys(pane_id, keys)`,
`dialog_answer(pane_id, **payload)`, `approval_current(pane_id=None, **q)`,
`approval_action(gate_id, decision)`. A handler returns a payload (200)
or `(status, payload)`; the owner's 404 for a dead gate travels as-is.
The dispatcher re-reads the roster per request, so rotations,
revocations and promotions land without a restart.

Message handlers receive `member` (the authenticated TLS identity) and
`payload` (the untrusted JSON body) as separate keyword arguments.
An identically named body field cannot replace the transport identity.
`CrewClient.message(operation, payload)` uses the existing pinned crew
transport and bearer secret. It never retries mutations: a lost response
does not establish that the destination did nothing. These additive
routes return 404 until a message handler is installed by the server.

## Lead-side integration (parent wiring)

- `/herd`: `merge_herds(local_herd, [PeerHerd(name, herd) | (name, None)])`
  — remote panes gain `host`, local panes keep the single-host shape,
  duplicates on `(host, pane_id)` collapse, unreachable peers appear in
  `peers: [{name, state: "fresh"|"unreachable"}]`.
- Pane routing: `split_pane_id("host-b:p-1") → ("host-b", "p-1")`,
  `owner_host_for_pane(pid, roster) → "local" | peer name | None`.
  `None` (unknown host) must surface as an error, never fall back to local.
- Reads/writes to a remote pane go through `CrewClient` (one per peer,
  `cert_pem=roster.peer_cert_pem(name)`). Reads: `herd/view/history/mirror`;
  writes: `reply/keys/dialog_answer`. Timeouts default to 8 s;
  `client.health` tracks per-peer backoff — mark and skip dead peers in
  sweeps, but always try approvals (they must not be dropped).
- Gates (doc 10 risk 4): a gate is owned by the host that ran the send.
  On the lead, route decisions with `GateRouter`: local gates through the
  local `ApprovalGateStore`; a remote gate through
  `forward=client.approval_resolve` (its 404 is the answer — no retry).
  Populate `GateIndex` when a proxied `approval/current` reports a
  remote gate. Concurrent resolves funnel into the owner's store, so
  exactly one applies (second attempt → 404).
- Manual promotion only: `crew promote` rewrites roles explicitly. There
  is no watcher, election, timeout or takeover anywhere in this package.

## TLS listener (parent wiring)

Proposed crew port: **8747** (brain's own 8741 + 6; configurable),
a separate uvicorn listener bound on the tailnet interface, mounted
only when the roster has a crew (peer or lead). Peer cert material
comes from `ensure_host_certificate(<state>/crew-tls, name)` — openssl
binary, self-signed, ~10 years; the CLI prints the exact command when
openssl is missing. Never hand-roll X.509.

```python
# peer-side listener: the lead is the only expected client and its
# cert is enrolled at join → require it in the handshake
ssl_ctx = server_ssl_context(cert, key, ca_bundle_of_enrolled_pems,
                             require_client=True)
uvicorn.run(app, host=..., port=8747, ssl_certfile=cert,
            ssl_keyfile=key, ssl_cert_reqs=ssl.CERT_REQUIRED,
            ssl_ca_certs=ca_bundle)

# lead-side listener during an ENROLMENT window: a candidate's cert is
# not enrolled yet, so the handshake cannot require it (CERT_OPTIONAL);
# every route except /crew/v1/enrol still fails closed —
# check_crew_auth requires the DER pin, so a client without a
# certificate authenticates as nobody.
```

The FastAPI adapter is mechanical: extract the peer certificate DER
from `request.scope["ssl_object"].getpeercert(binary_form=True)` (or
the transport's equivalent), then call
`service.handle(method, request.url.path, dict(request.query_params),
await body, request.headers.get, der)`.

## CLI verbs (bin/herdr-brain crew …)

All run as `python -m herdr_brain.crew <verb>` (the `pairing_cli`
pattern). Secrets never on argv (`-` = stdin, `@file`); PEMs likewise.

| Verb | Semantics |
|---|---|
| `init [--name NAME] [--secret SRC]` | solo → lead. Mints and prints the secret ONCE when `--secret` omitted (marker line `crew-secret: …`); generates the host cert via openssl; refuses if already in a crew. |
| `join-token` | Lead only. Prints a one-use, 10-minute join token; replaces any previous one. |
| `join --name --lead-name --lead-url --lead-cert @F --url --cert @F --secret - --token T` | Peer side. Presents `{name,url,cert_pem}` to the lead's `/crew/v1/enrol` with the token as bearer; on the lead's 200 records the lead (pinned) and writes the peer roster. A refusal writes NOTHING. |
| `list` | Prints role, self, lead, each peer with url + pin prefix. |
| `remove <MEMBER>` | Lead: drops a peer. `remove` of self (or, on a peer, of the lead) leaves the crew → solo. |
| `promote [<MEMBER>]` | MANUAL promotion. Default member = this host: it becomes the lead, the roster's roles are rewritten explicitly (the old lead stays a member). Naming another member demotes this host to peer. No automatic takeover exists. |
| `rotate-secret [--secret SRC]` | Sets a new secret (minted + printed once when omitted); the previous stays valid 600 s. Run with the SAME new secret on every member inside the window. |

## Testing stance

No network in tests: transports are injected; certificate pins are
checked against committed fixture DERs (`tests/fixtures/crew/`);
certificate generation shells to the local openssl only. The gate
race is exercised through an in-process transport joining a real
`CrewClient` to a real `CrewService` — two fake hosts, eight
concurrent resolves, exactly one applied.
