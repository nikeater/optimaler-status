"""Confirm-and-dispatch, phase-0 shape.

Nothing here sends anything. What it does is stamp the facts that a dispatch
WOULD produce - the date, the absolute response deadline computed from it, the
channel shape the letter needs - onto the journal at the one moment a human
takes responsibility for the letter, and drop an xdomea-shaped handover file in
an out-directory for a pilot adapter that does not exist yet.
"""

from engine.dispatch.xdomea import (
    DISPATCH_DIR_ENV,
    STUB_NAMESPACE,
    DispatchFacts,
    DispatchStub,
    build_stub_xml,
    dispatch_dir,
    stub_filename,
    write_stub,
)

__all__ = [
    "DISPATCH_DIR_ENV",
    "STUB_NAMESPACE",
    "DispatchFacts",
    "DispatchStub",
    "build_stub_xml",
    "dispatch_dir",
    "stub_filename",
    "write_stub",
]
