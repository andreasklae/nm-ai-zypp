import logging
import sys
from tempfile import TemporaryDirectory

import sentry_sdk

logging.basicConfig(
    format="%(asctime)s.%(msecs)03d [%(levelname)-5s] [%(name)s] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)

tmp_dir = TemporaryDirectory(suffix="_project_naam")

if sys.platform in ["darwin", "win32"]:
    sentry_env = "development"
    tmp_dir.name = "data"
elif sys.platform == "linux":
    sentry_env = "production"
else:
    raise ValueError(f"Environment {sys.platform} unknown")

sentry_sdk.init(
    # "SENTRY_PROJECT_STRING",
    traces_sample_rate=1.0,
    environment=sentry_env,
)
