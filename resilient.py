"""Bounded retry helpers for transient network and PDF operations."""
from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

T=TypeVar("T")
TRANSIENT={408,425,429,500,502,503,504}


def retry_call(fn: Callable[[],T], attempts: int=4, base_delay: float=0.5,
               max_delay: float=8.0, sleep: Callable[[float],None]=time.sleep) -> T:
    for attempt in range(max(1,int(attempts))):
        try:
            response=fn()
            status=getattr(response,"status_code",None)
            if status not in TRANSIENT or attempt==attempts-1:
                return response
        except Exception:
            if attempt==attempts-1: raise
        delay=min(max_delay,base_delay*(2**attempt))
        sleep(delay*(0.8+random.random()*0.4))
    raise RuntimeError("Retry attempts exhausted")


def build_pdf_safely(builder: Callable[[],T], attempts: int=2, sleep=time.sleep) -> T:
    """Retry only transient I/O-like PDF build failures; re-raise deterministic layout bugs."""
    for attempt in range(max(1,int(attempts))):
        try:
            return builder()
        except (OSError,TimeoutError):
            if attempt+1>=attempts: raise
            sleep(min(2.0,0.25*(2**attempt)))
    raise RuntimeError("PDF build attempts exhausted")
