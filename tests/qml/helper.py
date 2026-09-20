"""Inert fixture, not the backend: no network, files, keyring, or real login."""
import json
import sys
import time

value = json.loads(sys.stdin.read(8192))
assert value == {} and sys.argv[1] in ("status", "preview", "fixture-failure", "fixture-timeout")
if sys.argv[1] == "fixture-timeout":
    time.sleep(5)
state = dict(connected=False, ship="", url="", automatic=False, pending=False,
             authenticationRequired=False, lastPublished="", lastTheme="", lastError="")
palette = dict(id="omarchy-urbit-theme", name="Fixture", dark=True, primary="#123456",
               secondary="#ABCDEF", tertiary="#987654", background="#112233", surface="#223344")
sys.stderr.write("FIXTURE_STDERR_MUST_NOT_BE_FORWARDED\n")
failed = sys.argv[1] == "fixture-failure"
sys.stdout.write(json.dumps(dict(schemaVersion=1, ok=not failed, state=None if failed else state,
                               palette=palette if sys.argv[1] == "preview" else None,
                               error=dict(code="busy", message="Fixture failure", retryable=True) if failed else None)))
sys.exit(1 if failed else 0)
