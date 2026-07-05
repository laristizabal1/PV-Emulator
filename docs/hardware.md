# Hardware communication

[← Back to README](../README.md)

The source connects over USB and appears as a virtual COM port. The
`SCPIController` sends SCPI ASCII commands and controls the Modbus bridge.

```mermaid
flowchart LR
    EA["EA-PS 10060-170"] <-->|SCPI / USB-COM| SCPI["SCPIController"]
    SCPI -->|shared serial.Serial| BRIDGE["ScpiModbusBridge<br/>(shared serial)"]
    BRIDGE -->|Modbus TCP| SCADA["SCADA clients"]
```

The serial is opened **once** by `SCPIController` and shared with
`ScpiModbusBridge` via `start_shared()`. When the bridge stops, the serial
remains available to the controller (`_owns_serial` flag).

> **Serial driver.** The EA-PS 10060-170 enumerates as a USB-CDC (virtual COM)
> device. **Windows 10/11** installs the driver automatically — no extra
> download. **Linux** needs no extra driver either; the built-in `cdc-acm`
> module exposes it as `/dev/ttyACM0` (add your user to the `dialout` group for
> access: `sudo usermod -aG dialout $USER`, then re-login). No FTDI/CH340
> driver is required.

---

## Portable configuration

All connection parameters are read from **environment variables** with defaults
in `config/hardware.py`. To run on another machine you do not need to edit any
file.

| Variable | Default | Description |
|----------|---------|-------------|
| `PV_SERIAL_PORT` | `COM3` | Serial port (`COM4`, `/dev/ttyUSB0`, …) |
| `PV_SERIAL_BAUD` | `115200` | SCPI baudrate |
| `PV_MODBUS_HOST` | `0.0.0.0` | Modbus TCP server bind interface |
| `PV_MODBUS_PORT` | `502` | Modbus TCP server port |
| `PV_MODBUS_CLIENT_HOST` | `127.0.0.1` | Host the Modbus monitor points to |

**PowerShell:**
```powershell
$env:PV_SERIAL_PORT = "COM4"
$env:PV_MODBUS_PORT = "5020"   # use a high port to avoid admin permissions
python app.py
```

**Bash/Linux:**
```bash
PV_SERIAL_PORT=/dev/ttyUSB0 python app.py
```

### Serial port auto-detection

If `PV_SERIAL_PORT` is not defined or the configured port does not exist,
`comm/scpi.py` automatically detects the first available port and reports it on
the console. Works on Windows (`COMx`) and Linux/macOS (`/dev/ttyUSB*`).

---

## Cross-platform compatibility

| Aspect | Windows | Linux / macOS |
|---------|---------|---------------|
| Serial port | `COM3`, `COM4`, … | `/dev/ttyUSB0`, `/dev/ttyACM0`, … |
| Auto-detection | ✅ | ✅ |
| Modbus port 502 | Requires admin | Requires root — use `PV_MODBUS_PORT=5020` |
| `pywebview` (desktop) | ✅ | Requires GTK or Qt |
| Browser mode | ✅ | ✅ (fallback always available) |
