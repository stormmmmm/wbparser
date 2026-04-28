"""WB → MAX bridge worker.

This package contains *only* the glue between two upstream services and
deliberately holds **no** business logic of its own:

* the WB parser publishes ``ReadyPost`` payloads (``parser/`` package);
* the MAX gateway consumes ``ReadyPost`` via ``POST
  /v1/accounts/{account}/publication-jobs`` (``maxapi/`` package);
* this bridge polls the parser, locks each post, forwards it to the
  gateway, and reports the outcome back to the parser.

The bridge does not filter, score, rewrite or schedule posts — that
is the parser's job. It also does not own any MAX session state — that
is the gateway's job.
"""

from bridge.config import BridgeSettings, load_settings
from bridge.translator import translate_ready_post
from bridge.worker import process_one, run_loop

__all__ = [
    "BridgeSettings",
    "load_settings",
    "process_one",
    "run_loop",
    "translate_ready_post",
]

__version__ = "0.1.0"
