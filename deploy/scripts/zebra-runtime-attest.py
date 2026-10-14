#!/usr/bin/env python3
"""Host-side Zebra image and container provenance (the trusted Docker boundary).

Runs on the Docker host with the operator's Docker access. The engine and web
containers never mount the Docker socket; they only receive what this prints.

    python3 -I scripts/zebra-runtime-attest.py check-image --image "$ZWA_ZEBRA_IMAGE"
        The tag resolves to the Zebra image ID pinned for this host's
        platform, and that image was built for this platform (preflight).

    python3 -I scripts/zebra-runtime-attest.py attest --image "$ZWA_ZEBRA_IMAGE" --project zwa-demo
        Also: the project's one zebra container is running exactly that image
        ID. Prints the attestation read by `exchange.zebra_provenance record`.

    python3 -I scripts/zebra-runtime-attest.py verify --image "$ZWA_ZEBRA_IMAGE" --project zwa-demo \
        [--recorded-show show.json]
        The same checks as `attest`, as a fresh verification. A chain identity's
        recorded runtime is point-in-time evidence only, so run this after
        every Zebra start, restart or container replacement. With the output
        of `exchange.zebra_provenance show`, also refuses an image that
        differs from the recorded one and reports whether the container is
        still the attested one.

Pins: exchange/persistent_chain.py PINNED_ZEBRA_IMAGES. An emulated image (an
amd64 image on an arm64 host, or the reverse) is refused.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY))
from exchange.persistent_chain import PINNED_ZEBRA_IMAGES  # noqa: E402
from exchange.zebra_provenance import ATTESTATION_SCHEMA, MACHINE_PLATFORMS, PROJECT  # noqa: E402


DOCKER = ["docker"]  # ["sudo", "-n", "docker"] with --docker-sudo


class AttestError(RuntimeError):
    pass


def _docker(*args: str) -> str:
    result = subprocess.run([*DOCKER, *args], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise AttestError(result.stderr.strip() or f"docker {args[0]} failed")
    return result.stdout


def _one(raw: str) -> dict:
    data = json.loads(raw)
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise AttestError("unexpected docker inspect output")
    return data[0]


def host_platform() -> str:
    info = json.loads(_docker("info", "--format", "{{json .}}"))
    platform = MACHINE_PLATFORMS.get(str(info.get("Architecture", "")).lower())
    if info.get("OSType") != "linux" or platform is None:
        raise AttestError("Docker host is not linux/arm64 or linux/amd64")
    return platform


def check_image(tag: str) -> tuple[str, str]:
    if not tag or len(tag) > 256 or tag.startswith("-"):
        raise AttestError("invalid image reference")
    image = _one(_docker("image", "inspect", "--", tag))
    pinned = {image_id: platform for platform, image_id in PINNED_ZEBRA_IMAGES.items()}
    image_id = image.get("Id")
    if image_id not in pinned:
        raise AttestError(f"{tag} resolves to {image_id}, which is not a pinned Zebra image")
    platform = f"{image.get('Os')}/{image.get('Architecture')}"
    if platform != pinned[image_id]:
        raise AttestError(f"image {image_id} reports {platform}; its pin is {pinned[image_id]}")
    host = host_platform()
    if host != platform:
        raise AttestError(f"the {platform} Zebra image is not native to this {host} host")
    return image_id, platform


def zebra_container(project: str) -> dict:
    ids = _docker("ps", "-a", "--no-trunc", "--filter", f"label=com.docker.compose.project={project}",
                  "--filter", "label=com.docker.compose.service=zebra", "--format", "{{.ID}}").split()
    if len(ids) != 1:
        raise AttestError(f"expected one zebra container in Compose project {project}, found {len(ids)}")
    info = _one(_docker("inspect", "--type", "container", "--", ids[0]))
    labels = (info.get("Config") or {}).get("Labels") or {}
    if (info.get("Id") != ids[0] or labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.service") != "zebra"):
        raise AttestError("zebra container identity or Compose labels differ")
    return info


def attest(tag: str, project: str, now: int | None = None) -> dict:
    if not PROJECT.fullmatch(project):
        raise AttestError("invalid Compose project name")
    image_id, platform = check_image(tag)
    info = zebra_container(project)
    if info.get("Image") != image_id:
        raise AttestError(f"the running zebra container uses {info.get('Image')}, not {image_id}")
    if (info.get("State") or {}).get("Running") is not True:
        raise AttestError("the zebra container is not running")
    return {"schema": ATTESTATION_SCHEMA, "imageId": image_id, "platform": platform, "configuredImage": tag,
            "containerId": info["Id"], "containerImageId": info["Image"], "containerRunning": True,
            "composeProject": project, "attestedAt": int(time.time()) if now is None else now}


def verify(tag: str, project: str, recorded: dict | None = None, now: int | None = None) -> dict:
    fresh = attest(tag, project, now)
    report = {"verified": True, "checkedAt": fresh["attestedAt"], "imageId": fresh["imageId"],
              "platform": fresh["platform"], "containerId": fresh["containerId"], "composeProject": project}
    if recorded is not None:
        provenance = recorded.get("provenance") if isinstance(recorded, dict) else None
        if not isinstance(provenance, dict) or provenance.get("evidence") != "POINT_IN_TIME_HOST_ATTESTATION":
            raise AttestError("the `show` output records no runtime attestation")
        if (provenance.get("attestedImage"), provenance.get("attestedPlatform")) != (fresh["imageId"],
                                                                                     fresh["platform"]):
            raise AttestError("the running Zebra image differs from the recorded runtime")
        report["sameContainerAsRecord"] = provenance.get("attestedContainerId") == fresh["containerId"]
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = parser.add_subparsers(dest="action", required=True)
    checked = actions.add_parser("check-image")
    checked.add_argument("--image", required=True)
    attested = actions.add_parser("attest")
    attested.add_argument("--image", required=True)
    attested.add_argument("--project", required=True)
    verified = actions.add_parser("verify")
    verified.add_argument("--image", required=True)
    verified.add_argument("--project", required=True)
    verified.add_argument("--recorded-show", type=Path)
    for sub in (checked, attested, verified):
        sub.add_argument("--docker-sudo", action="store_true", help="run Docker as `sudo -n docker`")
    args = parser.parse_args(argv)
    if args.docker_sudo:
        DOCKER[:] = ["sudo", "-n", "docker"]
    try:
        if args.action == "check-image":
            image_id, platform = check_image(args.image)
            output = {"imageId": image_id, "platform": platform}
        elif args.action == "verify":
            recorded = json.loads(args.recorded_show.read_text()) if args.recorded_show else None
            output = verify(args.image, args.project, recorded)
        else:
            output = attest(args.image, args.project)
    except (AttestError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"ZEBRA_ATTEST_REFUSED {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()
