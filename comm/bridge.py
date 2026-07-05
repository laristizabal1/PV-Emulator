"""
comm/bridge.py
==============
SCPI <-> Modbus TCP bridge  |  EA-PS 10060-170
Compatibility: pymodbus >= 3.12

Architecture:
    [EA-PS 10060-170]
          | SCPI / USB-COM  (2-3 ms/query)
          v
    EAPowerSupply  (optimized driver)
          | polling every POLL_INTERVAL ms
          v
    ModbusDeviceContext  (in-memory registers)
          | TCP/IP Ethernet
          v
    PLC / SCADA / DC microgrid

Holding Register map (Float32 = 2 HR, big-endian high-word first):
  HR  0-1  : Measured voltage (V)       Float32
  HR  2-3  : Measured current (A)       Float32
  HR  4-5  : Measured power (W)         Float32
  HR  6-7  : Voltage setpoint (V)       Float32  [local cache]
  HR  8-9  : Current setpoint (A)       Float32  [local cache]
  HR  10   : Output state (0=OFF/1=ON)  UInt16
  HR  11   : Remote active              UInt16   [always 1]
  HR  12   : SCPI error code            UInt16

Coil 0 (write from Modbus client): 1 = Output ON, 0 = Output OFF

Dependencies:
    pip install pyserial pymodbus>=3.12

Designed for 20 ms:
    - A single SCPI query per cycle (MEAS:ALL? -> V,I,P in one response)
    - V/I setpoints cached locally (not queried from the source)
    - Block write of all registers in a single setValues()
    - Minimal serial delays: 2 ms write / 3 ms query
    - asyncio for the Modbus server (does not block polling)
    - threading.Event.wait() instead of time.sleep() for clean interruption
"""

from __future__ import annotations

import asyncio
import struct
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple

from config.hardware import (V_MAX, I_MAX, DT_MIN, DEFAULT_PORT, DEFAULT_BAUD,
                             DEFAULT_MODBUS_HOST, DEFAULT_MODBUS_PORT)

# pyserial
try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

# pymodbus 3.12
try:
    from pymodbus.pdu.device import ModbusDeviceIdentification
    from pymodbus.server import ModbusTcpServer
    from pymodbus.datastore import (
        ModbusSequentialDataBlock,
        ModbusServerContext,
        ModbusSlaveContext,
    )
    MODBUS_AVAILABLE = True
except ImportError:
    MODBUS_AVAILABLE = False

log = logging.getLogger("Bridge")


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BridgeConfig:
    # SCPI connection to the EA source
    serial_port:    str   = DEFAULT_PORT
    serial_baud:    int   = DEFAULT_BAUD
    serial_timeout: float = 0.05     # 50 ms — reduced to the max tolerable

    # Minimal SCPI communication delays (ms)
    write_delay_ms: float = 2.0      # delay after write
    query_delay_ms: float = 3.0      # delay between write and readline

    # Servidor Modbus TCP
    modbus_host:    str   = DEFAULT_MODBUS_HOST
    modbus_port:    int   = DEFAULT_MODBUS_PORT
    modbus_unit:    int   = 1

    # Register update rate
    # 20 ms = 50 Hz — practical limit with serial SCPI at 115200 baud
    poll_interval_ms: float = 20.0


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS: Float32 <-> Holding Registers Modbus
# ─────────────────────────────────────────────────────────────────────────────

def _f2r(value: float) -> Tuple[int, int]:
    """Float32 -> (high_word, low_word) big-endian."""
    raw = struct.pack(">f", float(value))
    return struct.unpack(">HH", raw)


def _r2f(high: int, low: int) -> float:
    """(high_word, low_word) -> Float32."""
    return struct.unpack(">f", struct.pack(">HH", high, low))[0]


