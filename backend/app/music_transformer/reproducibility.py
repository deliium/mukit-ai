"""Seed helpers for reproducible CPU train/generate."""

from __future__ import annotations

import logging
import os
import random


logger = logging.getLogger(__name__)


def seed_everything(seed: int) -> None:
    """Seed Python, random, numpy (if present), and torch (+ cuda best-effort)."""
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:
        logger.debug("numpy not installed; skipping numpy seed")
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
    except ImportError:
        logger.debug("torch not installed; skipping torch seed")
    logger.info("seed_everything applied", extra={"seed": seed})
