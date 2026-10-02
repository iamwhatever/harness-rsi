"""The app cron's script: ask the backend to start what is due (``backend.schedule``); it decides."""


def run(ctx):
    ctx._post("/api/apps/harness-rsi/schedule/tick", {})  # the gateway call ScriptContext.notify uses
