"""
comm/
=====
Communication with the EA-PS 10060-170 source.

    scpi     — control over USB/COM (SCPI ASCII)  <- active
    bridge   — SCPI <-> Modbus TCP bridge         <- DC microgrid

Do not import anything here directly — each module imports what it needs from
comm.scpi, comm.bridge, etc. to avoid circular imports.
"""