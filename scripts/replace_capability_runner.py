"""Replace one idle runner from a private inspection snapshot, with rollback.

Configuration files contain credentials: keep them host-local and mode 0600.
This is a host tool; it never uses a shell to construct container arguments.
"""

import argparse
import http.client
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen


class Engine(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=30)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect("/var/run/docker.sock")


def api(method, path, body=None):
    connection = Engine()
    connection.request(
        method,
        path,
        body=json.dumps(body) if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    response = connection.getresponse()
    data = response.read()
    connection.close()
    if response.status >= 300:
        raise RuntimeError(
            f"Docker Engine {method} {path.split('?')[0]} returned {response.status}"
        )
    return json.loads(data) if data else {}


def replace(snapshot, image, environment, command, health_url, state_dir):
    name = snapshot["Name"].lstrip("/")
    current = api("GET", "/containers/" + quote(name) + "/json")
    if current["Id"] != snapshot["Id"]:
        raise ValueError("Container changed since inspection; refuse stale replacement")
    image_info = api("GET", "/images/" + quote(image, safe="") + "/json")
    config = dict(snapshot["Config"])
    base_env = dict(v.split("=", 1) for v in image_info["Config"].get("Env", []))
    base_env.update(v.split("=", 1) for v in config.get("Env", []))
    base_env.update(environment)
    config.update(
        Image=image_info["Id"],
        Env=[k + "=" + v for k, v in base_env.items()],
        Entrypoint=image_info["Config"].get("Entrypoint"),
        Cmd=command,
        Hostname="",
        Domainname="",
        HostConfig=snapshot["HostConfig"],
    )
    private = Path(state_dir) / (name + ".replacement-" + str(time.time_ns()) + ".json")
    fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(config, handle)
    backup_name = name + "-rollback-" + str(time.time_ns())
    subprocess.run(["docker", "stop", "--timeout", "90", name], check=True)
    subprocess.run(
        ["docker", "update", "--restart=no", name], check=True, stdout=subprocess.DEVNULL
    )
    api("POST", "/containers/" + current["Id"] + "/rename?name=" + quote(backup_name))
    created = None
    try:
        created = api("POST", "/containers/create?name=" + quote(name), config)["Id"]
        api("POST", "/containers/" + created + "/start")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if not api("GET", "/containers/" + created + "/json")["State"]["Running"]:
                raise RuntimeError("Replacement runner exited during startup")
            try:
                with urlopen(health_url, timeout=3) as response:
                    if response.status == 200:
                        print(name + " replacement health=200 image=" + image_info["Id"])
                        return
            except Exception:
                pass
            time.sleep(2)
        raise RuntimeError("Replacement runner did not become healthy")
    except BaseException:
        if created:
            subprocess.run(
                ["docker", "stop", "--timeout", "15", created],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            api(
                "POST",
                "/containers/"
                + created
                + "/rename?name="
                + quote(name + "-failed-" + str(int(time.time()))),
            )
        api("POST", "/containers/" + current["Id"] + "/rename?name=" + quote(name))
        subprocess.run(
            ["docker", "update", "--restart=unless-stopped", name],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(["docker", "start", name], check=True, stdout=subprocess.DEVNULL)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    snapshots = json.loads(Path(plan["snapshot"]).read_text())
    snapshot = next(x for x in snapshots if x["Name"].lstrip("/") == plan["name"])
    replace(
        snapshot,
        plan["image"],
        plan["environment"],
        plan["command"],
        plan["health_url"],
        args.plan.parent,
    )


if __name__ == "__main__":
    main()