def _parse_float(raw: str) -> Optional[float]:
    """Parse SCPI response '10.00 V' or '2.5' -> float. None on failure."""
    try:
        return float(raw.strip().split()[0])
    except (ValueError, IndexError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# OPTIMIZED SCPI DRIVER
# Designed for minimal latency: 2-3 ms delays, combined query, cache
# ─────────────────────────────────────────────────────────────────────────────

class EAPowerSupply:
    """
    Low-latency SCPI driver for the EA-PS 10060-170.

    20 ms strategy:
      1. _read_meas_fast(): tries MEAS:ALL? -> V,I,P in a single query.
         If the source does not support it, falls back to MEAS:VOLT? +
         MEAS:CURR? separately (16 ms worst-case, still within the 20 ms budget
         with baud 115200 and a low-latency USB cable).
      2. V/I setpoints are NOT queried from the source — they are cached locally
         each time they are sent from the pipeline or manual control.
      3. write_batch() sends multiple commands with no delay between them
         (only 1 ms apart) for initialization.
    """

    def __init__(self, cfg: BridgeConfig):
        self.cfg   = cfg
        self.ser: Optional[object] = None
        self._lock = threading.Lock()

        # Delays converted to seconds for use in time.sleep
        self._wd = cfg.write_delay_ms / 1000.0
        self._qd = cfg.query_delay_ms / 1000.0

        # Setpoint cache (updated when sending commands, not queried)
        self._cache_v:   float = 0.0
        self._cache_i:   float = 0.0
        self._cache_out: int   = 0

        # Read mode: "all" uses MEAS:ALL?, "separate" uses two queries
        self._meas_mode: str = "all"

    # -- Connection -----------------------------------------------------------
    def open(self) -> str:
        if not SERIAL_AVAILABLE:
            raise RuntimeError("pyserial not installed: pip install pyserial")
        self.ser = serial.Serial(
            port      = self.cfg.serial_port,
            baudrate  = self.cfg.serial_baud,
            timeout   = self.cfg.serial_timeout,
            write_timeout = self.cfg.serial_timeout,
        )
        time.sleep(0.1)
        return self._query_raw("*IDN?")

    def close(self) -> None:
        if self.ser and self.ser.is_open:
            self.ser.close()

    # -- Base communication ---------------------------------------------------
    def _write_raw(self, cmd: str) -> None:
        """Write an SCPI command. Does NOT use the lock — call from methods that already hold it."""
        self.ser.write((cmd.strip() + "\n").encode("ascii"))
        time.sleep(self._wd)

    def _readline_raw(self) -> str:
        """Read one line of SCPI response."""
        time.sleep(self._qd)
        return self.ser.readline().decode("ascii", errors="ignore").strip()

    def _query_raw(self, cmd: str) -> str:
        """Write + readline without lock."""
        self.ser.write((cmd.strip() + "\n").encode("ascii"))
        time.sleep(self._qd)
        return self.ser.readline().decode("ascii", errors="ignore").strip()

    def write(self, cmd: str) -> None:
        with self._lock:
            self._write_raw(cmd)

    def query(self, cmd: str) -> str:
        with self._lock:
            return self._query_raw(cmd)

    def write_batch(self, commands: list[str]) -> None:
        """Send multiple commands 1 ms apart (fast initialization)."""
        with self._lock:
            for cmd in commands:
                self.ser.write((cmd.strip() + "\n").encode("ascii"))
                time.sleep(0.001)

    # -- Measurement reading OPTIMIZED for 20 ms ------------------------------
    def read_meas_fast(self) -> Tuple[float, float, float]:
        """
        Read measured V, I, P in the least possible time.

        Tries MEAS:ALL? first (a single query, ~8 ms at baud 115200). If the
        source returns an error or unexpected format, falls back to separate
        mode MEAS:VOLT? + MEAS:CURR? (~16 ms).

        Power is computed as V*I if MEAS:ALL? does not include it.

        Returns: (V_meas, I_meas, P_meas) — (0.0, 0.0, 0.0) on failure.
        """
        with self._lock:
            if self._meas_mode == "all":
                try:
                    resp = self._query_raw("MEAS:ALL?")
                    parts = [p.strip() for p in resp.split(",")]
                    if len(parts) >= 2:
                        v = _parse_float(parts[0]) or 0.0
                        i = _parse_float(parts[1]) or 0.0
                        p = _parse_float(parts[2]) if len(parts) >= 3 else v * i
                        return v, i, (p or v * i)
                    # Invalid response -> switch to separate mode
                    self._meas_mode = "separate"
                    log.warning("Bridge: MEAS:ALL? unsupported, using separate queries (~16ms)")
                except Exception:
                    self._meas_mode = "separate"

            # Separate mode: two independent queries
            v = 0.0
            i = 0.0
            for cmd, store in [("MEAS:VOLT?", "v"), ("MEAS:CURR?", "i")]:
                try:
                    raw = self._query_raw(cmd)
                    val = _parse_float(raw) or 0.0
                    if store == "v":
                        v = val
                    else:
                        i = val
                except Exception:
                    pass
            return v, i, v * i

    def get_output_state(self) -> int:
        """Read the DC output state (0 or 1). Uses the cache if the read fails."""
        try:
            resp = self.query("OUTP:STAT?")
            state = 1 if resp.strip() in ("1", "ON") else 0
            self._cache_out = state
            return state
        except Exception:
            return self._cache_out

    def get_error_code(self) -> int:
        """Read the SCPI error code. Returns 0 on failure."""
        try:
            raw = self.query("SYST:ERR?")
            return int(raw.split(",")[0])
        except Exception:
            return 0

    # -- Control commands -----------------------------------------------------
    def output(self, on: bool) -> None:
        self.write(f"OUTP {'ON' if on else 'OFF'}")
        self._cache_out = 1 if on else 0

    def set_voltage(self, v: float) -> None:
        v_safe = min(max(float(v), 0.0), V_MAX)
        self.write(f"VOLT {v_safe:.4g}")
        self._cache_v = v_safe

    def set_current_limit(self, a: float) -> None:
        a_safe = min(max(float(a), 0.0), I_MAX)
        self.write(f"CURR {a_safe:.4g}")
        self._cache_i = a_safe

    def setup_batch(self, voltage: float, current: float) -> None:
        """Set V and I in a single batch (2 commands, ~3 ms total)."""
        v_safe = min(max(float(voltage), 0.0), V_MAX)
        a_safe = min(max(float(current), 0.0), I_MAX)
        self.write_batch([f"VOLT {v_safe:.4g}", f"CURR {a_safe:.4g}"])
        self._cache_v = v_safe
        self._cache_i = a_safe

    @property
    def cached_vset(self) -> float:
        return self._cache_v

    @property
    def cached_iset(self) -> float:
        return self._cache_i


# ─────────────────────────────────────────────────────────────────────────────
# MAIN BRIDGE: SCPI <-> Modbus TCP
# ─────────────────────────────────────────────────────────────────────────────

class ScpiModbusBridge:
    """
    SCPI <-> Modbus TCP bridge with 20 ms register updates.

    Two components in separate threads:
      - _poll_loop (daemon thread): reads SCPI and updates the Modbus HRs
      - Modbus TCP server (asyncio in a daemon thread): serves clients

    The PV pipeline (SCPIController in comm/scpi.py) uses the same COM port to
    send profile setpoints. To avoid collision:
      - During profile execution the bridge keeps polling measurements but does
        NOT send voltage/current commands.
      - The Modbus Coil 0 (remote control from SCADA) is automatically disabled
        while the pipeline is active.

    Public attributes:
      running        : True if the bridge is active
      last_readings  : dict with the latest measurements (for the HMI)
    """

    def __init__(self, cfg: BridgeConfig):
        self.cfg  = cfg
        self.ps   = EAPowerSupply(cfg)

        self._stop       = threading.Event()
        self._prev_coil0 = 0
        self.running     = False

        # Serial port ownership:
        #   True  -> the bridge opened it (start) and must close it in stop()
        #   False -> it is a serial borrowed from SCPIController (start_shared);
        #            stop() must NOT close it, only drop the reference.
        self._owns_serial = False

        # External lock: if True, the bridge does not respond to Coil 0
        # (the pipeline sets it while a profile runs)
        self.profile_running = False

        # Reading cache for the HMI (no lock overhead in the UI)
        self.last_readings: dict = {
            "V": 0.0, "I": 0.0, "P": 0.0,
            "Vset": 0.0, "Iset": 0.0,
            "output": 0, "error": 0,
            "meas_mode": "all",
            "poll_ms": 0.0,
        }

        # Modbus datastore — built in start()
        self._device: Optional[object] = None   # ModbusDeviceContext
        self._ctx:    Optional[object] = None   # ModbusServerContext

    # -- Modbus datastore -----------------------------------------------------
    def _build_datastore(self):
        di = ModbusSequentialDataBlock(0, [0] * 10)
        co = ModbusSequentialDataBlock(0, [0] * 10)
        hr = ModbusSequentialDataBlock(0, [0] * 20)
        ir = ModbusSequentialDataBlock(0, [0] * 20)
        self._device = ModbusSlaveContext(di=di, co=co, hr=hr, ir=ir)
        self._ctx    = ModbusServerContext(
            slaves={self.cfg.modbus_unit: self._device}, single=False
        )

    def _write_registers(self, v: float, i: float, p: float,
                         vset: float, iset: float,
                         out: int, err: int) -> None:
        """
        Write all 13 Holding Registers (0-12) in ONE setValues call.

        Fase 1.3 — torn reads: two separate setValues (HR 0-9 then HR 10-12)
        let the asyncio Modbus server serve a client read between them, exposing
        a half-updated frame. A single setValues does one list-slice assignment,
        which is atomic under the GIL relative to the server's slice read — a
        client sees either the whole old frame or the whole new one, never a mix.
        # ponytail: GIL-atomic slice write, no extra lock needed. If pymodbus
        # ever drops the plain-list datastore, guard with a shared context lock.
        """
        hv,  lv  = _f2r(v)
        hi,  li  = _f2r(i)
        hp,  lp  = _f2r(p)
        hvs, lvs = _f2r(vset)
        his, lis = _f2r(iset)

        self._device.setValues(
            3, 0,
            [hv, lv, hi, li, hp, lp, hvs, lvs, his, lis,
             out, 1, err & 0xFFFF],
        )

    def _read_coil0(self) -> int:
        return int(self._device.getValues(1, 0, 1)[0])

    # -- Polling loop (daemon thread) -----------------------------------------
    def _poll_loop(self) -> None:
        """
        Main cycle: SCPI -> Modbus at the configured rate.

        Time compensation: measures the real time of each cycle and adjusts the
        wait to keep the interval stable at 20 ms.
        """
        dt_s = self.cfg.poll_interval_ms / 1000.0
        log.info(
            f"Bridge: polling started — interval {self.cfg.poll_interval_ms} ms"
        )

        while not self._stop.is_set():
            t_start = time.perf_counter()

            try:
                # --- SCPI read (most expensive part: 8-16 ms) ----------------
                v, i, p = self.ps.read_meas_fast()
                vset    = self.ps.cached_vset
                iset    = self.ps.cached_iset

                # Output state: only every 10 cycles (200 ms) to avoid saturating
                # the serial bus with queries that rarely change
                if not hasattr(self, "_out_cycle"):
                    self._out_cycle = 0
                self._out_cycle = (self._out_cycle + 1) % 10
                if self._out_cycle == 0:
                    out = self.ps.get_output_state()
                    err = self.ps.get_error_code()
                else:
                    # Off-cycle: keep the last known output state (querying it
                    # every cycle would saturate the serial bus).
                    out = self.last_readings.get("output", 0)
                    err = 0

                # --- Write to Modbus registers (< 0.1 ms) --------------------
                self._write_registers(v, i, p, vset, iset, int(out), int(err))

                # --- Cache for the HMI ---------------------------------------
                elapsed_ms = (time.perf_counter() - t_start) * 1000.0
                self.last_readings = {
                    "V":        round(v,    3),
                    "I":        round(i,    4),
                    "P":        round(p,    2),
                    "Vset":     round(vset, 3),
                    "Iset":     round(iset, 4),
                    "output":   int(out),
                    "error":    int(err),
                    "meas_mode": self.ps._meas_mode,
                    "poll_ms":  round(elapsed_ms, 2),
                }

                # --- Coil 0: remote control from SCADA -----------------------
                # Only acts if the pipeline is NOT running a profile
                if not self.profile_running:
                    c0 = self._read_coil0()
                    if c0 != self._prev_coil0:
                        self.ps.output(bool(c0))
                        self._prev_coil0 = c0
                        log.info(
                            f"Bridge: Coil0 -> OUTP {'ON' if c0 else 'OFF'}"
                        )

            except Exception as exc:
                log.error(f"Bridge poll error: {exc}")

            # Time compensation to keep dt stable
            elapsed = time.perf_counter() - t_start
            wait    = max(0.0, dt_s - elapsed)
            if wait > 0:
                self._stop.wait(timeout=wait)   # interruptible

        log.info("Bridge: polling stopped.")

    # -- Modbus TCP server (asyncio) ------------------------------------------
    def _run_modbus_server(self) -> None:
        """
        Run the Modbus TCP server in its own asyncio loop.
        Launched in a daemon thread so it does not block the HMI.
        """
        async def _serve():
            identity = ModbusDeviceIdentification(
                info_name={
                    "VendorName":   "EA Elektro-Automatik",
                    "ProductCode":  "PS10060-170",
                    "ProductName":  "PV Emulator Bridge",
                    "ModelName":    "ScpiModbusBridge 1.0",
                }
            )
            server = ModbusTcpServer(
                context  = self._ctx,
                identity = identity,
                address  = (self.cfg.modbus_host, self.cfg.modbus_port),
            )
            log.info(
                f"Bridge: Modbus TCP at "
                f"{self.cfg.modbus_host}:{self.cfg.modbus_port} "
                f"Unit={self.cfg.modbus_unit}"
            )
            await server.serve_forever()

        # Own asyncio loop (does not share Dash/Flask's)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_serve())
        except Exception as exc:
            log.error(f"Bridge Modbus server error: {exc}")
        finally:
            loop.close()

    def start_shared(self, ser, lock=None) -> None:
        """
        Start the bridge reusing a serial.Serial already opened by
        SCPIController — avoids PermissionError from double-opening COM.

        Use in scpi_cb.py:
            bridge.start_shared(_controller._ser, _controller._lock)

        Concurrency fix (Fase 0.1, Opción A): the bridge poll loop and the
        controller's run_profile write/read the SAME physical port. Their own
        locks (EAPowerSupply._lock vs SCPIController._lock) don't know about each
        other, so commands can interleave and corrupt SCPI framing. Passing the
        controller's RLock here makes EAPowerSupply serialize on the very same
        lock as the controller — one mutex guards the port for both threads.
        """
        if lock is not None:
            self.ps._lock = lock   # share the controller's RLock over this port
        self.ps.ser = ser
        self._owns_serial = False   # serial borrowed from SCPIController
        self._build_datastore()
        self._stop.clear()
        self.running = True

        threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name='BridgePoll',
        ).start()

        threading.Thread(
            target=self._run_modbus_server,
            daemon=True,
            name='BridgeTCP',
        ).start()

        log.info(
            f'Bridge active (shared serial) — '
            f'poll {self.cfg.poll_interval_ms} ms'
        )

    # -- original start() (opens its own COM) ---------------------------------
    # -- Public API -----------------------------------------------------------
    def start(self) -> None:
        """
        Open the COM port, build the Modbus datastore, launch polling and start
        the TCP server. Does NOT block.
        """
        idn = self.ps.open()
        log.info(f"Bridge connected: {idn}")

        self._owns_serial = True   # the bridge opened its own port
        self._build_datastore()
        self._stop.clear()
        self.running = True

        # SCPI -> Modbus polling thread
        threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="BridgePoll",
        ).start()

        # Modbus TCP server thread (asyncio)
        threading.Thread(
            target=self._run_modbus_server,
            daemon=True,
            name="BridgeTCP",
        ).start()

        log.info(
            f"Bridge active — poll {self.cfg.poll_interval_ms} ms, "
            f"modo MEAS: {self.ps._meas_mode}"
        )

    def stop(self) -> None:
        """
        Stop polling and turn the output off.

        Only closes the serial port if the bridge opened it (start()). If the
        serial was borrowed from SCPIController (start_shared()), the reference
        is dropped without closing it, to return clean control to the controller
        and the EAMonitor (which reads SCPI directly again).
        """
        self._stop.set()
        self.running = False
        try:
            self.ps.output(False)
            self.ps.set_voltage(0.0)
        except Exception:
            pass
        if self._owns_serial:
            self.ps.close()
            log.info("Bridge stopped. Output off and port closed.")
        else:
            self.ps.ser = None   # borrowed serial: do not close, just drop
            log.info("Bridge stopped. Output off (serial returned to the controller).")

    def get_readings(self) -> dict:
        """Copy of the latest measurements to display in the HMI."""
        return dict(self.last_readings)