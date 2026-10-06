"""The RunPod image must be able to start Vault and log the app into it.

Three defects kept the first staging Pod from booting, and none of them shows
up anywhere except on a real Pod:

  * `deploy/vault/start.sh` and `deploy/vault/bootstrap.sh` default to the
    paths Compose MOUNTS their files at (`/vault/config/server.hcl`,
    `/app-policy.hcl`). The image has neither mount, so the entrypoint must
    point both scripts at the copies under `/app/deploy/vault/`.
  * The app reads its MinIO keys out of Vault at startup and refuses to boot
    without a Vault login. On a Pod no `VAULT_SECRET_ID` can exist before Vault
    is initialised, so the bootstrap mints one per boot and `app`/`worker`
    start through `aizzak-with-vault`, which reads it.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNPOD = _REPO_ROOT / "deploy" / "runpod"
_ENTRYPOINT = _RUNPOD / "entrypoint.sh"
_BOOTSTRAP = _RUNPOD / "bootstrap.sh"
_SUPERVISORD = _RUNPOD / "supervisord.conf"
_DOCKERFILE = _RUNPOD / "Dockerfile"
_WRAPPER = _RUNPOD / "with-vault.sh"
_VAULT_BOOTSTRAP = _REPO_ROOT / "deploy" / "vault" / "bootstrap.sh"
_VAULT_START = _REPO_ROOT / "deploy" / "vault" / "start.sh"

# `COPY deploy/ ./deploy/` with WORKDIR /app: an /app/deploy/... path in the
# image is the same path under the repository root.
_IMAGE_APP_ROOT = PurePosixPath("/app")
_WRAPPER_IN_IMAGE = "/usr/local/bin/aizzak-with-vault"
_CRED_FILE = "/run/aizzak/vault-approle.env"


def _exported(name: str) -> str:
    text = _ENTRYPOINT.read_text(encoding="utf-8")
    match = re.search(rf"^export {name}=(\S+)$", text, flags=re.MULTILINE)
    assert match is not None, f"deploy/runpod/entrypoint.sh does not export {name}"
    return match.group(1)


def _repo_path(image_path: str) -> Path:
    return _REPO_ROOT / PurePosixPath(image_path).relative_to(_IMAGE_APP_ROOT)


def _program_command(name: str) -> str:
    text = _SUPERVISORD.read_text(encoding="utf-8")
    # Anchored: the comments above a section mention `[program:worker]` too.
    header = re.search(rf"^\[program:{name}\]$", text, flags=re.MULTILINE)
    assert header is not None, f"supervisord.conf has no [program:{name}]"
    start = header.start()
    end = text.find("\n[program:", start + 1)
    section = text[start : end if end != -1 else len(text)]
    match = re.search(r"^command=(.+)$", section, flags=re.MULTILINE)
    assert match is not None, f"[program:{name}] has no command="
    return match.group(1)


def test_entrypoint_points_vault_scripts_at_files_the_image_has() -> None:
    for name, script in (
        ("VAULT_CONFIG_TEMPLATE", _VAULT_START),
        ("VAULT_POLICY_FILE", _VAULT_BOOTSTRAP),
    ):
        assert f"${{{name}:-" in script.read_text(encoding="utf-8"), (
            f"{script.name} no longer reads {name}, so the entrypoint's export is dead"
        )
        path = _exported(name)
        assert _repo_path(path).is_file(), f"{name}={path} does not exist in the image"


def test_bootstrap_mints_the_login_the_wrapper_reads() -> None:
    bootstrap = _BOOTSTRAP.read_text(encoding="utf-8")
    wrapper = _WRAPPER.read_text(encoding="utf-8")
    assert "auth/approle/role/app/secret-id" in bootstrap
    assert f"VAULT_CRED_FILE={_CRED_FILE}" in bootstrap
    assert f"AIZZAK_VAULT_CRED_FILE:-{_CRED_FILE}" in wrapper
    # The mint must come after the role exists and before anything starts.
    assert bootstrap.index("deploy/vault/bootstrap.sh") < bootstrap.index("secret-id")
    assert bootstrap.index("secret-id") < bootstrap.index("start worker")


def test_vault_readers_start_through_the_wrapper() -> None:
    for name in ("app", "worker"):
        command = _program_command(name)
        assert command.startswith(f"{_WRAPPER_IN_IMAGE} /opt/venv/bin/"), (
            f"[program:{name}] starts without the Vault login: {command}"
        )
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(
        rf"^COPY deploy/runpod/with-vault\.sh\s+{re.escape(_WRAPPER_IN_IMAGE)}$",
        dockerfile,
        flags=re.MULTILINE,
    ), "the Dockerfile does not install the wrapper supervisord.conf names"
    assert _WRAPPER_IN_IMAGE in dockerfile[dockerfile.index("RUN chmod +x") :]
