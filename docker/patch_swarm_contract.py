"""Add an envelope to the existing caller-stamped publisher without replacing it."""

from pathlib import Path

path = Path("/app/runner.py")
text = path.read_text()
if "comfystream.swarm-publish.v1" not in text:
    source = "from livepeer_gateway.live_runner import register_runner"
    target = source + "\nfrom runner_contract import envelope"
    if text.count(source) != 1 or text.count("            app=APP_ID,") != 1:
        raise RuntimeError("Unexpected publisher source; refusing an unreviewed patch")
    text = text.replace(source, target)
    text = text.replace(
        "            app=APP_ID,",
        "            app=APP_ID,\n"
        '            metadata=envelope("comfystream.swarm-publish.v1", '
        '{"start": {"channels": {"in": ["in", "stamps", "control"], "out": ["events"]}}}, '
        'settings={"protocol": 1}),',
    )
    compile(text, str(path), "exec")
    path.write_text(text)
