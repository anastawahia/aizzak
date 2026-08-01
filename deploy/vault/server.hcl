# Vault server configuration -- persistent `file` storage (release-blockers-
# plan.md §3 step 1 · docs/quickstart.md §5). Replaces `vault server -dev`,
# which kept KV, Transit and the AppRole ENTIRELY in memory: a container
# restart wiped them, and a regenerated Transit key cannot decrypt ciphertext
# produced under the old one (`CipherRef`, INV-C2/INV-I1) -- permanent data
# loss for whichever tenant credential or OAuth token was encrypted first.
#
# ⚠️ This file is a TEMPLATE, not a config Vault reads directly. `start.sh`
# substitutes the two placeholders below before handing the result to
# `vault server -config=`, because the safe listener address differs by
# deployment target and Vault has no environment-variable override for a
# listener's bind address:
#   Docker Compose -- 0.0.0.0  (the per-service CONTAINER is the isolation
#                      boundary; every sibling service in this stack binds
#                      0.0.0.0 inside its own container the same way)
#   RunPod         -- 127.0.0.1 (one Pod = one container = one network
#                      namespace for EVERYTHING; loopback is the only
#                      boundary that deployment has at all)
#
# ⚠️ `disable_mlock = true` is a deliberate simplification, not an oversight.
# Locking Vault's memory (via CAP_IPC_LOCK) stops secrets from being paged to
# swap -- a real protection, but one that only matters against a threat this
# deployment does not defend against anywhere else: this wrapper already
# writes the unseal key and root token to a PLAIN FILE on the same host
# (there is no KMS to auto-unseal against instead -- see start.sh's header
# and docs/quickstart.md §5). Spending complexity on mlock while the unseal
# key sits unencrypted next to it would be security theatre, not defense in
# depth. NOT acceptable for real production -- see docs/quickstart.md §5 for
# what production needs instead (an external KMS or Transit auto-unseal),
# which would also remove the reason for `disable_mlock` in the first place.
storage "file" {
  path = "__VAULT_DATA_DIR__"
}

listener "tcp" {
  address     = "__VAULT_LISTEN_ADDR__:8200"
  tls_disable = "true"
}

disable_mlock = true
ui            = true
